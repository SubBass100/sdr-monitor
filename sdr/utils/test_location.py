from django.test import TestCase
from sdr.app_settings import AppSettingsKey
from unittest.mock import MagicMock, patch
import sdr.utils.location


class LocationTestCase(TestCase):
    @patch("sdr.utils.location.AppSettings")
    def test_manual_source_returns_configured_lat_lon(self, mock_app_settings):
        values = {
            AppSettingsKey.LOCATION_SOURCE: "manual",
            AppSettingsKey.MANUAL_LAT: 51.5,
            AppSettingsKey.MANUAL_LON: -0.1,
        }
        mock_app_settings.get.side_effect = lambda key: values[key]

        result = sdr.utils.location.Location().get_current_location()

        self.assertEqual(result, (51.5, -0.1))

    @patch("sdr.utils.location.GPSDClient", None)
    @patch("sdr.utils.location.AppSettings")
    def test_gps_source_returns_none_when_library_not_installed(self, mock_app_settings):
        mock_app_settings.get.return_value = "gps"

        result = sdr.utils.location.Location().get_current_location()

        self.assertIsNone(result)

    @patch("sdr.utils.location.GPSDClient")
    @patch("sdr.utils.location.AppSettings")
    def test_gps_source_returns_none_when_gpsd_unreachable(self, mock_app_settings, mock_gpsd_client_cls):
        mock_app_settings.get.return_value = "gps"
        mock_gpsd_client_cls.side_effect = ConnectionRefusedError("no gpsd listening")

        result = sdr.utils.location.Location().get_current_location()

        self.assertIsNone(result)

    @patch("sdr.utils.location.GPSDClient")
    @patch("sdr.utils.location.AppSettings")
    def test_gps_source_returns_fix_once_available(self, mock_app_settings, mock_gpsd_client_cls):
        mock_app_settings.get.return_value = "gps"
        mock_client = MagicMock()
        mock_client.__enter__.return_value = mock_client
        mock_client.__exit__.return_value = False
        # gpsd typically emits one or more "no fix yet" TPV reports (no lat/lon keys)
        # before a real fix shows up in the same stream.
        mock_client.dict_stream.return_value = iter(
            [
                {"class": "TPV", "mode": 1},
                {"class": "TPV", "mode": 3, "lat": 51.5, "lon": -0.1},
            ]
        )
        mock_gpsd_client_cls.return_value = mock_client

        result = sdr.utils.location.Location().get_current_location()

        self.assertEqual(result, (51.5, -0.1))
