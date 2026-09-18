# M5 — Approved Persistence into GCP Healthcare API DICOM Store

> **Public-release redaction note (2026-09-17):** the author's personal email and the specific GCP
> project identifier have been replaced with `<redacted-...>` placeholders below and in
> `docs/m5_evidence/latest_result.json` / `docs/publication_refresh/m5_refresh_result.json`, for
> public release. This is a value-level substitution only — no reported command, argument,
> timestamp, log content, digest, count, or conclusion was changed. In particular, the finding that
> retrieval used *a personal human identity, distinct from the gateway's own service identity* is
> unchanged; only the specific email value is redacted.

**Status: the first intentional durable-persistence boundary in this project.** Everything through
M4 proved the gateway didn't persist anything; M5 introduces exactly one place it does, on purpose,
after policy — and proves, by independent retrieval from that durable store, that what crossed the
boundary is the approved representation, not the source one.

## 1. Objective

Extend `fastDICOMgateway` so an approved, post-policy DICOM instance can be written directly from
memory to a Healthcare API DICOM store via STOW-RS, then independently verify — by retrieving the
exact stored instance through a separate identity, never by asking the gateway what it thinks it
stored — that the durable object contains the approved transformed content, not the source
identifiers.

## 2. Starting state

`fastDICOMgateway` at `d597272` (M4 complete), `fastDICOMstructure` at `4eb44cb` (unmodified), both
clean. Cloud Run service `fastdicom-gateway` (project `<redacted-gcp-project>`,
`us-central1`) running M4's revision, default compute service account, no Healthcare resources of
any kind existed yet (Cloud Healthcare API itself was not enabled). No existing Healthcare
dataset/store was reused, since none existed.

## 3. Architecture

```text
synthetic source DICOM
        |
        v
Cloud Run ingress (fastdicom-gateway, dedicated M5 service account)
        |
        v
POST /dicom/store  ->  read_buffer() -> policy mutation -> write_bytes() -> verification
        |
        v
authenticated DICOMweb STOW-RS  (sink.py, Application Default Credentials)
        |
        v
Healthcare API DICOM store (approved-dicom)
        |
        v
durable approved object
```

