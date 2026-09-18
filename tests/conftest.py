"""Shared synthetic-fixture builders for the M1 test suite.

No real patient data is used anywhere. DICOM objects are hand-built with
the same low-level element-encoding approach
fastDICOMstructure/python/examples/pipeline_demo.py uses for its own
"untrusted input" -- not pydicom -- so the fixtures used to exercise the
gateway's production path never depend on a second DICOM implementation.
pydicom is used elsewhere in this test suite only as an independent
verification tool (see test_transform.py), never to build these fixtures.
"""

from __future__ import annotations

import struct

import pytest


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


# M1-AC2's exact values.
PATIENT_NAME = b"NEVER_PERSIST^KRIS"
PATIENT_ID = b"SECRET-123456789"
PATIENT_BIRTH_DATE = b"19610217"
DEMO_PATIENT_ID = b"DEMO"

PRIVATE_TAG = (0x0019, 0x0010)
PRIVATE_VALUE = b"Vendor-specific private data"

# Deterministic, non-trivial Pixel Data payload (2048 bytes, even length).
PIXEL_DATA = bytes(range(256)) * 8


def build_valid_dicom(
    *,
    patient_name: bytes | None = PATIENT_NAME,
    patient_id: bytes | None = PATIENT_ID,
    patient_birth_date: bytes | None = PATIENT_BIRTH_DATE,
    include_private: bool = True,
    pixel_data: bytes = PIXEL_DATA,
) -> bytes:
    """A synthetic Explicit VR Little Endian DICOM Part 10 object."""
    ts_uid = b"1.2.840.10008.1.2.1"  # Explicit VR Little Endian
    group_body = (
        _element_short(0x0002, 0x0002, "UI", b"1.2.840.10008.5.1.4.1.1.7")  # SOP Class UID
        + _element_short(0x0002, 0x0003, "UI", b"1.2.3.4.5.6.7.8")           # SOP Instance UID
        + _element_short(0x0002, 0x0010, "UI", ts_uid)
    )
    file_meta = _element_short(0x0002, 0x0000, "UL", _u32(len(group_body))) + group_body

    dataset = _element_short(0x0008, 0x0060, "CS", b"CT")  # Modality
    if patient_name is not None:
        dataset += _element_short(0x0010, 0x0010, "PN", patient_name)
    if patient_id is not None:
        dataset += _element_short(0x0010, 0x0020, "LO", patient_id)
    if patient_birth_date is not None:
        dataset += _element_short(0x0010, 0x0030, "DA", patient_birth_date)
    dataset += _element_short(0x0018, 0x0050, "DS", b"1.0")  # SliceThickness
    if include_private:
        dataset += _element_short(*PRIVATE_TAG, "LO", PRIVATE_VALUE)
    dataset += _element_long(0x7FE0, 0x0010, "OW", pixel_data)  # Pixel Data

    return b"\x00" * 128 + b"DICM" + file_meta + dataset


MALFORMED_INPUT = b"NOT A DICOM FILE" * 8


@pytest.fixture
def valid_dicom_bytes() -> bytes:
    return build_valid_dicom()
