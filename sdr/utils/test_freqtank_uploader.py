from django.test import TestCase
from django.utils import timezone
from sdr.app_settings import AppSettings, AppSettingsKey
from sdr.models import AudioClass, Device, Group, Transmission, get_default_audio_class_id
from sdr.utils.freqtank_uploader import FreqTankUploader
from unittest.mock import MagicMock, patch
import os


def build_transmission(modulation="FM", audio_class=None, end_seconds_ago=30, uploaded=False, lat=None, lon=None):
    device = Device.objects.create(name="Test device", raw_name="test_device_%s" % modulation)
    group = Group.objects.get_or_create(name="Test group", begin_frequency=100000000, end_frequency=100100000, modulation=modulation)[0]
    if audio_class is None:
        audio_class = AudioClass.objects.get_or_create(name="Speech", subname="Speech")[0]
    now = timezone.now()
    end_date = now - timezone.timedelta(seconds=end_seconds_ago)
    begin_date = end_date - timezone.timedelta(seconds=5)
    return Transmission.objects.create(
        begin_frequency=100000000,
        end_frequency=100100000,
        begin_date=begin_date,
        end_date=end_date,
        sample_size=2,
        data_file="transmission/fake.bin",
        data_type="audio",
        audio_class=audio_class,
        group=group,
        device=device,
        source="test",
        lat=lat,
        lon=lon,
        uploaded_at=timezone.now() if uploaded else None,
    )


def write_fake_wav(in_file, out_file, modulation, sample_rate, **kwargs):
    # Stand-in for a real decode_audio() call: writes non-empty bytes to `out_file`
    # (the path FreqTankUploader.__upload() creates via NamedTemporaryFile) so tests
    # that aren't specifically exercising the empty-WAV guard don't trip it -- a real
    # NamedTemporaryFile starts out empty, and decode_audio is mocked out everywhere,
    # so without this the temp file would stay 0 bytes in every test.
    with open(out_file, "wb") as f:
        f.write(b"RIFF....WAVEfmt fake-audio-bytes")


def success_response():
    return MagicMock(status_code=200)


