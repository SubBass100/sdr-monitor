from django.test import TestCase
from django.utils import timezone
from sdr.models import Transmission
from sdr.utils.transmission_reader import TransmissionReader
from unittest.mock import patch
import base64
import json


class FakeMessage:
    def __init__(self, topic, payload):
        self.topic = topic
        self.payload = payload


def build_message(topic, samples, source, name, frequency, bandwidth, modulation, dt):
    # mirrors the XOR-0x80 obfuscation TransmissionReader.on_message() undoes
    payload_bytes = bytes(b ^ 0x80 for b in samples)
    body = {
        "data": base64.b64encode(payload_bytes).decode("ascii"),
        "source": source,
        "name": name,
        "frequency": frequency,
        "bandwidth": bandwidth,
        "modulation": modulation,
        "time": int(dt.timestamp() * 1000),
    }
    return FakeMessage(topic, json.dumps(body).encode("utf-8"))


class TransmissionReaderTestCase(TestCase):
    TOPIC = "sdr/transmission/x/rtlsdr_00000001"

    @patch("sdr.utils.location.Location")
    def test_new_transmission_is_stamped_with_current_location(self, mock_location_cls):
        mock_location_cls.return_value.get_current_location.return_value = (51.5, -0.1)
        reader = TransmissionReader()
        dt = timezone.now()
        message = build_message(self.TOPIC, b"\x01\x02\x03\x04", "test", None, 100000000, 4, "FM", dt)

        handled = reader.on_message(None, message)

        self.assertTrue(handled)
        t = Transmission.objects.get()
        self.assertEqual(t.lat, 51.5)
        self.assertEqual(t.lon, -0.1)

    @patch("sdr.utils.location.Location")
    def test_existing_transmission_chunk_append_does_not_restamp_location(self, mock_location_cls):
        mock_location_cls.return_value.get_current_location.return_value = (51.5, -0.1)
        reader = TransmissionReader()
        dt1 = timezone.now()
        message1 = build_message(self.TOPIC, b"\x01\x02\x03\x04", "test", None, 100000000, 4, "FM", dt1)
        reader.on_message(None, message1)

        # a later chunk for the same in-progress transmission, arriving within the
        # append window (end_date__gt dt-1s and end_date__lt dt)
        mock_location_cls.return_value.get_current_location.return_value = (99.9, -99.9)
        dt2 = dt1 + timezone.timedelta(milliseconds=500)
        message2 = build_message(self.TOPIC, b"\x05\x06\x07\x08", "test", None, 100000000, 4, "FM", dt2)

        handled = reader.on_message(None, message2)

        self.assertTrue(handled)
        self.assertEqual(Transmission.objects.count(), 1)
        t = Transmission.objects.get()
        # still the location captured at creation time, not the second (mocked) value
        self.assertEqual(t.lat, 51.5)
        self.assertEqual(t.lon, -0.1)
