"""Permanent regression coverage for the two gaps found by the adversarial
experiments in ADVERSARIAL_VALIDATION_A.md, and for the fix in
ADVERSARIAL_REMEDIATION_A.md:

1. The fixed policy's PatientName/PatientID/PatientBirthDate operations
   must reach every structurally visible occurrence, including ones nested
   inside a sequence item, not just a top-level occurrence.
2. Implicit VR Little Endian input must be rejected before any policy
   mutation or serialization is attempted -- a fail-closed acceptance
   scope, not a claim that Implicit VR is unsafe in general.

Fixtures here are self-contained (not imported from conftest.py or
validation/fixtures.py) and use unmistakably synthetic values distinct
from every other canary already used elsewhere in this repository.
"""

from __future__ import annotations

import io
import struct

import pytest
from fastapi.testclient import TestClient

from fastdicom_gateway import app as app_module
from fastdicom_gateway import sink, transform
from fastdicom_gateway.transform import fds

client = TestClient(app_module.app)

# ---------------------------------------------------------------------------
# Self-contained low-level encoders (see publication_validation/
# adversarial_validation_a.py for the same pattern; duplicated here on
# purpose so this permanent suite has no dependency on that one-off
# artifact, which must stay intact as pre-fix evidence).
# ---------------------------------------------------------------------------


def _u16(value: int) -> bytes:
    return struct.pack("<H", value)


def _u32(value: int) -> bytes:
    return struct.pack("<I", value)


def _tag(group: int, element: int) -> bytes:
    return _u16(group) + _u16(element)


def _element_short(group: int, element: int, vr: str, value: bytes) -> bytes:
    if len(value) % 2 != 0:
        value += b"\x00"
    return _tag(group, element) + vr.encode("ascii") + _u16(len(value)) + value


def _element_long(group: int, element: int, vr: str, value: bytes) -> bytes:
    if len(value) % 2 != 0:
        value += b"\x00"
    return _tag(group, element) + vr.encode("ascii") + b"\x00\x00" + _u32(len(value)) + value


def _element_sq(group: int, element: int, item_bytes: bytes) -> bytes:
    return _tag(group, element) + b"SQ" + b"\x00\x00" + _u32(len(item_bytes)) + item_bytes


def _dicom_item(content: bytes) -> bytes:
    return _tag(0xFFFE, 0xE000) + _u32(len(content)) + content


def _element_implicit(group: int, element: int, value: bytes) -> bytes:
    if len(value) % 2 != 0:
        value += b"\x00"
    return _tag(group, element) + _u32(len(value)) + value


def _file_meta(ts_uid: bytes, sop_instance_uid: bytes = b"1.2.3.4.5.6.7.8") -> bytes:
    group_body = (
        _element_short(0x0002, 0x0002, "UI", b"1.2.840.10008.5.1.4.1.1.7")
        + _element_short(0x0002, 0x0003, "UI", sop_instance_uid)
        + _element_short(0x0002, 0x0010, "UI", ts_uid)
    )
    return _element_short(0x0002, 0x0000, "UL", _u32(len(group_body))) + group_body


_TAG_PATIENT_NAME = (0x0010, 0x0010)
_TAG_PATIENT_ID = (0x0010, 0x0020)
_TAG_PATIENT_BIRTH_DATE = (0x0010, 0x0030)
_TAG_OTHER_PATIENT_IDS_SEQ = (0x0010, 0x1002)

DEMO_PATIENT_ID = b"DEMO"

TOP_PATIENT_NAME = b"REGRESSION_TOPLEVEL_CONTROL^SYNTHETIC"
TOP_PATIENT_ID = b"REGRESSION-TOPLEVEL-CONTROL-ID"
TOP_PATIENT_BIRTH_DATE = b"20990101"  # impossible/obviously-fake future date

NESTED_PATIENT_NAME_CANARY = b"REGRESSION_NESTED_NAME_DO_NOT_PERSIST"
NESTED_PATIENT_ID_CANARY = b"REGRESSION_NESTED_ID_DO_NOT_PERSIST"
NESTED_PATIENT_BIRTH_DATE_CANARY = b"20990202"  # impossible/obviously-fake future date

