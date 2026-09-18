# Adversarial Claim Closure — fastDICOMgateway Publication Candidate

Scope: fact-finding only. No code, test, documentation, or article changes were made while
producing this report. Primary sources: `fastDICOMgateway` (this repo, `HEAD` = `2467a0f`, prior
milestone commits `d597272`…`bcde8c3`), `fastDICOMstructure` (sibling repo, referenced at the
commit M5's doc pins: `4eb44cb`), the frozen `docs/m{2,3,4,5}_evidence/latest_result.json`
artifacts, the test suites of both repos, and
`pysynapse_article_publication_candidate_v2.md` (the current article candidate).

---

## A. Implicit VR / policy visibility

**FACTS OBSERVED**

Two distinct, independently real gaps, both confirmed by reading the parser and the gateway's
policy code directly (not inferred):

1. **The three named patient tags are erased/replaced top-level only, regardless of transfer
   syntax.** `Structure.erase(tag)` / `Structure.set_value(tag, value)` / `Structure.get(tag)` are
   explicitly documented, in their own docstrings, as operating on **top-level** elements only.
   `erase_private()` is the only mutation documented as recursing "at any nesting depth." The
   gateway's fixed policy (`_apply_demo_policy` in `transform.py`) calls `structure.erase(tag)` /
   `structure.set_value(tag, ...)` for PatientName/PatientID/PatientBirthDate, and
   `structure.erase_private()` for private elements. So: a private element nested inside any
   sequence, at any depth, **is** removed; a PatientName/PatientID/PatientBirthDate nested inside a
   sequence item (e.g. a standards-conformant `Other Patient IDs Sequence (0010,1002)` item
   carrying its own `(0010,0020) PatientID`) is **not** removed — `erase`/`set_value` would not find
   it, because no top-level `Element` exists at that tag. This applies identically to Explicit and
   Implicit VR input; it is not an Implicit-VR-specific bug.
2. **Implicit VR Little Endian additionally has a structural blind spot the library documents by
   name.** A *defined-length* element under Implicit VR cannot be distinguished from a nested
   sequence without a data dictionary, so it is parsed as one opaque `VR::Unknown` `Value` —
   `is_sequence()` is `false`, and its raw bytes (including whatever nested tag/value encoding is
   inside) are never expanded into inspectable `Element`s. Critically, **this parses with zero
   diagnostics of any severity** — confirmed by reading `implicit_vr_le_parser.cpp`'s `parse_one()`:
   the defined-length branch calls `Value::from_source(...)` and returns `Outcome::Ok` with no
   `diagnostics.push_back(...)` call anywhere on that path. A defined-length nested sequence
   containing a patient tag or a private tag would therefore (a) parse successfully with no blocking
   diagnostic, so `transform.py`'s `_parse_or_reject` would not reject it, and (b) be invisible to
   *every* mutation in `_apply_demo_policy`, including the nesting-aware `erase_private()`, because
   there is no `Sequence`/`Item` structure to recurse into — just one opaque blob under the outer
   tag.

Whether this reaches an attacker-visible output depends on `is_modified()`: if nothing else in the
object gets mutated, `write()` on an **unmodified** Implicit-VR structure returns `Unsupported`
(fails closed, 500, nothing returned/stored — this is the *only* Implicit VR path the project
actually tests). If anything *else* in the same object is mutated (e.g. a top-level private element
elsewhere, or a top-level patient tag elsewhere), `is_modified()` becomes true, `write()` succeeds
and always emits Explicit VR output — carrying the untouched opaque blob straight through. **No
fixture, unit test, or milestone scenario in this repository constructs or exercises that combined
case** (defined-length nested sequence carrying a targeted tag, *plus* a top-level mutation trigger
elsewhere in the same object). The repository's only Implicit VR fixture
(`build_unmodified_implicit_vr_fixture`, scenario D) deliberately has *no* targeted tags anywhere
and is *unmodified*, specifically to exercise the `Unsupported`/500 path — not the leak path.

**EXACT EVIDENCE**

- `fastDICOMstructure/include/fastdicomstructure/dicom_structure.hpp:53-84` — `set_value`, `set`,
  `erase` docstrings say "top-level"; `erase_private_elements()` says "at any nesting depth."
- `fastDICOMstructure/python/fastdicomstructure/__init__.py:531-599` — Python docstrings restate
  the same top-level-vs-any-depth split for `set_value`/`set`/`erase`/`erase_private`.
- `fastDICOMstructure/include/fastdicomstructure/element_path.hpp:19-40` — `ElementPath(Tag)`
  constructs a single top-level step; multi-step paths require explicit `push(tag, item_index)`
  calls the gateway never makes.
- `fastDICOMgateway/src/fastdicom_gateway/transform.py:138-158` (`_apply_demo_policy`) — calls
  `structure.erase(_TAG_PATIENT_NAME)`, `structure.set_value(_TAG_PATIENT_ID, ...)`,
  `structure.erase(_TAG_PATIENT_BIRTH_DATE)`, `structure.erase_private()` — no `ElementPath`
  construction, no recursive walk.
- `fastDICOMstructure/docs/roundtrip-contract.md`, "Implicit VR Little Endian" section — "a
  *defined-length* nested sequence cannot be told apart from a large opaque value without a
  dictionary, so it is parsed as the latter... Proven by
  `tests/integration/test_parse_implicit_vr_le.cpp`'s 'defined-length nested sequence is NOT
  expanded' test."
- `fastDICOMstructure/src/parser/implicit_vr_le_parser.cpp:222-252` (`parse_one`, defined-length
  branch) — no diagnostic emitted for this case; `tests/integration/test_parse_implicit_vr_le.cpp:75-97`
  asserts `REQUIRE(result.status != ParseStatus::Failed)` with no diagnostic assertion at all.
- `fastDICOMgateway/src/fastdicom_gateway/validation/fixtures.py:132-145`
  (`build_unmodified_implicit_vr_fixture`) — comment: "none of the three targeted patient tags and
  no private elements, so the fixed policy never mutates it" (i.e. deliberately avoids the
  leak-relevant case).
- `fastDICOMgateway/README.md:251-258` ("Known limitations") — documents only the
  unmodified-Implicit-VR-500 case and the fixed/non-configurable policy scope; does not mention
  top-level-only tag matching or the zero-diagnostic opaque-sequence case.

**CURRENT ARTICLE CLAIM**

