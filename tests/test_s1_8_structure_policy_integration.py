"""S1.8: proves the gateway's fixed demonstration policy is now applied
through fastdicomstructure.policy.apply() rather than a hand-rolled
imperative sequence, and pins the production path's output against the
frozen pre-integration baseline recorded in
docs/S1_8_PRE_INTEGRATION_BASELINE.md.

Existing coverage (test_transform.py, test_recursive_policy_and_vr_scope.py,
test_app.py, etc.) already regression-tests accepted/rejected/malformed
behavior end to end and is deliberately not duplicated here -- this file
adds only what the substitution itself introduces: a delegation proof
(not merely two independently-computed answers that happen to agree) and
an explicit byte-identity pin against the recorded baseline hash.
"""

from __future__ import annotations

import hashlib
from unittest import mock

from conftest import build_valid_dicom
from fastdicom_gateway import transform
from fastdicomstructure import policy as fds_policy
from fastdicomstructure.policy import PrivateTagPolicy, Remove, Replace

_BASELINE_OUTPUT_SHA256 = "3bf45c727c1703b7e2f99ff0c5de1f32138eeff8a86caf60770ba9a8dfcdc761"
_BASELINE_INPUT_BYTE_COUNT = 2414
_BASELINE_OUTPUT_BYTE_COUNT = 2324
_BASELINE_ELEMENTS_TOUCHED = 3
_BASELINE_PRIVATE_ELEMENTS_REMOVED = 1


def test_production_path_delegates_to_structure_policy_apply() -> None:
    """Direct, executable proof the production path reaches
    fastdicomstructure.policy.apply() -- not an equivalence test that
    computes two independently-correct answers and compares them."""
    real_apply = fds_policy.apply
    calls = []

    def counting_apply(structure, pol):
        calls.append(pol)
        return real_apply(structure, pol)

    with mock.patch.object(fds_policy, "apply", side_effect=counting_apply):
        transform.process(build_valid_dicom())

    assert len(calls) == 1
    assert calls[0] is transform._GATEWAY_DEMO_POLICY


def test_output_matches_frozen_pre_integration_baseline() -> None:
    """Byte-identical output against the pre-substitution baseline
    captured in docs/S1_8_PRE_INTEGRATION_BASELINE.md, for the same
    deterministic fixture the baseline itself used."""
    accepted = build_valid_dicom()
    result = transform.process(accepted)

    assert result.input_byte_count == _BASELINE_INPUT_BYTE_COUNT
    assert result.output_byte_count == _BASELINE_OUTPUT_BYTE_COUNT
    assert result.elements_touched == _BASELINE_ELEMENTS_TOUCHED
    assert result.private_elements_removed == _BASELINE_PRIVATE_ELEMENTS_REMOVED
    assert hashlib.sha256(result.output_bytes).hexdigest() == _BASELINE_OUTPUT_SHA256


def test_gateway_demo_policy_is_the_declarative_structure_representation() -> None:
    """The fixed policy is now a real fastdicomstructure.policy.Policy
    object, not a private implementation detail -- confirms the four
    operations match the pre-existing, already-frozen
    PolicyReproducesGatewayDemoTest construction exactly (kind/tag/value),
    so any future drift between the two is caught here rather than only
    by chance."""
    remove_name, replace_id, remove_dob, private_policy = transform._GATEWAY_DEMO_POLICY.operations
    assert isinstance(remove_name, Remove)
    assert isinstance(replace_id, Replace)
    assert isinstance(remove_dob, Remove)
    assert isinstance(private_policy, PrivateTagPolicy)
    assert remove_name.locator.tag == transform._TAG_PATIENT_NAME
    assert replace_id.locator.tag == transform._TAG_PATIENT_ID
    assert replace_id.value == transform._DEMO_PATIENT_ID
    assert remove_dob.locator.tag == transform._TAG_PATIENT_BIRTH_DATE
    assert private_policy.remove is True