IMPLICIT_VR_NESTED_CANARY = b"REGRESSION_IMPLICIT_VR_NESTED_DO_NOT_PERSIST"


def _build_explicit_vr_nested_fixture() -> bytes:
    """Explicit VR LE. Top-level controls for all three targeted tags, plus
    one sequence item nesting all three targeted tags with distinct
    canary values -- the smallest fixture that proves recursion, not just
    absence of a regression in the (already-covered) top-level case.
    """
    ts_uid = b"1.2.840.10008.1.2.1"
    nested = (
        _element_short(*_TAG_PATIENT_NAME, "PN", NESTED_PATIENT_NAME_CANARY)
        + _element_short(*_TAG_PATIENT_ID, "LO", NESTED_PATIENT_ID_CANARY)
        + _element_short(*_TAG_PATIENT_BIRTH_DATE, "DA", NESTED_PATIENT_BIRTH_DATE_CANARY)
    )
    other_patient_ids_seq = _element_sq(*_TAG_OTHER_PATIENT_IDS_SEQ, _dicom_item(nested))

    dataset = b""
    dataset += _element_short(0x0008, 0x0060, "CS", b"OT")
    dataset += _element_short(*_TAG_PATIENT_NAME, "PN", TOP_PATIENT_NAME)
    dataset += _element_short(*_TAG_PATIENT_ID, "LO", TOP_PATIENT_ID)
    dataset += _element_short(*_TAG_PATIENT_BIRTH_DATE, "DA", TOP_PATIENT_BIRTH_DATE)
    dataset += other_patient_ids_seq
    dataset += _element_long(0x7FE0, 0x0010, "OW", bytes(range(256)))
    return b"\x00" * 128 + b"DICM" + _file_meta(ts_uid) + dataset


def _build_implicit_vr_defined_length_sequence_fixture() -> bytes:
    """Implicit VR LE. Top-level PatientName present (the mutation trigger
    that, before this fix, made is_modified() true and let write_bytes()
    proceed past the Unsupported/500 path). Other Patient IDs Sequence
    (0010,1002) is encoded with Implicit VR's tag+length-only header at a
    *defined* length equal to a hand-built Item's size -- per
    fastDICOMstructure's documented Implicit-VR heuristic, this parses as
    one opaque, unexpanded value.
    """
    ts_uid = b"1.2.840.10008.1.2"
    nested_patient_id = _element_implicit(*_TAG_PATIENT_ID, IMPLICIT_VR_NESTED_CANARY)
    wrapped_item = _dicom_item(nested_patient_id)
    other_patient_ids_seq = _element_implicit(*_TAG_OTHER_PATIENT_IDS_SEQ, wrapped_item)

    dataset = b""
    dataset += _element_implicit(0x0008, 0x0060, b"OT")
    dataset += _element_implicit(*_TAG_PATIENT_NAME, TOP_PATIENT_NAME)
    dataset += other_patient_ids_seq
    return b"\x00" * 128 + b"DICM" + _file_meta(ts_uid) + dataset


# ---------------------------------------------------------------------------
# 1. Explicit VR nested PatientName/PatientID/PatientBirthDate
# ---------------------------------------------------------------------------


