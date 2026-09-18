# M3 — Container Persistence-Boundary Validation

**Status: containerization plus preservation of the M2-demonstrated boundary**, not packaging
alone. This document treats the container as a new execution boundary and re-validates the
persistence-boundary claim against it, rather than assuming containerization is a neutral
packaging change.

## 1. Objective

Containerize `fastDICOMgateway` in a minimal, production-shaped way, and confirm — with evidence,
not assumption — that the M2-demonstrated persistence boundary still holds when the gateway runs
as a container instead of a bare host process.

## 2. Container architecture

Two-stage Docker build (`Dockerfile`):

```text
structure-builder (python:3.12-slim-bookworm + build-essential/cmake/ninja)
  → compiles fastDICOMstructure's C++ core + C ABI (FDS_BUILD_TESTS/BENCH=OFF, FDS_BUILD_ABI=ON)
  → cmake --install to a staging prefix (shared library + the python/fastdicomstructure package)

runtime (python:3.12-slim-bookworm + libstdc++6 only)
  → libfastdicomstructure_c.so + fastdicomstructure Python package, copied from the builder
  → pip install . (fastdicom_gateway + fastapi + uvicorn; no dev/test dependencies)
  → non-root user (uid=gid=1000)
  → CMD uvicorn fastdicom_gateway.app:app --host 0.0.0.0 --port 8080
```

Both stages pin the same base image tag so the compiled shared library and the runtime stage that
links it stay glibc/libstdc++-ABI-compatible. The builder stage (compiler, cmake, ninja, apt
package lists) never reaches the runtime image — verified by inspection (§7).

### Build design: two source trees, one build context