class FreqTankUploaderUploadTestCase(TestCase):
    def setUp(self):
        AppSettings.set(AppSettingsKey.FREQTANK_SERVER_URL, "http://freqtank.example.com")
        AppSettings.set(AppSettingsKey.FREQTANK_API_KEY, "secret-key")

    @patch("sdr.utils.freqtank_uploader.requests.post")
    @patch("sdr.utils.freqtank_uploader.decode_audio", side_effect=write_fake_wav)
    def test_successful_upload_sets_uploaded_at(self, mock_decode_audio, mock_post):
        mock_post.return_value = success_response()
        t = build_transmission()

        uploader = FreqTankUploader()
        uploader._FreqTankUploader__upload(t)

        t.refresh_from_db()
        self.assertIsNotNone(t.uploaded_at)
        mock_post.assert_called_once()

    @patch("sdr.utils.freqtank_uploader.requests.post")
    @patch("sdr.utils.freqtank_uploader.decode_audio", side_effect=write_fake_wav)
    def test_upload_disables_following_redirects(self, mock_decode_audio, mock_post):
        # allow_redirects=True (requests' default) would silently turn a 3xx-redirected
        # POST into a GET, dropping the multipart body -- the redirect must surface as
        # a response instead of being followed transparently.
        mock_post.return_value = success_response()
        t = build_transmission()

        uploader = FreqTankUploader()
        uploader._FreqTankUploader__upload(t)

        self.assertEqual(mock_post.call_args.kwargs["allow_redirects"], False)

    @patch("sdr.utils.freqtank_uploader.requests.post")
    @patch("sdr.utils.freqtank_uploader.decode_audio", side_effect=write_fake_wav)
    def test_non_2xx_status_leaves_uploaded_at_null(self, mock_decode_audio, mock_post):
        # A bare raise_for_status() would miss this: it only raises for status >= 400,
        # so a redirect (3xx) followed transparently, or any other non-2xx that isn't
        # itself an error status, would otherwise be treated as a confirmed upload.
        mock_post.return_value = MagicMock(status_code=302)
        t = build_transmission()

        uploader = FreqTankUploader()
        with self.assertRaises(Exception):
            uploader._FreqTankUploader__upload(t)

        t.refresh_from_db()
        self.assertIsNone(t.uploaded_at)

    @patch("sdr.utils.freqtank_uploader.requests.post")
    @patch("sdr.utils.freqtank_uploader.decode_audio", side_effect=write_fake_wav)
    def test_post_exception_leaves_uploaded_at_null(self, mock_decode_audio, mock_post):
        mock_post.side_effect = ConnectionError("network unreachable")
        t = build_transmission()

        uploader = FreqTankUploader()
        with self.assertRaises(Exception):
            uploader._FreqTankUploader__upload(t)

        t.refresh_from_db()
        self.assertIsNone(t.uploaded_at)

    @patch("sdr.utils.freqtank_uploader.requests.post")
    @patch("sdr.utils.freqtank_uploader.decode_audio", side_effect=write_fake_wav)
    def test_temp_wav_removed_on_success(self, mock_decode_audio, mock_post):
        mock_post.return_value = success_response()
        t = build_transmission()

        uploader = FreqTankUploader()
        uploader._FreqTankUploader__upload(t)

        out_file = mock_decode_audio.call_args[0][1]
        self.assertFalse(os.path.exists(out_file))

    @patch("sdr.utils.freqtank_uploader.requests.post")
    @patch("sdr.utils.freqtank_uploader.decode_audio", side_effect=write_fake_wav)
    def test_temp_wav_removed_on_failure(self, mock_decode_audio, mock_post):
        mock_post.return_value = MagicMock(status_code=500)
        t = build_transmission()

        uploader = FreqTankUploader()
        with self.assertRaises(Exception):
            uploader._FreqTankUploader__upload(t)

        out_file = mock_decode_audio.call_args[0][1]
        self.assertFalse(os.path.exists(out_file))

    @patch("sdr.utils.freqtank_uploader.requests.post")
    @patch("sdr.utils.freqtank_uploader.decode_audio")
    def test_decode_audio_exception_leaves_uploaded_at_null_and_cleans_up(self, mock_decode_audio, mock_post):
        mock_decode_audio.side_effect = Exception("gnuradio flowgraph failed")
        t = build_transmission()

        uploader = FreqTankUploader()
        with self.assertRaises(Exception):
            uploader._FreqTankUploader__upload(t)

        # decode_audio never got to write anything, and requests.post must never be
        # reached -- the failure happened before any network call was attempted.
        mock_post.assert_not_called()
        t.refresh_from_db()
        self.assertIsNone(t.uploaded_at)
        out_file = mock_decode_audio.call_args[0][1]
        self.assertFalse(os.path.exists(out_file))

    @patch("sdr.utils.freqtank_uploader.requests.post")
    @patch("sdr.utils.freqtank_uploader.decode_audio")
    def test_empty_decoded_wav_leaves_uploaded_at_null_and_cleans_up(self, mock_decode_audio, mock_post):
        # decode_audio "succeeds" (raises nothing) but the source IQ was corrupt/
        # truncated and no real samples came out -- the temp file NamedTemporaryFile
        # creates is empty by default, and this mock deliberately does not write to
        # it, standing in for that case.
        t = build_transmission()

        uploader = FreqTankUploader()
        with self.assertRaises(Exception):
            uploader._FreqTankUploader__upload(t)

        mock_post.assert_not_called()
        t.refresh_from_db()
        self.assertIsNone(t.uploaded_at)
        out_file = mock_decode_audio.call_args[0][1]
        self.assertFalse(os.path.exists(out_file))

    @patch("sdr.utils.freqtank_uploader.requests.post")
    @patch("sdr.utils.freqtank_uploader.decode_audio", side_effect=write_fake_wav)
    def test_started_at_sent_as_epoch_milliseconds(self, mock_decode_audio, mock_post):
        mock_post.return_value = success_response()
        t = build_transmission()

        uploader = FreqTankUploader()
        uploader._FreqTankUploader__upload(t)

        sent_fields = mock_post.call_args.kwargs["data"]
        expected = str(int(t.begin_date.timestamp() * 1000))
        self.assertEqual(sent_fields["started_at"], expected)
        # must be a plain integer-valued string, not an ISO-8601 date string
        self.assertTrue(sent_fields["started_at"].isdigit())
        self.assertNotIn("T", sent_fields["started_at"])

    @patch("sdr.utils.freqtank_uploader.requests.post")
    @patch("sdr.utils.freqtank_uploader.decode_audio", side_effect=write_fake_wav)
    def test_wire_fields_match_transmission(self, mock_decode_audio, mock_post):
        mock_post.return_value = success_response()
        audio_class = AudioClass.objects.get_or_create(name="Speech", subname="Male speech")[0]
        t = build_transmission(modulation="AM", audio_class=audio_class)

        uploader = FreqTankUploader()
        uploader._FreqTankUploader__upload(t)

        sent_fields = mock_post.call_args.kwargs["data"]
        self.assertEqual(sent_fields["frequency_hz"], str(t.middle_frequency()))
        self.assertEqual(sent_fields["mode_key"], "AM")
        self.assertEqual(sent_fields["duration_ms"], str(int(t.duration().total_seconds() * 1000)))
        self.assertEqual(sent_fields["audio_class"], "Speech")
        self.assertEqual(sent_fields["audio_subclass"], "Male speech")

    @patch("sdr.utils.freqtank_uploader.requests.post")
    @patch("sdr.utils.freqtank_uploader.decode_audio", side_effect=write_fake_wav)
    def test_lat_lon_included_when_present(self, mock_decode_audio, mock_post):
        mock_post.return_value = success_response()
        t = build_transmission(lat=51.5, lon=-0.1)

        uploader = FreqTankUploader()
        uploader._FreqTankUploader__upload(t)

        sent_fields = mock_post.call_args.kwargs["data"]
        self.assertEqual(sent_fields["lat"], "51.5")
        self.assertEqual(sent_fields["lon"], "-0.1")

    @patch("sdr.utils.freqtank_uploader.requests.post")
    @patch("sdr.utils.freqtank_uploader.decode_audio", side_effect=write_fake_wav)
    def test_lat_lon_omitted_when_absent(self, mock_decode_audio, mock_post):
        mock_post.return_value = success_response()
        t = build_transmission(lat=None, lon=None)

        uploader = FreqTankUploader()
        uploader._FreqTankUploader__upload(t)

        sent_fields = mock_post.call_args.kwargs["data"]
        self.assertNotIn("lat", sent_fields)
        self.assertNotIn("lon", sent_fields)


