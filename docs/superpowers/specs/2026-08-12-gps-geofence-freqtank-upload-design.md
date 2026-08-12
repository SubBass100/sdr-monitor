# GPS Tagging, Geofence Gating, and FreqTank Auto-Upload Design

## Context

This is a fork of [shajen/sdr-monitor](https://github.com/shajen/sdr-monitor), the Django
web panel half of the [SDR-Hub](https://github.com/shajen/sdr-hub) project (the other half,
[rtl-sdr-scanner-cpp](https://github.com/shajen/rtl-sdr-scanner-cpp), is the C++ wideband
scanner/DSP engine and is **not** forked or modified by this plan). sdr-monitor receives
finished/in-progress transmissions over MQTT from the scanner, assembles them into
`Transmission` rows + raw IQ files, and (already, unmodified) runs a background AI classifier
(YAMNet) that tags each one `Speech`/`Music`/`Noise`/`Unknown`.

This is Part 1 of a 4-part project (tracked informally, not as separate repos yet):
1. **This plan** — GPS tagging, geofence-gated scanning, FreqTank auto-upload (sdr-monitor fork).
2. FreqTank agent-integration + install script to deploy this fork to a remote Pi.
3. FreqTank remote-config UI (Settings/Network/Upload-style tabs) for this integration.
4. FreqTank Field Recordings page: a voice/noise filter dropdown, using the `audio_class`
   data this plan's uploader already carries.

Parts 2-4 are out of scope here and will each get their own brainstorm/plan cycle once this
one is built and working.

## Goal

Bring three Field Scanner capabilities to this fork: live GPS tagging of each recording,
geofence-gated scanning (only scan when outside a configured area, matching Field Scanner's
exact semantics), and automatic upload of finished recordings to FreqTank — while reusing as
much of sdr-monitor's existing architecture (settings, background-thread, MQTT-client
patterns) as possible rather than introducing new patterns.

## Non-goals

- **No WiFi/AP management.** Field Scanner's "Network" tab also manages the Pi's own WiFi
  (home SSID + AP fallback) — that's Field Scanner owning host networking directly. This fork
  runs in a normal (bridge-networked) Docker container with no reason to touch the host's
  network interface, and building that would be a large, unrelated undertaking. Only the
  *scanning-behavior* half of Field Scanner's Network tab (geofence, boot-scan, periodic
  recheck) carries over.
- **No changes to `rtl-sdr-scanner-cpp`.** The live MQTT `tmp_config`/`reset_tmp_config`
  channel it already exposes (see Architecture) is sufficient for geofence pause/resume
  without touching the DSP engine at all.
- **No FreqTank-side changes.** The uploader posts to FreqTank's existing
  `POST /api/field-recordings/upload` — the same endpoint Field Scanner already uses, with
  the same multipart contract (`audio`, `frequency_hz`, `mode_key`, `started_at`,
  `duration_ms`, `lat`, `lon`, `snr_db`, `key`/`X-API-Key`). No new FreqTank server code.
- **No remote-config API yet.** All new settings are configured through sdr-monitor's own
  local web UI (extending the existing `AppSettingsForm`) in this pass. A remote HTTP API for
  FreqTank to read/write these settings is Part 3's job, not this plan's.
- **No changes to the AI classifier.** `Speech`/`Music`/`Noise`/`Unknown` tagging already
  works; this plan only carries that data through to the FreqTank upload payload.

## Architecture

### Settings

Reuse the existing `sdr/app_settings.py` pattern (`AppSettingsKey` enum entries of
`(key, default, cast)`, `AppSettings.get(key)`/`AppSettings.set(key, value)`, backed by the
existing generic `AppSetting` key-value model — no new Django model). New keys, mirroring
Field Scanner's own field names/semantics so a future remote-config UI (Part 3) can reuse the
same client-side shape almost verbatim:

