# M2 — Persistence-Boundary Validation

**Status: evidence and validation milestone, not a feature milestone.** This document reports the
result of one specific, falsifiable hypothesis test, not a general security or compliance claim.

## 1. Hypothesis

> Under the defined local M2 execution environment and exercised request scenarios, the
> `fastDICOMgateway` application path does not persist the original incoming DICOM object or
> selected synthetic source identifiers to application-accessible filesystem artifacts or
> application logs before policy transformation.

## 2. Scope

Application-layer persistence, visible from the local Linux execution environment the harness
itself runs in:

- regular filesystem files, `/tmp`, temporary directories, cache files;
- application-created logs, debug/error output;
- explicit file open/create/write/rename/unlink operations made by the gateway process or its
  children;
- any database/object-store access, had one unexpectedly appeared (the gateway has none by
  design — see README.md).

Observed via: `strace -f` on the gateway's real OS process (not an in-process ASGI test client),
captured stdout/stderr, and a bounded, documented post-run filesystem scan.

## 3. Explicit non-claims

This milestone does **not** claim anything about: RAM, kernel buffers, network/socket buffers,
Python/ASGI internal buffers, virtual memory, swap, crash/core dumps (not tested), shell history,
packet capture, reverse proxies, container runtimes, Docker, Cloud Run, GCP infrastructure or
logging, OS facilities outside the actual observation mechanisms used, physical memory remanence,
hardware/firmware, or a malicious/privileged observer. It does not claim "PHI never touches disk,"
"zero persistence," "guaranteed memory-only," or any compliance status (HIPAA, PS3.15, etc.). Where
this document doesn't say a location was scanned, treat it as **not scanned**, not as clean.

## 4. Test environment

| | |
|---|---|
| OS | Linux, `uname -r` `6.8.0-138-generic` |
| Python | 3.14.2 (project supports 3.10+; validated on 3.14.2 here) |
| `strace` | 5.16 |
| `fastdicom-gateway` commit | `03f135294856d9a7b3291e9f6814b0ce5918275e` (M1.1) |
| `fastDICOMstructure` commit | `4eb44cb45cc87df5527dfe837f7c4b08e9248c0f` (M1.1, unmodified for M2) |

`fastDICOMstructure` was inspected and confirmed to require **no** changes for M2 — the gateway is
the only thing exercised here; the sibling repository is a fixed, already-validated dependency.

## 5. Observation methodology

The harness (`src/fastdicom_gateway/validation/`) launches `fastdicom_gateway` as its own real OS
process — `strace -f -tt -yy -s 8192 -e trace=%file,write ... -- python -m uvicorn
fastdicom_gateway.app:app` — and drives it over real HTTP (`urllib`, not Starlette's in-process
`TestClient`, which never leaves the harness's own process and so could never be traced). Four
canary-bearing synthetic requests are sent in sequence; each scenario's wall-clock window is
recorded and used to attribute syscall events.

Two independent evidence sources are cross-checked:

1. **Syscalls.** Every `openat`/`open`/`creat` call requesting `O_CREAT`/`O_WRONLY`/`O_RDWR`, and
   every `rename`/`unlink`, is extracted from the trace and bucketed into the scenario window it
   falls in (or `startup`/`shutdown`/`between_scenarios`). This catches a file even if it's deleted
   before the post-run scan gets to it.
2. **Filesystem scan.** After the run, `docs/M2_PERSISTENCE_BOUNDARY_VALIDATION.md`'s scanned areas
   (below) are walked for files with `mtime >= process-launch-time - 2s`; each such file's raw
   bytes are searched for every canary.

Any request-time write-capable `open()` fails that scenario **on its own**, independent of whether
a canary is later found in the file — see "Harness vs. application persistence" below for why
content-only matching would under-detect a file that gets overwritten by a later request.

Application logs are the gateway subprocess's captured stdout/stderr (harness-captured pipes; the
gateway itself only ever writes to stdout — see `src/fastdicom_gateway/logging.py` — no
`FileHandler` is attached).

### Scanned areas (this run)