The article never mentions Implicit VR or nesting at all. The closest claims are §5's policy table
row "Private elements | remove" (no depth qualifier) and §8's "the policy boundary is *selective*:
it removes exactly what policy says to remove, and preserves the rest" (`pysynapse_article_publication_candidate_v2.md:175`,
via the earlier per-line read). §5's own qualifying paragraph already concedes the policy is "nothing
close to a full de-identification profile" and that "real de-identification has to reckon with a
much larger identifier surface" — but that paragraph is about attribute *coverage* (PS3.15's dozens
of attributes), not about the *named* tags failing to fire when nested, which is a narrower and more
surprising gap than "we didn't cover every PS3.15 attribute."

**VERDICT:** SUPPORTED BUT IMPRECISE — the underlying facts (top-level-only matching; Implicit VR
opaque-sequence blind spot with no diagnostic) are real and directly verified in source. The
article makes no false claim about this today because it never makes a nesting-scope claim at all;
the risk is a reader inferring from "removes exactly what policy says to remove" that the three
named tags are caught *wherever* they occur, which is not true.

**MINIMUM REMEDIATION:** PROSE CLARIFICATION. One clause narrowing "removes exactly what policy
says to remove" (or the policy table) to top-level occurrences would close the gap without
reopening the engineering work. The Implicit-VR-specific zero-diagnostic blind spot is arguably an
IMPLEMENTATION FIX candidate for `fastDICOMstructure` (emit at least an informational diagnostic
when a defined-length element could not be verified as non-sequence), but that is out of scope for
the article and not required to make the article's current claims accurate.

---

## B. M5 store scope

**FACTS OBSERVED**

The automated, evidence-artifact-backed M5 positive validation (`m5.py`'s `run()`, which produced
`docs/m5_evidence/latest_result.json`) does exactly one retrieval: a **targeted WADO-RS GET of the
one instance whose Study/Series/SOPInstanceUID came back in the gateway's own STOW-RS receipt**
(`retrieve_instance()`). It does not enumerate the store, and it does not scan the store for
canaries generically — it fetches one named object and inspects that object.

The milestone document's §12 additionally *describes* "A QIDO-RS search
(`.../dicomWeb/instances`) after the run additionally confirms... that exactly one instance exists
in the store and its PatientID is DEMO" — but **this QIDO-RS check exists nowhere in the codebase**.
A repo-wide search for `QIDO`/`qido`/`dicomWeb/instances` finds only the three prose mentions in
`docs/M5_APPROVED_PERSISTENCE_VALIDATION.md` itself (lines 116, 197, 307); there is no function, no
script, and no field in `latest_result.json` corresponding to it. Per §18's cost table it was run
"1 (post-hoc sanity check)" — i.e. a manual, one-off command, not part of the reproducible/frozen
evidence chain, and not re-run automatically alongside `m5.py`.

So the evidence supports the **narrower** claim — "the retrieved object contained no source
representation" — with full automation and a frozen artifact behind it. The **stronger** claim —
"the exercised store contained no source representation [in any object]" — rests partly on an
un-automated, non-reproduced, manually-described QIDO query that is not part of the frozen evidence
and was performed once.

**Duplicate/extra STOW submissions:** §13 of the M5 doc and the accompanying log excerpt show the
Healthcare API itself rejects a second STOW-RS submission under the *same, fixed* UID triple with
HTTP 409 — that is Healthcare API's own conflict semantics, not a check this project's code
performs. Because the fixture's UIDs are fixed by design (`fixtures.py:152-159`, "so repeated M5
validation runs submit the *same* instance"), this only characterizes what happens when the *same*
identity is resubmitted. It says nothing about a hypothetical extra submission under a *different*
UID triple (e.g. an untransformed object stored under a fresh SOPInstanceUID): `retrieve_instance()`
would never look for it (it only fetches the receipt's own UIDs), and the one-off manual QIDO count
is not automatically re-verified per run, so such an object — had one ever been submitted — would
not necessarily be caught by the automated, reproducible part of the M5 evidence chain. The
negative control (§17) that *does* prove the retrieval/inspection logic can catch a source-bearing
object was run against a **separate, temporary, isolated store** (`negctl-dicom`), not against
`approved-dicom` itself, so it validates the checker's ability to detect a violation *when pointed
at one*, not the automated harness's ability to discover an unexpected object in the *actual*
production store without being told where to look.

**EXACT EVIDENCE**

- `src/fastdicom_gateway/validation/m5.py:88-112` (`retrieve_instance`) — single WADO-RS GET keyed
  to `study_uid`/`series_uid`/`sop_uid` arguments.
- `src/fastdicom_gateway/validation/m5.py:158-297` (`run`) — no QIDO/search call anywhere; `result`
  dict has no store-wide field.
- `docs/M5_APPROVED_PERSISTENCE_VALIDATION.md:196-199` — prose-only QIDO-RS claim.
- `docs/M5_APPROVED_PERSISTENCE_VALIDATION.md:307` — "Search (QIDO-RS) requests | 1 (post-hoc sanity
  check)".
- `docs/M5_APPROVED_PERSISTENCE_VALIDATION.md:201-217` (§13) — duplicate-submission behavior is
  Healthcare-API-enforced 409, observed for the *same* fixed UID triple only.
- `src/fastdicom_gateway/validation/fixtures.py:152-163` — UIDs are fixed, not per-run, by design.
- `docs/M5_APPROVED_PERSISTENCE_VALIDATION.md:278-294` (§17) — negative control used a separate,
  temporary store (`negctl-dicom`), deleted afterward, not `approved-dicom`.
- `docs/m5_evidence/latest_result.json` — confirmed to contain exactly `source`/`transformed`/
  `retrieved` snapshots for one instance; no store-wide enumeration field.

**CURRENT ARTICLE CLAIM**

§8: "It verifies the state of this retrieved object, not every possible network transmission or
provider-internal event; the broader no-intentional-transmission claim rests on that result plus
the sequencing in §5 and the behavioral evidence in M2–M4 — not on WADO-RS retrieval alone."
(`pysynapse_article_publication_candidate_v2.md:163`). §10's "demonstrates" list: "only the
transformed representation... was present in durable storage upon independent retrieval" — already
scoped to "upon independent retrieval," not to "anywhere in the store."

**VERDICT:** SUPPORTED — the article's current wording already states the narrower, single-object
claim and explicitly disclaims that this covers "every possible network transmission." It does not
claim store-wide absence. The adversarial review's distinction (A vs. B) is factually correct as a
description of what the evidence chain actually did, but the article had already been narrowed to
match A before this review, not B.