def test_nested_patient_tags_are_recursively_transformed_explicit_vr():
    source = _build_explicit_vr_nested_fixture()
    result = transform.process(source)

    # Top-level controls transform correctly (unchanged behavior).
    assert TOP_PATIENT_NAME not in result.output_bytes
    assert TOP_PATIENT_ID not in result.output_bytes
    assert TOP_PATIENT_BIRTH_DATE not in result.output_bytes
    assert DEMO_PATIENT_ID in result.output_bytes

    # The nested occurrences no longer survive as literal bytes.
    assert NESTED_PATIENT_NAME_CANARY not in result.output_bytes
    assert NESTED_PATIENT_ID_CANARY not in result.output_bytes
    assert NESTED_PATIENT_BIRTH_DATE_CANARY not in result.output_bytes

    # Structural confirmation: the sequence is still present (it isn't a
    # targeted tag itself and Pixel Data/"everything else" is preserved),
    # but its item's nested PatientID is now DEMO and PatientName/
    # PatientBirthDate are gone from it.
    structure = fds.read_buffer(result.output_bytes, fidelity="lossless")
    try:
        seq = structure.get(_TAG_OTHER_PATIENT_IDS_SEQ)
        assert seq is not None
        assert seq.is_sequence
        items = list(seq.items())
        assert len(items) == 1
        nested_tags = {}
        for element in items[0]:
            nested_tags[element.tag] = element.value
        assert _TAG_PATIENT_NAME not in nested_tags
        assert _TAG_PATIENT_BIRTH_DATE not in nested_tags
        assert nested_tags[_TAG_PATIENT_ID].rstrip(b"\x00") == DEMO_PATIENT_ID
    finally:
        structure.close()


def test_nested_patient_id_replacement_is_independently_confirmed_by_pydicom():
    pydicom = pytest.importorskip("pydicom")
    source = _build_explicit_vr_nested_fixture()
    result = transform.process(source)

    dataset = pydicom.dcmread(io.BytesIO(result.output_bytes))
    other_ids_seq = dataset.get(_TAG_OTHER_PATIENT_IDS_SEQ)
    assert other_ids_seq is not None
    nested_patient_ids = [
        str(item.PatientID) for item in other_ids_seq.value if "PatientID" in item
    ]
    assert nested_patient_ids == [DEMO_PATIENT_ID.decode()]
    assert NESTED_PATIENT_ID_CANARY.decode() not in nested_patient_ids
    for item in other_ids_seq.value:
        assert "PatientName" not in item
        assert "PatientBirthDate" not in item


# ---------------------------------------------------------------------------
# 2. Implicit VR: fail-closed acceptance scope
# ---------------------------------------------------------------------------


def test_implicit_vr_defined_length_sequence_is_rejected():
    source = _build_implicit_vr_defined_length_sequence_fixture()
    with pytest.raises(transform.RejectedInput) as excinfo:
        transform.process(source)
    assert excinfo.value.rejection.reason == "unsupported_transfer_syntax"


def test_implicit_vr_rejection_produces_no_transform_result():
    source = _build_implicit_vr_defined_length_sequence_fixture()
    result = None
    try:
        result = transform.process(source)
    except transform.RejectedInput:
        pass
    assert result is None


def test_implicit_vr_rejection_does_not_expose_the_nested_canary():
    source = _build_implicit_vr_defined_length_sequence_fixture()
    with pytest.raises(transform.RejectedInput):
        transform.process(source)
    # RejectedInput itself must never carry DICOM content -- consistent
    # with Rejection's own "never carries DICOM element values" contract.
    try:
        transform.process(source)
    except transform.RejectedInput as rejection:
        assert IMPLICIT_VR_NESTED_CANARY.decode() not in str(rejection)
        assert IMPLICIT_VR_NESTED_CANARY.decode() not in repr(rejection.rejection)


def test_dicom_endpoint_rejects_implicit_vr_before_any_output():
    response = client.post(
        "/dicom",
        content=_build_implicit_vr_defined_length_sequence_fixture(),
        headers={"Content-Type": "application/dicom"},
    )
    assert 400 <= response.status_code < 500
    assert response.headers["content-type"] != app_module.DICOM_MEDIA_TYPE
    assert IMPLICIT_VR_NESTED_CANARY.decode() not in response.text
    assert TOP_PATIENT_NAME.decode() not in response.text


