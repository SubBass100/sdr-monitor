from common.utils.type import *
from django import forms
from enum import Enum
from sdr.models import *


class AppSettingsKey(Enum):
    SPECTROGRAMS_TOTAL_SIZE_GB = ("spectrograms_total_size_gb", 0, int)
    TRANSMISSIONS_TOTAL_SIZE_GB = ("transmissions_total_size_gb", 0, int)
    N2YO_API_KEY = ("n2yo_api_key", "", str)
    GAIN_TESTER_DATA_ENABLED = ("gain_tester_data_enabled", False, str_to_bool)
    LOCATION_SOURCE = ("location_source", "gps", str)
    MANUAL_LAT = ("manual_lat", 0.0, float)
    MANUAL_LON = ("manual_lon", 0.0, float)
    AUTO_SCAN_MODE = ("auto_scan_mode", "boot", str)
    GEOFENCE_CENTER_LAT = ("geofence_center_lat", 0.0, float)
    GEOFENCE_CENTER_LON = ("geofence_center_lon", 0.0, float)
    GEOFENCE_RADIUS_M = ("geofence_radius_m", 0, int)
    GEOFENCE_DEBOUNCE_SAMPLES = ("geofence_debounce_samples", 3, int)
    GEOFENCE_RECHECK_INTERVAL_MS = ("geofence_recheck_interval_ms", 30000, int)
    FREQTANK_SERVER_URL = ("freqtank_server_url", "", str)
    FREQTANK_API_KEY = ("freqtank_api_key", "", str)
    FREQTANK_UPLOAD_MODE = ("freqtank_upload_mode", "off", str)  # valid values: "off", "auto" (see AppSettingsForm.freqtank_upload_mode's choices)
    FREQTANK_CHECK_INTERVAL_MS = ("freqtank_check_interval_ms", 5000, int)

    def __init__(self, key: str, default, cast):
        self.key = key
        self.default = default
        self.cast = cast


class AppSettings:
    @staticmethod
    def get(setting: AppSettingsKey):
        try:
            obj = AppSetting.objects.get(key=setting.key)
            return setting.cast(obj.value)
        except AppSetting.DoesNotExist:
            return setting.default
        except (ValueError, TypeError):
            return setting.default

    @staticmethod
    def set(setting: AppSettingsKey, value):
        AppSetting.objects.update_or_create(key=setting.key, defaults={"value": str(value)})


