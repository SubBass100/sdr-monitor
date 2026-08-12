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
    def __init__(self):
        threading.Thread.__init__(self)
        self.__is_running = True
        self.__logger = logging.getLogger("Geofence")
        self.__location = Location()
        self.__consistent_count = 0
        self.__last_state = None  # None = unknown yet, True = outside (scanning), False = inside (paused)
        self.__applied_state = None

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
        return topic.rsplit("/", 1)[-1]

    def __apply(self, scanning_enabled, client):
        scanner_id = self.__get_scanner_id(client)
        if scanner_id is None:
            self.__logger.warning("no scanner responded to discovery, skipping")
            return
        if scanning_enabled:
            client.send_and_get(f"sdr/reset_tmp_config/{scanner_id}", f"sdr/reset_tmp_config/{scanner_id}/success")
        else:
            config = json.loads(client.send_and_get("sdr/list", f"sdr/status/{scanner_id}"))
            for device in config.get("devices", []):
                device["enabled"] = False
            client.send_and_get(f"sdr/tmp_config/{scanner_id}", f"sdr/tmp_config/{scanner_id}/success", json.dumps(config))
        self.__applied_state = scanning_enabled
        self.__logger.info("geofence state applied, scanning enabled: %s" % scanning_enabled)

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
                if self.__consistent_count >= debounce and self.__applied_state != outside:
                    try:
                        self.__apply(outside, client)
                    except Exception as e:
                        self.__logger.warning("exception: %s" % e)
            interval_ms = AppSettings.get(AppSettingsKey.GEOFENCE_RECHECK_INTERVAL_MS)
            time.sleep(max(1, interval_ms / 1000))
        client.stop()
        self.__logger.debug("stop")

    def stop(self):
        self.__is_running = False
