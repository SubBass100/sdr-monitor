from django.utils import timezone
from sdr.app_settings import AppSettings, AppSettingsKey
from sdr.models import Transmission, get_default_audio_class_id
from sdr.signals import decode_audio
import logging
import os
import requests
import tempfile
import threading
import time


class FreqTankUploader(threading.Thread):
    def __init__(self):
        threading.Thread.__init__(self)
        self.__is_running = True
        self.__logger = logging.getLogger("FreqTankUploader")

    def __upload(self, t):
        sample_rate = t.end_frequency - t.begin_frequency
        with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tmp:
            out_file = tmp.name
        try:
            decode_audio(t.data_file.path, out_file, t.group.modulation, sample_rate)
            fields = {
                "frequency_hz": str(t.middle_frequency()),
                "mode_key": t.group.modulation,
                "started_at": str(int(t.begin_date.timestamp() * 1000)),  # epoch ms -- FreqTank's route does Number(fields['started_at']), not a date parser
                "duration_ms": str(int(t.duration().total_seconds() * 1000)),
            }
            if t.lat is not None:
                fields["lat"] = str(t.lat)
            if t.lon is not None:
                fields["lon"] = str(t.lon)
            if t.audio_class_id is not None:
                fields["audio_class"] = t.audio_class.name
                fields["audio_subclass"] = t.audio_class.subname
            url = AppSettings.get(AppSettingsKey.FREQTANK_SERVER_URL).rstrip("/") + "/api/field-recordings/upload"
            headers = {"X-API-Key": AppSettings.get(AppSettingsKey.FREQTANK_API_KEY)}
            with open(out_file, "rb") as audio_fp:
                response = requests.post(url, headers=headers, data=fields, files={"audio": ("recording.wav", audio_fp, "audio/wav")}, timeout=30)
            response.raise_for_status()
            t.uploaded_at = timezone.now()
            t.save()
            self.__logger.info("uploaded, id: %d, frequency: %d Hz" % (t.id, t.middle_frequency()))
        finally:
            if os.path.exists(out_file):
                os.remove(out_file)

    def run(self):
        self.__logger.debug("start")
        default_audio_class_id = get_default_audio_class_id()
        while self.__is_running:
            if AppSettings.get(AppSettingsKey.FREQTANK_UPLOAD_MODE) == "auto":
                cut_dt = timezone.now() - timezone.timedelta(seconds=10)
                pending = Transmission.objects.filter(
                    end_date__lt=cut_dt,
                    uploaded_at__isnull=True,
                    group__modulation__in=["FM", "AM"],
                ).exclude(audio_class_id=default_audio_class_id).order_by("begin_date")
                for t in pending:
                    if not self.__is_running:
                        break
                    try:
                        self.__upload(t)
                    except Exception as e:
                        self.__logger.warning("exception: %s" % e)
            interval_ms = AppSettings.get(AppSettingsKey.FREQTANK_CHECK_INTERVAL_MS)
            time.sleep(max(1, interval_ms / 1000))
        self.__logger.debug("stop")

    def stop(self):
        self.__is_running = False
