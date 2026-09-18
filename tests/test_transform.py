"""Unit tests against fastdicom_gateway.transform directly (no HTTP layer).

Covers M1-AC2 through M1-AC6 at the transform-pipeline level. See
test_app.py for the HTTP-level counterparts (M1-AC1, M1-AC6, M1-AC7,
M1-AC8).
"""

from __future__ import annotations

import pytest

from conftest import (
    DEMO_PATIENT_ID,
    MALFORMED_INPUT,
    PATIENT_BIRTH_DATE,
    PATIENT_ID,
    PATIENT_NAME,
    PIXEL_DATA,
    PRIVATE_TAG,
    build_valid_dicom,
)
from fastdicom_gateway import transform
from fastdicom_gateway.transform import fds

_TAG_PATIENT_NAME = (0x0010, 0x0010)
_TAG_PATIENT_ID = (0x0010, 0x0020)
_TAG_PATIENT_BIRTH_DATE = (0x0010, 0x0030)


def test_valid_input_is_transformed(valid_dicom_bytes: bytes) -> None:
    result = transform.process(valid_dicom_bytes)
    assert result.output_bytes
    assert result.input_byte_count == len(valid_dicom_bytes)
    assert result.output_byte_count == len(result.output_bytes)


def test_policy_removes_patient_name_and_birth_date_replaces_patient_id(
    valid_dicom_bytes: bytes,
) -> None:
    result = transform.process(valid_dicom_bytes)

    structure = fds.read_buffer(result.output_bytes, fidelity="lossless")
    try:
        assert _TAG_PATIENT_NAME not in structure
        assert _TAG_PATIENT_BIRTH_DATE not in structure

        patient_id = structure.get(_TAG_PATIENT_ID)
        assert patient_id is not None
        assert patient_id.value == DEMO_PATIENT_ID
        assert patient_id.value != PATIENT_ID
    finally:
        structure.close()

    # And the original values are nowhere in the output bytes at all.
    assert PATIENT_NAME not in result.output_bytes
    assert PATIENT_ID not in result.output_bytes
    assert PATIENT_BIRTH_DATE not in result.output_bytes


def test_private_element_is_removed(valid_dicom_bytes: bytes) -> None:
    result = transform.process(valid_dicom_bytes)
    assert result.private_elements_removed >= 1

    structure = fds.read_buffer(result.output_bytes, fidelity="lossless")
    try:
        assert PRIVATE_TAG not in structure
    finally:
        structure.close()


def test_pixel_data_is_preserved_byte_identical(valid_dicom_bytes: bytes) -> None:
    """fastDICOMstructure deliberately never exposes Pixel Data bytes
    through Structure.get()/iteration at all -- not even read-only -- so
    there is no in-library way to fetch the value for this assertion (see
    docs/architecture.md section 8 in fastDICOMstructure:
    PixelDataReference holds only a source byte range, never an allocated
    Value, and pixel_data_kind() is the only pixel-related accessor the
    Python binding exposes). pydicom's `.PixelData` attribute is used here
    purely as an independent, raw-bytes reader of the *encoded* element (no
    numpy, no `.pixel_array`, no interpretation of the pixel content) --
    exactly the "additional independent verification using pydicom" the
    task allows, not a second implementation of the transform path.
    """
    pydicom = pytest.importorskip("pydicom")
    import io

    result = transform.process(valid_dicom_bytes)

    original = pydicom.dcmread(io.BytesIO(valid_dicom_bytes))
    transformed = pydicom.dcmread(io.BytesIO(result.output_bytes))
    assert transformed.PixelData == original.PixelData == PIXEL_DATA


def test_output_reparses_successfully(valid_dicom_bytes: bytes) -> None:
    """M1-AC5: the returned object can be reparsed using fastDICOMstructure
    itself. transform.process() already performs this as an internal
    self-check (_verify_output); this test additionally performs an
    independent reparse from the test's own perspective.
    """
    result = transform.process(valid_dicom_bytes)

    structure = fds.read_buffer(result.output_bytes, fidelity="lossless")
    try:
        blocking = [d for d in structure.diagnostics if d.severity != "info"]
        assert blocking == []
        assert len(structure) > 0
    finally:
        structure.close()


def test_output_reparses_with_pydicom() -> None:
    """Independent verification tool, per the task's explicit allowance:
    pydicom may be used in tests to independently verify output, but never
    as the gateway's implementation engine (see transform.py's docstring
    and README.md).
    """
    pydicom = pytest.importorskip("pydicom")
    import io

    result = transform.process(build_valid_dicom())
    dataset = pydicom.dcmread(io.BytesIO(result.output_bytes))
    assert "PatientName" not in dataset
    assert str(dataset.PatientID) == DEMO_PATIENT_ID.decode("ascii")
    assert "PatientBirthDate" not in dataset


def test_malformed_input_is_rejected() -> None:
    with pytest.raises(transform.RejectedInput) as excinfo:
        transform.process(MALFORMED_INPUT)
    assert excinfo.value.rejection.reason in ("parse_failed", "blocking_diagnostic")


def test_empty_input_is_rejected() -> None:
    with pytest.raises(transform.RejectedInput):
        transform.process(b"")


def test_absent_optional_tags_do_not_error() -> None:
    """PatientID/PatientBirthDate absent entirely: erase()/set_value()
    are no-ops (return False) rather than errors, per their documented
    behavior -- policy only acts on tags that are present.
    """
    data = build_valid_dicom(patient_id=None, patient_birth_date=None, include_private=False)
    result = transform.process(data)
    assert result.private_elements_removed == 0

    structure = fds.read_buffer(result.output_bytes, fidelity="lossless")
    try:
        assert _TAG_PATIENT_ID not in structure
        assert _TAG_PATIENT_BIRTH_DATE not in structure
    finally:
        structure.close()
