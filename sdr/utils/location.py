from sdr.app_settings import AppSettings, AppSettingsKey
import logging

try:
    from gpsdclient import GPSDClient
except ImportError:
    GPSDClient = None


class Location:
    def __init__(self):
        self.__logger = logging.getLogger("Location")

    def __get_gps_fix(self):
        if GPSDClient is None:
            self.__logger.warning("gpsdclient not installed")
            return None
        try:
            # `timeout` bounds every socket read on this connection (gpsdclient sets it
            # via socket.create_connection(), and it stays in effect for all subsequent
            # recv() calls, not just the initial connect), so this can't block forever
            # waiting for a fix. It's needed because the TPV reports gpsd emits right
            # after a client connects are frequently "no fix yet" (mode 1, no lat/lon
            # keys at all) while it re-acquires satellites - only later TPV reports in
            # the same stream carry an actual fix - so we must keep reading until either
            # a fix with lat/lon shows up or we time out, not just inspect the first one.
            with GPSDClient(host="127.0.0.1", timeout=5) as client:
                for result in client.dict_stream(convert_datetime=False, filter=["TPV"]):
                    lat = result.get("lat")
                    lon = result.get("lon")
                    if lat is not None and lon is not None:
                        return (lat, lon)
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