```python
LOCATION_SOURCE = ("location_source", "gps", str)          # "gps" | "manual"
MANUAL_LAT = ("manual_lat", 0.0, float)
MANUAL_LON = ("manual_lon", 0.0, float)
AUTO_SCAN_MODE = ("auto_scan_mode", "boot", str)            # "manual" | "boot" | "geofence"
GEOFENCE_CENTER_LAT = ("geofence_center_lat", 0.0, float)
GEOFENCE_CENTER_LON = ("geofence_center_lon", 0.0, float)
GEOFENCE_RADIUS_M = ("geofence_radius_m", 0, int)
GEOFENCE_DEBOUNCE_SAMPLES = ("geofence_debounce_samples", 3, int)
GEOFENCE_RECHECK_INTERVAL_MS = ("geofence_recheck_interval_ms", 30000, int)
FREQTANK_SERVER_URL = ("freqtank_server_url", "", str)
FREQTANK_API_KEY = ("freqtank_api_key", "", str)
FREQTANK_UPLOAD_MODE = ("freqtank_upload_mode", "off", str)  # "off" | "auto" | "direct"
FREQTANK_CHECK_INTERVAL_MS = ("freqtank_check_interval_ms", 5000, int)
```

(`AUTO_SCAN_MODE=manual` means always-on scanning, same as sdr-monitor's current default
behavior, unaffected by geofence. `boot` is kept as a distinct value for parity with Field
Scanner even though sdr-monitor has no separate "boot" trigger of its own today — it behaves
identically to `manual` here. `geofence` activates the pause/resume behavior below.)

Extend the existing `AppSettingsForm` (`sdr/app_settings.py`) with fields for all of the
above, and its `config.html`/`config.js` template, following the same
`load_initial()`/`save()` shape already used for the existing settings — so these are all
editable from sdr-monitor's own web UI immediately, independent of Part 3.

### GPS tagging

- Add a gpsd Python client (`gpsd-py3` or equivalent) to `requirements.txt`.
- Add `gpsd` + USB GPS device passthrough to the fork's own `Dockerfile`, alongside the
  existing `/dev/bus/usb` mount already granted for the RTL-SDR — no networking-mode change
  (stays on normal bridge networking), gpsd runs inside the container itself.
- Add `lat`/`lon` (nullable `FloatField`) to the `Transmission` model, via a new migration.
- New small module `sdr/utils/location.py`: `get_current_location() -> tuple[float, float] |
  None` — returns the live gpsd fix if `LOCATION_SOURCE=gps` (with a documented fallback to
  `None` if gpsd has no fix yet), or the fixed `MANUAL_LAT`/`MANUAL_LON` settings if
  `LOCATION_SOURCE=manual`.
- Stamp `lat`/`lon` in `sdr/utils/transmission_reader.py`'s `append_transmission()`, only in
  the branch that creates a **new** `Transmission` row (not on chunk-append to an existing
  one) — a recording's location is where the device was when it *started*.

### Geofence-gated scanning

New `sdr/utils/geofence.py`:
- `distance_m(lat1, lon1, lat2, lon2) -> float` — haversine, matching Field Scanner's own
  formula (duplicated, not shared — different repo/language, same convention this whole
  project already follows for cross-repo logic).
- `is_outside_geofence(lat, lon) -> bool` — `distance_m(...) > GEOFENCE_RADIUS_M`.

New `sdr/utils/geofence_controller.py`, a `GeofenceController(threading.Thread)` following the
exact same shape as the existing `Cleaner`/`ClassifierController` threads: polls
`get_current_location()` every `GEOFENCE_RECHECK_INTERVAL_MS`, evaluates
`is_outside_geofence`, and only acts on a state *change* once it's been consistent for
`GEOFENCE_DEBOUNCE_SAMPLES` consecutive polls in a row (matching Field Scanner's own debounce
semantics — avoids flapping right at the boundary). No-ops entirely unless
`AUTO_SCAN_MODE=geofence`.

On a debounced transition to *inside* the fence: publish `sdr/tmp_config/{scanner}` with the
device's `enabled` set to `false` (the same MQTT topic and payload shape
`gain_tester_thread.py` already uses via its MQTT client class — reuse that client, don't
build a new one). On a transition to *outside*: publish `sdr/reset_tmp_config/{scanner}` to
revert to the persisted (enabled) config. This pauses/resumes the actual C++ scanner engine
live, with zero changes to that engine — confirmed via its existing `RemoteController`
MQTT channel (`sdr/tmp_config/<scanner>`, `sdr/reset_tmp_config/<scanner>`, already in
production use by the gain-tester feature).

### FreqTank auto-uploader

New `sdr/utils/freqtank_uploader.py`, structured identically to the existing
`sound_classifier.py` (does-the-work class) + `classifier_controller.py`
(`threading.Thread` polling loop) split:

