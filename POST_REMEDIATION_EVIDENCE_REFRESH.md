# Post-Remediation Evidence Refresh

> **Public-release redaction note (2026-09-17):** the specific GCP project identifier, the
> deployed Cloud Run service URL, and local development-machine absolute paths have been replaced
> with `<redacted-...>`/`/path/to/...` placeholders below and in the referenced
> `docs/m2_evidence/latest_result.json` / `docs/publication_refresh/m2_refresh_result.json`. This
> is a value-level substitution only — the disclosed dirty-working-tree limitation this document
> already records, and every reported command, timestamp, count, and conclusion, are unchanged.

## A. Purpose

This is a confirmatory bridge, not a new milestone. The frozen M1–M5 evidence tested the
pre-remediation gateway; `ADVERSARIAL_REMEDIATION_A.md` then corrected two specific behaviors
(recursive PatientName/PatientID/PatientBirthDate matching; fail-closed rejection of non-Explicit-VR
input). That correction left two of M2/M3's own end-to-end tests failing — not because anything
regressed, but because those tests assert historical Scenario D's expectation (`HTTP 500`) against a
gateway that now intentionally does something different (`HTTP 400`) for a documented reason.

The purpose here is narrow: (1) confirm the same four-scenario boundary behavior still holds at M2
and M3 once Scenario D's *expectation* is updated to match the corrected gateway's intentional
behavior, (2) confirm the corrected recursive policy survives the real M5 durable-storage path, not
just local unit tests, and (3) make the ordinary CI-tested suite green again by testing current
behavior with current expectations — without touching, reinterpreting in place, or falsifying any
frozen M1–M5 artifact. It is explicitly **not** M6, not a redesign, not a new research question, and
not a general validation expansion — no additional scenarios were added, no additional milestones
were exercised, and M4 was not force-fit into scope (see section F).

## B. Historical/current distinction

| | Historical Scenario D (`scenarios.run_scenario_d`, unchanged) | Corrected Scenario D (`scenarios_publication_refresh.run_scenario_d_corrected`, new) |
|---|---|---|
| Fixture | `fixtures.build_unmodified_implicit_vr_fixture` (unchanged, reused as-is) | Same fixture, same function, unchanged |
| Mechanism exercised | `write_bytes()` on an unmodified Implicit-VR structure → `FDS_STATUS_UNSUPPORTED` → uncaught exception → `app.py`'s generic `except Exception` → 500 | `_parse_or_reject`'s new acceptance-scope gate (`not structure.is_explicit_vr`) → `RejectedInput(reason="unsupported_transfer_syntax")` → 400, **before** any policy mutation or serialization is attempted |
| Expected HTTP status | `500` | `400` |
| Also checks `/dicom/store` | No | Yes — same rejection, before `sink.store()` is ever reachable |
| Still true today? | Only for `fastDICOMstructure`'s own `write_bytes()` behavior in isolation (still pinned by `test_implicit_vr_fixture_has_no_targeted_tags_and_is_unsupported_to_write`, which calls it directly, bypassing the gateway) | Yes — this is what the live gateway now does |
| Where it lives | `scenarios.py` (frozen, untouched); `docs/M2_PERSISTENCE_BOUNDARY_VALIDATION.md` / `docs/M3_CONTAINER_VALIDATION.md` / `docs/M4_CLOUD_RUN_VALIDATION.md` (frozen, untouched) | `scenarios_publication_refresh.py` (new file) |

Both are true statements — about different points in time, for different code. Historical Scenario D
is not wrong; it is a correct record of the pre-remediation implementation. Corrected Scenario D is
not a rewrite of that record; it is a new, separately-named check of the post-remediation
implementation, run through the same observation apparatus.

## C. Repository state

**Before any change in this task:**

