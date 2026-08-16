from django.test import TestCase
from sdr.app_settings import AppSettingsKey
from sdr.utils.geofence_controller import GeofenceController
from unittest.mock import MagicMock, patch
import json


class GeofenceControllerTestCase(TestCase):
    def __settings(self, debounce=3):
        values = {
            AppSettingsKey.AUTO_SCAN_MODE: "geofence",
            AppSettingsKey.GEOFENCE_RADIUS_M: 100,
            AppSettingsKey.GEOFENCE_CENTER_LAT: 0.0,
            AppSettingsKey.GEOFENCE_CENTER_LON: 0.0,
            AppSettingsKey.GEOFENCE_DEBOUNCE_SAMPLES: debounce,
            AppSettingsKey.GEOFENCE_RECHECK_INTERVAL_MS: 1,
        }
        return lambda key: values[key]

    def __run_iterations(self, controller, count):
        # `run()`'s loop is a plain `while self.__is_running: ... time.sleep(...)`, so we
        # can drive it synchronously (no real thread needed) by making the mocked
        # `time.sleep` stand-in count down and call `stop()` once exactly `count`
        # iterations have completed, which flips the while condition false on the next
        # check and returns control back to the caller.
        remaining = [count]

        def fake_sleep(_seconds):
            remaining[0] -= 1
            if remaining[0] <= 0:
                controller.stop()

        with patch("sdr.utils.geofence_controller.time.sleep", side_effect=fake_sleep):
            controller._GeofenceController__is_running = True
            controller.run()

    @patch("sdr.utils.geofence_controller.MqttSyncClient")
    @patch.object(GeofenceController, "_GeofenceController__apply")
    @patch("sdr.utils.geofence_controller.Location")
    @patch("sdr.utils.geofence_controller.AppSettings")
    def test_apply_called_once_after_n_consistent_polls_not_before(self, mock_app_settings, mock_location_cls, mock_apply, mock_mqtt_cls):
        debounce = 3
        mock_app_settings.get.side_effect = self.__settings(debounce)
        mock_location = MagicMock()
        # far from the (0, 0) geofence center configured above -> always "outside".
        mock_location.get_current_location.return_value = (10.0, 10.0)
        mock_location_cls.return_value = mock_location
        mock_client = MagicMock()
        mock_mqtt_cls.return_value = mock_client

        controller = GeofenceController()
        # The real `__apply` sets `__applied_state`, which is what makes the
        # "don't re-apply an already-applied state" guard work; since `__apply` is
        # mocked out here, its side effect needs to be reproduced explicitly so that
        # guard still behaves as it would for real.
        mock_apply.side_effect = lambda scanning_enabled, client: setattr(controller, "_GeofenceController__applied_state", scanning_enabled)

        # N-1 consistent "outside" polls: not enough to cross the debounce threshold yet.
        self.__run_iterations(controller, debounce - 1)
        mock_apply.assert_not_called()

        # One more consistent poll crosses the threshold -> apply fires exactly once.
        self.__run_iterations(controller, 1)
        mock_apply.assert_called_once_with(True, mock_client)

        # Further still-consistent "outside" polls must NOT re-trigger apply, since
        # __applied_state already matches the current state.
        self.__run_iterations(controller, 5)
        mock_apply.assert_called_once()

    @patch("sdr.utils.geofence_controller.MqttSyncClient")
    @patch.object(GeofenceController, "_GeofenceController__apply")
    @patch("sdr.utils.geofence_controller.Location")
    @patch("sdr.utils.geofence_controller.AppSettings")
    def test_apply_not_called_when_mode_is_not_geofence(self, mock_app_settings, mock_location_cls, mock_apply, mock_mqtt_cls):
        values = {
            AppSettingsKey.AUTO_SCAN_MODE: "boot",
            AppSettingsKey.GEOFENCE_RADIUS_M: 100,
            AppSettingsKey.GEOFENCE_CENTER_LAT: 0.0,
            AppSettingsKey.GEOFENCE_CENTER_LON: 0.0,
            AppSettingsKey.GEOFENCE_DEBOUNCE_SAMPLES: 1,
            AppSettingsKey.GEOFENCE_RECHECK_INTERVAL_MS: 1,
        }
        mock_app_settings.get.side_effect = lambda key: values[key]
        mock_location = MagicMock()
        mock_location.get_current_location.return_value = (10.0, 10.0)
        mock_location_cls.return_value = mock_location
        mock_mqtt_cls.return_value = MagicMock()

        controller = GeofenceController()
        self.__run_iterations(controller, 5)

        mock_apply.assert_not_called()

    @patch("sdr.utils.geofence_controller.MqttSyncClient")
    @patch.object(GeofenceController, "_GeofenceController__apply")
    @patch("sdr.utils.geofence_controller.Location")
    @patch("sdr.utils.geofence_controller.AppSettings")
    def test_alternating_states_never_reach_debounce_threshold(self, mock_app_settings, mock_location_cls, mock_apply, mock_mqtt_cls):
        # Flip-flopping between "outside" and "inside" every poll should reset the
        # consistent-count run each time, so apply must never fire.
        debounce = 3
        mock_app_settings.get.side_effect = self.__settings(debounce)
        mock_location = MagicMock()
        locations = [(10.0, 10.0), (0.0, 0.0)]  # outside, then inside, alternating
        mock_location.get_current_location.side_effect = lambda: locations[mock_location.get_current_location.call_count % 2]
        mock_location_cls.return_value = mock_location
        mock_mqtt_cls.return_value = MagicMock()

        controller = GeofenceController()
        self.__run_iterations(controller, 10)

        mock_apply.assert_not_called()

    @patch("sdr.utils.geofence_controller.MqttSyncClient")
    @patch.object(GeofenceController, "_GeofenceController__apply")
    @patch("sdr.utils.geofence_controller.Location")
    @patch("sdr.utils.geofence_controller.AppSettings")
    def test_unknown_location_resets_the_consistent_count(self, mock_app_settings, mock_location_cls, mock_apply, mock_mqtt_cls):
        debounce = 3
        mock_app_settings.get.side_effect = self.__settings(debounce)
        mock_location = MagicMock()
        # Two consistent "outside" polls, then a lost fix (None), then only one more
        # consistent poll - since the None reset the count, this must NOT be enough
        # to cross a debounce of 3.
        mock_location.get_current_location.side_effect = [(10.0, 10.0), (10.0, 10.0), None, (10.0, 10.0)]
        mock_location_cls.return_value = mock_location
        mock_mqtt_cls.return_value = MagicMock()

        controller = GeofenceController()
        self.__run_iterations(controller, 4)

        mock_apply.assert_not_called()

    @patch("sdr.utils.geofence_controller.MqttSyncClient")
    @patch.object(GeofenceController, "_GeofenceController__apply")
    @patch("sdr.utils.geofence_controller.Location")
    @patch("sdr.utils.geofence_controller.AppSettings")
    def test_reasserts_paused_state_periodically_even_without_a_transition(self, mock_app_settings, mock_location_cls, mock_apply, mock_mqtt_cls):
        # tmp_config is not persisted by the scanner and __applied_state only lives in
        # this thread's memory, so if the scanner restarts while paused (inside the
        # geofence), it comes back up on its persisted (enabled/scanning) config and
        # nothing would otherwise ever re-publish the pause. Confirms `run()` re-invokes
        # `__apply` for the *same* desired "paused" state once enough consistent polls
        # have elapsed since the last apply, even though `__applied_state` already
        # matches and no transition occurred.
        debounce = 1
        mock_app_settings.get.side_effect = self.__settings(debounce)
        mock_location = MagicMock()
        # exactly at the (0, 0) geofence center configured above -> always "inside" (paused).
        mock_location.get_current_location.return_value = (0.0, 0.0)
        mock_location_cls.return_value = mock_location
        mock_client = MagicMock()
        mock_mqtt_cls.return_value = mock_client

        controller = GeofenceController()
        mock_apply.side_effect = lambda scanning_enabled, client: setattr(controller, "_GeofenceController__applied_state", scanning_enabled)
        reassert_interval = GeofenceController._GeofenceController__REASSERT_EVERY_N_POLLS

        # First poll applies the transition (debounce=1).
        self.__run_iterations(controller, 1)
        mock_apply.assert_called_once_with(False, mock_client)

        # One poll short of the reassertion interval: no re-publish yet.
        self.__run_iterations(controller, reassert_interval - 1)
        mock_apply.assert_called_once()

        # The poll that crosses the reassertion interval re-fires apply for the same
        # (unchanged) desired "paused" state.
        self.__run_iterations(controller, 1)
        self.assertEqual(mock_apply.call_count, 2)

    @patch("sdr.utils.geofence_controller.MqttSyncClient")
    @patch.object(GeofenceController, "_GeofenceController__apply")
    @patch("sdr.utils.geofence_controller.Location")
    @patch("sdr.utils.geofence_controller.AppSettings")
    def test_does_not_reassert_while_desired_state_is_scanning(self, mock_app_settings, mock_location_cls, mock_apply, mock_mqtt_cls):
        # The periodic reassertion exists to correct a scanner restarting while
        # paused and coming back up already-scanning (the persisted config always has
        # devices enabled). While the desired state is "outside" (scanning), a restart
        # would already restart into the desired state, so there's nothing to correct
        # - and reasserting there would periodically republish
        # sdr/reset_tmp_config/<id>, a channel gain_tester_thread.py also uses to run
        # gain tests, resetting an in-progress gain test's config for no reason.
        debounce = 1
        mock_app_settings.get.side_effect = self.__settings(debounce)
        mock_location = MagicMock()
        # far from the (0, 0) geofence center configured above -> always "outside" (scanning).
        mock_location.get_current_location.return_value = (10.0, 10.0)
        mock_location_cls.return_value = mock_location
        mock_client = MagicMock()
        mock_mqtt_cls.return_value = mock_client

        controller = GeofenceController()
        mock_apply.side_effect = lambda scanning_enabled, client: setattr(controller, "_GeofenceController__applied_state", scanning_enabled)
        reassert_interval = GeofenceController._GeofenceController__REASSERT_EVERY_N_POLLS

        # First poll applies the transition (debounce=1).
        self.__run_iterations(controller, 1)
        mock_apply.assert_called_once_with(True, mock_client)

        # Many further consistent "outside" (scanning) polls, well past the
        # reassertion interval - must NOT re-fire apply, since only the paused state
        # is periodically reasserted.
        self.__run_iterations(controller, reassert_interval * 3)
        mock_apply.assert_called_once()

    @patch("sdr.utils.geofence_controller.MqttSyncClient")
    @patch.object(GeofenceController, "_GeofenceController__apply")
    @patch("sdr.utils.geofence_controller.Location")
    @patch("sdr.utils.geofence_controller.AppSettings")
    def test_resumes_when_mode_leaves_geofence_while_paused(self, mock_app_settings, mock_location_cls, mock_apply, mock_mqtt_cls):
        # If AUTO_SCAN_MODE is switched away from "geofence" (to "manual" or "boot",
        # both meant to be "always-on scanning") while the controller currently has the
        # scanner paused, nothing else ever resumes it - __evaluate() would just start
        # returning None every poll from here on, and the `outside is None` branch only
        # resets debounce bookkeeping. Confirms `run()` issues a resume itself in that
        # case instead of leaving the scanner stuck paused forever.
        mode = ["geofence"]
        values = {
            AppSettingsKey.GEOFENCE_RADIUS_M: 100,
            AppSettingsKey.GEOFENCE_CENTER_LAT: 0.0,
            AppSettingsKey.GEOFENCE_CENTER_LON: 0.0,
            AppSettingsKey.GEOFENCE_DEBOUNCE_SAMPLES: 1,
            AppSettingsKey.GEOFENCE_RECHECK_INTERVAL_MS: 1,
        }

        def get_setting(key):
            if key == AppSettingsKey.AUTO_SCAN_MODE:
                return mode[0]
            return values[key]

        mock_app_settings.get.side_effect = get_setting
        mock_location = MagicMock()
        # exactly at the (0, 0) geofence center configured above -> always "inside" (paused).
        mock_location.get_current_location.return_value = (0.0, 0.0)
        mock_location_cls.return_value = mock_location
        mock_client = MagicMock()
        mock_mqtt_cls.return_value = mock_client

        controller = GeofenceController()
        mock_apply.side_effect = lambda scanning_enabled, client: setattr(controller, "_GeofenceController__applied_state", scanning_enabled)

        # First poll (debounce=1) applies the pause.
        self.__run_iterations(controller, 1)
        mock_apply.assert_called_once_with(False, mock_client)

        # Operator switches auto-scan mode away from "geofence" while paused.
        mode[0] = "manual"
        self.__run_iterations(controller, 1)

        self.assertEqual(mock_apply.call_count, 2)
        mock_apply.assert_called_with(True, mock_client)
        self.assertEqual(controller._GeofenceController__applied_state, True)

    @patch("sdr.utils.geofence_controller.MqttSyncClient")
    @patch.object(GeofenceController, "_GeofenceController__apply")
    @patch("sdr.utils.geofence_controller.Location")
    @patch("sdr.utils.geofence_controller.AppSettings")
    def test_does_not_resume_when_mode_leaves_geofence_while_already_scanning(self, mock_app_settings, mock_location_cls, mock_apply, mock_mqtt_cls):
        # Sanity check for the fix above: if the controller was already scanning (not
        # paused) when geofence mode is turned off, there's nothing to resume - apply
        # must not fire just because the mode changed.
        mode = ["geofence"]
        values = {
            AppSettingsKey.GEOFENCE_RADIUS_M: 100,
            AppSettingsKey.GEOFENCE_CENTER_LAT: 0.0,
            AppSettingsKey.GEOFENCE_CENTER_LON: 0.0,
            AppSettingsKey.GEOFENCE_DEBOUNCE_SAMPLES: 1,
            AppSettingsKey.GEOFENCE_RECHECK_INTERVAL_MS: 1,
        }

        def get_setting(key):
            if key == AppSettingsKey.AUTO_SCAN_MODE:
                return mode[0]
            return values[key]

        mock_app_settings.get.side_effect = get_setting
        mock_location = MagicMock()
        # far from the (0, 0) geofence center configured above -> always "outside" (scanning).
        mock_location.get_current_location.return_value = (10.0, 10.0)
        mock_location_cls.return_value = mock_location
        mock_client = MagicMock()
        mock_mqtt_cls.return_value = mock_client

        controller = GeofenceController()
        mock_apply.side_effect = lambda scanning_enabled, client: setattr(controller, "_GeofenceController__applied_state", scanning_enabled)

        # First poll (debounce=1) applies the "scanning" state.
        self.__run_iterations(controller, 1)
        mock_apply.assert_called_once_with(True, mock_client)

        mode[0] = "manual"
        self.__run_iterations(controller, 5)

        mock_apply.assert_called_once()


