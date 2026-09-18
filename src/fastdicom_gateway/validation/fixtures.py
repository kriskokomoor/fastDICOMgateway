"""Deterministic, synthetic, canary-bearing DICOM fixtures for M2.

No real patient data anywhere. Elements are hand-encoded (same low-level
approach tests/conftest.py and fastDICOMstructure's own pipeline_demo.py
use) rather than built with pydicom, so these fixtures exercise the
gateway's production path without depending on a second DICOM
implementation.

Values intentionally match tests/conftest.py's M1-AC2 canaries (restated
here as independent data constants -- this harness deliberately does not
import from tests/, since it is a standalone subprocess-driving tool, not
a pytest fixture module).
"""

from __future__ import annotations

import struct

# ---------------------------------------------------------------------------
# Fixed synthetic source canaries -- the exact values M2 searches logs and
# artifacts for. Never derived from, or resembling, real patient data.
# ---------------------------------------------------------------------------

PATIENT_NAME = b"NEVER_PERSIST^KRIS"
PATIENT_ID = b"SECRET-123456789"
PATIENT_BIRTH_DATE = b"19610217"
DEMO_PATIENT_ID = b"DEMO"  # the fixed policy's replacement value

PRIVATE_TAG = (0x0019, 0x0010)
PRIVATE_VALUE = b"Vendor-specific private data"

PIXEL_DATA = bytes(range(256)) * 8  # deterministic, non-trivial, even length

FIXED_CANARIES = (PATIENT_NAME, PATIENT_ID, PATIENT_BIRTH_DATE)


def run_canary(run_id: str, scenario_id: str) -> bytes:
    """A per-run, per-scenario canary extremely unlikely to occur anywhere
    else, per M2 section 3. Placed in Institution Name (0008,0080) --
    untouched by the fixed policy (see README.md's policy table), so using
    it requires no production code change."""
    return f"M2_CANARY_{run_id}_{scenario_id}".encode("ascii")


# ---------------------------------------------------------------------------
# Low-level element encoding (Explicit VR LE and Implicit VR LE)
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


def _element_implicit(group: int, element: int, value: bytes) -> bytes:
    if len(value) % 2 != 0:
        value += b"\x00"
    return _tag(group, element) + _u32(len(value)) + value


def _file_meta(ts_uid: bytes, sop_instance_uid: bytes = b"1.2.3.4.5.6.7.8") -> bytes:
    group_body = (
        _element_short(0x0002, 0x0002, "UI", b"1.2.840.10008.5.1.4.1.1.7")  # SOP Class UID
        + _element_short(0x0002, 0x0003, "UI", sop_instance_uid)             # SOP Instance UID
        + _element_short(0x0002, 0x0010, "UI", ts_uid)
    )
    return _element_short(0x0002, 0x0000, "UL", _u32(len(group_body))) + group_body


# ---------------------------------------------------------------------------
# Scenario fixtures
# ---------------------------------------------------------------------------


def build_success_fixture(run_id: str, scenario_id: str = "A") -> bytes:
    """Scenario A: a valid Explicit VR LE object carrying all three fixed
    patient canaries, a private element, deterministic Pixel Data, and a
    per-run canary in an untouched tag."""
    ts_uid = b"1.2.840.10008.1.2.1"  # Explicit VR Little Endian
    dataset = _element_short(0x0008, 0x0060, "CS", b"CT")  # Modality
    dataset += _element_short(0x0008, 0x0080, "LO", run_canary(run_id, scenario_id))
    dataset += _element_short(0x0010, 0x0010, "PN", PATIENT_NAME)
    dataset += _element_short(0x0010, 0x0020, "LO", PATIENT_ID)
    dataset += _element_short(0x0010, 0x0030, "DA", PATIENT_BIRTH_DATE)
    dataset += _element_short(0x0018, 0x0050, "DS", b"1.0")  # SliceThickness
    dataset += _element_short(*PRIVATE_TAG, "LO", PRIVATE_VALUE)
    dataset += _element_long(0x7FE0, 0x0010, "OW", PIXEL_DATA)
    return b"\x00" * 128 + b"DICM" + _file_meta(ts_uid) + dataset


def build_malformed_fixture(run_id: str, scenario_id: str = "B") -> bytes:
    """Scenario B: bytes that are not DICOM at all (no preamble/DICM magic),
    with a per-run canary embedded as raw ASCII so a leak of *any* request
    content -- not just recognized DICOM element values -- would be caught."""
    canary = run_canary(run_id, scenario_id)
    return b"NOT A DICOM FILE " * 8 + canary


def build_rejected_fixture(run_id: str, scenario_id: str = "C") -> bytes:
    """Scenario C: syntactically DICOM-like (valid preamble/DICM magic and
    File Meta) but truncated mid-Pixel-Data, so the declared length exceeds
    the bytes actually present -- a different failure mode from Scenario
    B's not-DICOM-at-all input, even though both currently surface as
    reason='blocking_diagnostic'. fastDICOMstructure's structural parser
    reports the truncation as a non-info ('recoverable_error') diagnostic,
    distinguishable via diagnostic_severities. No production code was
    changed to manufacture this; it is the parser's existing, documented
    truncation handling (see fastDICOMstructure's test_malformed.cpp)."""
    full = build_success_fixture(run_id, scenario_id)
    return full[:-50]