| | |
|---|---|
| `fastDICOMgateway` HEAD | `2467a0f54b9445e4024d94145b7d3277ceca07ff` (unchanged — no commit exists yet for the M1-M5 remediation itself) |
| `fastDICOMstructure` HEAD | `ecba5926e23e4da10d1642c47d1d8df13dc5e4b0` (unchanged) |
| `fastDICOMgateway` dirty state | `M README.md`, `M docs/PUBLICATION_BRIEF.md`, `M src/fastdicom_gateway/transform.py`, plus untracked `ADVERSARIAL_CLAIM_CLOSURE.md`, `ADVERSARIAL_REMEDIATION_A.md`, `ADVERSARIAL_VALIDATION_A.md`, `publication_validation/`, `tests/test_recursive_policy_and_vr_scope.py` — all from the two prior tasks, none touched or re-touched by this one before its own changes began |
| `fastDICOMstructure` dirty state | 6 modified files, all from the prior remediation task (see `ADVERSARIAL_REMEDIATION_A.md` section 7); unchanged by this task except where explicitly noted below |
| Test result before this task | `2 failed, 72 passed, 1 skipped` |

**Failure characterization, confirmed precisely before any change was made** (per instructions,
this was checked first and matched exactly, so the task proceeded):

```
A successful_transform PASS http=200 failing_checks={}
B malformed_input       PASS http=400 failing_checks={}
C parser_rejected_...   PASS http=400 failing_checks={}
D internal_write_...    FAIL http=400 failing_checks={'http_status_500': False}
```

Both `test_m2_end_to_end_run_passes` and `test_m3_end_to_end_run_passes_read_only` failed **solely**
because historical Scenario D's `http_status_500` check no longer matches — the corrected gateway
returns `400` — with no other check, in any scenario, affected. This matched the task's stated
characterization exactly; the task proceeded rather than stopping.

**Important caveat about the commit fields recorded in every new artifact below:** none of this
task's changes (nor the prior remediation task's) have been committed. `git rev-parse HEAD` is still
`2467a0f` / `ecba592` throughout, so every `gateway_commit`/`structure_commit` field recorded in the
new evidence JSON below reflects that pre-remediation commit hash — it does **not** mean the
corrected code was somehow present at that commit. The corrected code exists only in the current
uncommitted working tree, which is what was actually exercised. This is expected, given the explicit
instruction not to commit, and is noted here so the commit fields in the JSON artifacts are not
misread later.

## D. M2 refresh

**Apparatus:** `src/fastdicom_gateway/validation/m2.py`'s `run()` — unchanged in every respect
(subprocess launch, `strace -f` observation, log/filesystem canary scanning) except one additive
parameter, `scenario_fns` (defaults to `sc.ALL_SCENARIOS`, so every existing call site — including
`docs/M2_PERSISTENCE_BOUNDARY_VALIDATION.md`'s own reproduction command — is unaffected). Invoked
here with `scenario_fns=scenarios_publication_refresh.CORRECTED_SCENARIOS` (Scenarios A/B/C from
`scenarios.py` unchanged; Scenario D redefined in the new `scenarios_publication_refresh.py`).

**Command:** `python -m fastdicom_gateway.validation.publication_refresh m2`

**Result** (`docs/publication_refresh/m2_refresh_result.json`):

| Scenario | Result | HTTP | Notes |
|---|---|---|---|
| A successful_transform | PASS | 200 | all checks pass, unchanged from historical M2 |
| B malformed_input | PASS | 400 | all checks pass, unchanged from historical M2 |
| C parser_rejected_truncated_pixel_data | PASS | 400 | all checks pass, unchanged from historical M2 |
| D acceptance_boundary_rejection (corrected) | **PASS** | **400** | `reason=unsupported_transfer_syntax` confirmed on both `/dicom` and `/dicom/store`; no traceback; no canary in either response |

`overall_result: "PASS"`. `filesystem_scan_hits: []` (zero request-time write-capable filesystem
operations across all four scenarios — same M2 persistence-boundary observation A/B/C already
provided, now also true for the corrected D).

**Evidence location:** `docs/publication_refresh/m2_refresh_result.json` — new location, does not
touch `docs/m2_evidence/latest_result.json` or `docs/M2_PERSISTENCE_BOUNDARY_VALIDATION.md` (both
unchanged, `git diff` empty for both).

## E. M3 refresh

**Apparatus:** `src/fastdicom_gateway/validation/m3.py`'s `run()` — identical treatment: one
additive `scenario_fns` parameter, defaulting to `sc.ALL_SCENARIOS`; `docker diff`-based observation
of the read-only, non-root container is otherwise untouched.

**Command:** `python -m fastdicom_gateway.validation.publication_refresh m3` (built a fresh image,
`fastdicom-gateway:publication-refresh`, from the current working tree — including the corrected
`transform.py` and the rebuilt `fastDICOMstructure` ABI — via the existing `Dockerfile`/build-context
mechanism, unchanged)

**Result** (`docs/publication_refresh/m3_refresh_result.json`):

| Scenario | Result | HTTP | Notes |
|---|---|---|---|
| A successful_transform | PASS | 200 | all checks pass |
| B malformed_input | PASS | 400 | all checks pass |
| C parser_rejected_truncated_pixel_data | PASS | 400 | all checks pass |
| D acceptance_boundary_rejection (corrected) | **PASS** | **400** | same corrected-rejection checks as M2, now confirmed inside the read-only, non-root container |

`overall_result: "PASS"`. The container's read-only-root/zero-writable-mounts posture (the M3 threat
model itself) was not touched or redesigned — only the scenario set run against it changed.