class AppSettingsForm(forms.Form):
    n2yo_api_key = forms.CharField(label="n2yo api key", max_length=255, required=False, help_text="get your key from https://n2yo.com/api/")
    spectrograms_total_size_gb = forms.IntegerField(label="Spectrograms size", help_text="keep only the last n GB of spectrograms, 0 for unlimited")
    transmissions_total_size_gb = forms.IntegerField(label="Transmissions size", help_text="keep only the last n GB of transmissions, 0 for unlimited")
    gain_tester_data_enabled = forms.BooleanField(label="Gain tester data", help_text="show spectrograms and transmissions in table view", required=False)
    location_source = forms.ChoiceField(label="Location source", choices=[("gps", "GPS"), ("manual", "Manual")])
    manual_lat = forms.FloatField(label="Manual latitude", required=False)
    manual_lon = forms.FloatField(label="Manual longitude", required=False)
    auto_scan_mode = forms.ChoiceField(label="Auto-scan mode", choices=[("manual", "Manual"), ("boot", "Start automatically at boot"), ("geofence", "Start automatically outside the geofence")])
    geofence_center_lat = forms.FloatField(label="Geofence center latitude", required=False)
    geofence_center_lon = forms.FloatField(label="Geofence center longitude", required=False)
    geofence_radius_m = forms.IntegerField(label="Geofence radius (m)", required=False, help_text="0 disables the geofence even if auto-scan mode is set to geofence")
    geofence_debounce_samples = forms.IntegerField(label="Geofence debounce samples", help_text="consecutive same-state polls required before pausing/resuming")
    geofence_recheck_interval_ms = forms.IntegerField(label="Geofence recheck interval (ms)")
    freqtank_server_url = forms.CharField(label="FreqTank server URL", required=False)
    freqtank_api_key = forms.CharField(label="FreqTank API key", required=False, widget=forms.PasswordInput(render_value=True))
    freqtank_upload_mode = forms.ChoiceField(label="FreqTank upload mode", choices=[("off", "Off"), ("auto", "Automatic")])
    freqtank_check_interval_ms = forms.IntegerField(label="FreqTank check interval (ms)")

    def load_initial(self):
        self.initial = {
            AppSettingsKey.SPECTROGRAMS_TOTAL_SIZE_GB.key: AppSettings.get(AppSettingsKey.SPECTROGRAMS_TOTAL_SIZE_GB),
            AppSettingsKey.TRANSMISSIONS_TOTAL_SIZE_GB.key: AppSettings.get(AppSettingsKey.TRANSMISSIONS_TOTAL_SIZE_GB),
            AppSettingsKey.N2YO_API_KEY.key: AppSettings.get(AppSettingsKey.N2YO_API_KEY),
            AppSettingsKey.GAIN_TESTER_DATA_ENABLED.key: AppSettings.get(AppSettingsKey.GAIN_TESTER_DATA_ENABLED),
            AppSettingsKey.LOCATION_SOURCE.key: AppSettings.get(AppSettingsKey.LOCATION_SOURCE),
            AppSettingsKey.MANUAL_LAT.key: AppSettings.get(AppSettingsKey.MANUAL_LAT),
            AppSettingsKey.MANUAL_LON.key: AppSettings.get(AppSettingsKey.MANUAL_LON),
            AppSettingsKey.AUTO_SCAN_MODE.key: AppSettings.get(AppSettingsKey.AUTO_SCAN_MODE),
            AppSettingsKey.GEOFENCE_CENTER_LAT.key: AppSettings.get(AppSettingsKey.GEOFENCE_CENTER_LAT),
            AppSettingsKey.GEOFENCE_CENTER_LON.key: AppSettings.get(AppSettingsKey.GEOFENCE_CENTER_LON),
            AppSettingsKey.GEOFENCE_RADIUS_M.key: AppSettings.get(AppSettingsKey.GEOFENCE_RADIUS_M),
            AppSettingsKey.GEOFENCE_DEBOUNCE_SAMPLES.key: AppSettings.get(AppSettingsKey.GEOFENCE_DEBOUNCE_SAMPLES),
            AppSettingsKey.GEOFENCE_RECHECK_INTERVAL_MS.key: AppSettings.get(AppSettingsKey.GEOFENCE_RECHECK_INTERVAL_MS),
            AppSettingsKey.FREQTANK_SERVER_URL.key: AppSettings.get(AppSettingsKey.FREQTANK_SERVER_URL),
            AppSettingsKey.FREQTANK_API_KEY.key: AppSettings.get(AppSettingsKey.FREQTANK_API_KEY),
            AppSettingsKey.FREQTANK_UPLOAD_MODE.key: AppSettings.get(AppSettingsKey.FREQTANK_UPLOAD_MODE),
            AppSettingsKey.FREQTANK_CHECK_INTERVAL_MS.key: AppSettings.get(AppSettingsKey.FREQTANK_CHECK_INTERVAL_MS),
        }

    def save(self):
        data = self.cleaned_data
        AppSettings.set(AppSettingsKey.SPECTROGRAMS_TOTAL_SIZE_GB, data[AppSettingsKey.SPECTROGRAMS_TOTAL_SIZE_GB.key])
        AppSettings.set(AppSettingsKey.TRANSMISSIONS_TOTAL_SIZE_GB, data[AppSettingsKey.TRANSMISSIONS_TOTAL_SIZE_GB.key])
        AppSettings.set(AppSettingsKey.N2YO_API_KEY, data[AppSettingsKey.N2YO_API_KEY.key])
        AppSettings.set(AppSettingsKey.GAIN_TESTER_DATA_ENABLED, data[AppSettingsKey.GAIN_TESTER_DATA_ENABLED.key])
        AppSettings.set(AppSettingsKey.LOCATION_SOURCE, data[AppSettingsKey.LOCATION_SOURCE.key])
        AppSettings.set(AppSettingsKey.MANUAL_LAT, data[AppSettingsKey.MANUAL_LAT.key])
        AppSettings.set(AppSettingsKey.MANUAL_LON, data[AppSettingsKey.MANUAL_LON.key])
        AppSettings.set(AppSettingsKey.AUTO_SCAN_MODE, data[AppSettingsKey.AUTO_SCAN_MODE.key])
        AppSettings.set(AppSettingsKey.GEOFENCE_CENTER_LAT, data[AppSettingsKey.GEOFENCE_CENTER_LAT.key])
        AppSettings.set(AppSettingsKey.GEOFENCE_CENTER_LON, data[AppSettingsKey.GEOFENCE_CENTER_LON.key])
        AppSettings.set(AppSettingsKey.GEOFENCE_RADIUS_M, data[AppSettingsKey.GEOFENCE_RADIUS_M.key])
        AppSettings.set(AppSettingsKey.GEOFENCE_DEBOUNCE_SAMPLES, data[AppSettingsKey.GEOFENCE_DEBOUNCE_SAMPLES.key])
        AppSettings.set(AppSettingsKey.GEOFENCE_RECHECK_INTERVAL_MS, data[AppSettingsKey.GEOFENCE_RECHECK_INTERVAL_MS.key])
        AppSettings.set(AppSettingsKey.FREQTANK_SERVER_URL, data[AppSettingsKey.FREQTANK_SERVER_URL.key])
        AppSettings.set(AppSettingsKey.FREQTANK_API_KEY, data[AppSettingsKey.FREQTANK_API_KEY.key])
        AppSettings.set(AppSettingsKey.FREQTANK_UPLOAD_MODE, data[AppSettingsKey.FREQTANK_UPLOAD_MODE.key])
        AppSettings.set(AppSettingsKey.FREQTANK_CHECK_INTERVAL_MS, data[AppSettingsKey.FREQTANK_CHECK_INTERVAL_MS.key])