**MINIMUM REMEDIATION:** NONE for the article as currently worded. (If the underlying milestone
documentation is ever revised, downgrading §12's QIDO-RS sentence from an unqualified "confirms" to
"a one-off, non-automated check found" would bring the *milestone doc* in line with what's actually
reproducible — but that is a `docs/` precision fix, not an article fix, and not required by
anything the article currently asserts.)

---

## C. Independent semantic verification

**FACTS OBSERVED**

Production path: `fastDICOMstructure` is the only DICOM engine. `transform.py` and `sink.py` import
no other DICOM library; `sink.py`'s own docstring states it "deliberately knows nothing about DICOM
structure." Confirmed by `tests/test_app.py::test_request_path_source_contains_no_persistence_apis`
(scans `app`/`transform`/`sink` source for forbidden tokens) and by direct reading of all three
modules — no `import pydicom` anywhere in `app.py`, `transform.py`, or `sink.py`.

`pydicom` participates only as an out-of-band, test/validation-only independent oracle:
- `tests/test_transform.py::test_pixel_data_is_preserved_byte_identical` and
  `::test_output_reparses_with_pydicom` — checks Pixel Data equality and absence/value of the three
  named tags, via pydicom, against `transform.process()`'s output.
- `tests/conftest.py` — imports pydicom-adjacent low-level helpers only for building fixtures by
  hand (not for verification).
- `src/fastdicom_gateway/validation/m5.py:120-145` (`_structural_snapshot`) — pydicom is the
  independent oracle for source/transformed/retrieved snapshots in the M5 harness. Its docstring
  states this explicitly: "never used on the production path."
- `src/fastdicom_gateway/validation/fixtures.py` — fixtures are hand-encoded at the byte level, not
  built with pydicom, specifically so fixture construction doesn't depend on a second DICOM
  implementation either.

So: source, transformed, and WADO-RS-retrieved DICOM are all inspected with **pydicom** in the M5
harness (an independent implementation from the production engine) — this is a genuine
cross-implementation check, not fastDICOMstructure grading its own homework, for the *specific
fields* `_structural_snapshot` extracts (PatientName/ID/BirthDate presence/value, private-tag
presence via `tag.is_private`, Pixel Data hash, the three UIDs). `fastDICOMstructure` **also**
separately verifies its own output via `transform.py`'s `_verify_output` (a reparse using itself,
for structural self-consistency / M1-AC5 — not an independent-implementation check).

**What backs "Everything else preserve":** two different kinds of evidence, at two different
layers, neither of which is a byte-for-byte, all-attributes comparison at the gateway level:

1. **Design/library-level (fastDICOMstructure's own claim, not this gateway's measurement).** The
   round-trip contract states that a modified write reconstructs headers deterministically and
   copies untouched **value** bytes verbatim from the source span; this is tested in
   `fastDICOMstructure`'s own `tests/integration/test_mutation_roundtrip.cpp`, and measured, on a
   *different* real-world corpus (not this gateway's fixtures), via `Structure.write_with_stats()`
   as "99.99% preserved" (`docs/roundtrip-contract.md`, "Modified-structure output..." paragraph).
2. **Gateway-level tests** (`test_transform.py`, `test_app.py`, `m5.py`) check only the five
   specific attributes the fixed policy targets (PatientName, PatientID, PatientBirthDate, the one
   private tag, Pixel Data) plus a generic "output reparses cleanly with zero blocking diagnostics"
   check and (for scenario A) presence of the untouched run-canary tag and `SliceThickness`/
   `Modality` bytes as a literal substring match. **No gateway-level test or evidence artifact
   computes `write_with_stats()` / `source_backed_value_bytes` / `regenerated_value_bytes` for this
   project's own fixtures**, and no test asserts "every tag other than the five targeted ones is
   byte-identical" as a general property — a targeted grep of `src/` and `tests/` for
   `write_with_stats`, `WriteStats`, `source_backed`, and `regenerated` returns zero gateway-level
   hits.

So "Everything else preserve" is checked **structurally and for selected attributes plus a Pixel
Data hash** at the gateway level, backed by a **design-level, library-tested, differently-corpus-
measured** general preservation guarantee one layer down — not an element-for-element or
byte-for-byte measurement performed on this project's own fixtures.

**EXACT EVIDENCE**

- `src/fastdicom_gateway/transform.py:1-13` (module docstring) — "fastDICOMstructure is the *only*
  DICOM engine used on this path... No pydicom import exists in this module."
- `src/fastdicom_gateway/sink.py:16-20` — "This module deliberately knows nothing about DICOM
  structure."
- `tests/test_app.py:119-123` — forbidden-token source scan across `app`/`transform`/`sink`.
- `tests/test_transform.py:71-91,111-125` — pydicom-based Pixel Data and attribute checks.
- `src/fastdicom_gateway/validation/m5.py:120-145` — `_structural_snapshot`, pydicom-based, used for
  all three of source/transformed/retrieved.
- `fastDICOMstructure/docs/roundtrip-contract.md`, "Two write contracts" and "Known gaps" sections —
  the general preservation contract and its 99.99% figure, measured on a different corpus.
- Repo-wide grep (`grep -rn "write_with_stats\|WriteStats\|source_backed\|regenerated" src tests
  docs README.md`) in `fastDICOMgateway` — zero results outside the one prose mention of "everything
  else" in `transform.py:146`.

**CURRENT ARTICLE CLAIM**

§5 table: "Everything else | preserve"; §8: "The harder, more useful claim is that the policy
boundary is *selective*: it removes exactly what policy says to remove, and preserves the rest —
including a large binary payload — without corruption, through the full path to a durable
clinical-imaging store" (`pysynapse_article_publication_candidate_v2.md:175`).

**VERDICT:** SUPPORTED BUT IMPRECISE. The claim is true for the attributes actually checked
(PatientName/ID/BirthDate, one private tag, Pixel Data) and is consistent with, but not itself a
direct measurement of, fastDICOMstructure's own general byte-preservation contract. "Preserves the
rest... without corruption" is accurate as a qualitative/structural claim (output reparses cleanly,
specific untouched fields observed present) but is not backed by a quantitative byte-level
preservation measurement performed on this project's own fixtures.