`fastDICOMstructure` is never vendored into this repository (see README.md "Relationship to
fastDICOMstructure"). The build needs its source, though, so the Dockerfile requires a named
BuildKit **build context** pointing at the sibling checkout, rather than vendoring or a
parent-directory build context:

```sh
docker build -f Dockerfile --build-context structure=../fastDICOMstructure \
    -t fastdicom-gateway:m3 .
```

This keeps the build context (and `.dockerignore`) rooted at this repository — run from inside
`fastDICOMgateway/`, not from its parent — while still pulling in the sibling repository's source
under a distinct, explicitly-named context (`structure`), referenced in the Dockerfile via
`COPY --from=structure`. The alternative (building from the parent directory of both repos) would
have put `.dockerignore` outside any git repository, unversioned and easy to lose; this way it's
committed, ordinary Dockerfile-adjacent tooling.

One inefficiency, accepted rather than worked around: this sends fastDICOMstructure's *entire*
repository (including its own `.git/` and any local `build/` directory, ~69MB observed) as the
named build context, since adding a `.dockerignore` to trim it would mean writing into that
repository — out of scope without approval (see §20/M3 task text: "do not modify
fastDICOMstructure"). Functionally harmless (a one-time local directory read, not a network
transfer) and noted here rather than worked around silently. A local pre-existing `build/`
directory inside that checkout is `rm -rf`'d and rebuilt under a differently-named directory
(`container-build`) inside the Docker build — reusing the copied host `build/`'s CMake cache
fails outright (`cmake` refuses a cache generated from a different absolute path), which is
exactly the failure the fresh `container-build` directory avoids.

### How the Python binding locates the shared library

No `PYTHONPATH` hack: `ENV FASTDICOMSTRUCTURE_LIB=/usr/local/lib/libfastdicomstructure_c.so` is
set explicitly in the image, which `fastdicomstructure/__init__.py`'s `_find_library()` already
checks first (see that module — this is its documented override mechanism, not new behavior). The
`fastdicomstructure` Python *package* itself (a handful of `.py` files, no compiled extension) is
copied directly into the runtime image's site-packages
(`/usr/local/lib/python3.12/site-packages/fastdicomstructure/`), so `import fastdicomstructure`
resolves through Python's normal import machinery — no `sys.path` insertion needed at runtime.
(`transform.py`'s existing `_add_fastdicomstructure_to_path()` sibling-repo-detection logic still
runs unconditionally — unchanged, since M3 must not modify production code — but is a harmless
no-op inside the container: the default sibling path it would insert doesn't exist there, so it
falls through to the already-satisfied site-packages import.)

## 3. Runtime user

| | |
|---|---|
| UID:GID | `1000:1000` (`appuser:appuser`) |
| Home directory | none (`--no-create-home`) |
| Shell | `/usr/sbin/nologin` |
| Filesystem ownership required | none — all image files are root-owned, world-readable; the application opens nothing for writing |
| Additional permissions needed | none |

Baked into the image via `USER appuser:appuser`; also passed explicitly at `docker run` time
(`--user 1000:1000`) for defense-in-depth/explicitness in the documented run command.

## 4. Filesystem posture

**The container runs with `--read-only` and zero writable mounts — no tmpfs, no volume.** This was
tested, not assumed (§9): health check, all four scenarios, and multiple repeat runs all completed
successfully with no writable filesystem surface at all.

### Writable-surface accounting

| Surface | Writable? |
|---|---|
| Container root filesystem | No — `--read-only` |
| `/tmp` | No — no tmpfs mounted; not needed |
| Any volume/bind mount | None configured |
| Application-controlled path | None — the app opens nothing for writing (confirmed: see §8/§10) |

`PYTHONDONTWRITEBYTECODE=1` is set, but turned out to be unnecessary for the "no writes at all"
result: `pip install .` already compiles `.pyc` files for the installed packages at **build** time
(baked into the image layer), and with the env var set, Python never attempts to write a fresh one
at container start or per-request — so the container's observed lifetime write count is zero, not
merely "zero after excluding startup bytecode caching" the way M2's host runs were. The env var is
kept and documented rather than removed, since it's what makes that true rather than incidental.

## 5. Validation hypothesis (M3-specific)

> Under the defined local M3 container execution environment and exercised request scenarios, the
> containerized `fastDICOMgateway` does not persist the original incoming synthetic DICOM object or
> selected source canaries to application-created writable filesystem artifacts or application logs
> before policy transformation.

## 6. Observation methodology

The harness (`src/fastdicom_gateway/validation/m3.py`) reuses M2's canary fixtures and the four
request scenarios verbatim (both now live in `scenarios.py`, shared between `m2.py` and `m3.py` —
see that module's docstring; this is the same scenario/scoring code M2 already validated, not a
reimplementation). What differs from M2 is how persistence is observed, because M2's mechanism
(`strace -f` on a host process) doesn't reach usefully inside a container's own PID/mount namespace
without deliberately weakening its security posture for validation purposes (extra
`--cap-add=SYS_PTRACE`/seccomp changes) — which would contradict a "production-shaped minimal
container." Two container-native mechanisms were used instead, and are stronger for this specific
question than syscall tracing would have been:

1. **`--read-only` rootfs enforcement itself is architectural evidence, not just observed
   evidence.** An attempted write fails at the kernel level (`EROFS`) before a single byte is
   written, rather than merely going unobserved by a fallible tracer. An attempted write still
   surfaces indirectly: it raises a Python `OSError` inside request handling, which `app.py`'s
   existing generic `except Exception` branch turns into a 500 with a logged traceback — covered
   by the same log-canary scan as everything else.
2. **`docker diff` against the running container**, snapshotted after each scenario and diffed
   against the previous snapshot to attribute a filesystem change to the scenario window it
   appeared in. Every path `docker diff` has *ever* reported changed (not just each scenario's
   incremental delta) is additionally content-checked via `docker cp` while the container is still
   running, for canaries — see §11 for why the delta alone is insufficient on its own.

Container logs are `docker logs`' captured stdout+stderr (a harness-side `subprocess.run` capture,
not a file the container itself wrote — the container never redirects logs anywhere).

### Not used, and why

- **In-container `strace`.** Would require added capabilities/relaxed seccomp specifically for
  validation, contradicting the minimal/production-shaped container this milestone is supposed to
  produce. `--read-only` is strictly stronger evidence for "did anything persist" than syscall
  tracing would add here, since it prevents the write rather than merely observing it.
- **Filesystem export/overlay inspection beyond `docker diff`.** Not needed once `--read-only`
  succeeds cleanly with an empty diff across every scenario — there is no writable overlay content
  to export or inspect.

## 7. Image contents (M3-AC2)

```
$ docker run --rm fastdicom-gateway:m3 sh -c "find /app -maxdepth 3; which cmake gcc g++ git; whoami; id"
/app  /app/src  /app/src/fastdicom_gateway/{transform,app,logging,__init__}.py  /app/pyproject.toml  /app/README.md
(cmake/gcc/g++/git: none found)
appuser
uid=1000(appuser) gid=1000(appuser) groups=1000(appuser)
```