def test_dicom_store_endpoint_never_attempts_persistence_for_implicit_vr(monkeypatch):
    """The important invariant this whole task is about: if the gateway
    has not demonstrated sufficient structural visibility to apply its
    stated policy, the object is not eligible for persistence -- so
    sink.store() (the only intentional durable-persistence call in this
    codebase) must never even be invoked."""
    calls = []
    monkeypatch.setattr(sink, "store", lambda *a, **k: calls.append((a, k)))

    response = client.post(
        "/dicom/store",
        content=_build_implicit_vr_defined_length_sequence_fixture(),
        headers={"Content-Type": "application/dicom"},
    )
    assert 400 <= response.status_code < 500
    assert response.json()["status"] == "rejected"
    assert response.json()["reason"] == "unsupported_transfer_syntax"
    assert calls == []  # sink.store() never called
    assert IMPLICIT_VR_NESTED_CANARY.decode() not in response.text


# ---------------------------------------------------------------------------
# Explicit VR (the accepted scope) must still be servable end to end,
# including through /dicom/store -- the acceptance gate must not reject
# input the fixed policy actually knows how to handle.
# ---------------------------------------------------------------------------


def _build_explicit_vr_nested_fixture_with_uids() -> bytes:
    """Same as _build_explicit_vr_nested_fixture(), plus Study/Series/
    SOPInstanceUID, since /dicom/store additionally requires those (an
    unrelated, pre-existing requirement -- see app.py's
    missing_required_uids check) to have anything to submit a receipt for.
    """
    ts_uid = b"1.2.840.10008.1.2.1"
    nested = (
        _element_short(*_TAG_PATIENT_NAME, "PN", NESTED_PATIENT_NAME_CANARY)
        + _element_short(*_TAG_PATIENT_ID, "LO", NESTED_PATIENT_ID_CANARY)
        + _element_short(*_TAG_PATIENT_BIRTH_DATE, "DA", NESTED_PATIENT_BIRTH_DATE_CANARY)
    )
    other_patient_ids_seq = _element_sq(*_TAG_OTHER_PATIENT_IDS_SEQ, _dicom_item(nested))

    sop_instance_uid = b"1.2.826.0.1.3680043.8.498.90000000000003"
    dataset = b""
    dataset += _element_short(0x0008, 0x0018, "UI", sop_instance_uid)  # SOPInstanceUID
    dataset += _element_short(0x0008, 0x0060, "CS", b"OT")
    dataset += _element_short(*_TAG_PATIENT_NAME, "PN", TOP_PATIENT_NAME)
    dataset += _element_short(*_TAG_PATIENT_ID, "LO", TOP_PATIENT_ID)
    dataset += _element_short(*_TAG_PATIENT_BIRTH_DATE, "DA", TOP_PATIENT_BIRTH_DATE)
    dataset += _element_short(0x0020, 0x000D, "UI", b"1.2.826.0.1.3680043.8.498.90000000000001")
    dataset += _element_short(0x0020, 0x000E, "UI", b"1.2.826.0.1.3680043.8.498.90000000000002")
    dataset += other_patient_ids_seq
    dataset += _element_long(0x7FE0, 0x0010, "OW", bytes(range(256)))
    return b"\x00" * 128 + b"DICM" + _file_meta(ts_uid, sop_instance_uid) + dataset


def test_explicit_vr_nested_fixture_is_still_accepted_and_stored(monkeypatch):
    """Confirms the new acceptance gate is Implicit-VR-specific: it must
    not reject the Explicit VR input the fixed policy actually knows how
    to fully transform, including one exercising the same nested-sequence
    shape as the other tests in this file."""
    captured = {}

    def _fake_store(dicom_bytes, **kwargs):
        captured["bytes"] = dicom_bytes
        return sink.StoreResult(**kwargs)

    monkeypatch.setattr(sink, "store", _fake_store)
    source = _build_explicit_vr_nested_fixture_with_uids()

    response = client.post(
        "/dicom/store", content=source, headers={"Content-Type": "application/dicom"},
    )
    assert response.status_code == 200
    assert response.json()["status"] == "stored"
    assert "bytes" in captured
    assert NESTED_PATIENT_ID_CANARY not in captured["bytes"]
    assert DEMO_PATIENT_ID in captured["bytes"]