**MINIMUM REMEDIATION:** NONE required for the article's current wording, which already stays at
the qualitative/structural level and does not claim a specific measured byte-preservation
percentage. If tightening is wanted, PROSE CLARIFICATION (attribute distinct from "we measured
byte-for-byte preservation of literally everything") is the ceiling; no claim narrowing is strictly
necessary since the article never asserts a specific preservation metric.

---

## D. Ordering claim

**FACTS OBSERVED**

The production call path is:

```
app.py: body = await request.body()                      # Starlette in-memory bytes (see F)
app.py: result = transform.process(body)                  # <-- fully completes, returns, or raises
   transform.py: structure = _parse_or_reject(data)        # parse
   transform.py: touched, private_removed = _apply_demo_policy(structure)   # transform
   transform.py: output_bytes = _write_to_bytes(structure) # serialize
   transform.py: study_uid, series_uid, sop_uid = _verify_output(output_bytes)  # verify
   transform.py: return TransformResult(output_bytes=output_bytes, ...)
app.py (/dicom/store only): stored = sink.store(result.output_bytes, study_instance_uid=..., ...)
   sink.py: body, content_type = _multipart_body(dicom_bytes)   # STOW-RS request construction
   sink.py: response = _session().post(url, data=body, ...)     # STOW-RS send
```

Three separate kinds of evidence, which the article's own §8 addition already keeps distinct
(`pysynapse_article_publication_candidate_v2.md:163`, "...the sequencing in §5 and the behavioral
evidence in M2–M4..."):

1. **Source-code structure (the load-bearing evidence for "constructed" ordering).** `sink.store()`
   only ever receives `result.output_bytes` — the value `transform.process()` returns *after*
   completing parse→policy→serialize→verify. There is no code path in `app.py` that passes the raw
   `body` variable to `sink.store()`; `sink.py`'s `_multipart_body` (the function that actually
   constructs the STOW-RS request bytes) only ever sees whatever `dicom_bytes` argument its caller
   passed. Python's synchronous, single-threaded execution of this handler function makes the
   alternate order (constructing/sending a persistence request before `transform.process()` returns)
   impossible without rewriting the code to call `sink.store()` on `body` directly, which it does
   not do anywhere. This is a property of the code's structure and Python's execution semantics, not
   an inference from absence of counter-evidence.
2. **Unit tests (data-flow, not literal ordering/timing assertions).**
   `tests/test_app.py::test_store_endpoint_applies_the_same_fixed_policy` monkeypatches
   `sink.store` to *capture* the bytes it receives and asserts they already lack `PATIENT_NAME`/
   `PATIENT_ID` and contain `DEMO_PATIENT_ID` — i.e. whatever would become the STOW-RS request body
   is already policy-applied. `::test_store_endpoint_rejects_malformed_input_before_touching_sink`
   asserts `sink.store` is never called at all (`calls == []`) when `transform.process()` raises
   `RejectedInput`. Neither test measures wall-clock ordering or asserts on mock call sequence
   numbers; both verify the data-flow invariant that matters (only transformed bytes, and only after
   a successful transform, ever reach the call site that builds the outbound request).
3. **M2–M5 observation.** Strace/filesystem scans (M2), a read-only/no-writable-mounts container
   (M3), Cloud Run log inspection (M4), and independent WADO-RS retrieval (M5) all establish that no
   source-bearing content was **observed** to reach a persistence surface. None of these establish
   *ordering* by themselves — a hypothetical implementation that sent source bytes to a store this
   harness never queried, or that raced a persistence call against the transform without this
   project's tests catching it, would not necessarily be caught by M2–M5's absence-of-observation
   evidence alone. Per this report's instructions, absence of observed persistence is treated here
   as corroboration of the *outcome*, not as proof of *ordering* — ordering itself is established by
   (1) and (2) above.

**EXACT EVIDENCE**

- `src/fastdicom_gateway/app.py:109-159` (`receive_and_store_dicom`) — `result = transform.process(body)`
  precedes, and its return value alone feeds, `sink.store(result.output_bytes, ...)`.
- `src/fastdicom_gateway/transform.py:204-229` (`process`) — linear parse→policy→write→verify,
  single return statement.
- `src/fastdicom_gateway/sink.py:122-141` (`store`, `_multipart_body`) — `store()`'s only
  bytes-shaped parameter is `dicom_bytes`; no reference to the original request body anywhere in
  the module.
- `tests/test_app.py:235-249` (`test_store_endpoint_applies_the_same_fixed_policy`) and
  `tests/test_app.py:225-232` (`test_store_endpoint_rejects_malformed_input_before_touching_sink`).
- `docs/M5_APPROVED_PERSISTENCE_VALIDATION.md:47-65` (§4) — "`/dicom/store` calls the *same*
  `transform.process()` `/dicom` always has, not a second implementation."

**CURRENT ARTICLE CLAIM**

§5: "**No outbound persistence request is constructed or sent until the source DICOM has completed
parse, policy transformation, in-memory serialization, and post-transform verification. Only the
resulting transformed bytes are supplied to the Healthcare API client.**"
(`pysynapse_article_publication_candidate_v2.md:112`, "load-bearing sentence").

**VERDICT:** SUPPORTED. The claim is fully backed by source-code structure and reinforced by unit
tests; M2–M5 corroborate the outcome without being asked to carry the ordering claim alone, which
matches how the article already frames it.

**MINIMUM REMEDIATION:** NONE.

---

## E. Cross-environment consistency

**FACTS OBSERVED**

M2, M3, and M4 each independently re-run the **same four fixed scenarios** (A: successful
transform, B: malformed non-DICOM, C: truncated/parser-rejected, D: unmodified Implicit VR →
internal write failure), defined once in `validation/scenarios.py` and shared by all three
harnesses. Each scenario's PASS criterion is a fixed set of checks: HTTP status bucket, specific
named-attribute absence/presence in the response body (literal substring match, not a full
structural diff), Pixel Data literal-bytes presence, output reparses with zero blocking
diagnostics, and (per-environment) absence of a canary in the relevant observation surface
(filesystem/strace for M2, `docker diff` for M3, Cloud Run logs for M4). Because
`fixtures.run_canary(run_id, scenario_id)` embeds a fresh per-run value, the raw response bytes
differ across M2/M3/M4 runs (different canary bytes baked into an untouched tag) — so the harnesses
cannot and do not assert byte-identical output across environments. What they do assert, and what
is actually recorded, is: the same PASS/FAIL classification, the same HTTP status code, and the same
named-attribute checks succeeding, in every environment — i.e. equivalent response/failure behavior
and absence of observed source persistence, not identical transformation bytes.

No comparison logic anywhere in `m2.py`, `m3.py`, or `m4.py` hashes or diffs one environment's
output against another's (confirmed by grep for `compare`/`identical`/`hash` across all three
modules — the only hashing is `sha256_12` used for canary-matching within a single run, and
`_docker_image_digest`-style checks unrelated to transformation output).

