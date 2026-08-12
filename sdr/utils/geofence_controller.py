from common.utils.mqtt import MqttSyncClient
from sdr.app_settings import AppSettings, AppSettingsKey
from sdr.utils.geofence import is_outside_geofence
from sdr.utils.location import Location
import json
import logging
import monitor.settings as settings
import threading
import time


class GeofenceController(threading.Thread):
    # `tmp_config` is not persisted by the scanner (that's the whole point - it's a
    # live override, restored to the persisted config on `reset_tmp_config`), and
    # `__applied_state` only lives in this thread's memory. If the scanner process
    # or container restarts while the device is inside the geofence (paused), it
    # comes back up on its *persisted* (enabled) config, but this controller still
    # believes the pause is in effect and, without this reassertion, would never
    # re-publish it - silently resuming scanning inside the fence until the
    # geofence state next changes (which may be never, if the device doesn't
    # leave). So every this-many debounced polls while paused, we re-publish the
    # pause even though nothing changed, to bound how long that drift can persist.
    # Not a specific/tuned number - just a small constant that keeps the staleness
    # window bounded without re-publishing on every single poll.
    #
    # Deliberately scoped to the paused state only (see the `outside is False`
    # check where this is used) - a restart always comes back up scanning (the
    # persisted config), so there's nothing to correct on that side, and
    # reasserting there would periodically republish sdr/reset_tmp_config/<id> on
    # the same channel gain_tester_thread.py uses to run gain tests, which could
    # reset a gain test's applied config out from under it for no reason.
    __REASSERT_EVERY_N_POLLS = 10

    def __init__(self):
        threading.Thread.__init__(self)
        self.__is_running = True
        self.__logger = logging.getLogger("Geofence")
        self.__location = Location()
        self.__consistent_count = 0
        self.__last_state = None  # None = unknown yet, True = outside (scanning), False = inside (paused)
        self.__applied_state = None
        self.__polls_since_apply = 0

    def __evaluate(self):
        mode = AppSettings.get(AppSettingsKey.AUTO_SCAN_MODE)
        if mode != "geofence":
            return None
        radius_m = AppSettings.get(AppSettingsKey.GEOFENCE_RADIUS_M)
        if radius_m <= 0:
            return None
        location = self.__location.get_current_location()
        if location is None:
            return None
        (lat, lon) = location
        center_lat = AppSettings.get(AppSettingsKey.GEOFENCE_CENTER_LAT)
        center_lon = AppSettings.get(AppSettingsKey.GEOFENCE_CENTER_LON)
        return is_outside_geofence(lat, lon, center_lat, center_lon, radius_m)

    def __get_scanner_id(self, client):
        # Mirrors sdr/static/js/config.js's own scanner-discovery pattern: "sdr/list" is a
        # broadcast request (not addressed to a specific scanner), and each connected scanner
        # replies on its own "sdr/status/<scanner_id>" topic -- config.js subscribes to the
        # wildcard "sdr/status/+" and reads the id back out of whichever reply topic(s)
        # arrive. This fork's deployment model is one agent = one SDR-Hub container = one
        # scanner (same 1-agent-1-instance model every other FreqTank integration uses), so
        # taking the first reply within a short timeout is sufficient -- no need for the
        # multi-scanner enumeration a shared, multi-box sdr-monitor install could require.
        client.subscribe("sdr/status/+")
        client.publish("sdr/list")
        (topic, _data) = client.get_message(timeout=5)
        client.unsubscribe("sdr/status/+")
        if topic is None:
            return None
        # `MqttSyncClient` has a single shared response slot that isn't cleared before a
        # new request goes out (see common/utils/mqtt.py) - if an earlier, unrelated
        # `send_and_get` timed out and its reply arrives late, this `get_message()` can
        # return that stale message instead of a real "sdr/status/<id>" reply. Validate
        # the topic shape before trusting it, rather than deriving a garbage "scanner id"
        # (e.g. "success" from a late "sdr/tmp_config/<id>/success") from it.
        if not (topic.startswith("sdr/status/") and len(topic.split("/")) == 3):
            self.__logger.warning("discarding unexpected MQTT reply on topic: %s" % topic)
            return None
        return topic.rsplit("/", 1)[-1]

    def __apply(self, scanning_enabled, client):
        # Hazard for future maintainers: sdr-monitor's own Config page (config.js)
        # populates its form from "sdr/status/<id>" and its Save button republishes
        # that same object back as the *persisted* config. If "sdr/status" reflects
        # the live tmp_config (all devices disabled) while we're paused rather than
        # the persisted config, an operator who opens that page and clicks Save while
        # inside the geofence would persist an all-disabled config - meaning the
        # eventual `reset_tmp_config` below restores an all-disabled setup and
        # scanning never actually resumes. Not verified either way (unclear whether
        # "sdr/status" returns live or persisted config) - flagging as a known risk
        # rather than coding around unconfirmed behavior.
        scanner_id = self.__get_scanner_id(client)
        if scanner_id is None:
            self.__logger.warning("no scanner responded to discovery, skipping")
            return
        if scanning_enabled:
            reply = client.send_and_get(f"sdr/reset_tmp_config/{scanner_id}", f"sdr/reset_tmp_config/{scanner_id}/success")
        else:
            raw_config = client.send_and_get("sdr/list", f"sdr/status/{scanner_id}")
            if raw_config is None:
                self.__logger.warning("timed out fetching scanner config, skipping")
                return
            config = json.loads(raw_config)
            for device in config.get("devices", []):
                device["enabled"] = False
            reply = client.send_and_get(f"sdr/tmp_config/{scanner_id}", f"sdr/tmp_config/{scanner_id}/success", json.dumps(config))
        # `send_and_get` returns None on timeout/no reply (common/utils/mqtt.py). Only
        # record the state as applied - and log success - once the scanner actually
        # confirmed it; otherwise leave `__applied_state` as it was so the next poll
        # (as long as the debounced desired state hasn't changed) retries the apply,
        # instead of silently claiming success on a publish nobody acknowledged.
        if reply is None:
            self.__logger.warning("scanner did not confirm geofence state change (scanning enabled: %s), will retry" % scanning_enabled)
            return
        self.__applied_state = scanning_enabled
        self.__logger.info("geofence state applied, scanning enabled: %s" % scanning_enabled)

    def __apply_and_reset_counter(self, outside, client):
        try:
            self.__apply(outside, client)
        except Exception as e:
            self.__logger.warning("exception: %s" % e)
        self.__polls_since_apply = 0

    def run(self):
        self.__logger.debug("start")
        client = MqttSyncClient(settings.MQTT["url"], settings.MQTT["user"], settings.MQTT["password"], "geofence")
        client.start()
        while self.__is_running:
            outside = self.__evaluate()
            if outside is None:
                self.__consistent_count = 0
                self.__last_state = None
            else:
                if outside == self.__last_state:
                    self.__consistent_count += 1
                else:
                    self.__last_state = outside
                    self.__consistent_count = 1
                debounce = AppSettings.get(AppSettingsKey.GEOFENCE_DEBOUNCE_SAMPLES)
                if self.__consistent_count >= debounce:
                    if self.__applied_state != outside:
                        self.__apply_and_reset_counter(outside, client)
                    elif outside is False:
                        # Only the paused state needs periodic reassertion (see the
                        # class-level comment on __REASSERT_EVERY_N_POLLS): a restarted
                        # scanner container always comes back up on its *persisted*
                        # config, which has devices enabled - i.e. it restarts straight
                        # into the "scanning" state, so there's nothing to correct
                        # there. Reasserting while already scanning would instead
                        # periodically republish sdr/reset_tmp_config/<id> for no
                        # reason, which is the same channel gain_tester_thread.py uses
                        # to run gain tests - doing so could reset a gain test's
                        # applied config out from under it.
                        self.__polls_since_apply += 1
                        if self.__polls_since_apply >= self.__REASSERT_EVERY_N_POLLS:
                            self.__apply_and_reset_counter(outside, client)
            interval_ms = AppSettings.get(AppSettingsKey.GEOFENCE_RECHECK_INTERVAL_MS)
            time.sleep(max(1, interval_ms / 1000))
        client.stop()
        self.__logger.debug("stop")

    def stop(self):
        self.__is_running = False