**Evidence location:** `docs/publication_refresh/m3_refresh_result.json` — new location;
`docs/m3_evidence/latest_result.json` and `docs/M3_CONTAINER_VALIDATION.md` unchanged.

## F. M4 refresh — not executed; explanation

**Stopped, per the task's own explicit provision for this outcome.** `gcloud`/`docker` tooling and
credentials are present in this environment, and the original Cloud Run service (`fastdicom-gateway`,
project `<redacted-gcp-project>`) and its Healthcare API resources still exist — confirmed:

```
gcloud run services describe fastdicom-gateway ... →
  url: https://<redacted-cloud-run-url>
  latestReadyRevisionName: fastdicom-gateway-00003-lg8   (the pre-remediation M4/M5 revision)
```

However, confirming the corrected implementation's M4 behavior would require **actually deploying
the fix** — the currently-deployed revision still runs the pre-remediation code (no image containing
this task's or the prior task's changes has ever been pushed or deployed). That means, at minimum:
building a new container image (embedding the corrected `fastDICOMstructure` ABI and gateway code),
pushing it to Artifact Registry, and deploying a new Cloud Run revision to the live service — a real,
externally-visible, cost-bearing (though small) mutation of shared cloud infrastructure, not a
read-only observation.

This task treats that category of action — modifying a live, deployed, shared cloud service — as
requiring an explicit go-ahead rather than silent execution, consistent with the task's own
instruction: "Do not create a new infrastructure project merely to satisfy this assignment without
first reporting what would be required." Redeploying the *existing* service is a smaller ask than
that, but is still a live deployment change, so this task stopped at the same boundary and reports
what would be required instead of performing it:

