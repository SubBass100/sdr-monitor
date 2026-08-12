from sdr.app_settings import AppSettings, AppSettingsKey
import logging
import time

try:
    from gpsdclient import GPSDClient
except ImportError:
    GPSDClient = None


class Location:
    # `get_current_location()` runs synchronously on the MQTT message-handling thread
    # (see transmission_reader.py), so how long we're willing to wait for a GPS fix
    # matters beyond just this call. This is a *total elapsed-time* deadline for the
    # whole "wait for a fix" loop below - see the comment in __get_gps_fix() for why
    # gpsdclient's own `timeout` constructor arg isn't sufficient on its own.
    __MAX_WAIT_SECONDS = 5

    def __init__(self):
        self.__logger = logging.getLogger("Location")

    def __get_gps_fix(self):
        if GPSDClient is None:
            self.__logger.warning("gpsdclient not installed")
            return None
        try:
            # `timeout` bounds every individual socket read on this connection
            # (gpsdclient sets it via socket.create_connection(), and it stays in
            # effect for all subsequent recv() calls, not just the initial connect) -
            # but it is a *per-recv* timeout, not a deadline for the whole stream. gpsd
            # keeps emitting TPV reports at ~1Hz even with no fix (mode 1, no lat/lon
            # keys) while it re-acquires satellites, so as long as gpsd is alive this
            # per-recv timeout alone never trips: each read succeeds well within
            # `timeout`, it's just that none of the reports carry a fix yet. A cold GPS
            # can take tens of seconds to minutes to lock, so without our own
            # elapsed-time deadline (`__MAX_WAIT_SECONDS`, tracked via
            # `time.monotonic()` below) this loop could block for that entire duration
            # instead of the ~5s this is meant to bound.
            with GPSDClient(host="127.0.0.1", timeout=self.__MAX_WAIT_SECONDS) as client:
                start = time.monotonic()
                for result in client.dict_stream(convert_datetime=False, filter=["TPV"]):
                    lat = result.get("lat")
                    lon = result.get("lon")
                    if lat is not None and lon is not None:
                        return (lat, lon)
                    if time.monotonic() - start > self.__MAX_WAIT_SECONDS:
                        self.__logger.warning("timed out waiting for a GPS fix")
                        break
        except Exception as e:
            self.__logger.warning("exception: %s" % e)
        return None

    def get_current_location(self):
        source = AppSettings.get(AppSettingsKey.LOCATION_SOURCE)
        if source == "manual":
            lat = AppSettings.get(AppSettingsKey.MANUAL_LAT)
            lon = AppSettings.get(AppSettingsKey.MANUAL_LON)
            return (lat, lon)
        return self.__get_gps_fix()