**EXACT EVIDENCE**

- `src/fastdicom_gateway/validation/scenarios.py:1-8` (module docstring) — "the definition of
  'successful transform'... stays identical whether the gateway under test is a bare subprocess (M2)
  or a container (M3)... Only how the harness reaches the gateway... differs."
- `src/fastdicom_gateway/validation/scenarios.py:66-114` (`run_scenario_a`/`run_scenario_b`) — the
  fixed `checks` dict, identical in shape for both M2 and M3 call sites.
- `docs/M2_PERSISTENCE_BOUNDARY_VALIDATION.md:130-137` vs. `docs/M3_CONTAINER_VALIDATION.md:204-211`
  vs. `docs/M4_CLOUD_RUN_VALIDATION.md:152-159` — three per-environment result tables with matching
  PASS/HTTP-code columns per scenario; no cross-table byte comparison anywhere in any of the three
  documents (confirmed by grep for "identical"/"consistent"/"reproduce" in each doc).
- `src/fastdicom_gateway/validation/fixtures.py:37-42` (`run_canary`) — per-run, per-scenario value,
  which is why byte-identical cross-environment output was never possible to assert in the first
  place.

**CURRENT ARTICLE CLAIM**

§10: "Consistent transformation and failure behavior across a bare host process, a read-only
container, and Cloud Run's managed environment."
(`pysynapse_article_publication_candidate_v2.md:210`).

**VERDICT:** SUPPORTED BUT IMPRECISE. "Behavior" is the operative word and is accurate (same
policy-check outcomes, same failure classification, same HTTP codes, in every environment); a
reader could misread "consistent transformation... behavior" as "byte-identical transformation
output across environments," which was neither tested nor true given the per-run canary.

**MINIMUM REMEDIATION:** PROSE CLARIFICATION (optional). Swapping "behavior" for something like
"policy outcome and failure classification" would foreclose the byte-identical misreading, but the
existing word "behavior" (as opposed to "output" or "bytes") already leans the correct direction, so
this is a nice-to-have, not a correction of a false claim.

---

## F. Memory / request body

**FACTS OBSERVED**

- `POST /dicom(/store)` calls only `await request.body()` — never `request.form()` or
  `UploadFile`. Traced directly in the installed Starlette (`1.6.0`, via FastAPI `0.141.1`,
  `pyproject.toml` pins only `fastapi>=0.110` / `uvicorn>=0.27`, so this is the currently-resolved
  version, not a hash-locked one): `Request.body()` (`starlette/requests.py:254-260`) accumulates
  ASGI `receive()` chunks into a plain Python `list`, then `b"".join(chunks)` — pure in-memory
  accumulation, no size threshold, no spill-to-disk path anywhere in this method. Disk spooling
  (`SpooledTemporaryFile`) exists in Starlette only inside `_get_form()`
  (`starlette/requests.py:268-283`), which this project's code never calls. So: **for the current
  code, the "does Starlette ever spool to disk" question is moot** — the only body-reading API this
  project calls has no disk-spooling code path at all, regardless of body size.
- `fastdicomstructure.read_buffer(data, ...)` **copies** `data` into C-owned storage — this is
  stated explicitly in its own docstring ("The C ABI copies `data` into storage owned by the
  returned structure") and confirmed in `fds.py`'s `read_buffer` (`ctypes.cast` + `fds_parse_buffer`
  call, no zero-copy buffer-sharing mechanism). "Zero-copy Values" (per `source.hpp`'s comment) means
  element *Values* reference spans into that one already-copied buffer — it does not mean the
  original Python bytes and the C buffer are the same memory.
- `Structure.write_bytes()` returns a pointer into structure-owned memory, which the Python binding
  then copies again via `ctypes.string_at(data, length.value)` into a fresh Python `bytes` object
  (`fastdicomstructure/__init__.py:490-506`).
- `_verify_output()` reparses the already-twice-copied output bytes via a second `read_buffer` call
  — another full C-owned copy, freed via `structure.close()` in `transform.process()`'s `finally`.
- `sink._multipart_body(dicom_bytes)` builds `body = header + dicom_bytes + footer` — Python bytes
  concatenation, which allocates and copies a new buffer containing the full object again.

**Approximate peak copies**, tracing only this project's own code (not counting whatever
`requests`/`urllib3`/the OS socket layer buffer internally beyond this call): the source object
exists as (1) the joined-chunks Python `bytes` from `request.body()`, (2) the C-owned copy made by
`read_buffer` during parse, (3) the C-owned output buffer from `write_bytes()`, (4) the Python
`bytes` copy of that output (`ctypes.string_at`), (5) a further C-owned copy made when
`_verify_output` reparses that output, and (6) the concatenated multipart body in `sink.py`. That is
roughly **five to six full-object-sized copies**, at minimum, before any HTTP-client-library-internal
buffering is counted. Pixel Data's raw bytes are part of `data`/`output_bytes`/`dicom_bytes` at every
one of these points — they are never separated out, so they are copied exactly as many times as the
rest of the object.