- `FreqTankUploader(threading.Thread)`: polls (interval = `FREQTANK_CHECK_INTERVAL_MS`) for
  `Transmission` rows where `end_date` is more than 10 seconds old (the same "genuinely
  finished, not just between chunks" staleness window `ClassifierController` already uses),
  `group.modulation in ["FM", "AM"]` (same modulation gate `decode_audio`/the classifier
  already apply — other modulations aren't decodable to audio), and `uploaded_at IS NULL`.
  No-ops entirely unless `FREQTANK_UPLOAD_MODE == "auto"`.
- For each matching row: call the existing `sdr.signals.decode_audio(...)` to produce a WAV
  (same function the download view and the classifier already both call), then POST it as
  multipart form data to `{FREQTANK_SERVER_URL}/api/field-recordings/upload` with header
  `X-API-Key: {FREQTANK_API_KEY}` and fields `frequency_hz` (transmission's
  `middle_frequency()`), `mode_key` (`group.modulation`), `started_at` (`begin_date`, ISO 8601
  or epoch ms — match whatever FreqTank's existing route parses; verify against
  freqtank/server/src/routes/fieldRecordings.ts at implementation time), `duration_ms`
  (`duration()` in ms), `lat`/`lon` (the new fields, omitted if `None`), and — since
  FreqTank's upload route doesn't have a dedicated audio-class field — pack the AI
  classification into a value the upload contract already accepts, or leave it for Part 4 to
  decide how to carry across (**open question, see below**).
- On success (2xx): set `uploaded_at = now()` and save. On failure: leave `uploaded_at` null
  so the next poll retries it (no separate retry/backoff bookkeeping needed — matches the
  simplicity of the existing `Cleaner`/`ClassifierController` threads, which also just retry
  next tick on exception).
- Add a new `-fu`/`--freqtank-uploader` flag to `scripts/monitor_worker.py`, mirroring the
  existing `-cls`/`--classifier` flag exactly (same try/except-on-import guard, same
  `threads.append(...)` pattern).
- **This requires forking `sdr-hub` too, but only for one line**: the `-r -clr -cls`-style
  flags passed to `monitor_worker.py` are wired in `sdr-hub`'s own
  `config/supervisord.conf`, not in this repo. A minimal `SubBass100/sdr-hub` fork adding
  `-fu` to that one invocation is needed; everything else about `sdr-hub` (its Dockerfile,
  build process, the rest of its config) stays on unmodified upstream — we still build the
  final image with `--build-arg SDR_MONITOR_IMAGE=<our custom-built tag>` against upstream's
  own Dockerfile logic, just forking to touch this single config line.

### Open question: how does `audio_class` reach FreqTank in this pass?

FreqTank's existing upload endpoint has no field for it. Two options, to be resolved before
or during implementation (not blocking the rest of this design):
1. **Skip it in this pass.** Upload without the classification; Part 4's own design (FreqTank
   Field Recordings filter) decides then whether to extend the upload contract or backfill.
2. **Wait for classification before uploading.** Change the uploader's query to also require
   `audio_class_id != default_audio_class_id` (i.e. wait until `ClassifierController` has
   already tagged it, which the design's own 10-second staleness window makes likely to
   already be true or arrive shortly after), and encode the class into a field the existing
   upload contract *does* accept as a stretch (there isn't a clean one — this option is
   weaker than it looks).

Recommendation: **Option 1** — upload without the classification now, let Part 2/3's actual
FreqTank-side integration work decide how to extend the contract (a new optional multipart
field is a small, natural addition to make *there*, not something to hack around here).

## Testing

- `sdr/tests/` (check the fork's existing test layout/framework at implementation time —
  Django's `TestCase` most likely, given `manage.py`/`requirements.txt` show a standard
  Django project) for: `geofence.distance_m`/`is_outside_geofence` (pure functions, easy unit
  tests with known coordinate pairs); `GeofenceController`'s debounce logic (mock
  `get_current_location`, assert MQTT publish only fires after N consistent samples, not
  before); `FreqTankUploader`'s query (only picks up stale + FM/AM + not-yet-uploaded rows)
  and its retry-on-failure behavior (a failed POST leaves `uploaded_at` null).
- Manual/integration verification against a real device: confirm GPS coordinates land on new
  `Transmission` rows, confirm a geofence transition actually pauses/resumes real scanning
  (watch for the device's `enabled` state to flip in sdr-monitor's own Config page), confirm
  an uploaded recording actually appears in FreqTank's Field Recordings list.
