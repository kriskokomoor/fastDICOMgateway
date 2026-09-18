# M4 — Cloud Run Deployment + Managed-Ingress Boundary Validation

> **Public-release redaction note (2026-09-17):** the author's personal email, the specific GCP
> project identifier, its default compute service account's numeric prefix, and the deployed
> Cloud Run service URL have been replaced with `<redacted-...>` placeholders below and in
> `docs/m4_evidence/latest_result.json`, for public release. This is a value-level substitution
> only — no reported command, argument, timestamp, log content, digest, count, or conclusion was
> changed.

**Status: deployment plus empirical characterization of a new managed execution boundary**, not
packaging alone — the same posture M3 took toward containerization. This is not the Healthcare API
milestone; no persistence sink was added.

## 1. Objective

Deploy the exact M3 container to Google Cloud Run and determine, empirically, whether the
demonstrated application/container persistence boundary survives the move to a managed ingress and
managed execution environment neither M2 nor M3 could observe.

## 2. Starting state

`fastDICOMgateway` at `1fc9e48` (M3 complete), `fastDICOMstructure` at `4eb44cb` (unmodified),
both clean working trees. `gcloud` (Google Cloud SDK 583.0.0) was authenticated as
`<redacted-personal-email>`, default project `<redacted-gcp-project>` ("dbt Cloud
Functions") — an org-affiliated, billing-enabled project already running one unrelated Cloud
Function. **Confirmed with the user before deploying anything** (this project is not an empty
sandbox — see the conversation's explicit confirmation) rather than assuming it was suitable.
Cloud Run, Artifact Registry, Cloud Build, and Cloud Logging APIs were already enabled; no
existing Cloud Run service or Artifact Registry repo for this project existed prior to M4.

## 3. Cloud Run architecture

```text
public/test client (synthetic data only)
      |
      v
Cloud Run managed ingress (Google Frontend)
      |
      v
fastdicom-gateway container (M3's image, unchanged app/container design)
      |
      v
HTTP response
```

No load balancer, no custom domain, no VPC connector, no database, no downstream API, no secrets.
One Cloud Run service, one region.

## 4. Image publication (M4-AC1)

New Artifact Registry Docker repository `fastdicom-gateway` created in `us-central1` (the existing
`gcf-artifacts` repo is Cloud-Functions-managed; mixing concerns there would have been messy, not
"unnecessary resource creation" — no other suitable repo existed).

| | |
|---|---|
| Image URI | `us-central1-docker.pkg.dev/<redacted-gcp-project>/fastdicom-gateway/fastdicom-gateway:m4` |
| Image digest | `sha256:7ef411b149e8e90c987693e3278e87f31177806a011d4d747797df56a308c125` |
| Built from | gateway commit `1fc9e48` (plus this milestone's two source changes, see §5) + structure commit `4eb44cb`, same two-repository build arrangement as M3 (`Dockerfile`, `--build-context structure=../fastDICOMstructure`) |

Reproducible: `docker build` (M3's `Dockerfile`, unvendored sibling source) → `docker push` to
Artifact Registry — no new build mechanism introduced.

## 5. Cloud Run container compatibility — two source changes, both minimal and justified

M3's application/container design was preserved; two small, additive changes were needed, both
found by testing against the real platform rather than assumed from documentation:

**`$PORT` handling (`Dockerfile`).** Cloud Run supplies the listen port via the `$PORT` env var
(defaulting to 8080, but not guaranteed to be 8080). The CMD changed from an exec-form array
(hardcoded `--port 8080`, no shell expansion) to `["/bin/sh", "-c", "exec uvicorn ... --port
${PORT:-8080}"]` — the `exec` keeps uvicorn as PID 1 (without it, `/bin/sh` staying PID 1 would
break `SIGTERM` forwarding, breaking graceful shutdown for both M3's `docker stop` and Cloud Run's
scale-down signal). Verified locally: `docker run -e PORT=9090 ...` correctly listens on 9090, and
`docker top` confirms uvicorn — not `sh` — is PID 1.

**`/healthz` is a reserved path on Cloud Run (`app.py`).** Empirically discovered, not assumed:
`GET /healthz` against the deployed service returns Google's own branded 404 HTML page — not this
app's FastAPI JSON 404 — while every *other* path (`/health`, `/healthzz`, `/Healthz`, `/foo`,
`/dicom`) correctly reaches the container and gets a real FastAPI response. This is a documented
class of Google Frontend behavior: the exact path `/healthz` is intercepted before reaching a Cloud
Run backend. The existing `/healthz` route was left completely unchanged (still correct for
local/M1–M3 use, where no real Google infrastructure sits in front of it); a plain additive alias,
`GET /livez` (identical handler), was added for anything that needs to health-check the *deployed*
service. Confirmed after deploying: `/livez` → 200, `/healthz` → 404 (as expected and documented,
not a regression).

Also added: a one-time, request-independent startup log line (`app.py`'s FastAPI `lifespan`)
recording UID/GID, cwd, `/tmp` existence/writability, and Cloud Run's own `K_SERVICE`/`K_REVISION`/
`K_CONFIGURATION` env vars — never anything derived from a request. This is what makes §13's
filesystem-posture claims verified rather than assumed (see §13).

Neither change touches the DICOM transformation path. `transform.py` is unchanged since M1.1 (`git
log` confirms); the memory-native `read_buffer → mutation → write_bytes` path is untouched.

## 6. Deployment configuration (M4-AC10)

```sh
gcloud run deploy fastdicom-gateway \
    --project=<redacted-gcp-project> --region=us-central1 \
    --image=us-central1-docker.pkg.dev/<redacted-gcp-project>/fastdicom-gateway/fastdicom-gateway:m4 \
    --port=8080 --memory=512Mi --cpu=1 \
    --min-instances=0 --max-instances=2 --timeout=60 \
    --allow-unauthenticated
```

| | |
|---|---|
| Service | `fastdicom-gateway` |
| Region | `us-central1` |
| Revision | `fastdicom-gateway-00002-nnn` |
| Service URL | `https://<redacted-cloud-run-url>` |
| Ingress | all (no restriction — no load balancer/VPC in front) |
| Authentication | **unauthenticated** (`--allow-unauthenticated`) — see §7 |
| Service account | `<redacted-project-number>-compute@developer.gserviceaccount.com` (project default compute SA; no custom SA created) |
| CPU / memory | 1 / 512Mi |
| Concurrency | 80 (Cloud Run default, not overridden) |
| Timeout | 60s |
| Min / max instances | 0 / 2 — scale-to-zero, capped, no reserved capacity |
| Env vars explicitly set | none (only the image's own baked-in `ENV`s: `FASTDICOMSTRUCTURE_LIB`, `PYTHONDONTWRITEBYTECODE`, `PYTHONUNBUFFERED`) |

## 7. Authentication/ingress posture

**Unauthenticated invocation is enabled.** This is a temporary, synthetic-data-only demonstration
service — no real PHI is ever sent to it, by construction (see §8) — and unauthenticated access was
chosen specifically to keep M4's validation simple, per the task's explicit allowance, not as a
statement about production security posture. It is not represented as such anywhere in this
document. No IAM complexity was added beyond what `--allow-unauthenticated` requires.

## 8. Synthetic data only

Every request sent during this milestone — scenario fixtures, the negative control's request
bodies — used only the same synthetic canaries established in M2/M3 (`NEVER_PERSIST^KRIS`,
`SECRET-123456789`, `19610217`, plus per-run/per-scenario canaries). No real PHI was ever
constructed, sent, or referenced.

## 9. Validation hypothesis

> Under the defined M4 Cloud Run deployment and exercised synthetic request scenarios, the deployed
> `fastDICOMgateway` preserves its expected transformation/failure behavior and no selected source
> canaries are observed in the application logs or Cloud Run log records inspected for the service.

This does **not** claim Google infrastructure never transiently buffers, processes, replicates, or
retains request data outside the logs and surfaces actually inspected (see §16).

## 10. Scenario methodology

`src/fastdicom_gateway/validation/m4.py` reuses M2/M3's canary fixtures and four scenarios verbatim
(`scenarios.py`, unchanged) against the live HTTPS endpoint. Each scenario's request/response is
timed with a real UTC timestamp window (a 4-second gap is deliberately held between scenarios so
those windows don't overlap once padded — see §15 for why this matters), then Cloud Logging is
queried for the full test window and every returned entry is checked for every canary.

## 11. Scenario results (M4-AC3)

Latest run: **`docs/m4_evidence/latest_result.json`** (regenerated by the reproduction command
below).

| Scenario | Result | HTTP | Canary in app logs | Canary in Cloud Run logs |
|---|---|---|---|---|
| A successful_transform | PASS | 200 | No | No |
| B malformed_input | PASS | 400 | No | No |
| C parser_rejected_truncated_pixel_data | PASS | 400 | No | No |
| D internal_write_unsupported | PASS | 500 | No | No |

**Overall: PASS.** All per-scenario checks (patient canaries removed/replaced, Pixel Data
preserved, output reparses, no traceback in the *response*, etc.) matched M2/M3 exactly — see
the evidence file's `checks` per scenario.

## 12. Application-log inspection (M4-AC6)

Cloud Run's own `run.googleapis.com%2Fstdout`/`%2Fstderr` log streams (this container's captured
stdout/stderr — confirmed by content: entries include this app's own `logger.info(...)` lines like
`dicom_request_transformed input_bytes=...`, not just uvicorn's access-log lines) were searched for
all three fixed canaries and all four per-scenario run canaries: **none found.** A traceback is
present for Scenario D's 500 (`logger.exception`, by design, same as M2/M3); confirmed canary-free.

## 13. Cloud Run log inspection / request-log characterization (M4-AC7, M4-AC8)

**Two distinct log streams exist per service**, distinguished by `logName` suffix — established by
directly inspecting real entries, not inferred from documentation:

- `run.googleapis.com%2Fstdout` / `%2Fstderr` — this container's own captured output (§12).
- `run.googleapis.com%2Frequests` — Cloud Run's own auto-generated per-HTTP-request log entry.

Fields actually observed on `requests`-stream entries in this run's test window:
`latency, protocol, remoteIp, requestMethod, requestSize, requestUrl, responseSize, serverIp,
status, userAgent` (see `http_request_fields_observed` in the evidence file). No field carrying
request or response **body** content was present in any queried entry — `requestSize`/
`responseSize` are byte counts, not content. Per the task's own wording standard:

> No request body content was observed in the inspected Cloud Run log records.

(Deliberately not claimed: "Cloud Run never logs request bodies" — only what was actually
observed for this service, this configuration, this test window.) No canary from any scenario's
request or response body appeared in this stream either (M4-AC7).

## 14. Runtime filesystem posture (M4-AC9)

The startup diagnostic log (§5) reports real, observed values from the live Cloud Run container:

```
runtime_startup uid=1000 gid=1000 cwd=/app tmp_exists=True tmp_writable=True
  k_service=fastdicom-gateway k_revision=fastdicom-gateway-00002-nnn k_configuration=fastdicom-gateway
```

**Platform vs. application, precisely distinguished:**

- **Platform capability:** Cloud Run provides a writable `/tmp` by default (`tmp_writable=True`) —
  unlike M3's `docker run --read-only`, which left `/tmp` non-writable (confirmed there via a
  direct write attempt, not just `os.access`). Cloud Run has no direct equivalent of `--read-only`;
  it always supplies an ephemeral writable filesystem. M4 does not claim otherwise.
- **Application behavior:** the application does not require or use that writable path. `open(`
  does not appear anywhere in `app.py`/`transform.py` (grepped again this milestone, after the two
  source changes in §5 — still none); `transform.py` is byte-for-byte unchanged since M1.1's
  `read_buffer → mutation → write_bytes` memory-native path.

## 15. Negative control (M4-AC11)

A temporary, uncommitted one-line change to `app.py` (`logger.info("DELIBERATE_M4_NEGATIVE_
CONTROL_LEAK body=%r", body)` at the top of `receive_dicom`) was built into a throwaway image
(`fastdicom-gateway:m4-negctl`), pushed under a distinct tag, and deployed to a **separate,
throwaway Cloud Run service** (`fastdicom-gateway-m4-negctl`) — never the canonical service, never
sharing its revision history.

Running the M4 harness against that service correctly reported `overall_result: FAIL`, with
`canary_in_application_logs: true`.

**This also found and fixed a real bug in the harness itself** — the same spirit as M2's and M3's
negative controls surfacing gaps in their own detection logic, not just the target's. The first
version attributed a log hit to a scenario by canary-name match alone
(`patient_name`/`patient_id`/`patient_birth_date` are "relevant" to *every* scenario that could
plausibly leak them), with no time-window check — so a hit from Scenario A's own leaked log line
was also credited to Scenario D just because both scenarios' `relevant` sets included
`patient_name`. Symptom: with the scenarios fired 0.3s apart, nearly every scenario showed the
*same* log-entry IDs in its notes. Fixed by (a) recording each scenario's own real UTC timestamp
window, (b) widening the gap between scenario requests to 4 seconds specifically so those windows
don't overlap even with clock-skew padding, and (c) only crediting a hit to a scenario when the log
entry's own timestamp falls inside that scenario's window. Re-run after the fix: each of the four
scenarios' `notes` named exactly one, correctly-attributed log entry — no cross-contamination.

The throwaway service and image were deleted immediately after
(`gcloud run services delete fastdicom-gateway-m4-negctl`, `gcloud artifacts docker images
delete ...:m4-negctl`), and the sabotage line was reverted (`git diff` confirmed clean before
continuing). The canonical service was never running the sabotaged code — verified by rebuilding
the (now-reverted) source locally and confirming it reproduces the exact same local image ID as the
one already pushed and deployed as the canonical revision, byte-for-byte, without needing a
redeploy.

## 16. Explicit non-claims

M4 does **not** establish: absence of request buffering by Google infrastructure; absence of data
in RAM/kernel/network buffers; absence of Google internal replicas; absence from platform telemetry
not visible to this project; absence from packet capture; absence from swap; absence from crash
dumps (not tested); absence from a malicious/privileged observer; Healthcare API or DICOM-store
behavior (none exists yet); HIPAA compliance; PS3.15 compliance; complete de-identification; or
production security readiness. It does not claim "Google never sees PHI," "PHI never persists in
GCP," "Cloud Run is memory-only," or any compliance status — only what was actually queried and
observed for this specific service, this configuration, and this test window.

## 17. Limitations

- One project, one region, one revision, four sequential synthetic requests — not concurrent load,
  not sustained traffic, not a multi-region deployment.
- Cloud Logging query completeness depends on ingestion/propagation timing; a 15-second wait before
  querying was used, and `log_entries_queried` in the evidence file records exactly how many
  entries were actually returned for the window, so a suspiciously low count would be visible
  rather than silently assumed complete.
- `/healthz`'s interception is a Google Frontend behavior confirmed for this project/region at the
  time of this run; not asserted to be a permanently documented, unchanging platform contract.
- Everything M2/M3 already scoped out remains out of scope here (RAM, kernel/socket buffers, swap,
  crash dumps unless tested, packet capture, a malicious/privileged observer, etc.).

## 18. Tests (M4-AC14)

37 pre-existing local tests (M1/M1.1: 18, M2: 14, M3: 5) continue to pass. M3's own live-container
regression (`--read-only`, zero filesystem writes, zero canaries) was re-run after this milestone's
two source changes and is still clean — confirmed via `pytest tests/test_m3_validation.py`, not
assumed unaffected. 8 new M4 tests added (`tests/test_m4_validation.py`): 7 unit tests for log-entry
classification/scanning logic (always run) plus 1 opt-in live end-to-end test (gated behind
`FASTDICOM_GATEWAY_M4_LIVE_TEST=1`, since — unlike M2's `strace` and M3's local Docker, both free
and near-instant — it hits real billed Cloud Logging/Cloud Run and takes ~40s for log propagation).

## 19. Conclusion

Under the defined M4 Cloud Run deployment and exercised synthetic request scenarios,
`fastDICOMgateway` preserved the previously validated transformation/failure behavior, and no
selected source canaries were observed in the application or Cloud Run log records inspected for
the test window.

## 20. Reproduction

```sh
export PROJECT=<redacted-gcp-project>
export REGION=us-central1
export IMAGE="${REGION}-docker.pkg.dev/${PROJECT}/fastdicom-gateway/fastdicom-gateway:m4"

# Build + push (from inside fastDICOMgateway/):
docker build -f Dockerfile --build-context structure=../fastDICOMstructure -t "$IMAGE" .
gcloud auth configure-docker "${REGION}-docker.pkg.dev" --quiet
docker push "$IMAGE"

# Deploy:
gcloud run deploy fastdicom-gateway \
    --project="$PROJECT" --region="$REGION" --image="$IMAGE" \
    --port=8080 --memory=512Mi --cpu=1 \
    --min-instances=0 --max-instances=2 --timeout=60 --allow-unauthenticated

# Validate (builds nothing; drives the already-deployed service):
source .venv/bin/activate
python -m fastdicom_gateway.validation.m4 --project "$PROJECT" --region "$REGION" \
    --service fastdicom-gateway --out docs/m4_evidence/latest_result.json
```

### Teardown

```sh
gcloud run services delete fastdicom-gateway --project="$PROJECT" --region="$REGION"
gcloud artifacts repositories delete fastdicom-gateway --project="$PROJECT" --location="$REGION"
```

**The canonical service was left deployed** (not torn down) after this milestone — `min-instances=0`
means it scales to zero and costs effectively nothing while idle, and the task's own guidance treats
leaving it up as acceptable when a later milestone may build on it. Run the teardown commands above
at any time to remove it.
