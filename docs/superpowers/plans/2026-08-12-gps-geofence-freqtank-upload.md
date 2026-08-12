# GPS Tagging, Geofence Gating, and FreqTank Auto-Upload Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add live GPS tagging, geofence-gated scanning, and automatic FreqTank upload (including AI voice/noise/music classification) to the `SubBass100/sdr-monitor` fork, plus the small `SubBass100/sdr-hub` fork change needed to start the new background thread, plus the small FreqTank server/client additions needed to accept, store, and filter on the classification.

**Architecture:** Everything in `sdr-monitor` reuses existing patterns: `AppSettingsKey`/`AppSettings` for settings, the `Cleaner`/`ClassifierController` `threading.Thread` shape for the two new background threads, the existing `MqttSyncClient` for live scanner pause/resume, and the existing `decode_audio()` for producing upload-ready WAVs. FreqTank's changes mirror the `ownerId` filter pattern added earlier this project (new nullable columns, accept on write, expose+filter on read).

**Tech Stack:** Python 3 / Django 5.2 (sdr-monitor, sdr-hub's one config line), TypeScript / Fastify / React (FreqTank).

## Global Constraints

- **Three repos, no cross-repo worktree.** `sdr-monitor` and `sdr-hub` are plain persistent
  checkouts at `/home/sysadmin/sdr-monitor` and (to be cloned) `/home/sysadmin/sdr-hub`; work
  on a feature branch directly in each (no isolating worktree needed — these are fresh forks
  with no other in-flight work). `freqtank` uses this project's established
  `superpowers:using-git-worktrees` pattern (EnterWorktree), same as every other FreqTank
  phase this project has done.
- **Source type**: the FreqTank source this fork's uploader authenticates as must be a
  `field_scanner`-typed source (created via FreqTank's existing Field Scanner Create flow) —
  do not add a new `source_type` value in this plan.
- **Upload contract stays backward compatible.** `audio_class`/`audio_subclass` are optional
  on `POST /api/field-recordings/upload` — Field Scanner's own uploads (which never send
  them) must keep working exactly as before, resulting in `NULL` for both columns.
- **No WiFi/AP management, no `rtl-sdr-scanner-cpp` changes, no remote-config API** — see the
  design spec's Non-goals (`docs/superpowers/specs/2026-08-12-gps-geofence-freqtank-upload-design.md`)
  for the full reasoning; not repeated per-task here.
- **Every new Python module mirrors an existing one's shape** named in its task below — read
  the named existing file first, match its style (docstring/comment density, logger naming
  convention `logging.getLogger("Name")`, `threading.Thread` subclass shape with
  `__init__`/`run`/`stop`) before writing the new one.

---

### Task 1: Settings — new `AppSettingsKey` entries

**Files:**
- Modify: `sdr/app_settings.py`

**Interfaces:**
- Produces: `AppSettingsKey.LOCATION_SOURCE`, `.MANUAL_LAT`, `.MANUAL_LON`,
  `.AUTO_SCAN_MODE`, `.GEOFENCE_CENTER_LAT`, `.GEOFENCE_CENTER_LON`, `.GEOFENCE_RADIUS_M`,
  `.GEOFENCE_DEBOUNCE_SAMPLES`, `.GEOFENCE_RECHECK_INTERVAL_MS`, `.FREQTANK_SERVER_URL`,
  `.FREQTANK_API_KEY`, `.FREQTANK_UPLOAD_MODE`, `.FREQTANK_CHECK_INTERVAL_MS` — all readable
  via `AppSettings.get(AppSettingsKey.X)`, consumed by Tasks 2-4.

- [ ] **Step 1: Read the existing pattern**

Read `sdr/app_settings.py` in full (already small — under 60 lines). Note the exact shape:
`AppSettingsKey` is an `Enum` whose members are 3-tuples `(key: str, default, cast)`, read via
`AppSettings.get(AppSettingsKey.X)` (returns `cast(stored_value)`, or `default` if unset/
invalid) and written via `AppSettings.set(AppSettingsKey.X, value)`. `AppSettingsForm` is a
plain `django.forms.Form` with one field per setting, a `load_initial()` populating from
`AppSettings.get`, and a `save()` writing via `AppSettings.set`.

- [ ] **Step 2: Add the new enum members**

Add these members to `AppSettingsKey`, immediately after the existing ones (same style —
one line each, uppercase name, snake_case key string matching the name):

```python
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
    FREQTANK_UPLOAD_MODE = ("freqtank_upload_mode", "off", str)
    FREQTANK_CHECK_INTERVAL_MS = ("freqtank_check_interval_ms", 5000, int)
```

`LOCATION_SOURCE`/`AUTO_SCAN_MODE`/`FREQTANK_UPLOAD_MODE` are free-text `str` here (not a
Django `ChoiceField` enum) — matching every other setting's `cast` being a plain type, not a
validated choice set. Validate the allowed values (`"gps"`/`"manual"`,
`"manual"`/`"boot"`/`"geofence"`, `"off"`/`"auto"`/`"direct"`) at the **form** level (Step 3)
via `forms.ChoiceField`, not by adding enum-level validation — matches how this codebase keeps
`AppSettingsKey` itself dumb (storage-shape only) and puts validation in the form.

- [ ] **Step 3: Extend `AppSettingsForm`**

Add matching `forms.Field` declarations to `AppSettingsForm` (mirror the existing
`n2yo_api_key = forms.CharField(...)`/`spectrograms_total_size_gb = forms.IntegerField(...)`
style exactly — `label=`, `help_text=` where it clarifies units, `required=False` for anything
that's meaningfully optional):

```python
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
    freqtank_upload_mode = forms.ChoiceField(label="FreqTank upload mode", choices=[("off", "Off"), ("auto", "Automatic"), ("direct", "Direct")])
    freqtank_check_interval_ms = forms.IntegerField(label="FreqTank check interval (ms)")
```

Extend `load_initial()`'s dict with one line per new key (mirror the existing 4 lines
exactly: `AppSettingsKey.X.key: AppSettings.get(AppSettingsKey.X)`), and extend `save()`'s
body — read `save()`'s current implementation first (it wasn't fully shown during design
research; confirm whether it iterates `self.cleaned_data` generically or lists keys
explicitly, and match whichever pattern is actually there).

