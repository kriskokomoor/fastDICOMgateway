# S1.8 Structure Policy Integration — Implementation/Qualification Report

## 1. Starting gateway commit

`e4cba92902750798088344995bc99d99569f73ac` (the frozen pre-S1.8 baseline; see
`docs/S1_8_PRE_INTEGRATION_BASELINE.md`).

## 2. Frozen Structure and attrs dependency commits

- `fastDICOMstructure`: `ffa117f1a63c7e269bfad24bfc0f5e1938430f35`
- `fastDICOMattrs`: `46bf7d374c2d2d3a5618d31b7b2a2872ce3425f6`

Both confirmed unchanged, clean, throughout this integration (section 12).

## 3. Exact production files changed

**One file**: `src/fastdicom_gateway/transform.py` (+52/-19 lines). `app.py` and `sink.py` are
byte-for-byte unchanged (confirmed: `git diff --stat` against both is empty).

## 4. Old imperative path removed

```python
touched = 0
touched += structure.erase_recursive(_TAG_PATIENT_NAME)
touched += structure.set_value_recursive(_TAG_PATIENT_ID, _DEMO_PATIENT_ID)
touched += structure.erase_recursive(_TAG_PATIENT_BIRTH_DATE)
private_removed = structure.erase_private()
```

## 5. New `fastdicomstructure.policy.apply()` production delegation

```python
result = fds_policy.apply(structure, _GATEWAY_DEMO_POLICY)
if result.execution is not fds_policy.PolicyExecutionStatus.COMPLETED:
    raise RuntimeError(f"gateway demo policy did not complete cleanly: {result.execution}")
private_removed = next(op.count for op in result.operations if op.kind == "private_tag_policy")
touched = sum(op.count for op in result.operations if op.kind != "private_tag_policy")
return touched, private_removed
```

`_apply_demo_policy`'s own name, signature, and `(elements_touched, private_elements_removed)`
return contract are unchanged — no caller of it required any change. `process()` is **unmodified**
— the `PolicyExecutionStatus` check the approved design sketched for `process()` was placed inside
`_apply_demo_policy()` instead, a small, deliberate deviation from the design document's own
"roughly" phrasing: it keeps the function self-contained and its own contract fully intact, and
makes `process()` need zero changes at all (smaller than the design document's own estimate, not
larger). This deviation is disclosed here explicitly, not silent.

## 6. Exact fixed policy representation used

```python
_GATEWAY_DEMO_POLICY = fds_policy.Policy(
    name="gateway-demo-policy",
    version="1.0.0",
    operations=(
        fds_policy.Remove(_TAG_PATIENT_NAME),
        fds_policy.Replace(_TAG_PATIENT_ID, _DEMO_PATIENT_ID),
        fds_policy.Remove(_TAG_PATIENT_BIRTH_DATE),
        fds_policy.PrivateTagPolicy(remove=True),
    ),
)
```

Identical in kind, tag, and value to the construction `fastDICOMstructure`'s own
`tests/python/test_policy.py::PolicyReproducesGatewayDemoTest` already uses and already proves
byte-identical to the imperative sequence it replaces. `Remove`/`Replace` use their own
`recursive=True` default, exactly matching `erase_recursive`/`set_value_recursive`'s scope.

## 7. Q1 pre/post hashes and counts

| Field | Baseline (pre-integration) | Post-integration | Match |
|---|---|---|---|
| Output SHA-256 | `3bf45c727c1703b7e2f99ff0c5de1f32138eeff8a86caf60770ba9a8dfcdc761` | `3bf45c727c1703b7e2f99ff0c5de1f32138eeff8a86caf60770ba9a8dfcdc761` | **exact** |
| Input byte count | 2414 | 2414 | exact |
| Output byte count | 2324 | 2324 | exact |
| `elements_touched` | 3 | 3 | exact |
| `private_elements_removed` | 1 | 1 | exact |

Fixture: `tests/conftest.py::build_valid_dicom()` (default args), run through the real, live
`transform.process()` production path both times.

## 8. Q2 result

Non-Explicit-VR fixture (`tests/test_recursive_policy_and_vr_scope.py::
_build_implicit_vr_defined_length_sequence_fixture()`): `reason=unsupported_transfer_syntax`,
`diagnostic_count=0`, `diagnostic_severities=()` — identical to the frozen baseline. No transformed
output or sink interaction occurs (this path is entirely upstream of `_apply_demo_policy`, untouched
by the substitution).

## 9. Q3 result

`tests/conftest.py::MALFORMED_INPUT`: `reason=blocking_diagnostic`, `diagnostic_count=3`,
`diagnostic_severities=('warning', 'warning', 'recoverable_error')` — identical to the frozen
baseline.

## 10. Complete gateway test result

```text
77 passed, 1 skipped, 2 warnings
```

74 pre-existing tests (unmodified, all still passing) + 3 new tests added in
`tests/test_s1_8_structure_policy_integration.py` (delegation proof, baseline-hash pin, declarative
policy-shape check). The one skip is the same pre-existing, opt-in live Cloud Run test
(`test_m4_validation.py::test_m4_end_to_end_run_passes_against_live_service`), unrelated to this
change.

## 11. Relevant Structure test result

Full suite: **299/299 passing**, unchanged. Specifically re-run in isolation:
`tests/python/test_policy.py::PolicyReproducesGatewayDemoTest::test_apply_reproduces_gateway_demo_policy`
— passing.

## 12. Confirmation `fastDICOMstructure`/`fastDICOMattrs` were not modified

- `fastDICOMstructure`: `git status --short` shows only the two pre-existing, untouched S1.8 design
  documents (`S1_8_EXTERNAL_DICOM_GATEWAY_DESIGN_CHECKPOINT.md`,
  `S1_8_GATEWAY_INTEGRATION_IMPLEMENTATION_DESIGN.md`); HEAD unchanged at
  `ffa117f1a63c7e269bfad24bfc0f5e1938430f35`.
- `fastDICOMattrs`: `git status --short` empty; HEAD unchanged at
  `46bf7d374c2d2d3a5618d31b7b2a2872ce3425f6`.

## 13. Observed semantic differences

**None found.** Q1's output-byte-hash match is exact (not merely semantically equivalent), Q2/Q3's
rejection reasons and diagnostic fields are exact matches, and all 74 pre-existing tests pass
unmodified. The one behavioral case named as a risk in the approved design (a
`PolicyExecutionStatus` other than `COMPLETED`, e.g. from a theoretical `Replace` mutation failure
or `RollbackError`) was never observed for this fixed policy against any fixture exercised here —
consistent with the design document's own disclosure that this case has no known triggering fixture,
not evidence it is unreachable.