1. Build a corrected image: `docker build -f Dockerfile --build-context structure=../fastDICOMstructure -t <image> .` (same command M4/M5's own reproduction steps already document).
2. Push it to the existing Artifact Registry repo.
3. `gcloud run deploy fastdicom-gateway --image=<image> ...` (same flags M5's doc already documents, reusing the existing service/service-account).
4. Re-run `m4.run(scenario_fns=scenarios_publication_refresh.CORRECTED_SCENARIOS)` against the new revision (the same additive-parameter treatment would need to be added to `m4.py`, mirroring the one already added to `m2.py`/`m3.py` — not yet done, since it depends on step 3 happening at all).
5. Confirm Scenario D returns 400 and no canary appears in the same two Cloud Run log streams M4
   already checks.

None of this was performed. No cloud resource was created, modified, or deleted for M4 specifically.
The M4 *architectural* qualification from the original evidence — Cloud Run is not a monotonically
stronger filesystem boundary than the read-only M3 container, because Cloud Run's writable ephemeral
`/tmp` exists regardless of what the container image declares — is preserved and not contradicted by
anything in this refresh; it simply was not re-exercised against the corrected code.

## G. M5 positive refresh

**Fixture:** `publication_refresh.build_m5_refresh_fixture()` — Explicit VR Little Endian, same shape
as the original M5 canonical fixture (fixed UIDs, minimal Image Pixel module, one private element),
**plus** a nested `PatientID (0010,0020)` inside `Other Patient IDs Sequence (0010,1002)`'s one item —
the exact construct `ADVERSARIAL_VALIDATION_A.md`'s Validation 1 found unprotected before
remediation. Top-level `PatientName`/`PatientID`/`PatientBirthDate` reuse the established
`fixtures.PATIENT_NAME`/`PATIENT_ID`/`PATIENT_BIRTH_DATE` values (needed because `m5.py`'s own
`_structural_snapshot()` — reused unchanged — checks presence against those exact constants; a
different top-level value would have silently made those specific checks meaningless). The nested
occurrence uses its own distinct, unmistakably-synthetic value,
`REFRESH_NESTED_ID_DO_NOT_PERSIST`. UIDs (`...30000000000001/2/3`) are new and distinct from both
the original M5 canonical instance (`...10000000000001/2/3`) and every `ADVERSARIAL_VALIDATION_A.md`
fixture.

**Execution mode — stated precisely:** the corrected `transform.process()` → `sink.store()` path was
exercised **in-process**, via FastAPI's `TestClient` calling the actual, current `app.py`, **not**
through the deployed Cloud Run revision (which — see section F — still runs the pre-remediation
code, so hitting it would not have tested the fix at all). `sink.store()`'s outbound STOW-RS call was
real, made from this environment directly to the real Healthcare API. This means: the
transform/store/retrieve *mechanics* and the corrected *policy* were genuinely, fully exercised
against real durable storage; the Cloud-Run-*specific* ingress boundary was not re-exercised (that
remains M4's territory, deferred per section F).

**Command:** `python -m fastdicom_gateway.validation.publication_refresh m5`
(`--project <redacted-gcp-project> --region us-central1 --dataset fastdicom-m5
--dicom-store approved-dicom` — the same dataset/store the original M5 evidence used; the original
canonical instance was not read, modified, or deleted).

**STOW-RS result:** `store_http_status: 200`, receipt `study=...30000000000001,
series=...30000000000002, sop=...30000000000003`. Gateway log line confirms the corrected recursive
policy actually fired: `elements_touched=4` (one more than the original M5's `elements_touched`
would have been for this shape, because the nested PatientID replacement is now counted too),
`private_elements_removed=1`.

**WADO-RS independent retrieval + verification** (using `m5.py`'s own
`retrieve_instance()`/`_structural_snapshot()`, unchanged, plus one additional direct `pydicom`
inspection of the nested structure specifically):

| Attribute | Source | Transformed | Retrieved (independent WADO-RS) |
|---|---|---|---|
| PatientName (top-level) | present | absent | **absent** |
| PatientID (top-level) | not `DEMO` | `DEMO` | **`DEMO`** |
| PatientBirthDate (top-level) | present | absent | **absent** |
| Private element | present | absent | **absent** |
| Pixel Data (SHA-256) | `fdeab9ac…` | `fdeab9ac…` (match) | **`fdeab9ac…` (match)** |
| PatientID (nested, `0010,1002` item) | source canary | `DEMO` | **`DEMO`** |

`pixel_hash_match: true`, `top_level_policy_correctly_applied_end_to_end: true`,
`nested_policy_correctly_applied_end_to_end: true`,
`nested_source_canary_absent_after_independent_retrieval: true`,
`source_canary_in_stored_object: false` (covering both top-level and nested checks),
`retrieved_object_parses_with_pydicom: true`. `overall_result: "PASS"`.

**One diagnostic note, disclosed rather than hidden:** the first attempt at this refresh failed
(`overall_result: "FAIL"`) — not because of the gateway, but because of a bug in this refresh
script's first draft, which used new, non-standard top-level canary values that `m5.py`'s
`_structural_snapshot()` doesn't know to check for (it has the original M5 constants hardcoded).
That failed run's diagnostic JSON was deleted (not preserved as evidence, since it recorded a
harness bug, not gateway behavior) and the one stray Healthcare API instance it created (same UID
triple used above) was deleted via WADO-RS `DELETE` before the corrected re-run, which reused the
same UIDs and produced the result reported above. A follow-up manual QIDO-RS listing (not automated,
not part of the JSON evidence, run once purely as a repository-hygiene check) confirmed the store
now contains exactly two instances — the original M5 canonical one and this refresh's one — both
with `PatientID=DEMO`, with no stray third instance left over from the failed attempt.

**Evidence location:** `docs/publication_refresh/m5_refresh_result.json` — new location;
`docs/m5_evidence/latest_result.json` and `docs/M5_APPROVED_PERSISTENCE_VALIDATION.md` unchanged. No
QIDO-RS store-wide enumeration was automated into this evidence (per instructions); the one manual
listing above is disclosed as exactly that — a manual sanity check, not a claim of store-wide
absence.

**Identity-separation caveat (stated once, precisely, per instructions):** unlike the original M5
setup — which used a dedicated `fastdicom-gateway-m5@...` service account for the STOW-RS write and
a separate human `gcloud` identity for the WADO-RS read — this refresh's STOW-RS submission and its
WADO-RS retrieval both used this session's own `gcloud` ADC identity (a project-owner human account),
because the write happened in-process rather than through the deployed, dedicated-service-account
Cloud Run revision. The identity-separation property M5 originally established is therefore **not**
re-confirmed by this refresh — only the transform/store/retrieve mechanics and the corrected
recursive policy are.

## H. Test-suite result

**Reconciliation performed:** `tests/test_m2_validation.py::test_m2_end_to_end_run_passes` and
`tests/test_m3_validation.py::test_m3_end_to_end_run_passes_read_only` were renamed to
`..._with_corrected_scenario_d` and updated to call `m2.run(scenario_fns=spr.CORRECTED_SCENARIOS)` /
`m3.run(..., scenario_fns=spr.CORRECTED_SCENARIOS)`, with docstrings explaining exactly why (current
CI tests current behavior; the frozen milestone evidence and historical Scenario D are untouched and
still accurate for the implementation they describe). No other test in either file was touched;
`test_implicit_vr_fixture_has_no_targeted_tags_and_is_unsupported_to_write` — which pins the
still-true, still-unchanged `fastDICOMstructure`-level fact that `write_bytes()` returns
`Unsupported` for an unmodified Implicit-VR structure — was left exactly as it was, since it remains
correct and does not go through the gateway's new acceptance gate at all.

**Full suite result after reconciliation** (`python -m pytest -v`, the same invocation
`.github/workflows/ci.yml` uses), run twice to confirm stability:

```
74 passed, 1 skipped, 2 warnings
```

- **74 passed**: every pre-existing test that was passing before this task, plus the two renamed
  end-to-end tests (now passing against the corrected scenario set).
- **1 skipped**: `test_m4_end_to_end_run_passes_against_live_service` — requires live infrastructure
  this task deliberately did not modify; unrelated, unchanged, pre-existing skip.
- **0 failed.**

## I. Claim impact

**What this refresh establishes:**
- The corrected gateway's M2 (bare host) and M3 (read-only container) boundary behavior for
  Scenarios A, B, and C is unchanged from the frozen evidence — same PASS outcomes, same absence of
  observed source persistence.
- The corrected gateway's Scenario D behavior at M2 and M3 is now intentional, fail-closed
  acceptance-boundary rejection (400), not an internal failure (500) — confirmed live, through the
  same strace/`docker diff` observation surfaces the original M2/M3 evidence used, including
  confirmation this holds for `/dicom/store` too (so `sink.store()` is structurally unreachable for
  this input).
- The corrected recursive policy — specifically the nested-`PatientID`-inside-a-sequence case
  `ADVERSARIAL_VALIDATION_A.md` found unprotected — survives the complete, real
  transform → STOW-RS → durable storage → WADO-RS → independent-pydicom-inspection path, not just
  local unit tests.
- No remediation-introduced evidence of source persistence appeared anywhere in this refresh: zero
  filesystem writes (M2/M3), no source canary in any response or log surface checked (M2/M3), and no
  source canary — top-level or nested — in the durably retrieved M5 object.

**What this refresh does NOT establish:**
- It does not re-confirm the Cloud-Run-specific ingress-boundary claim against the corrected code
  (M4 was deferred; see section F). The original M4 evidence remains the only evidence for that
  boundary, and it describes the pre-remediation implementation.
- It does not re-confirm M5's original identity-separation property (dedicated service account vs.
  human identity) — this refresh used one identity for both write and read (see section G).
- It does not establish store-wide absence of source content in `approved-dicom` beyond the one
  object each retrieval targeted (consistent with how the original M5 evidence was already scoped,
  per `ADVERSARIAL_CLAIM_CLOSURE.md` Question B).
- It does not re-test or extend coverage beyond the two specific gaps `ADVERSARIAL_REMEDIATION_A.md`
  fixed — no new scenarios, no broader de-identification claim, no dictionary-backed Implicit VR
  support.

## J. Publication implications

Facts the final article may now safely state (no article prose was drafted):

- The fixed policy is recursive over structurally visible elements — confirmed locally
  (`ADVERSARIAL_VALIDATION_A.md`, `tests/test_recursive_policy_and_vr_scope.py`) **and** now through
  the real durable-storage path (section G above).
- Explicit VR Little Endian is the demonstrated acceptance scope.
- Unsupported representations (Implicit VR, in this demonstration) fail closed before persistence is
  attempted — confirmed at the unit level, at the M2 bare-host level, and at the M3 read-only
  container level, including confirmation that `/dicom/store` never reaches `sink.store()` for such
  input.
- The Implicit VR exclusion is an experimental scope control for this demonstration, not an
  architectural requirement of the transform-before-persist boundary itself.
- Dictionary-backed Implicit-VR interpretation remains feasible but was not implemented or validated
  as part of this demonstration (only pydicom's own dictionary, used purely as an independent
  verification oracle, demonstrated the feasibility — see `ADVERSARIAL_VALIDATION_A.md` section 6).
- The corrected recursive behavior was confirmed through durable storage and independent retrieval —
  **true**, per the M5 refresh in section G: the nested PatientID was independently retrieved via
  WADO-RS and shown to be `DEMO`, not the source canary.

## K. Stop/go recommendation

**READY_FOR_PUBLICATION_EDIT**

The corrected behavior is confirmed across the two boundaries this refresh actually exercised (M2,
M3) plus the positive durable-storage path (M5), with zero evidence of remediation-introduced source
persistence anywhere. M4 was deliberately not re-exercised because doing so would require deploying
new code to a live cloud service — a decision this task treats as needing separate, explicit
authorization rather than folding into an evidence refresh, not as a sign of unresolved risk. Per the
default stopping rule given in the task: the corrected behavior is confirmed across the affected
boundaries and the positive durable-storage path, so engineering validation is complete for this
publication. The article may state the M4/Cloud-Run-specific claim only as it stood in the original,
unmodified M4 evidence (i.e., describing the pre-remediation implementation, exactly as that
evidence already does) unless and until an M4 redeploy is separately authorized and performed —
this is a scoping note for accurate article wording, not a reason to withhold publication or to
default to `ADDITIONAL_BOUNDED_VALIDATION_REQUIRED`. Nothing in this refresh reopens the
architectural transform-before-persist claim.

---

## Repository safety

**Every file created (this task):**
- `POST_REMEDIATION_EVIDENCE_REFRESH.md` (this file)
- `src/fastdicom_gateway/validation/scenarios_publication_refresh.py`
- `src/fastdicom_gateway/validation/publication_refresh.py`
- `docs/publication_refresh/m2_refresh_result.json`
- `docs/publication_refresh/m3_refresh_result.json`
- `docs/publication_refresh/m5_refresh_result.json`

**Every file modified (this task):**
- `src/fastdicom_gateway/validation/m2.py` — added one optional `scenario_fns` parameter
  (default preserves prior behavior exactly)
- `src/fastdicom_gateway/validation/m3.py` — same additive parameter
- `tests/test_m2_validation.py` — renamed/updated one test (`test_m2_end_to_end_run_passes` →
  `test_m2_end_to_end_run_passes_with_corrected_scenario_d`), added one import
- `tests/test_m3_validation.py` — same treatment for its M3 counterpart

**Not modified by this task** (modified by the *prior* `ADVERSARIAL_REMEDIATION_A` task instead, and
already dirty before this task began — listed here only to avoid misattribution): `README.md`,
`docs/PUBLICATION_BRIEF.md` (pre-existing, unrelated to any adversarial-review task),
`src/fastdicom_gateway/transform.py`, and all six previously-modified `fastDICOMstructure` files.

**Whether any production source changed during THIS task:** Yes, narrowly —
`src/fastdicom_gateway/validation/m2.py` and `m3.py` (both part of the validation harness, not the
request-serving production path `app.py`/`transform.py`/`sink.py`, which were not touched by this
task). Both changes are additive (one new optional parameter each, default-preserving).

**Whether `fastDICOMstructure` changed during THIS task:** No. Its working tree is byte-for-byte
identical to how the prior task left it (`git diff --stat` identical to the pre-task baseline in
section C); no file in it was opened for editing in this task, and no rebuild was needed since no
source changed.

**Whether any frozen evidence artifact changed:** No.
`git diff --stat -- docs/m2_evidence docs/m3_evidence docs/m4_evidence docs/m5_evidence
docs/M2_PERSISTENCE_BOUNDARY_VALIDATION.md docs/M3_CONTAINER_VALIDATION.md
docs/M4_CLOUD_RUN_VALIDATION.md docs/M5_APPROVED_PERSISTENCE_VALIDATION.md` produced no output —
confirmed empty, both before and after this task's changes.

**Whether any cloud resource was created, modified, or deleted:** Yes, narrowly, and disclosed in
full in section G: one new DICOM instance was stored into the existing `approved-dicom` Healthcare
API DICOM store (SOPInstanceUID `...30000000000003`) and independently retrieved; one earlier,
diagnostic-only attempt at the same instance (from a script bug, not a gateway behavior) was stored
and then deleted before the successful re-run. No dataset, store, service account, IAM binding,
Cloud Run service/revision, or Artifact Registry image was created, modified, or deleted. The
original M5 canonical instance was untouched.

**`git status --short`** (fastDICOMgateway, after this task):
```
 M README.md
 M docs/PUBLICATION_BRIEF.md
 M src/fastdicom_gateway/transform.py
 M src/fastdicom_gateway/validation/m2.py
 M src/fastdicom_gateway/validation/m3.py
 M tests/test_m2_validation.py
 M tests/test_m3_validation.py
?? ADVERSARIAL_CLAIM_CLOSURE.md
?? ADVERSARIAL_REMEDIATION_A.md
?? ADVERSARIAL_VALIDATION_A.md
?? POST_REMEDIATION_EVIDENCE_REFRESH.md
?? docs/publication_refresh/
?? publication_validation/
?? src/fastdicom_gateway/validation/publication_refresh.py
?? src/fastdicom_gateway/validation/scenarios_publication_refresh.py
?? tests/test_recursive_policy_and_vr_scope.py
```

**`git diff --stat`** (fastDICOMgateway, tracked files only):
```
 README.md                              | 41 ++++++++++++++++------
 docs/PUBLICATION_BRIEF.md              |  9 ++---
 src/fastdicom_gateway/transform.py     | 62 ++++++++++++++++++++++++++++------
 src/fastdicom_gateway/validation/m2.py | 20 +++++++++--
 src/fastdicom_gateway/validation/m3.py |  8 ++++-
 tests/test_m2_validation.py            | 26 ++++++++++++--
 tests/test_m3_validation.py            | 14 ++++++--
 7 files changed, 148 insertions(+), 32 deletions(-)
```
(The first three lines — `README.md`, `docs/PUBLICATION_BRIEF.md`, `transform.py` — are entirely the
prior task's and pre-existing changes, unaffected by this task; the remaining four lines are this
task's own.)

**fastDICOMstructure `git status --short`** (unchanged from before this task):
```
 M abi/include/fastdicomstructure_c/fds.h
 M abi/src/fds_abi.cpp
 M include/fastdicomstructure/dicom_structure.hpp
 M python/fastdicomstructure/__init__.py
 M src/dicom_structure.cpp
 M tests/python/test_mutation.py
```
**fastDICOMstructure `git diff --stat`** (unchanged from before this task): `234 insertions(+), 2
deletions(-)` across the same six files as the prior task — identical to the baseline recorded in
section C.

**HEADs (both repositories):** `fastDICOMgateway` still `2467a0f54b9445e4024d94145b7d3277ceca07ff`;
`fastDICOMstructure` still `ecba5926e23e4da10d1642c47d1d8df13dc5e4b0`.

**Whether anything was committed:** No.

**Whether anything was pushed:** No.

---

POST_REMEDIATION_EVIDENCE_REFRESH_COMPLETE