`POST /dicom` (M1–M4's endpoint) is completely unchanged and untouched by any of this — see §4.

## 4. Trust/persistence boundary

The one new intentional persistence operation is `sink.store()`'s authenticated HTTPS POST of
already-transformed, already-self-verified bytes. Nothing else changed:

- `transform.py`'s `process()` pipeline (`read_buffer → mutation → write_bytes → verify`) is
  exercised identically by both endpoints; `/dicom/store` calls the *same* `transform.process()`
  `/dicom` always has, not a second implementation. The only addition to `TransformResult` is three
  UID fields, read from the reparse `_verify_output()` already performed for M1-AC5 — no second
  parse added.
- `POST /dicom` keeps its M1–M4 transform-and-return behavior verbatim, and does not require
  Healthcare API to be reachable at all (confirmed: `tests/test_app.py::
  test_dicom_endpoint_unaffected_by_store_endpoint_existing`, and empirically — M4's validation
  harness was re-run against the M5-deployed revision after this milestone's changes and is still
  clean, see §16).
- `sink.py` is the only module that talks to Healthcare API. It builds a `multipart/related` STOW-RS
  body and sends it in memory (see `_multipart_body`) — no `tempfile`, no `open(`, no local file at
  any point. `tests/test_app.py::test_request_path_source_contains_no_persistence_apis` now scans
  `sink.py` too, alongside `app.py`/`transform.py`.

## 5. Healthcare resources (M5-AC1)

| | |
|---|---|
| Dataset | `fastdicom-m5` |
| DICOM store | `approved-dicom` |
| Location | `us-central1` (same region as Cloud Run) |
| Storage class | platform default — no storage-class flag exists on `gcloud healthcare dicom-stores create` in the installed SDK version, so none was set; the STANDARD/default class was used, never Nearline/Coldline/Archive (those require explicit configuration this command never touched) |

No FHIR store, HL7v2 store, or Consent store created. One additional, **temporary** DICOM store
(`negctl-dicom`, same dataset) existed only for the negative control (§17) and was deleted
immediately after.

## 6. IAM design (M5-AC2)

A dedicated runtime service account was created — Cloud Run no longer uses the project default
compute service account for this service:

```text
fastdicom-gateway-m5@<redacted-gcp-project>.iam.gserviceaccount.com
```

Granted `roles/healthcare.dicomEditor`, **scoped to the `approved-dicom` DICOM store resource
only** (`gcloud healthcare dicom-stores add-iam-policy-binding`), not project-wide:

```sh
gcloud healthcare dicom-stores add-iam-policy-binding approved-dicom \
    --dataset=fastdicom-m5 --location=us-central1 \
    --member="serviceAccount:fastdicom-gateway-m5@..." --role="roles/healthcare.dicomEditor"
```

**Why `dicomEditor` and not something narrower:** no predefined Healthcare IAM role grants
`dicomWebWrite` alone — every role listed by `gcloud iam roles list --filter="name:roles/
healthcare"` that includes it (`dicomEditor`, `dicomStoreAdmin`) also includes read/update/delete/
import/export. `dicomEditor` is the narrowest of those. What actually narrows this binding to
"gateway can write here and nowhere else" is the **resource scope**, not the role: the binding is
attached to one DICOM store, not the project. Confirmed by inspection, not assumed —
`gcloud projects get-iam-policy project-... --filter="bindings.members:fastdicom-gateway-m5@..."`
returns **nothing**: this service account has zero project-level IAM bindings of any kind. A
dynamic verification (impersonating the service account and attempting an unrelated project
operation, e.g. `gcloud run services list`) was attempted but stayed inconclusive within this
session's time window due to IAM-propagation delay after granting the temporary
`iam.serviceAccountTokenCreator` binding needed to impersonate; that temporary binding was removed
again immediately. The static inspection result stands as the primary evidence for this
acceptance criterion.

**Read is separated from write**, per the task's guidance: the runtime service account has no read
grant of its own — `/dicom/store` returns only the UIDs it already knew from the object it built
(never re-reading from the store), so it never needed one. All retrieval in this document (§12, the
negative control, the QIDO-RS sanity check) used the *validation identity's* own `gcloud` credentials
(`<redacted-personal-email>`), never the gateway's.

## 7. Authentication design (M5-AC3, §6)

`sink.py` uses `google.auth.default()` (Application Default Credentials) exclusively. On Cloud Run
this resolves to the attached runtime service account via the metadata server automatically — no
key file was created, no token was placed in an environment variable, and nothing credential-shaped
is written to disk at any point. `_session()` caches only the resolved `AuthorizedSession` object
(which itself handles token refresh); the credential material is never logged, and never appears in
`TransformResult`/`StoreResult`/`StoreFailure`, all of which carry only structural DICOM identifiers
or a small fixed set of failure-reason strings this project controls (see §14).

## 8. Gateway sink implementation (M5-AC3, §7-9)

`src/fastdicom_gateway/sink.py` — narrow, single-purpose: takes already-transformed bytes plus the
three UID strings the caller already has, builds a one-part `multipart/related` STOW-RS body
entirely in memory, POSTs it, and raises `PersistenceFailed` (carrying only a small fixed
`reason` string and an HTTP status code — never a downstream response body) on anything but HTTP
200. It has no DICOM-parsing knowledge at all — `fastDICOMstructure` remains the only DICOM engine
on the production path (unchanged from every prior milestone's claim).

`POST /dicom/store` (`app.py`) mirrors `/dicom`'s existing error-handling shape (`RejectedInput` →
400, unexpected exception → 500) and adds exactly one more failure category:
`sink.PersistenceFailed` → 502, with only `reason`/`status_code` logged — see §14 for why a real
bug here (an uncaught exception escaping to Starlette's default handler) was found and fixed before
this milestone's "official" evidence run.

## 9. Synthetic fixture (M5-AC on canaries)

`validation/fixtures.py`'s new `build_healthcare_store_fixture()`: all three fixed canaries
(`NEVER_PERSIST^KRIS` / `SECRET-123456789` / `19610217`), a private element, deterministic Pixel
Data, a per-run canary in Institution Name, plus (new for M5, not present in M2–M4's fixtures)
valid, PHI-free, numeric, deterministic `StudyInstanceUID`/`SeriesInstanceUID`/`SOPInstanceUID` and
a minimal Image Pixel module (required for the Healthcare API to accept a Secondary Capture
instance carrying Pixel Data — discovered by testing against the real STOW-RS endpoint directly
before wiring up the full gateway path, not guessed from documentation).

The three UIDs are **fixed**, not per-run, deliberately: this keeps the durable store's retained
byte volume from growing across repeated validation runs (a cost-guardrail concern), and is what
makes the duplicate-submission characterization (§13) mean anything at all.

## 10. Validation hypothesis

> Under the defined M5 Cloud Run and Healthcare API configuration and exercised synthetic test
> scenario, the gateway applies its demonstrated policy before submitting DICOM bytes to the
> Healthcare API DICOM Store, and independent retrieval from that durable store shows the approved
> transformed representation rather than the selected synthetic source identifiers.

Secondary:

> No selected source canaries are observed in the application, Cloud Run request, or inspected
> Healthcare API audit log records associated with the test window.

## 11. Source → transformed → stored comparison (M5-AC7–AC10)

From `docs/m5_evidence/latest_result.json` (the canonical run):

| Attribute | Source | Transformed | Durable store |
|---|---|---|---|
| PatientName | present (`NEVER_PERSIST^KRIS`) | absent | absent |
| PatientID | `SECRET-123456789` | `DEMO` | `DEMO` |
| PatientBirthDate | present (`19610217`) | absent | absent |
| Private element | present | absent | absent |
| Pixel Data (SHA-256) | `fdeab9ac...` | `fdeab9ac...` (match) | `fdeab9ac...` (match) |

`policy_correctly_applied_end_to_end: true`, `pixel_hash_match: true`, `uids_match_receipt: true`,
`source_canary_in_stored_object: false`. "Transformed" was computed by calling the *same*
production `transform.process()` locally in the harness — not a second implementation, not
pydicom — so it's a true apples-to-apples middle column, not a recomputation with different logic.

## 12. Durable retrieval methodology (M5-AC6)

`m5.py`'s `retrieve_instance()` performs a WADO-RS GET against
`.../dicomWeb/studies/{study}/series/{series}/instances/{sop}` using **this validation process's
own `gcloud`-derived access token** (`gcloud auth print-access-token`) — never the gateway's
service account, and never a value the gateway itself reported. `Accept:
multipart/related; type="application/dicom"; transfer-syntax=*` retrieves the instance in its
stored transfer syntax (no transcoding). The single-part multipart response is parsed to raw DICOM
bytes and independently inspected with **pydicom** (`_structural_snapshot()`) — the task's explicit
allowance for an independent oracle in the validation harness; production code still uses only
fastDICOMstructure. A QIDO-RS search (`.../dicomWeb/instances`) after the run additionally confirms
via a third, independent path that exactly one instance exists in the store and its `PatientID` is
`DEMO`.

## 13. Duplicate submission behavior (§20)

Because the fixture's SOPInstanceUID is fixed, a first STOW-RS submission and any subsequent one
for the identical UID triple are distinguishable. Observed twice, independently: the Healthcare API
**rejects** a duplicate with `HTTP 409 Conflict` — it does not silently replace or merge the
existing instance. The gateway correctly reports this as `store_failed` (502), never as `stored`:

```text
2026-09-08T22:19:49Z ERROR ... dicom_store_failed input_bytes=764 reason=store_rejected status_code=409 ...
2026-09-08T22:21:17Z ERROR ... dicom_store_failed input_bytes=754 reason=store_rejected status_code=409 ...
```

(The first occurrence was incidental — a duplicate of an earlier manual conformance-check
submission made directly via `sink.store()` before deployment; the existing instance was deleted via
WADO-RS DELETE so the canonical evidence run in §11 reflects a clean first submission. The second
occurrence was deliberate: the same fixture was resubmitted once, specifically to characterize this
behavior, per the task's "perform at most the minimum experiment necessary.")

## 14. Failure semantics (M5-AC13)

A real bug was found and fixed here, before any "official" evidence was collected: the first
version of `sink.store()` only wrapped the HTTP call itself in `try/except requests.RequestException
→ PersistenceFailed`. Missing environment configuration and Application Default Credentials
resolution failures (both raised *before* any HTTP call) were not caught, so they escaped
`sink.store()` uncaught, past `app.py`'s `except sink.PersistenceFailed` handler entirely, and were
caught only by Starlette's own default exception middleware — a generic, uncontrolled 500 with its
own traceback logging outside this module's safe-logging contract. Reproduced locally (`docker run`
with no ADC available):

```text
google.auth.exceptions.DefaultCredentialsError: Your default credentials were not found.
... (uncaught, bare 500, Starlette's own traceback)
```

Fixed by wrapping configuration lookup, body construction, and the HTTP call in one `try` that
catches `ConfigurationError`, `requests.RequestException`, and `google.auth.exceptions.
GoogleAuthError` — all three now raise `PersistenceFailed` with a small, fixed `reason` string
(`configuration_error` / `request_error` / `credentials_unavailable` / `store_rejected`), and
`app.py` logs only that string plus the HTTP status code, never a response body or exception
message. Re-tested after the fix (same scenario): a clean, controlled `502 {"status":
"store_failed"}` with a safe log line (`reason=credentials_unavailable status_code=None`) — see
`tests/test_sink.py` for the regression coverage (9 of its 14 tests exist specifically to pin every
one of these failure paths, including one asserting a synthetic canary can never appear in the
exception's own string representation).

Transform success is never conflated with persistence success: `/dicom/store` only returns
`{"status": "stored", ...}` after `sink.store()` itself returns without raising — see
`tests/test_app.py::test_store_endpoint_never_claims_stored_on_failure`.

No automatic retries were added; one submission is one attempt, one outcome.

## 15. Cloud Run log findings (M5-AC11)

`docs/m5_evidence/latest_result.json`'s `source_canary_in_cloud_run_logs: false`,
`cloud_run_log_canary_hits: []` — the same log-query/classification machinery M4 built (`m4.py`,
reused unchanged) was run again for M5's test window, across both Cloud Run log streams
(container stdout/stderr and Cloud Run's own request log). No canary found in either.

## 16. Healthcare API audit log characterization (M5-AC12, §16)

Investigated before assuming anything: `gcloud projects get-iam-policy ... ` shows **no
`auditConfigs`** at the project level, meaning Healthcare API **Data Access** audit logging (which
would cover data-plane operations like `StoreInstances`/`RetrieveInstance`) is **not enabled** —
confirmed by inspection, and per the task's explicit instruction, not enabled for this milestone (no
broad project-wide Data Access logging was turned on merely to make this check possible).

Empirically confirmed rather than assumed: querying Cloud Logging for
`protoPayload.serviceName="healthcare.googleapis.com"` across this milestone's full working window
returns exactly four entries, all **Admin Activity** (always-on, no opt-in needed) —
`CreateDataset`, `CreateDicomStore`, `SetIamPolicy` — and **zero** entries for any `StoreInstances`
or `RetrieveInstance` call, including the ones this milestone's own evidence run performed. So:

```text
StoreInstances / RetrieveInstance activity: NOT OBSERVED / NOT ENABLED
Admin Activity (dataset/store/IAM changes): observed, characterized above
```

## 17. Negative control (M5-AC14)

A temporary, isolated DICOM store (`negctl-dicom`, same dataset) was created. A synthetic,
**deliberately untransformed** source-bearing object (the same fixture, unmodified — patient
canaries and private element all present) was stored directly into it using the validation
identity's own credentials, bypassing the gateway and `sink.py` entirely. It was then retrieved via
the same `retrieve_instance()`/`_structural_snapshot()` code path §11's real check uses, and that
checker correctly reported the violation:

```text
retrieved snapshot: patient_name_present=True, patient_id_is_source_value=True,
                     birth_date_present=True, private_element_present=True
CHECKER RESULT: FAIL (source canary detected)
```

`negctl-dicom` was deleted immediately after (`gcloud healthcare dicom-stores delete negctl-dicom
--quiet`) — confirmed gone; only `approved-dicom` remains in the dataset.

## 18. Cost accounting (M5-AC15)

| | |
|---|---|
| DICOM instances stored (canonical, retained) | 1 |
| Retained durable bytes | 674 |
| Source bytes submitted (canonical run) | 764 |
| Transformed bytes submitted to Healthcare API (canonical run) | 674 |
| STOW (store) requests, this milestone total | ~5 (1 pre-deployment conformance check, 1 accidental duplicate, 1 canonical success, 1 deliberate duplicate-behavior check, 1 negative-control direct store) |
| Retrieve (WADO-RS) requests | ~3 |
| Delete requests | 2 (removing the accidental-duplicate seed instance; deleting the negative-control store removes its one instance as part of store deletion) |
| Search (QIDO-RS) requests | 1 (post-hoc sanity check) |
| Healthcare API requests total (this milestone) | well under 20, far below the 500-request guardrail |
| Image builds/pushes | 2 (dependency addition, then the credential-handling fix) |
| Cloud Run validation request count (M5 + M4-regression re-run) | ~10 |

Reference pricing points (Google Cloud Healthcare API DICOM blob storage, confirmed via web
search): Nearline $0.02/GB-month, Coldline $0.01/GB-month, Archive $0.003/GB-month, **first 1 GB
free**. The exact Standard-class rate was not independently confirmed from the live pricing page
(fetch attempts returned a truncated page); it is not needed for this accounting, because at 674
retained bytes — roughly six hundred-thousandths of one percent of the free 1 GB tier, at any of
the confirmed per-GB rates — the storage cost is effectively zero regardless of which class applies.
API request volume (well under 20 calls) is likewise negligible against any published per-request
rate at this scale.

> At the measured M5 usage volume, incremental charges are expected to be zero or negligible under
> current pricing; actual billing depends on billing-account-wide free-tier consumption and other
> usage on this shared billing account, which this review did not and cannot fully characterize.

## 19. Limitations

- One dataset, one store, one region, one canonical retained instance — not a volume/scale
  characterization of the Healthcare API.
- Healthcare API Data Access audit logging is off; §16's "NOT OBSERVED / NOT ENABLED" reflects that
  configuration, not a claim that StoreInstances/RetrieveInstance activity is inherently unlogged by
  Google — only that this project's current logging configuration doesn't surface it.
- The IAM negative control (§6) was inconclusive within this session's time window due to IAM
  propagation delay; the static zero-project-bindings inspection is the primary evidence for that
  criterion.
- Everything M2/M3/M4 already scoped out remains scoped out here (concurrent load, deployment
  environment beyond what was queried, Google-internal telemetry, etc.).

## 20. Explicit non-claims

M5 does **not** establish: that Google infrastructure never buffers or internally replicates source
input; absence from internal telemetry not visible to this project; memory remanence; swap absence;
packet-capture absence; resistance to a malicious/privileged observer; complete DICOM
de-identification; PS3.15 compliance; HIPAA compliance; production security readiness; production
IAM completeness beyond what was actually configured and inspected here; production
availability/retry semantics (none were added); or concurrency/load behavior. It does not say "PHI
never enters GCP," "PHI never touches disk," "Google never persists PHI," "HIPAA compliant," or
"fully de-identified." This milestone demonstrates placement and observable behavior of one
approved-persistence boundary, using synthetic data, under one specific configuration.

## 21. Reproduction

```sh
export PROJECT=<redacted-gcp-project>
export REGION=us-central1
export IMAGE="${REGION}-docker.pkg.dev/${PROJECT}/fastdicom-gateway/fastdicom-gateway:m5"

docker build -f Dockerfile --build-context structure=../fastDICOMstructure -t "$IMAGE" .
docker push "$IMAGE"

gcloud run deploy fastdicom-gateway --project="$PROJECT" --region="$REGION" --image="$IMAGE" \
    --port=8080 --memory=512Mi --cpu=1 --min-instances=0 --max-instances=2 --timeout=60 \
    --service-account="fastdicom-gateway-m5@${PROJECT}.iam.gserviceaccount.com" \
    --set-env-vars="FASTDICOM_HEALTHCARE_PROJECT=${PROJECT},FASTDICOM_HEALTHCARE_LOCATION=${REGION},FASTDICOM_HEALTHCARE_DATASET=fastdicom-m5,FASTDICOM_HEALTHCARE_DICOM_STORE=approved-dicom" \
    --allow-unauthenticated

source .venv/bin/activate
python -m fastdicom_gateway.validation.m5 --project "$PROJECT" --region "$REGION" \
    --service fastdicom-gateway --dataset fastdicom-m5 --dicom-store approved-dicom \
    --out docs/m5_evidence/latest_result.json
```

Manual independent retrieval (what the harness's `retrieve_instance()` automates):

```sh
TOKEN=$(gcloud auth print-access-token)
curl -H "Authorization: Bearer ${TOKEN}" \
     -H 'Accept: multipart/related; type="application/dicom"; transfer-syntax=*' \
     "https://healthcare.googleapis.com/v1/projects/${PROJECT}/locations/${REGION}/datasets/fastdicom-m5/dicomStores/approved-dicom/dicomWeb/studies/1.2.826.0.1.3680043.8.498.10000000000001/series/1.2.826.0.1.3680043.8.498.10000000000002/instances/1.2.826.0.1.3680043.8.498.10000000000003"
```

## 22. Cleanup

Not deleted (retained — tiny, scale-to-zero, useful for continued demo/M6 work):

```text
Cloud Run service:            fastdicom-gateway
Artifact Registry repo/image: fastdicom-gateway (:m4, :m5)
Healthcare dataset:           fastdicom-m5
DICOM store:                  approved-dicom (1 retained instance, 674 bytes)
Service account:              fastdicom-gateway-m5@...
```

Deleted during this milestone (temporary/negative-control only):

```text
DICOM store: negctl-dicom (and its one instance) -- gcloud healthcare dicom-stores delete
IAM binding: iam.serviceAccountTokenCreator on fastdicom-gateway-m5 for the validation identity
             (added for the IAM impersonation check, removed immediately after)
```

To remove everything from this milestone:

```sh
gcloud healthcare dicom-stores delete approved-dicom --dataset=fastdicom-m5 --location="$REGION" --project="$PROJECT" --quiet
gcloud healthcare datasets delete fastdicom-m5 --location="$REGION" --project="$PROJECT" --quiet
gcloud iam service-accounts delete "fastdicom-gateway-m5@${PROJECT}.iam.gserviceaccount.com" --project="$PROJECT" --quiet
```

## 23. Conclusion

Under the defined M5 Cloud Run and Healthcare API configuration and exercised synthetic test
scenario, `fastDICOMgateway` applied its demonstrated policy before intentional durable persistence,
and independent retrieval from the Healthcare API DICOM Store showed the approved transformed DICOM
representation rather than the selected synthetic source identifiers.