class GeofenceControllerApplyTestCase(TestCase):
    def __client_replying_with_scanner_id(self, scanner_id="scanner1"):
        client = MagicMock()
        client.get_message.return_value = (f"sdr/status/{scanner_id}", None)
        return client

    def test_apply_pause_path_disables_all_devices_and_publishes_tmp_config(self):
        controller = GeofenceController()
        client = self.__client_replying_with_scanner_id()
        config = {"devices": [{"driver": "rtlsdr", "serial": "1", "enabled": True}, {"driver": "rtlsdr", "serial": "2", "enabled": True}]}
        client.send_and_get.side_effect = [json.dumps(config), "ok"]  # "sdr/list" fetch, then the tmp_config publish's success reply

        controller._GeofenceController__apply(False, client)

        fetch_call = client.send_and_get.call_args_list[0]
        self.assertEqual(fetch_call.args[0], "sdr/list")
        self.assertEqual(fetch_call.args[1], "sdr/status/scanner1")

        publish_call = client.send_and_get.call_args_list[1]
        self.assertEqual(publish_call.args[0], "sdr/tmp_config/scanner1")
        self.assertEqual(publish_call.args[1], "sdr/tmp_config/scanner1/success")
        published_config = json.loads(publish_call.args[2])
        self.assertTrue(all(device["enabled"] is False for device in published_config["devices"]))

        self.assertEqual(controller._GeofenceController__applied_state, False)

    def test_apply_resume_path_publishes_reset_tmp_config(self):
        controller = GeofenceController()
        client = self.__client_replying_with_scanner_id()
        client.send_and_get.return_value = "ok"

        controller._GeofenceController__apply(True, client)

        client.send_and_get.assert_called_once_with("sdr/reset_tmp_config/scanner1", "sdr/reset_tmp_config/scanner1/success")
        self.assertEqual(controller._GeofenceController__applied_state, True)

    def test_apply_does_not_update_applied_state_on_failed_resume_reply(self):
        controller = GeofenceController()
        client = self.__client_replying_with_scanner_id()
        client.send_and_get.return_value = None  # timeout / no confirmation

        controller._GeofenceController__apply(True, client)

        self.assertIsNone(controller._GeofenceController__applied_state)

    def test_apply_does_not_update_applied_state_on_failed_pause_publish_reply(self):
        controller = GeofenceController()
        client = self.__client_replying_with_scanner_id()
        config = {"devices": [{"driver": "rtlsdr", "serial": "1", "enabled": True}]}
        # config fetch succeeds, but the tmp_config publish itself is never confirmed.
        client.send_and_get.side_effect = [json.dumps(config), None]

        controller._GeofenceController__apply(False, client)

        self.assertIsNone(controller._GeofenceController__applied_state)

    def test_apply_does_not_update_applied_state_when_config_fetch_fails(self):
        controller = GeofenceController()
        client = self.__client_replying_with_scanner_id()
        client.send_and_get.return_value = None  # "sdr/list" fetch itself times out

        controller._GeofenceController__apply(False, client)

        self.assertIsNone(controller._GeofenceController__applied_state)
        client.send_and_get.assert_called_once()  # never got to the tmp_config publish

    def test_apply_skips_when_scanner_id_cannot_be_discovered(self):
        controller = GeofenceController()
        client = MagicMock()
        client.get_message.return_value = (None, None)  # discovery timed out

        controller._GeofenceController__apply(True, client)

        client.send_and_get.assert_not_called()
        self.assertIsNone(controller._GeofenceController__applied_state)

    def test_get_scanner_id_discards_stale_reply_on_unrelated_topic(self):
        # MqttSyncClient has a single shared response slot that isn't cleared before a
        # new request (common/utils/mqtt.py) - a late reply from an earlier, unrelated
        # send_and_get can surface here instead of a real "sdr/status/<id>" reply.
        controller = GeofenceController()
        client = MagicMock()
        client.get_message.return_value = ("sdr/tmp_config/scanner1/success", None)

        scanner_id = controller._GeofenceController__get_scanner_id(client)

        self.assertIsNone(scanner_id)

    def test_get_scanner_id_accepts_well_formed_status_topic(self):
        controller = GeofenceController()
        client = self.__client_replying_with_scanner_id("scanner1")

        scanner_id = controller._GeofenceController__get_scanner_id(client)

        self.assertEqual(scanner_id, "scanner1")