No `validation/`, no `tests/`, no `.git`, no `.venv`, no build tooling, no credentials, no `.env`
files. `src/fastdicom_gateway/validation/` and `tests/` are excluded via `.dockerignore` — the same
harness/test-tooling exclusion class documented in `docs/M2_PERSISTENCE_BOUNDARY_VALIDATION.md`
("Harness vs. application persistence"): those directories legitimately contain the literal canary
constants as Python source, and the runtime image shouldn't ship them either.

## 8. Baseline functional container tests (M3-AC4)

Run manually before persistence validation, against a `--read-only` container:

| Check | Result |
|---|---|
| `GET /healthz` | 200 |
| Successful DICOM transform | 200, `application/dicom`, PatientName removed, PatientID → `DEMO`, PatientBirthDate removed, private element removed, Pixel Data preserved, output reparses |
| Malformed request | 4xx, no DICOM response, malformed bytes not echoed |
| Natural unsupported-write case (unmodified Implicit VR, no targeted tags) | 500, no source canary in response |

All four also form the M3 validation harness's four scenarios (§9), run automatically and
repeatably rather than only by hand.

## 9. Scenarios

Identical fixtures/scoring to M2 (`scenarios.py`) — see
`docs/M2_PERSISTENCE_BOUNDARY_VALIDATION.md` §6 for the full description of each. Summary:

| ID | Name | Expected |
|---|---|---|
| A | Successful transform | 200, canaries removed/replaced, Pixel Data preserved, output reparses |
| B | Malformed input | 4xx, no DICOM result, malformed bytes not echoed |
| C | Parser-rejected (truncated Pixel Data) | 4xx, distinct failure mode from B |
| D | Internal write failure (unmodified Implicit VR, no targeted tags) | 500, no canary in response |

## 10. Results

Latest run: **`docs/m3_evidence/latest_result.json`** (regenerated by the reproduction command
below).

| Scenario | Result | HTTP | Canary in logs | Canary in artifacts | Filesystem changes this scenario |
|---|---|---|---|---|---|
| A successful_transform | PASS | 200 | No | No | 0 |
| B malformed_input | PASS | 400 | No | No | 0 |
| C parser_rejected_truncated_pixel_data | PASS | 400 | No | No | 0 |
| D internal_write_unsupported | PASS | 500 | No | No | 0 |

**Overall: PASS.** `container_filesystem_changes` (the full `docker diff` snapshot at the end of
the run, covering container startup through all four scenarios): **empty**. `canary_in_logs`:
**false**. A traceback is present in container logs (Scenario D's 500 — `logger.exception` by
design, same as M2); confirmed canary-free by the log scan.

## 11. Negative control (M3-AC11)

Two variants were run — one showing the architecture *preventing* a violation, one showing the
harness *detecting* one when the architecture doesn't prevent it. Both used a temporary,
uncommitted one-line change to `app.py` (writing the raw request body to
`/tmp/DELIBERATE_M3_NEGATIVE_CONTROL_LEAK.bin` before any processing), built into a throwaway image
tag (`fastdicom-gateway:m3-negctl`), exercised, and then **reverted** (`git checkout --`) and the
throwaway image deleted — neither the code change nor the image is part of any commit.

**Variant 1 — `--read-only` (the production default).** All three non-500-expecting scenarios
failed at the HTTP level (500 instead of 200/400) because the injected `open(..., "wb")` itself
raised before touching the request path:

```
OSError: [Errno 30] Read-only file system: '/tmp/DELIBERATE_M3_NEGATIVE_CONTROL_LEAK.bin'
```

`container_filesystem_changes` stayed **empty** and `canary_in_logs` stayed **false** — the write
was blocked before a single byte reached disk, so there was nothing left for either observation
method to find. This is the "prevented outright" case the read-only architecture is meant to
produce.