- [ ] **Step 4: Verify the settings page still renders**

This repo's test setup wasn't fully explored during design (see Task 7 for confirming the
actual test framework/runner). At minimum: run `python manage.py check` (or however this
project's Docker/dev setup normally validates the project — check `README.md`/`Dockerfile`
for the real command) to confirm no Django system-check errors from the new form fields
before moving on. If a fuller local run/test command exists, prefer that.

- [ ] **Step 5: Commit**

```bash
git add sdr/app_settings.py
git commit -m "feat: add settings for location, geofence, and FreqTank upload"
```

---

### Task 2: GPS tagging

**Files:**
- Modify: `requirements.txt`
- Modify: `Dockerfile`
- Create: `sdr/utils/location.py`
- Create: `sdr/migrations/0009_transmission_lat_lon.py` (exact number depends on what's HEAD
  at implementation time — check `sdr/migrations/` first and increment from the actual latest)
- Modify: `sdr/models.py`
- Modify: `sdr/utils/transmission_reader.py`

**Interfaces:**
- Consumes: `AppSettingsKey.LOCATION_SOURCE`/`.MANUAL_LAT`/`.MANUAL_LON` (Task 1).
- Produces: `sdr.utils.location.get_current_location() -> tuple[float, float] | None`,
  consumed by Task 3 (geofence) and reusable by anything else that needs "where is this
  device right now." `Transmission.lat`/`Transmission.lon` (nullable floats), consumed by
  Task 4's uploader.