| Area | Root | Excluded |
|---|---|---|
| `gateway_repo` | this repository | `.git`, `.venv`, `.pytest_cache`, `src/fastdicom_gateway/validation`, `tests` |
| `structure_repo` | sibling `fastDICOMstructure` checkout | `.git`, `build`, `build-release` |
| `system_temp_dir` | `/tmp` (`tempfile.gettempdir()`) | the harness's own workspace directory |

**Not scanned**, and not claimed clean: the user's home directory outside the two repos and
`/tmp`, any location outside this host entirely (the point of a later, stronger milestone), swap,
and anything requiring root to read.

### Harness vs. application persistence

Two exclusions above need justification, because building the fixtures for this validation
inevitably creates Python source files (`validation/fixtures.py`, and this suite's own
`tests/test_m2_validation.py`) that contain the literal canary constants — that's unavoidable; the
harness has to know what it's searching for. The **first** process in this environment to import
one of those modules causes CPython to compile and cache a `__pycache__/*.pyc`, which embeds those
constants in its bytecode `co_consts`. That happened during this document's own construction: the
harness's own orchestrator process (not the traced gateway subprocess) triggered exactly this for
`fixtures.py`, and `pytest`'s own collector did the same for `test_m2_validation.py`. Both are
**harness/test-tooling self-caching** — the traced gateway subprocess never imports `validation` or
`tests` (it imports only `app`/`transform`/`logging`) — so both directories are excluded from the
application-persistence scan, with the mechanism documented in `m2.py`'s `_scanned_directories()`
docstring for anyone auditing this claim later.

## 6. Scenarios

| ID | Name | Fixture | Expected |
|---|---|---|---|
| A | Successful transform | Valid Explicit VR LE DICOM, all 3 fixed patient canaries, a private element, deterministic Pixel Data, a per-run canary in an untouched tag (Institution Name) | 200, canaries removed/replaced, Pixel Data preserved, output reparses |
| B | Malformed input | Non-DICOM bytes (no preamble/`DICM` magic) with a per-run canary appended as raw ASCII | 4xx, no DICOM result, no traceback in the *response*, malformed bytes not echoed |
| C | Parser-rejected | A valid fixture truncated mid-Pixel-Data (declared length exceeds bytes present) — syntactically DICOM-like, rejected by the structural parser's own diagnostics (`recoverable_error`), a different failure mode from B | 4xx, no traceback in response |
| D | Internal write failure | Valid, **unmodified** Implicit VR Little Endian input with none of the three targeted patient tags and no private elements | 500 — see below |