- **Distinguishing the two claims the task asked about:** "Pixel Data is not decoded/materialized
  into an object model" is true and specific — confirmed by `PixelDataReference` holding only a
  source byte-range reference, never an allocated per-pixel `Value`
  (`fastDICOMstructure/include/fastdicomstructure/pixel_data_reference.hpp`, and
  `tests/test_transform.py:71-83`'s own comment: "fastDICOMstructure deliberately never exposes
  Pixel Data bytes through `Structure.get()`/iteration at all"). "The raw Pixel Data bytes never
  occupy memory" is false and is not a claim the article makes — the raw bytes occupy memory
  multiple times over, as shown above, as part of the whole-object buffers.
- Under a hypothetical M3-style read-only filesystem, if some future code path *did* trigger
  Starlette's form-spooling (`_get_form`), `SpooledTemporaryFile` only actually opens a real
  filesystem file once its in-memory threshold (`max_part_size`, default part-size-based in this
  Starlette version) is exceeded; on a read-only filesystem that would surface as an `OSError` at
  that point, consistent with the `OSError: [Errno 30] Read-only file system` pattern M3 already
  observed for a different write path (§ of `docs/M3_CONTAINER_VALIDATION.md`). This is not
  reachable via any code path this project currently exercises, so it is a hypothetical, not an
  observed behavior.

**EXACT EVIDENCE**

- `src/fastdicom_gateway/app.py:51-55` — `body = await request.body()` with the accompanying
  "Persistence constraint" comment.
- `.venv/lib/python3.14/site-packages/starlette/requests.py:254-260` (`body`) and `:268-283`
  (`_get_form`) — installed-version source, `starlette==1.6.0`.
- `pyproject.toml:8-9` — unpinned `fastapi>=0.110` (Starlette pulled in transitively, also
  unpinned to an exact version).
- `fastDICOMstructure/python/fastdicomstructure/__init__.py:630-636` (`read_buffer` docstring) —
  "The C ABI copies `data` into storage owned by the returned structure."
- `fastDICOMstructure/python/fastdicomstructure/__init__.py:490-506` (`write_bytes`) —
  `ctypes.string_at` copy into a new Python `bytes`.
- `src/fastdicom_gateway/transform.py:175-201` (`_verify_output`) — second `read_buffer` call on
  the output bytes.
- `src/fastdicom_gateway/sink.py:109-119` (`_multipart_body`) — Python bytes concatenation.
- `fastDICOMstructure/include/fastdicomstructure/pixel_data_reference.hpp` and
  `tests/test_transform.py:71-83` — Pixel Data never materialized as a decoded object model.

**CURRENT ARTICLE CLAIM**

§4: "Pixel Data is represented as a reference into the source byte range, not copied into an object
model, whether or not it's being modified." §6: "no temp files, no memory-mapped files standing in
for 'in-memory'... 'In-memory' describes that application-level design choice, not a guarantee about
the layers beneath it" (`pysynapse_article_publication_candidate_v2.md:137`).

**VERDICT:** SUPPORTED — the article's actual wording is careful on exactly the point the task asked
about: it claims no object-model decode for Pixel Data (true) and never claims the raw bytes occupy
memory only once, or that copying is minimal. There is no overstatement to correct here; the
multiple-copies fact is useful context, not a contradiction of anything currently claimed.

**MINIMUM REMEDIATION:** NONE.

---

## G. Failure path

**FACTS OBSERVED**

M2/M3/M4 exercise exactly four fixed scenarios per environment (A/B/C/D, as in E above); M5 adds
Healthcare-API-specific failure modes exercised only at the unit-test level plus one deployed
reproduction:

- **Scenario B (malformed, non-DICOM bytes):** checked for 4xx status, `content-type` not
  `application/dicom`, no traceback substring in the **HTTP response**, and the literal malformed
  bytes not echoed back. Checked in all of M2/M3/M4.
- **Scenario C (truncated mid-Pixel-Data, parser-rejected):** same response-level checks as B, plus
  distinguishing this failure reason (`blocking_diagnostic`/`recoverable_error`) from B's
  (`parse_failed`).
- **Scenario D (unmodified Implicit VR → `write_bytes()` `Unsupported` → uncaught exception → 500):**
  checked for 500 status and no canary in the HTTP response. **Explicitly documented as producing a
  traceback in the server-side log** (M2 doc, §7: "A traceback **is** present in the captured server
  logs (Scenario D's 500 — `logger.exception` in `app.py`, by design). This is normal operational
  behavior... what matters is whether a canary is *inside* it, and the log canary scan... found
  none.") — i.e. the check performed is "does the canary substring appear inside the traceback text,"
  not "does the traceback ever contain local-variable values in general." Python's default
  `logger.exception`/traceback formatting does not print local variable values unless something in
  the call chain deliberately embeds them in an exception's message; this project's own exception
  types (`RejectedInput`, `PersistenceFailed`) are documented and tested (see `test_sink.py`,
  Question C evidence) to construct their messages only from a small fixed set of `reason`
  strings/status codes, never from DICOM element values.
- **M2/M3 filesystem checks:** `strace`-based (M2) and `docker diff`-based (M3) checks were run
  across all four scenarios, including the failure scenarios, and both times found zero
  write-capable filesystem operations at request time (`docs/M2_PERSISTENCE_BOUNDARY_VALIDATION.md`
  §7; `docs/M3_CONTAINER_VALIDATION.md` §matching table).
- **M4 Cloud Run log checks:** re-ran the same four scenarios against the deployed Cloud Run
  service and scanned both Cloud Run log streams for canaries; found none, across all four
  scenarios including D's 500.
- **M5 sink-specific failures** (`configuration_error`, `credentials_unavailable`, `request_error`,
  `store_rejected`): unit-tested in `test_sink.py` (mocked HTTP/credentials, 14 tests, 9 of which
  target failure paths per the M5 doc's own count — independently verified: the file has 12 test
  functions, one parametrized four ways, totaling 14 collected tests, consistent with the doc's
  claim) including one test asserting a synthetic canary never appears in the exception's own
  `str()`/`repr()`. One of these (`credentials_unavailable`) was additionally reproduced against a
  **real** deployed container (`docker run` with no ADC available) before/after a real bug fix
  (§14 of the M5 doc) — this is the one M5 failure mode with both unit-test *and* real-environment
  evidence; the other three (`configuration_error`, `request_error`, `store_rejected`) are unit-test-only,
  though `store_rejected` (409) was also observed live once, incidentally, during the duplicate-
  submission characterization (§13).

**What was NOT checked:** no scenario exercises a request that partially transforms successfully
and then fails during the STOW-RS HTTP call itself against the real Healthcare API (i.e., a live
`request_error`/timeout mid-flight against the real service, as opposed to a mocked session) — only
the credentials-unavailable case got a live-environment reproduction. No scenario checks whether a
Python `Exception.__cause__`/`__context__` chain (visible via `logger.exception`'s full traceback)
could leak a value through a *lower-level library's* own exception message (e.g., a `requests`
exception embedding a URL or header) — the tests check this project's own exception types' string
forms, not every exception type that could theoretically propagate through `except Exception`.

**EXACT EVIDENCE**

- `src/fastdicom_gateway/validation/scenarios.py:66-160` — scenario A/B/C check definitions.
- `docs/M2_PERSISTENCE_BOUNDARY_VALIDATION.md:105-172` (§6-8) — scenario table, results table,
  traceback-in-logs note, negative control.
- `docs/M3_CONTAINER_VALIDATION.md:192-211`, `docs/M4_CLOUD_RUN_VALIDATION.md:150-159` — matching
  per-environment tables.
- `src/fastdicom_gateway/app.py:75-85,131-136` — `logger.exception` calls, never logging local
  values, only `input_bytes`/`elapsed_ms`.
- `tests/test_sink.py` (all 12 functions / 14 collected tests, esp. `:136-147`
  `test_persistence_failed_message_never_contains_dicom_bytes`).
- `docs/M5_APPROVED_PERSISTENCE_VALIDATION.md:219-250` (§14) — real-deployment reproduction of the
  `credentials_unavailable` bug/fix; §13 for the one live `store_rejected` (409) observation.

**CURRENT ARTICLE CLAIM**

The article does not claim exhaustive failure-path coverage; §10's "demonstrates" list only claims
"Consistent transformation and failure behavior across a bare host process, a read-only container,
and Cloud Run's managed environment" (already addressed under E) and the negative-controls
discussion (§9) is scoped to the four M2-through-M5 controls actually run.

**VERDICT:** SUPPORTED — everything the article claims about failure-path testing matches what was
actually exercised; the article does not overreach into failure modes that were not tested.

**MINIMUM REMEDIATION:** NONE.

---

## H. Synthetic identifiers

**FACTS OBSERVED**

The canonical fixed canary values are defined identically in `fastDICOMgateway/tests/conftest.py`
and `fastDICOMgateway/src/fastdicom_gateway/validation/fixtures.py`:
`PATIENT_NAME = b"NEVER_PERSIST^KRIS"`, `PATIENT_ID = b"SECRET-123456789"`,
`PATIENT_BIRTH_DATE = b"19610217"`. Both files carry an explicit, consistent comment asserting
synthetic intent: "Fixed synthetic source canaries... Never derived from, or resembling, real
patient data" (`fixtures.py:19-22`) / "M1-AC2's exact values" (`conftest.py:43`). `git log -p
--follow` on `fixtures.py` shows the constant was introduced once, in the commit that added the
file, with no subsequent edits and no additional commit-message rationale beyond that comment; the
same is true in `conftest.py`. There is no evidence in either repository's history contradicting the
synthetic-intent assertion, and no mechanism by which this report can verify or refute a value's
resemblance to any real person's birth date — per the task's instruction, no such inference was
attempted.

The optics distinction the review raises is real and independent of provenance: `PATIENT_NAME` and
`PATIENT_ID` are **self-marking** — their literal byte content ("NEVER_PERSIST", "SECRET-") signals
"test data" to any reader who encounters them in a log, a doc table, or a screenshot, independent of
any surrounding comment. `PATIENT_BIRTH_DATE = "19610217"` carries no such self-marking; taken out
of context (e.g. copy-pasted from `README.md:92` or `docs/M5_APPROVED_PERSISTENCE_VALIDATION.md:178`,
both of which display it in a plain markdown table next to the label "PatientBirthDate") it reads
exactly like a plausible real date of birth. The article itself never displays any of the three
literal values — confirmed by grep against
`pysynapse_article_publication_candidate_v2.md` — so this optics exposure exists only in the
repository's own README/docs tables, not in the article.

**EXACT EVIDENCE**

- `src/fastdicom_gateway/validation/fixtures.py:19-34` — the three constants and their "Never
  derived from, or resembling, real patient data" comment.
- `tests/conftest.py:42-46` — the matching constants and "M1-AC2's exact values" comment.
- `git log -p --follow -- src/fastdicom_gateway/validation/fixtures.py` — single introduction,
  no later edits, no additional rationale commit.
- `README.md:92`, `docs/M4_CLOUD_RUN_VALIDATION.md:127`, `docs/M3_CONTAINER_VALIDATION.md:264`,
  `docs/M5_APPROVED_PERSISTENCE_VALIDATION.md:147,178` — every place the literal value
  `19610217` appears in this repository, all in plain, unmarked tables/prose.
- Grep of `pysynapse_article_publication_candidate_v2.md` for `19610217`/`NEVER_PERSIST`/
  `SECRET-123456789` — zero matches; the article never inlines any canary value.

**CURRENT ARTICLE CLAIM**

None — the article does not mention the value.

**VERDICT:** SUPPORTED (repository evidence consistently and exclusively asserts synthetic intent;
no contradicting evidence found; no article claim is at risk). The optics concern raised by the
reviewer is a valid, separate observation about the **repository's own README/docs**, not about the
article, which never surfaces the value.

**MINIMUM REMEDIATION:** NONE for the article. For the repository's own documentation (out of
scope for "the article," flagged here only because the task asked): IMPLEMENTATION FIX — a one-line
constant swap to an unambiguously-fake pattern (e.g. a self-marking string in the same style as the
other two canaries, or an impossible calendar date) would remove the optics asymmetry entirely; this
is a trivial change but touches shared test/validation fixtures, so it was not performed here per
this task's "do not modify anything" instruction.

---

## I. Input interface

**FACTS OBSERVED**

`fastDICOMgateway` exposes exactly two DICOM-accepting HTTP routes,
`POST /dicom` and `POST /dicom/store`, both of which read the raw request body as bytes
(`await request.body()`) and treat it directly as one complete DICOM Part-10 object — there is no
multipart/related wrapping expected or parsed on ingest, no DIMSE upper-layer protocol (no
association negotiation, no AE title handling, no C-STORE SCP) anywhere in this codebase, and no
DICOMweb STOW-RS **server**-side conformance (real STOW-RS per PS3.18 requires a
`multipart/related; type="application/dicom"` request body with a boundary, which this project only
ever *constructs* when acting as a STOW-RS *client* toward the Healthcare API in `sink.py` — never
when *receiving* on `/dicom` or `/dicom/store`). This was confirmed by reading `app.py` in full (no
DIMSE library import, no multipart parsing) and by `Dockerfile`/`pyproject.toml` (no DIMSE
dependency such as `pynetdicom` anywhere). The one place this repository's own prose overstates
protocol conformance is `README.md:210`: "The gateway's transformation pipeline... is a thin
HTTP/DICOMweb wrapper" — calling the *ingest* side "DICOMweb" is imprecise, since DICOMweb STOW-RS
ingest would require multipart/related parsing this project's server side never does.

A real modality (CT/MR scanner, workstation) overwhelmingly speaks DIMSE C-STORE in clinical
practice, not raw HTTP POST and, in many deployments, not DICOMweb STOW-RS either (though STOW-RS
adoption exists in some modern PACS/VNA integrations). Neither this gateway's actual ingest
mechanism (bespoke raw-body HTTP POST) nor a hypothetical "DICOMweb-conformant" one would, by
itself, be reachable by a DIMSE-only modality without an additional DIMSE-to-HTTP bridge in front of
it, which this project does not implement or claim to implement.

**EXACT EVIDENCE**

- `src/fastdicom_gateway/app.py:49-55,109-111` — both routes read `await request.body()` directly;
  no multipart parsing, no DIMSE handling.
- `pyproject.toml` — dependencies are `fastapi`, `uvicorn`, `requests`, `google-auth*`; no DIMSE
  library (`pynetdicom` or similar) anywhere.
- `src/fastdicom_gateway/sink.py:109-119` (`_multipart_body`) — the only multipart/related
  construction in this codebase, used only for the *outbound* STOW-RS call to Healthcare API.
- `README.md:210` — "a thin HTTP/DICOMweb wrapper" (the one place this repo's own prose is
  imprecise about the ingest side).

**CURRENT ARTICLE CLAIM**

§5 diagram: "HTTP DICOM input" as the entry label
(`pysynapse_article_publication_candidate_v2.md`, the fenced diagram block). The article never uses
the words "DICOMweb," "STOW-RS," or "modality" to describe the *ingest* side anywhere — STOW-RS/
WADO-RS only ever appear in the diagram's later, outbound-to-Healthcare-API stages, which is
accurate (that leg genuinely is STOW-RS/WADO-RS).

**VERDICT:** REVIEW CRITICISM INCORRECT as applied to the article itself — "HTTP DICOM input" is
accurate and already appropriately minimal; the article does not claim DICOMweb or modality-facing
ingest anywhere. The underlying, real imprecision (SUPPORTED) exists one level down, in this
repository's own `README.md` line 210, not in the article.

**MINIMUM REMEDIATION:** NONE for the article. PROSE CLARIFICATION (optional, out of article scope)
for `README.md:210` — replacing "HTTP/DICOMweb wrapper" with something like "a thin HTTP wrapper
around a raw DICOM POST body" would remove the one overstatement found in this codebase's own prose.

---

## Summary table

| Issue | Critique validity | Current claim status | Minimum remediation |
|---|---|---|---|
| A. Implicit VR / policy visibility (top-level-only tag matching; zero-diagnostic opaque defined-length sequences under Implicit VR) | Valid — both mechanisms verified directly in `fastDICOMstructure` source and tests; neither is exercised together with a targeted tag by any existing fixture | SUPPORTED BUT IMPRECISE (article makes no nesting-scope claim, but "removes exactly what policy says to remove" invites over-reading) | PROSE CLARIFICATION |
| B. M5 store scope (single-instance retrieval vs. store-wide scan; QIDO-RS check is prose-only, not automated/frozen) | Valid as a description of the evidence chain, but the article already states the narrower claim | SUPPORTED (article's wording already matches the narrower, correct claim) | NONE |
| C. Independent semantic verification / "Everything else preserve" | Valid — no gateway-level byte/element-level "everything else" measurement exists; only selected attributes + Pixel Data hash are checked, backed by a library-level (not gateway-level) general contract | SUPPORTED BUT IMPRECISE | NONE (PROSE CLARIFICATION optional) |
| D. Ordering claim | Not upheld against the article as worded — ordering is backed by source structure + unit tests, and the article already separates that from M2–M5's corroboration | SUPPORTED | NONE |
| E. Cross-environment consistency | Valid nuance — "behavior," not byte-identical output, was actually compared | SUPPORTED BUT IMPRECISE | PROSE CLARIFICATION (optional) |
| F. Memory / request body (copies; Pixel Data "not decoded" vs. "never in memory") | Factually informative (5-6 full-object copies traced) but does not contradict any article claim, which is already scoped to "not decoded into an object model" | SUPPORTED | NONE |
| G. Failure path coverage | Not upheld — tested failure modes match what the article implies were tested; no overreach found | SUPPORTED | NONE |
| H. Synthetic identifiers (PatientBirthDate optics) | Valid as an optics observation about the *repository's* README/docs tables; synthetic intent itself is well-documented; the article never displays the value at all | SUPPORTED (synthetic intent); article unaffected | NONE for article; IMPLEMENTATION FIX optional for repo docs (not performed) |
| I. Input interface ("HTTP DICOM input") | Not upheld against the article, which is already accurate and minimal; the one real imprecision is in `README.md`, not the article | REVIEW CRITICISM INCORRECT (re: article) / SUPPORTED (re: `README.md:210`) | NONE for article; PROSE CLARIFICATION optional for `README.md` |

---

## SMALL VALIDATIONS ACTUALLY WORTH RUNNING

Only validations that could materially change a publication claim if their result came back
unfavorable — not hardening, fuzzing, or feature work:

1. **Construct one fixture with a targeted patient tag nested inside a standard sequence (e.g.
   `Other Patient IDs Sequence (0010,1002)` carrying a nested `(0010,0020)`), run it through
   `transform.process()`, and check whether the nested value survives into `output_bytes`.** This
   would either confirm or refute Question A's top-level-only gap as a live, reproducible fact
   against the actual code (right now it is established by reading docstrings/source, not by
   running a fixture through the pipeline) — directly relevant to how confidently §8's "removes
   exactly what policy says to remove" can be worded.
2. **Construct one Implicit VR fixture that (a) contains a defined-length nested sequence carrying
   a targeted tag, and (b) also has a top-level element the fixed policy touches (so `is_modified()`
   is true), then run it through `transform.process()` and inspect `output_bytes` for the source
   value.** This is the one concrete scenario the adversarial review hypothesized for Implicit VR;
   right now it is supported by parser-source reading and a unit test of the *parser* in isolation
   (`test_parse_implicit_vr_le.cpp`), but no fixture exercises it through the *gateway's* actual
   policy-and-write pipeline end to end.
3. **Re-run the M5 QIDO-RS "exactly one instance in the store" check as code, capture it in
   `latest_result.json`, and re-run it as part of `m5.py`'s automated `run()`.** This would convert
   the one prose-only, un-reproduced sanity check backing the *store-wide* (as opposed to
   single-object) framing in §12 of the M5 doc into frozen, reproducible evidence — worth doing if
   the milestone documentation's stronger framing is ever relied on again, though not required by
   the article as currently worded.

Explicitly **not** recommended: fuzzing the parser, load/concurrency testing, general Implicit-VR
hardening or a data-dictionary-based sequence-detection improvement, expanding the fixed policy to a
full PS3.15 profile, adding DIMSE support, or pinning exact dependency versions — none of these
would change what the article is currently entitled to claim; they would be production hardening or
feature work, which is out of scope here.

---

CLAIM_CLOSURE_COMPLETE