def build_unmodified_implicit_vr_fixture(run_id: str, scenario_id: str = "D") -> bytes:
    """Scenario D: a *valid, unmodified* Implicit VR Little Endian object
    with none of the three targeted patient tags and no private elements,
    so the fixed policy never mutates it. Per fastDICOMstructure's
    round-trip contract (docs/roundtrip-contract.md "Implicit VR Little
    Endian" in that repo), write() on an unmodified Implicit-VR structure
    returns Unsupported -- transform.py surfaces this as an uncaught
    FdsError, and app.py's generic `except Exception` turns it into a 500.
    This is the exact gap README.md's "Known limitations" already
    documents; reusing it here requires no change to production code."""
    ts_uid = b"1.2.840.10008.1.2"  # Implicit VR Little Endian
    dataset = _element_implicit(0x0008, 0x0060, b"CT")  # Modality, untouched by policy
    dataset += _element_implicit(0x0008, 0x0080, run_canary(run_id, scenario_id))
    return b"\x00" * 128 + b"DICM" + _file_meta(ts_uid) + dataset


# ---------------------------------------------------------------------------
# M5: Healthcare API DICOM store fixture
# ---------------------------------------------------------------------------

# Fixed (not per-run) so repeated M5 validation runs submit the *same*
# instance -- deliberately, both to keep the durable store's retained byte
# volume from growing across re-runs (see M5's cost guardrails) and
# because a fixed SOPInstanceUID is what lets the duplicate-submission
# characterization (docs/M5_APPROVED_PERSISTENCE_VALIDATION.md "Duplicate
# submission behavior") mean anything -- submitting a fresh UID every time
# would never actually exercise that path. Structural identifiers only,
# assigned by this project's own fixture builder; not PHI.
M5_STUDY_INSTANCE_UID = b"1.2.826.0.1.3680043.8.498.10000000000001"
M5_SERIES_INSTANCE_UID = b"1.2.826.0.1.3680043.8.498.10000000000002"
M5_SOP_INSTANCE_UID = b"1.2.826.0.1.3680043.8.498.10000000000003"
M5_SOP_CLASS_UID = b"1.2.840.10008.5.1.4.1.1.7"  # Secondary Capture Image Storage

# 8x8, 8 bits/pixel, 1 sample -- small and simple enough to need no
# compression/photometric ambiguity, but non-trivial (not all-zero).
M5_PIXEL_DATA = bytes(range(64))
_M5_ROWS = 8
_M5_COLUMNS = 8


def build_healthcare_store_fixture(run_id: str, scenario_id: str = "M5") -> bytes:
    """M5's synthetic source object: all three fixed patient canaries, a
    private element, deterministic Pixel Data, a per-run canary in an
    untouched tag, and -- unlike the M2/M3/M4 fixtures above -- real
    Study/Series/SOPInstanceUIDs and a minimal Image Pixel module, both
    required for the Healthcare API's STOW-RS transaction to accept the
    instance as a conformant DICOM object."""
    ts_uid = b"1.2.840.10008.1.2.1"  # Explicit VR Little Endian
    dataset = _element_short(0x0008, 0x0016, "UI", M5_SOP_CLASS_UID)   # SOP Class UID
    dataset += _element_short(0x0008, 0x0018, "UI", M5_SOP_INSTANCE_UID)  # SOP Instance UID
    dataset += _element_short(0x0008, 0x0060, "CS", b"OT")  # Modality (Other)
    dataset += _element_short(0x0008, 0x0080, "LO", run_canary(run_id, scenario_id))
    dataset += _element_short(0x0010, 0x0010, "PN", PATIENT_NAME)
    dataset += _element_short(0x0010, 0x0020, "LO", PATIENT_ID)
    dataset += _element_short(0x0010, 0x0030, "DA", PATIENT_BIRTH_DATE)
    dataset += _element_short(0x0020, 0x000D, "UI", M5_STUDY_INSTANCE_UID)
    dataset += _element_short(0x0020, 0x000E, "UI", M5_SERIES_INSTANCE_UID)
    dataset += _element_short(0x0020, 0x0013, "IS", b"1")  # InstanceNumber
    dataset += _element_short(*PRIVATE_TAG, "LO", PRIVATE_VALUE)
    # Minimal Image Pixel module -- required for the Healthcare API to
    # accept a Secondary Capture instance carrying Pixel Data.
    dataset += _element_short(0x0028, 0x0002, "US", _u16(1))              # SamplesPerPixel
    dataset += _element_short(0x0028, 0x0004, "CS", b"MONOCHROME2")       # PhotometricInterpretation
    dataset += _element_short(0x0028, 0x0010, "US", _u16(_M5_ROWS))       # Rows
    dataset += _element_short(0x0028, 0x0011, "US", _u16(_M5_COLUMNS))    # Columns
    dataset += _element_short(0x0028, 0x0100, "US", _u16(8))              # BitsAllocated
    dataset += _element_short(0x0028, 0x0101, "US", _u16(8))              # BitsStored
    dataset += _element_short(0x0028, 0x0102, "US", _u16(7))              # HighBit
    dataset += _element_short(0x0028, 0x0103, "US", _u16(0))              # PixelRepresentation
    dataset += _element_long(0x7FE0, 0x0010, "OB", M5_PIXEL_DATA)         # Pixel Data
    return b"\x00" * 128 + b"DICM" + _file_meta(ts_uid, M5_SOP_INSTANCE_UID) + dataset
