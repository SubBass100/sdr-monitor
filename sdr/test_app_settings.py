from django.test import TestCase
from sdr.app_settings import AppSettingsForm


class AppSettingsFormFreqtankUploadModeTestCase(TestCase):
    def test_direct_is_not_an_offered_choice(self):
        choice_values = [value for (value, _label) in AppSettingsForm.base_fields["freqtank_upload_mode"].choices]

        self.assertEqual(choice_values, ["off", "auto"])
        self.assertNotIn("direct", choice_values)

    def __valid_form_data(self, freqtank_upload_mode):
        # Minimal set of required fields for the form to validate cleanly, so the
        # only thing under test is whether `freqtank_upload_mode` itself is accepted.
        return {
            "spectrograms_total_size_gb": 0,
            "transmissions_total_size_gb": 0,
            "location_source": "gps",
            "auto_scan_mode": "boot",
            "geofence_debounce_samples": 3,
            "geofence_recheck_interval_ms": 30000,
            "freqtank_upload_mode": freqtank_upload_mode,
            "freqtank_check_interval_ms": 5000,
        }

    def test_form_bound_with_direct_is_invalid(self):
        form = AppSettingsForm(data=self.__valid_form_data("direct"))

        self.assertFalse(form.is_valid())
        self.assertIn("freqtank_upload_mode", form.errors)

    def test_form_bound_with_auto_is_still_valid(self):
        form = AppSettingsForm(data=self.__valid_form_data("auto"))

        self.assertTrue(form.is_valid())

    def test_form_bound_with_off_is_still_valid(self):
        form = AppSettingsForm(data=self.__valid_form_data("off"))

        self.assertTrue(form.is_valid())
