from django.test import TestCase
from sdr.app_settings import AppSettingsKey
from sdr.utils.geofence_controller import GeofenceController
from unittest.mock import MagicMock, patch


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