Scenario D exploits an already-documented, unmodified fastDICOMstructure behavior (README.md
"Known limitations": an unmodified Implicit-VR structure makes `write_bytes()` raise
`FDS_STATUS_UNSUPPORTED`, and `app.py`'s generic `except Exception` turns that into a 500) — no
production code was written or altered to manufacture this scenario; it reuses a natural failure
mode that already existed before M2.

No scenario was skipped: unlike the task's allowance for marking C/D `NOT_EXERCISED`, both had a
clean, natural, zero-production-change fixture, confirmed empirically before being wired into the
harness (see `tests/test_m2_validation.py`'s unit tests pinning each mechanism independently of the
end-to-end run).

## 7. Results

Latest run: **`docs/m2_evidence/latest_result.json`** (regenerated by the reproduction command
below; not hand-edited). Summary as committed:

| Scenario | Result | HTTP | Canary in logs | Canary in artifacts | Request-time writes |
|---|---|---|---|---|---|
| A successful_transform | PASS | 200 | No | No | 0 |
| B malformed_input | PASS | 400 | No | No | 0 |
| C parser_rejected_truncated_pixel_data | PASS | 400 | No | No | 0 |
| D internal_write_unsupported | PASS | 500 | No | No | 0 |

**Overall: PASS.**

Additional findings:

- `strace` observed **zero** request-time write-capable `openat`/`open`/`creat`/`rename`/`unlink`
  calls in any scenario window. The only filesystem-mutating syscalls seen anywhere in the trace
  are `.pyc` bytecode-cache writes at process **startup** (before Scenario A's window opens) — see
  `non_request_time_filesystem_writes` in the evidence JSON, and `tests/test_m2_validation.py`'s
  negative-control-style check that this bucketing works (deleting the gateway's `__pycache__`
  before a run reliably reproduces and correctly buckets these startup writes).
- A traceback **is** present in the captured server logs (Scenario D's 500 — `logger.exception` in
  `app.py`, by design). This is normal operational behavior, not evidence of a leak on its own;
  what matters is whether a canary is *inside* it, and the log canary scan (which covers the full
  log text, tracebacks included) found none.
- `filesystem_scan_hits`: empty.

## 8. Negative control — does this harness actually detect a real leak?

A validation harness that only ever reports PASS is not evidence of anything. Before trusting a
PASS result, `transform.py`/`app.py` was temporarily modified to write the raw incoming request
body to `/tmp/DELIBERATE_M2_NEGATIVE_CONTROL_LEAK.bin` on every request (a deliberate, reviewed,
temporary sabotage — reverted immediately after, never committed). Two things came out of that:

1. The harness caught it — `overall_result` flipped to `FAIL`, with the specific scenario(s)
   flagged.
2. The first version of the scoring logic under-detected: because all four scenarios wrote to the
   *same* path, only the last request's content survived to the post-run scan, so only Scenario D
   was flagged via content-matching even though `strace` had independently recorded a write-capable
   `open()` in every one of the four scenario windows. This was a real gap in the harness, not the
   gateway — fixed by making any request-time write-capable `open()` fail its scenario on its own,
   not only ones a later content scan happens to still be able to confirm (see §5, "any request-time
   write-capable `open()` fails that scenario on its own"). Re-running the same sabotage after the
   fix correctly failed all four scenarios.

This is why §5 describes two independent, cross-checked evidence sources rather than one: the
filesystem scan alone would have missed 3 of 4 in the overwrite case above.

## 9. Limitations

- **strace parser.** `strace_events.py` is a pragmatic, line-based regex parser for `strace -f -tt
  -yy` output, not a full strace-grammar parser. It does not reconstruct syscalls split across
  `<unfinished ...>`/`<... resumed>` lines under heavy thread/process interleaving (`ParseSummary`
  counts and samples such lines rather than silently dropping them — see `strace_parse` in the
  evidence JSON for this run's count). The mtime-based filesystem scan is the independent backstop
  for exactly this gap.
- **Timestamp correlation.** Scenario windows are matched against `strace -tt`'s same-day
  `HH:MM:SS.ffffff` timestamps via lexicographic string comparison — correct as long as one M2 run
  never spans midnight (true for a run that takes a few seconds).
- **`strace` availability.** If `strace` is missing, the harness still runs (subprocess launched
  directly, no tracing) and degrades to filesystem-scan-only evidence, noting this explicitly per
  scenario rather than silently reporting the same confidence level.
- **Single host, single run shape.** This validates one specific local environment and one
  specific sequence of four requests, not concurrent load, not long-running-process behavior over
  time, and not the deployment environment (reverse proxies, ASGI body buffering, infrastructure
  logging — unchanged from M1's own stated scope).
- **Harness workspace is not committed.** Raw per-run `strace` logs and captured stdout/stderr live
  in a temporary directory (printed at the end of each run) and are not part of this repository;
  only the curated JSON result and this report are durable evidence.

## 10. Conclusion

Under the defined M2 local Linux test environment and exercised request scenarios, syscall
observation, application-log inspection, and bounded artifact scanning found no evidence that
`fastDICOMgateway` persisted the original synthetic DICOM input or selected source canaries to
application-created filesystem artifacts before policy transformation.

## 11. Reproduction

```sh
cd fastDICOMgateway
source .venv/bin/activate
export FASTDICOMSTRUCTURE_LIB=../fastDICOMstructure/build/libfastdicomstructure_c.so   # if not auto-detected

# Existing M1/M1.1 regression suite + M2's own unit/integration tests:
pytest -v

# The M2 validation run itself (prints a PASS/FAIL summary; exit code reflects overall_result):
python -m fastdicom_gateway.validation.m2 --out docs/m2_evidence/latest_result.json
```

`python -m fastdicom_gateway.validation.m2 --no-strace` runs the degraded (no syscall tracing)
path. `--port <N>` pins a port; `--workspace <dir>` reuses a specific evidence directory instead of
a fresh temporary one.
