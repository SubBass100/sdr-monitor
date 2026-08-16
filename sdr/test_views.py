import base64
import json

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from sdr.app_settings import AppSettings, AppSettingsKey


def _basic_auth_header(username, password):
    credentials = base64.b64encode(f"{username}:{password}".encode("utf-8")).decode("ascii")
    return f"Basic {credentials}"


class FreqtankSettingsViewTestCase(TestCase):
    def setUp(self):
        self.url = reverse("freqtank_settings")
        self.username = "admin"
        self.password = "correct-horse-battery-staple"
        self.superuser = get_user_model().objects.create_superuser(
            username=self.username, email="admin@example.com", password=self.password
        )

    def test_url_is_mounted_at_root_not_under_sdr_prefix(self):
        self.assertEqual(self.url, "/api/freqtank-settings")

    def __put(self, body, auth_header=None):
        kwargs = {"content_type": "application/json"}
        if auth_header is not None:
            kwargs["HTTP_AUTHORIZATION"] = auth_header
        return self.client.put(self.url, data=json.dumps(body), **kwargs)

    def test_valid_auth_and_body_succeeds_and_settings_read_back_correctly(self):
        response = self.__put(
            {"server_url": "https://freqtank.example.com", "api_key": "sekret-key-1"},
            auth_header=_basic_auth_header(self.username, self.password),
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"status": "ok"})
        self.assertEqual(AppSettings.get(AppSettingsKey.FREQTANK_SERVER_URL), "https://freqtank.example.com")
        self.assertEqual(AppSettings.get(AppSettingsKey.FREQTANK_API_KEY), "sekret-key-1")
        self.assertEqual(AppSettings.get(AppSettingsKey.FREQTANK_UPLOAD_MODE), "auto")

    def test_missing_authorization_header_returns_401(self):
        response = self.__put({"server_url": "https://freqtank.example.com", "api_key": "sekret-key-1"})

        self.assertEqual(response.status_code, 401)

    def test_wrong_credentials_returns_401(self):
        response = self.__put(
            {"server_url": "https://freqtank.example.com", "api_key": "sekret-key-1"},
            auth_header=_basic_auth_header(self.username, "wrong-password"),
        )

        self.assertEqual(response.status_code, 401)

    def test_valid_auth_but_missing_fields_returns_400(self):
        response = self.__put(
            {"server_url": "https://freqtank.example.com"},
            auth_header=_basic_auth_header(self.username, self.password),
        )

        self.assertEqual(response.status_code, 400)

    def test_calling_twice_with_different_values_fully_overwrites_not_merges(self):
        self.__put(
            {"server_url": "https://first.example.com", "api_key": "first-key"},
            auth_header=_basic_auth_header(self.username, self.password),
        )

        response = self.__put(
            {"server_url": "https://second.example.com", "api_key": "second-key"},
            auth_header=_basic_auth_header(self.username, self.password),
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(AppSettings.get(AppSettingsKey.FREQTANK_SERVER_URL), "https://second.example.com")
        self.assertEqual(AppSettings.get(AppSettingsKey.FREQTANK_API_KEY), "second-key")
        self.assertEqual(AppSettings.get(AppSettingsKey.FREQTANK_UPLOAD_MODE), "auto")