class FreqTankUploaderRunTestCase(TestCase):
    def __run_one_iteration(self, uploader):
        # `run()`'s loop is a plain `while self.__is_running: ... time.sleep(...)`, so it
        # can be driven synchronously (no real thread) by making the mocked `time.sleep`
        # call `stop()` after exactly one iteration, which flips the while condition
        # false on the next check and returns control back to the caller.
        def fake_sleep(_seconds):
            uploader.stop()

        with patch("sdr.utils.freqtank_uploader.time.sleep", side_effect=fake_sleep):
            uploader._FreqTankUploader__is_running = True
            uploader.run()

    @patch.object(FreqTankUploader, "_FreqTankUploader__upload")
    def test_stale_fm_classified_not_uploaded_transmission_is_uploaded(self, mock_upload):
        AppSettings.set(AppSettingsKey.FREQTANK_UPLOAD_MODE, "auto")
        AppSettings.set(AppSettingsKey.FREQTANK_CHECK_INTERVAL_MS, 1)
        t = build_transmission(modulation="FM", end_seconds_ago=30, uploaded=False)

        uploader = FreqTankUploader()
        self.__run_one_iteration(uploader)

        mock_upload.assert_called_once_with(t)

    @patch.object(FreqTankUploader, "_FreqTankUploader__upload")
    def test_too_fresh_transmission_is_skipped(self, mock_upload):
        AppSettings.set(AppSettingsKey.FREQTANK_UPLOAD_MODE, "auto")
        AppSettings.set(AppSettingsKey.FREQTANK_CHECK_INTERVAL_MS, 1)
        build_transmission(modulation="FM", end_seconds_ago=1, uploaded=False)

        uploader = FreqTankUploader()
        self.__run_one_iteration(uploader)

        mock_upload.assert_not_called()

    @patch.object(FreqTankUploader, "_FreqTankUploader__upload")
    def test_non_fm_am_modulation_is_skipped(self, mock_upload):
        AppSettings.set(AppSettingsKey.FREQTANK_UPLOAD_MODE, "auto")
        AppSettings.set(AppSettingsKey.FREQTANK_CHECK_INTERVAL_MS, 1)
        build_transmission(modulation="RAW", end_seconds_ago=30, uploaded=False)

        uploader = FreqTankUploader()
        self.__run_one_iteration(uploader)

        mock_upload.assert_not_called()

    @patch.object(FreqTankUploader, "_FreqTankUploader__upload")
    def test_unclassified_transmission_is_skipped(self, mock_upload):
        AppSettings.set(AppSettingsKey.FREQTANK_UPLOAD_MODE, "auto")
        AppSettings.set(AppSettingsKey.FREQTANK_CHECK_INTERVAL_MS, 1)
        default_audio_class = AudioClass.objects.get(id=get_default_audio_class_id())
        build_transmission(modulation="FM", audio_class=default_audio_class, end_seconds_ago=30, uploaded=False)

        uploader = FreqTankUploader()
        self.__run_one_iteration(uploader)

        mock_upload.assert_not_called()

    @patch.object(FreqTankUploader, "_FreqTankUploader__upload")
    def test_already_uploaded_transmission_is_skipped(self, mock_upload):
        AppSettings.set(AppSettingsKey.FREQTANK_UPLOAD_MODE, "auto")
        AppSettings.set(AppSettingsKey.FREQTANK_CHECK_INTERVAL_MS, 1)
        build_transmission(modulation="FM", end_seconds_ago=30, uploaded=True)

        uploader = FreqTankUploader()
        self.__run_one_iteration(uploader)

        mock_upload.assert_not_called()

    @patch.object(FreqTankUploader, "_FreqTankUploader__upload")
    def test_mode_not_auto_uploads_nothing(self, mock_upload):
        AppSettings.set(AppSettingsKey.FREQTANK_UPLOAD_MODE, "off")
        AppSettings.set(AppSettingsKey.FREQTANK_CHECK_INTERVAL_MS, 1)
        build_transmission(modulation="FM", end_seconds_ago=30, uploaded=False)

        uploader = FreqTankUploader()
        self.__run_one_iteration(uploader)

        mock_upload.assert_not_called()

    @patch.object(FreqTankUploader, "_FreqTankUploader__upload")
    def test_upload_exception_does_not_stop_the_loop_or_crash(self, mock_upload):
        AppSettings.set(AppSettingsKey.FREQTANK_UPLOAD_MODE, "auto")
        AppSettings.set(AppSettingsKey.FREQTANK_CHECK_INTERVAL_MS, 1)
        mock_upload.side_effect = Exception("boom")
        build_transmission(modulation="FM", end_seconds_ago=30, uploaded=False)

        uploader = FreqTankUploader()
        # must not raise
        self.__run_one_iteration(uploader)

        mock_upload.assert_called_once()