- [ ] **Step 1: Add the gpsd dependency**

Add `gpsdclient` to `requirements.txt` (alphabetically, matching the file's existing sort
order). Prefer `gpsdclient` over `gpsd-py3` if both are viable — confirm current PyPI
availability/maintenance status at implementation time; either is acceptable as long as it
gives a straightforward "connect to a local gpsd, get the current TPV report" API.

- [ ] **Step 2: Add gpsd + USB GPS passthrough to the Dockerfile**

In the final stage of `Dockerfile` (the `FROM ubuntu:24.04` stage, not the `builder` stage),
add `gpsd` to the `apt-get install` line alongside the existing packages. USB device
passthrough (`/dev/bus/usb`) is granted at `docker run` time by the integration's install
script (FreqTank's side, Part 2 — not this repo), the same way the RTL-SDR's USB access
already is in the existing `sdr-hub`-level `docker run` command; no Dockerfile-level device
declaration is needed for that part. Confirm `gpsd` doesn't need a running system service
manager conflict with `supervisord` (it doesn't own port 8000 or anything else already used)
before finalizing — if gpsd needs to be supervisor-managed to actually start inside the
container, that's a `sdr-hub`-side `supervisord.conf` change (Task 5's territory, not this
one) — note this dependency explicitly in Task 2's completion report so Task 5's implementer
knows to check for it.

- [ ] **Step 3: Write `sdr/utils/location.py`**

```python
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
            with GPSDClient(host="127.0.0.1") as client:
                for result in client.dict_stream(convert_datetime=False, filter=["TPV"]):
                    lat = result.get("lat")
                    lon = result.get("lon")
                    if lat is not None and lon is not None:
                        return (lat, lon)
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
```

Confirm `gpsdclient`'s actual streaming API shape against its real (current, at
implementation time) documentation/source before finalizing — the `dict_stream(...)` call
above is a best-effort sketch based on the library's typical shape, not verified against its
source the way the rest of this plan's Python code was. This is the one piece of this plan
genuinely worth a quick doc/source check before writing, flagged explicitly rather than
presented as verified.

- [ ] **Step 4: Add `lat`/`lon` to the `Transmission` model**

In `sdr/models.py`, add two fields to `class Transmission(models.Model)`, alongside the
existing `source = models.CharField(...)` line:

```python
    lat = models.FloatField("Latitude", null=True, blank=True)
    lon = models.FloatField("Longitude", null=True, blank=True)
```

- [ ] **Step 5: Generate and check the migration**

Run `python manage.py makemigrations sdr` (from wherever this project's `manage.py` is
normally invoked — check `Dockerfile`/`scripts/` for the real working directory/command
convention) to generate the migration file. Confirm its `dependencies` correctly points at
the actual latest existing migration (check `sdr/migrations/` for the current tail file name
— was `0008_remove_transmission_name_alter_group_name_and_more` as of design time, but
confirm fresh) and that its `operations` are exactly two `AddField` calls matching the model
fields above. Do not hand-write the migration — let Django generate it, then review.

- [ ] **Step 6: Stamp GPS on new transmissions**

