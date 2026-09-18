# S1.8 Pre-Integration Baseline

## 1. Prior HEAD

`c8863c28c222bba3193ca1b77ea4d24e66912893`

## 2. Purpose

Freezes the gateway's existing, previously-uncommitted adversarial/remediation and
publication-refresh work as an independently identifiable baseline commit, and records
deterministic "before" evidence of the production transform path, ahead of a separately-authorized
S1.8 change that will delegate `_apply_demo_policy()` to `fastdicomstructure.policy.apply()`.

## 3. This is pre-integration behavior

`src/fastdicom_gateway/transform.py::_apply_demo_policy` remains fully imperative
(`Structure.erase_recursive`/`set_value_recursive`/`erase_private`, called directly). No file in
this repository imports `fastdicomstructure.policy` or calls `policy.apply()` anywhere. This commit
predates any S1.8 Structure-policy integration.

## 4. Complete test result

Command: `.venv/bin/python3 -m pytest -v` (repository root)

```text
74 passed, 1 skipped, 2 warnings in 26.98s
```

The one skip is `tests/test_m4_validation.py::test_m4_end_to_end_run_passes_against_live_service`
— an opt-in live Cloud Run test, expected/environmental (not run without a deployed service), not
a failure.

## 5. Q1 — representative accepted fixture

Fixture: `tests/conftest.py::build_valid_dicom()`, called with its default arguments (the same
fixture `tests/test_transform.py`'s own `valid_dicom_bytes` pytest fixture uses) — a synthetic
Explicit VR Little Endian object with `PatientName=NEVER_PERSIST^KRIS`,
`PatientID=SECRET-123456789`, `PatientBirthDate=19610217`, one private tag `(0019,0010)`, and a
2048-byte deterministic Pixel Data payload.

Captured by calling `transform.process()` directly (the real production path):

| Field | Value |
|---|---|
| Input SHA-256 | `05721ac890ee3149daef3ae66bd7942bbec656dc790a9cc00410efe4afb53e67` |
| Output SHA-256 | `3bf45c727c1703b7e2f99ff0c5de1f32138eeff8a86caf60770ba9a8dfcdc761` |
| Input byte count | 2414 |
| Output byte count | 2324 |
| `elements_touched` | 3 |
| `private_elements_removed` | 1 |
| `study_instance_uid` | `None` |
| `series_instance_uid` | `None` |
| `sop_instance_uid` | `None` |

(UID fields are `None` because this fixture does not set dataset-level Study/Series/SOP Instance
UID tags — only distinct File Meta UIDs, which `transform.py` does not read; this matches
`TransformResult`'s own documented "`None` for whichever tag the input didn't have" behavior, not
an anomaly.)

## 6. Q2 — representative rejection (non-Explicit-VR)

Fixture: `tests/test_recursive_policy_and_vr_scope.py::_build_implicit_vr_defined_length_sequence_fixture()`
(SHA-256 `aed04e957f1e944377f95ead87fa4c34ba0196cc9c0977197e0c9e12a4176c4d`).

```text
reason: unsupported_transfer_syntax
diagnostic_count: 0
diagnostic_severities: ()
```

## 7. Q3 — representative malformed-DICOM rejection

Fixture: `tests/conftest.py::MALFORMED_INPUT` (`b"NOT A DICOM FILE" * 8`).

```text
reason: blocking_diagnostic
diagnostic_count: 3
diagnostic_severities: ('warning', 'warning', 'recoverable_error')
```

(attrs' `read_buffer` is lenient for this input — it does not raise `FdsError`; it produces a
`Structure` whose own `.diagnostics` carry a blocking, non-`"info"` finding, which
`_parse_or_reject` correctly treats as `blocking_diagnostic`, not `parse_failed`.)

## 8. Confirmations

- `_apply_demo_policy()` remains fully imperative — confirmed by direct inspection.
- Production does not invoke `fastdicomstructure.policy.apply()` — confirmed: no such import or
  call exists anywhere in this repository's source.
- `fastDICOMstructure` frozen commit: `ffa117f1a63c7e269bfad24bfc0f5e1938430f35` (299 tests
  passing, clean tree apart from the two S1.8 design documents).
- `fastDICOMattrs` frozen commit: `46bf7d374c2d2d3a5618d31b7b2a2872ce3425f6` (clean).

## 9. Exact files included in this baseline freeze

```text
README.md
docs/PUBLICATION_BRIEF.md
src/fastdicom_gateway/transform.py
src/fastdicom_gateway/validation/m2.py
src/fastdicom_gateway/validation/m3.py
tests/test_m2_validation.py
tests/test_m3_validation.py
ADVERSARIAL_CLAIM_CLOSURE.md
ADVERSARIAL_REMEDIATION_A.md
ADVERSARIAL_VALIDATION_A.md
POST_REMEDIATION_EVIDENCE_REFRESH.md
docs/publication_refresh/m2_refresh_result.json
docs/publication_refresh/m3_refresh_result.json
docs/publication_refresh/m5_refresh_result.json
publication_validation/adversarial_validation_a.py
publication_validation/adversarial_validation_a_results.json
src/fastdicom_gateway/validation/publication_refresh.py
src/fastdicom_gateway/validation/scenarios_publication_refresh.py
tests/test_recursive_policy_and_vr_scope.py
docs/S1_8_PRE_INTEGRATION_BASELINE.md
```

## 10. No S1.8 integration included

This commit contains no `fastdicomstructure.policy` delegation, no new dependency wiring, and no
change to `app.py`, `sink.py`, or any packaging file. It is the pre-integration baseline only.