**Variant 2 — writable (`--writable` flag, for comparison only, never the production default).**
The write succeeded. First pass at the harness's scoring under-detected it: because all four
requests write to the *same* path, `docker diff` keeps reporting that path with the same
change-kind (`A`) on every subsequent overwrite — it does not reappear in a delta computed against
the previous snapshot — so only Scenario A (the first to create the file) was flagged; B/C/D showed
a false PASS even though the canary was, in fact, in that file by the time each of them finished.
This is the exact same class of gap M2's own negative control found (a file overwritten by later
requests only implicates its first/last writer under naive detection) — found here by deliberately
repeating that exercise for the new observation mechanism rather than assuming it didn't apply.
**Fixed** by content-checking every path `docker diff` has *ever* reported changed (not just each
scenario's incremental delta) while the container is still running — see `m3.py`'s `run()`, and
`tests/test_m3_validation.py::test_diff_delta_is_empty_when_nothing_new_appears`, which pins the
underlying `docker diff` behavior this depends on. Re-run after the fix: all four scenarios
correctly failed, each with `canary_in_observed_application_artifacts: true` and a note naming the
exact path.

## 12. Filesystem-change accounting (M3-AC10)

`docker diff` was captured after container startup and after each of the four scenarios; every
newly-appeared path is attributed to the scenario window it appeared in (§6). Across the reported
run: zero changes at any point in the container's lifetime — startup, all four scenarios, and final
teardown snapshot all empty.

## 13. Log findings (M3-AC9)

`docker logs` (combined stdout+stderr) searched for all three fixed canaries
(`NEVER_PERSIST^KRIS`, `SECRET-123456789`, `19610217`) and all four per-scenario run canaries: none
found (`canary_in_logs: false`). A traceback is present (Scenario D, expected); confirmed
canary-free.

## 14. Limitations

- **Single host, single Docker daemon, single run shape.** One local `dockerd`, four sequential
  requests — not concurrent load, not long-running-container behavior over time, not a
  multi-replica deployment.
- **`docker diff` visibility.** It reports changes to the container's own writable layer; it would
  not show a write to a bind-mounted host path if one were ever added (none is configured here —
  §4's writable-surface table is the actual current configuration, not just what `docker diff`
  happens to cover).
- **No in-container syscall tracing** — a deliberate scope decision (§6 "Not used, and why"), not
  an oversight: `--read-only` is stronger evidence for the specific question this milestone asks.
- **Build-context inefficiency** (§2) — functionally harmless, not a portability/runtime issue in
  fastDICOMstructure, so left as documented rather than worked around inside that repository.
- Everything M2 already stated as out of scope remains out of scope here too (RAM, kernel/socket
  buffers, swap, crash dumps unless tested, packet capture, a malicious/privileged observer, etc.)
  — see the non-claims list below, which restates the container-specific subset.

## 15. Explicit non-claims

M3 does **not** prove anything about: Cloud Run, GCP ingress, Google infrastructure logging, host
swap, Docker daemon storage internals, container layer implementation details beyond what
`docker diff` actually reported, packet capture, host kernel buffers, RAM, crash/core dumps (not
tested), a malicious/privileged host or container observer, compliance status of any kind
(HIPAA, PS3.15, etc.), or production de-identification completeness. It does not claim containers
in general cannot persist data — only what this specific image, run this specific way, was
observed (and, via `--read-only`, architecturally prevented) from doing across the scenarios
actually exercised.

## 16. Conclusion

Under the defined local M3 container execution environment and exercised request scenarios, the
containerized `fastDICOMgateway` completed the demonstrated transformation and failure paths
without observed application-created persistence of the original synthetic DICOM input or selected
source canaries to the container's writable filesystem surfaces or application logs before policy
transformation.

## 17. Reproduction

```sh
cd fastDICOMgateway

# Build (from inside this repository; requires the sibling fastDICOMstructure checkout):
docker build -f Dockerfile --build-context structure=../fastDICOMstructure \
    -t fastdicom-gateway:m3 .

# Run (production-shaped: read-only, non-root, unprivileged port):
docker run -d --rm --read-only --user 1000:1000 \
    -p 127.0.0.1:8080:8080 --name fastdicom-gateway-m3 fastdicom-gateway:m3
curl http://127.0.0.1:8080/healthz
curl -X POST -H 'Content-Type: application/dicom' --data-binary @input.dcm \
    http://127.0.0.1:8080/dicom --output transformed.dcm
docker stop fastdicom-gateway-m3

# Existing M1/M1.1/M2/M3 regression + validation-unit test suite:
source .venv/bin/activate
export FASTDICOMSTRUCTURE_LIB=../fastDICOMstructure/build/libfastdicomstructure_c.so
pytest -v

# The M3 container validation run itself (builds the image, runs all four
# scenarios against a --read-only container, prints PASS/FAIL, exit code
# reflects overall_result):
python -m fastdicom_gateway.validation.m3 --out docs/m3_evidence/latest_result.json
```

`python -m fastdicom_gateway.validation.m3 --no-build --image fastdicom-gateway:m3` reuses an
already-built image. `--writable` runs without `--read-only` for comparison (never the production
default). `--tmpfs /tmp:rw,size=16m` is available but was not needed for any result in this
document.