In `sdr/utils/transmission_reader.py`, `append_transmission()`'s `except
Transmission.DoesNotExist:` branch (the one that `Transmission.objects.create(...)`s a brand
new row — **not** the branch above it that extends an existing one), add the location lookup
and pass it through:

```python
        except Transmission.DoesNotExist:
            location = sdr.utils.location.Location().get_current_location()
            dir = "device_%d/transmission" % device.id
            (filename, filename_full) = sdr.utils.file.get_filename(dir, dt, "%s_%d_%s.bin" % (dt.strftime("%H_%M_%S"), (begin_frequency + end_frequency) // 2, sample_type), True)
            t = Transmission.objects.create(
                device=device,
                begin_frequency=begin_frequency,
                end_frequency=end_frequency,
                begin_date=dt,
                end_date=dt,
                sample_size=sample_size,
                data_file=filename,
                data_type=sample_type,
                group_id=group_id,
                source=source,
                lat=location[0] if location else None,
                lon=location[1] if location else None,
            )
```

Add `import sdr.utils.location` to this file's import block (alongside the existing
`import sdr.utils.device`/`import sdr.utils.file`/`import sdr.utils.group`).

- [ ] **Step 7: Write tests**

Add `sdr/tests/test_location.py` (check whether `sdr/tests/` already exists as a directory or
if tests live elsewhere in this project first — Django's default is `sdr/tests.py` or a
`sdr/tests/` package; match whatever's already there, or Django's default single-file
convention if there's genuinely no existing test infrastructure to match). Cover:
`get_current_location()` returns `(MANUAL_LAT, MANUAL_LON)` when `LOCATION_SOURCE=manual`
(mock `AppSettings.get`); returns `None` gracefully (not an exception) when GPSD is
unreachable/`GPSDClient` raises, with `LOCATION_SOURCE=gps`.

Add a test to wherever `transmission_reader.py`'s existing tests live (search for one first —
it may not have any yet, in which case write the file fresh): a new-transmission MQTT message
results in `lat`/`lon` populated from a mocked `get_current_location()`; an
existing-transmission chunk-append does **not** re-stamp/change `lat`/`lon`.

- [ ] **Step 8: Run tests, verify pass, commit**

```bash
git add requirements.txt Dockerfile sdr/utils/location.py sdr/models.py sdr/migrations/ sdr/utils/transmission_reader.py sdr/tests/
git commit -m "feat: add live GPS tagging for new transmissions"
```

---

### Task 3: Geofence-gated scanning

**Files:**
- Create: `sdr/utils/geofence.py`
- Create: `sdr/utils/geofence_controller.py`
- Modify: `scripts/monitor_worker.py`

**Interfaces:**
- Consumes: `sdr.utils.location.Location().get_current_location()` (Task 2),
  `AppSettingsKey.AUTO_SCAN_MODE`/`.GEOFENCE_*` (Task 1), `common.utils.mqtt.MqttSyncClient`
  (existing, in the `common` git submodule — read `common/utils/mqtt.py` first: constructor
  `MqttSyncClient(url, user, password, client_id)`, `.start()`, `.send_and_get(publish_topic,
  subscribe_topic, message=None) -> str`, `.stop()`), `monitor.settings.MQTT` (existing dict
  `{url, user, password, frontend_path}`).
- Produces: `GeofenceController(threading.Thread)`, started from `monitor_worker.py`.

- [ ] **Step 1: Read the patterns to mirror**

Read in full: `sdr/utils/cleaner.py` (simplest existing `threading.Thread` shape — `__init__`
sets `self.__is_running`/state, `run()` loops `while self.__is_running: ... time.sleep(1)`,
`stop()` sets the flag false) and `sdr/utils/gain_tester_thread.py` (the only existing code
that talks to the scanner's live MQTT config channel — note its exact `send_and_get` call
shape and the `config["devices"]` JSON structure it mutates).

- [ ] **Step 2: Write `sdr/utils/geofence.py`**

```python
import math


def distance_m(lat1, lon1, lat2, lon2):
    R = 6_371_000
    d_lat = math.radians(lat2 - lat1)
    d_lon = math.radians(lon2 - lon1)
    r_lat1 = math.radians(lat1)
    r_lat2 = math.radians(lat2)
    sin_d_lat = math.sin(d_lat / 2)
    sin_d_lon = math.sin(d_lon / 2)
    h = sin_d_lat * sin_d_lat + math.cos(r_lat1) * math.cos(r_lat2) * sin_d_lon * sin_d_lon
    return 2 * R * math.asin(math.sqrt(h))


def is_outside_geofence(lat, lon, center_lat, center_lon, radius_m):
    return distance_m(lat, lon, center_lat, center_lon) > radius_m
```

- [ ] **Step 3: Write `sdr/utils/geofence_controller.py`**

Structure (mirror `Cleaner`'s `threading.Thread` shape exactly for `__init__`/`run`/`stop`;
this is the one new thread in this plan that also needs an MQTT connection, so also mirror
`GainTesterThread`'s `MqttSyncClient` usage for that part):

```python
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
```

- [ ] **Step 4: Wire into `monitor_worker.py`**

In `scripts/monitor_worker.py`, mirror the `--classifier`/`-cls` block exactly for a new
`--geofence`/`-gf` flag:

```python
    parser.add_argument("-gf", "--geofence", help="enable geofence controller", action="store_true")
```

```python
    if args.geofence:
        try:
            from sdr.utils.geofence_controller import GeofenceController

            threads.append(GeofenceController())
        except Exception as e:
            logging.getLogger("Worker").warning("exception: %s" % e)
```

- [ ] **Step 5: Write tests**

`geofence.distance_m`/`is_outside_geofence`: known coordinate pairs (e.g. two points ~1km
apart, assert distance is within a few meters of the expected value; a point exactly at the
center is never outside; a point far away always is). `GeofenceController`'s debounce: mock
`Location.get_current_location` to return a fixed "outside" location for N-1 polls then
assert `__apply`/the MQTT client is NOT yet called, then one more consistent poll and assert
it IS called exactly once (not called again on subsequent still-consistent polls, since
`__applied_state` already matches). Mock `AppSettings.get` throughout rather than touching the
real DB-backed settings store.

- [ ] **Step 6: Run tests, verify pass, commit**

```bash
git add sdr/utils/geofence.py sdr/utils/geofence_controller.py scripts/monitor_worker.py sdr/tests/
git commit -m "feat: add geofence-gated scanning via live scanner pause/resume"
```

---

### Task 4: FreqTank auto-uploader

**Files:**
- Modify: `requirements.txt`
- Create: `sdr/utils/freqtank_uploader.py`
- Create: `sdr/migrations/000X_transmission_uploaded_at.py` (next number after Task 2's)
- Modify: `sdr/models.py`
- Modify: `scripts/monitor_worker.py`

**Interfaces:**
- Consumes: `AppSettingsKey.FREQTANK_*` (Task 1), `Transmission.lat`/`.lon` (Task 2),
  `sdr.signals.decode_audio(in_file, out_file, modulation, sample_rate, out_rate=32000,
  duration=...)` (existing — read `sdr/signals.py` and its usage in `sdr/views.py` around
  line 168 first), `Transmission.audio_class` / `AudioClass.name`/`.subname` (existing).
- Produces: `FreqTankUploader(threading.Thread)`, started from `monitor_worker.py` behind a
  new `-fu`/`--freqtank-uploader` flag — this exact flag name/spelling is load-bearing: Task 5
  (the `sdr-hub` fork) references it by this name.

- [ ] **Step 1: Read the patterns to mirror**

Read in full: `sdr/utils/sound_classifier.py` and `sdr/utils/classifier_controller.py` (the
does-the-work-class + polling-thread split this task's structure directly mirrors), and the
`elif t.group.modulation in ["FM", "AM"]:` branch in `sdr/views.py` (~line 168) for the exact
`decode_audio(...)` call shape/argument order already in production use.

- [ ] **Step 2: Add the `requests` dependency**

Add `requests` to `requirements.txt` (alphabetically) for the multipart upload POST.

- [ ] **Step 3: Add `uploaded_at` to the `Transmission` model + migration**

In `sdr/models.py`:

```python
    uploaded_at = models.DateTimeField("Uploaded at", null=True, blank=True)
```

Run `python manage.py makemigrations sdr` (same as Task 2 Step 5 — depends on Task 2's
migration being applied/present first, since this task's migration must chain after it).

- [ ] **Step 4: Write `sdr/utils/freqtank_uploader.py`**

```python
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
```

Confirm `t.group.modulation`, `t.data_file.path`, `t.middle_frequency()`, `t.duration()`, and
`get_default_audio_class_id` (imported from `sdr.models`, already used by
`classifier_controller.py`) all match the actual current `sdr/models.py` exactly — these were
read during design research but re-verify at implementation time, same as any plan.

- [ ] **Step 5: Wire into `monitor_worker.py`**

Mirror the `--classifier`/`-cls` block exactly (same shape as Task 3 Step 4):

```python
    parser.add_argument("-fu", "--freqtank-uploader", help="enable FreqTank uploader", action="store_true")
```

```python
    if args.freqtank_uploader:
        try:
            from sdr.utils.freqtank_uploader import FreqTankUploader

            threads.append(FreqTankUploader())
        except Exception as e:
            logging.getLogger("Worker").warning("exception: %s" % e)
```

- [ ] **Step 6: Write tests**

Mock `requests.post` and `decode_audio`. Cover: a stale + FM/AM + classified + not-yet-
uploaded transmission gets uploaded, `uploaded_at` set on success; a transmission missing any
one of those conditions (too fresh, wrong modulation, unclassified, already uploaded) is
skipped; a failed POST (mock a non-2xx response or an exception) leaves `uploaded_at` null so
it's retried; `FREQTANK_UPLOAD_MODE != "auto"` uploads nothing at all; the temp WAV file is
cleaned up (removed) whether the upload succeeds or fails.

- [ ] **Step 7: Run tests, verify pass, commit**

```bash
git add requirements.txt sdr/utils/freqtank_uploader.py sdr/models.py sdr/migrations/ scripts/monitor_worker.py sdr/tests/
git commit -m "feat: add automatic FreqTank upload for finished recordings"
```

---

### Task 5: `sdr-hub` fork — start the new threads

**Repo:** `SubBass100/sdr-hub` (clone to `/home/sysadmin/sdr-hub` if not already present).

**Files:**
- Modify: `config/supervisord.conf`

**Interfaces:**
- Consumes: the `-gf`/`--geofence` flag (Task 3) and `-fu`/`--freqtank-uploader` flag (Task 4)
  added to `scripts/monitor_worker.py` in the `sdr-monitor` fork.

- [ ] **Step 1: Read the current worker invocation**

Read `config/supervisord.conf` in full. Find the `[program:...]` block invoking
`monitor_worker.py` (with `-r -clr -cls` or similar flags — confirm the exact current flag
set, don't assume it matches design-time notes exactly).

- [ ] **Step 2: Add the two new flags**

Add `-gf -fu` to that program block's `command=` line, alongside the existing flags. Do not
touch any other part of this file.

- [ ] **Step 3: Confirm no gpsd supervisor entry is needed**

Check Task 2's completion report/notes for whether `gpsd` needs its own `[program:gpsd]`
block in this file to actually start inside the container (vs. being auto-started some other
way). If so, add one, mirroring the style of an existing simple `[program:...]` block in this
same file (e.g. `mosquitto`'s, if present) as closely as possible.

- [ ] **Step 4: Commit**

```bash
git add config/supervisord.conf
git commit -m "feat: start the geofence controller and FreqTank uploader threads"
```

(No test suite exists for this repo — it's a thin Docker/config wrapper. Verification is
building the combined image and confirming both threads log their "start" line, covered by
this plan's manual end-to-end verification, not a unit test.)

---

### Task 6: FreqTank server — accept, store, and filter `audio_class`

**Repo:** `freqtank`, in an isolated worktree per this project's established
`superpowers:using-git-worktrees` convention.

**Files:**
- Modify: `shared/src/types.ts`
- Modify: `server/src/routes/fieldRecordings.ts`
- Create: `server/migrations/00XX_field_recordings_audio_class.sql` (check
  `server/migrations/` for this project's actual migration-file naming convention and next
  number — do not guess the format independent of what's already there)
- Test: `server/tests/fieldRecordingsQuery.test.ts`, and wherever the upload route's own
  tests live (search for the existing upload-route test file first — likely
  `server/tests/fieldRecordings.test.ts` based on this project's naming pattern, confirm
  before adding to it)

**Interfaces:**
- Consumes: `audio_class`/`audio_subclass` multipart fields from `POST
  /api/field-recordings/upload`, sent by Task 4's uploader (optional — absent for Field
  Scanner's own uploads).
- Produces: `FieldRecording.audioClass: string | null`, `.audioSubclass: string | null`;
  `FieldRecordingFilters.audioClass?: string` — consumed by Task 7.

- [ ] **Step 1: Migration**

Add nullable `audio_class TEXT` and `audio_subclass TEXT` columns to `field_recordings`,
following this project's exact existing migration-file convention (check 2-3 recent files in
`server/migrations/` for the exact naming/numbering/SQL style before writing this one).

- [ ] **Step 2: Shared types**

In `shared/src/types.ts`:

```ts
export interface FieldRecording {
  // ...existing fields...
  audioClass: string | null
  audioSubclass: string | null
}

export interface FieldRecordingFilters {
  // ...existing fields...
  audioClass?: string
}
```

- [ ] **Step 3: Accept on upload**

In `server/src/routes/fieldRecordings.ts`'s `handleFieldRecordingUpload`, read the two new
optional fields from `fields['audio_class']`/`fields['audio_subclass']` (same `fields`
record the existing `frequency_hz`/`mode_key`/etc. already come from), default to `null` when
absent (no validation needed beyond "it's a string or absent" — these are free-text labels
from an upstream AI classifier, not FreqTank-validated data), and add them to the `INSERT`:

```ts
    const audioClass = fields['audio_class'] ?? null
    const audioSubclass = fields['audio_subclass'] ?? null
```

```ts
    ;[recording] = await sql`
      INSERT INTO field_recordings (filename, audio_path, spectrogram_path, file_size, source_id, frequency_hz, mode_key, started_at, duration_ms, lat, lon, snr_db, audio_class, audio_subclass)
      VALUES (
        ${uniqueFilename},
        ${audioPath},
        ${spectrogramPath},
        ${size},
        ${source.id},
        ${frequencyHz},
        ${modeKey},
        ${startDate.toISOString()},
        ${durationMs},
        ${lat},
        ${lon},
        ${snrDb},
        ${audioClass},
        ${audioSubclass}
      )
      RETURNING *
    `
```

Add the same two fields to the `broadcast(...)` call's `recording` object (mirror the
existing `snrDb: recording.snr_db !== null ? Number(recording.snr_db) : null,` line's shape,
but these are plain strings, no `Number(...)` cast needed:
`audioClass: recording.audio_class, audioSubclass: recording.audio_subclass,`).

- [ ] **Step 4: Expose and filter on read**

In the `GET /api/field-recordings` handler: add `audioClass?: string` to the `Querystring`
type, destructure it, add it to `selectCols` (`fr.audio_class as "audioClass", fr.audio_subclass as "audioSubclass",`),
and add a filter clause to `filter` matching the existing `ownerId` clause's exact shape:

```ts
      ${audioClass !== undefined ? sql`AND fr.audio_class = ${audioClass}` : sql``}
```

No integer-style validation needed (unlike `sourceId`/`ownerId`) — this is a plain string
equality filter against a small fixed set of client-supplied values
(`Speech`/`Music`/`Noise`/`Unknown`), same shape as `startAfter`/`startBefore`'s handling, not
`sourceId`'s numeric-validation handling.

- [ ] **Step 5: Write tests**

In the upload route's test file: uploading with `audio_class`/`audio_subclass` fields
persists and round-trips them through a subsequent GET; uploading **without** them (the
existing Field Scanner upload test fixtures, unmodified) still succeeds and both columns read
back `null` — this is the most important test in this task, since it's the backward-
compatibility guarantee. In `fieldRecordingsQuery.test.ts`: a new `'filters by audioClass'`
test mirroring the existing `'filters by sourceId'` test's exact shape.

- [ ] **Step 6: Run tests, verify pass, commit**

```bash
cd server && npm test
git add shared/src/types.ts server/src/routes/fieldRecordings.ts server/migrations/ server/tests/
git commit -m "feat: accept, store, and filter field recordings by AI audio classification"
```

---

### Task 7: FreqTank client — voice/noise filter dropdown

**Repo:** `freqtank`, same worktree as Task 6.

**Files:**
- Modify: `client/src/pages/FieldRecordingsPage.tsx`
- Modify: `client/src/pages/FieldRecordingsPage.test.tsx`
- Modify: `client/src/components/FieldRecordingRow.tsx`
- Modify: `client/src/components/FieldRecordingRow.test.tsx`

**Interfaces:**
- Consumes: `FieldRecordingFilters.audioClass` and `FieldRecording.audioClass`/
  `.audioSubclass` (Task 6).

- [ ] **Step 1: Add the filter dropdown**

In `FieldRecordingsPage.tsx`, alongside the existing Field Scanner Agent / Contributor
`<select>` filters, add one more with the same styling convention
(`bg-background border border-border rounded-md px-2 py-1 text-sm text-foreground
focus:outline-none focus:ring-1 focus:ring-ring`) and the same page-reset-on-change behavior:

```tsx
<label className="flex flex-col gap-1">
  Classification
  <select
    aria-label="Classification"
    value={audioClassFilter ?? 'all'}
    onChange={(e) => { setAudioClassFilter(e.target.value === 'all' ? undefined : e.target.value); setPage(1) }}
    className="bg-background border border-border rounded-md px-2 py-1 text-sm text-foreground focus:outline-none focus:ring-1 focus:ring-ring"
  >
    <option value="all">All</option>
    <option value="Speech">Speech</option>
    <option value="Music">Music</option>
    <option value="Noise">Noise</option>
    <option value="Unknown">Unknown</option>
  </select>
</label>
```

Add the corresponding `audioClassFilter` state (`useState<string | undefined>(undefined)`)
and thread it into the existing `listFieldRecordings({ ..., audioClass: audioClassFilter })`
call, matching exactly how `sourceFilter`/`ownerFilter` are already threaded through.

- [ ] **Step 2: Show the classification on rows/detail**

In `FieldRecordingRow.tsx`: if `recording.audioClass` is set, show it (small badge or plain
text, matching this row's existing muted-text metadata style — not a new visual pattern).
Omit entirely when null (Field Scanner recordings). In the page's `DetailModal`: add an
"Audio class" `<dt>`/`<dd>` pair to the existing metadata `<dl>`, same conditional-omit-when-
null pattern already used for `snrDb`/lat-lon there.

- [ ] **Step 3: Write tests**

In `FieldRecordingsPage.test.tsx`: changing the Classification filter calls
`listFieldRecordings` with `audioClass: 'Speech'` (mirror the existing Agent/Contributor
filter tests exactly). In `FieldRecordingRow.test.tsx`: a recording with `audioClass: 'Speech'`
renders it; a recording with `audioClass: null` does not render the line at all.

- [ ] **Step 4: Run tests, build, verify pass, commit**

```bash
cd client && npm test && npm run build
git add client/src/pages/FieldRecordingsPage.tsx client/src/pages/FieldRecordingsPage.test.tsx client/src/components/FieldRecordingRow.tsx client/src/components/FieldRecordingRow.test.tsx
git commit -m "feat: add audio classification filter to Field Recordings page"
```
