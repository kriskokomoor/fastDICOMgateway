"""Adversarial Validation A -- isolated, publication-validation-specific
experiment. NOT part of the M1-M5 milestone suites and NOT collected by
pytest (filename deliberately does not match pytest's default `test_*.py`
discovery pattern).

Purpose: determine experimentally, by calling the actual production entry
point `fastdicom_gateway.transform.process()`, whether two behaviors
identified by source inspection in ADVERSARIAL_CLAIM_CLOSURE.md's Question A
actually occur end to end:

  1. A fixed-policy patient tag nested inside a standard Explicit-VR
     sequence item survives the fixed policy (top-level-only erase/set_value
     hypothesis).
  2. Under Implicit VR, a defined-length nested sequence is parsed as an
     opaque value with no diagnostic, and -- if an unrelated top-level
     mutation makes the structure "modified" -- the opaque, canary-bearing
     blob survives serialization into Explicit-VR output.

This script does not modify fastDICOMstructure, transform.py, sink.py,
app.py, or any existing fixture/test file. All DICOM byte encoding here is
self-contained and duplicated on purpose (rather than importing
tests/conftest.py or validation/fixtures.py) so this experiment has zero
coupling to, and cannot accidentally perturb, the frozen milestone code.

Usage:
    .venv/bin/python publication_validation/adversarial_validation_a.py
"""

from __future__ import annotations

import hashlib
import io
import json
import struct
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_REPO_ROOT / "src"))

from fastdicom_gateway import transform  # the actual production entry point
from fastdicom_gateway.transform import fds  # only for read-only inspection of results

# ---------------------------------------------------------------------------
# Self-contained low-level DICOM byte encoders (deliberately duplicated from,
# not imported from, tests/conftest.py / validation/fixtures.py -- see module
# docstring).
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
    """Explicit VR Sequence element, defined length."""
    return _tag(group, element) + b"SQ" + b"\x00\x00" + _u32(len(item_bytes)) + item_bytes


def _dicom_item(content: bytes) -> bytes:
    """Item (FFFE,E000), defined length."""
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


# ---------------------------------------------------------------------------
# Constants -- unmistakably synthetic, unique to this experiment.
# ---------------------------------------------------------------------------

TOP_PATIENT_NAME = b"VALIDATION_A_TOPLEVEL_CONTROL^SYNTHETIC"
TOP_PATIENT_ID = b"VALIDATION-A-TOPLEVEL-CONTROL-ID"
TOP_PATIENT_BIRTH_DATE = b"20990101"  # impossible/obviously-fake future date
PRIVATE_VALUE = b"VALIDATION_A_PRIVATE_CONTROL"

NESTED_CANARY_V1 = b"NESTED_SQ_CANARY_VALIDATION_A1_DO_NOT_PERSIST"
NESTED_CANARY_V2 = b"NESTED_SQ_CANARY_VALIDATION_A2_DO_NOT_PERSIST"

_TAG_PATIENT_NAME = (0x0010, 0x0010)
_TAG_PATIENT_ID = (0x0010, 0x0020)
_TAG_PATIENT_BIRTH_DATE = (0x0010, 0x0030)
_TAG_OTHER_PATIENT_IDS_SEQ = (0x0010, 0x1002)
_TAG_PRIVATE = (0x0019, 0x0010)

DEMO_PATIENT_ID = b"DEMO"


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


# ---------------------------------------------------------------------------
# Fixture construction
# ---------------------------------------------------------------------------


def build_validation1_fixture() -> bytes:
    """Explicit VR Little Endian.

    Top-level PatientName / PatientID / PatientBirthDate / one private
    element are present as *controls* -- the fixed policy must remove/
    replace all of them for this experiment to be meaningful at all.
    Additionally carries a standard `Other Patient IDs Sequence (0010,1002)`
    with one item containing a nested `PatientID (0010,0020)` set to a
    distinctive canary distinct from the top-level control value.
    """
    ts_uid = b"1.2.840.10008.1.2.1"  # Explicit VR Little Endian
    nested_patient_id = _element_short(*_TAG_PATIENT_ID, "LO", NESTED_CANARY_V1)
    other_patient_ids_seq = _element_sq(*_TAG_OTHER_PATIENT_IDS_SEQ, _dicom_item(nested_patient_id))

    dataset = b""
    dataset += _element_short(0x0008, 0x0060, "CS", b"OT")  # Modality
    dataset += _element_short(*_TAG_PATIENT_NAME, "PN", TOP_PATIENT_NAME)
    dataset += _element_short(*_TAG_PATIENT_ID, "LO", TOP_PATIENT_ID)
    dataset += _element_short(*_TAG_PATIENT_BIRTH_DATE, "DA", TOP_PATIENT_BIRTH_DATE)
    dataset += _element_short(*_TAG_PRIVATE, "LO", PRIVATE_VALUE)
    dataset += other_patient_ids_seq
    dataset += _element_long(0x7FE0, 0x0010, "OW", bytes(range(256)))  # Pixel Data
    return b"\x00" * 128 + b"DICM" + _file_meta(ts_uid) + dataset


def build_validation2_fixture() -> bytes:
    """Implicit VR Little Endian.

    Top-level PatientName is present as the *mutation trigger* (condition
    B): the fixed policy erases it, which is what makes
    `Structure.is_modified()` true and lets `write_bytes()` proceed past
    the Unsupported/500 path an *unmodified* Implicit-VR structure hits.

    `Other Patient IDs Sequence (0010,1002)` is encoded with Implicit VR's
    tag+length-only header, where the declared length equals the size of a
    hand-built Item (FFFE,E000 + length + nested-element-bytes) -- i.e. a
    *defined-length* nested sequence, per fastDICOMstructure's documented
    Implicit-VR heuristic (docs/roundtrip-contract.md, "Implicit VR Little
    Endian"): undefined length would be inferred as SQ; defined length
    cannot be told apart from a large opaque value without a dictionary, so
    it should be parsed as one opaque `VR::Unknown` value instead.
    """
    ts_uid = b"1.2.840.10008.1.2"  # Implicit VR Little Endian
    nested_patient_id = _element_implicit(*_TAG_PATIENT_ID, NESTED_CANARY_V2)
    wrapped_item = _dicom_item(nested_patient_id)  # defined-length Item
    other_patient_ids_seq = _element_implicit(*_TAG_OTHER_PATIENT_IDS_SEQ, wrapped_item)

    dataset = b""
    dataset += _element_implicit(0x0008, 0x0060, b"OT")  # Modality
    dataset += _element_implicit(*_TAG_PATIENT_NAME, TOP_PATIENT_NAME)  # mutation trigger
    dataset += other_patient_ids_seq
    return b"\x00" * 128 + b"DICM" + _file_meta(ts_uid) + dataset


# ---------------------------------------------------------------------------
# Validation 1
# ---------------------------------------------------------------------------


def run_validation1() -> dict:
    record: dict = {"validation": 1, "transfer_syntax": "Explicit VR Little Endian",
                     "nested_canary": NESTED_CANARY_V1.decode()}
    source_bytes = build_validation1_fixture()
    record["source_bytes_len"] = len(source_bytes)
    record["source_bytes_sha256"] = _sha256(source_bytes)

    # --- Pre-transform structural observation (read-only; not a substitute
    #     for the gateway path -- transform.process() is exercised below).
    pre_structure = fds.read_buffer(source_bytes, fidelity="lossless")
    try:
        seq_element = pre_structure.get(_TAG_OTHER_PATIENT_IDS_SEQ)
        record["pretransform_sequence_found"] = seq_element is not None
        record["pretransform_sequence_is_sequence"] = bool(seq_element and seq_element.is_sequence)
        nested_values = []
        if seq_element is not None and seq_element.is_sequence:
            for item in seq_element.items():
                for element in item:
                    if element.tag == _TAG_PATIENT_ID:
                        nested_values.append(element.value.rstrip(b"\x00").decode("ascii", "replace"))
        record["pretransform_nested_patient_id_values"] = nested_values
        record["pretransform_nested_canary_visible_as_structured_element"] = (
            NESTED_CANARY_V1.decode() in nested_values
        )
    finally:
        pre_structure.close()

    # --- The actual production entry point.
    process_error = None
    result = None
    try:
        result = transform.process(source_bytes)
    except Exception as error:  # noqa: BLE001 -- we want to observe and report, not hide, any failure
        process_error = repr(error)
    record["transform_process_succeeded"] = result is not None
    record["transform_process_error"] = process_error

    if result is not None:
        output_bytes = result.output_bytes
        record["output_bytes_len"] = len(output_bytes)
        record["output_bytes_sha256"] = _sha256(output_bytes)
        record["elements_touched"] = result.elements_touched
        record["private_elements_removed"] = result.private_elements_removed

        # Literal-byte survival check on the raw output (independent of any
        # parser's interpretation).
        record["top_patient_name_absent_from_output_bytes"] = TOP_PATIENT_NAME not in output_bytes
        record["top_patient_id_absent_from_output_bytes"] = TOP_PATIENT_ID not in output_bytes
        record["top_patient_birth_date_absent_from_output_bytes"] = (
            TOP_PATIENT_BIRTH_DATE not in output_bytes
        )
        record["demo_patient_id_present_in_output_bytes"] = DEMO_PATIENT_ID in output_bytes
        record["nested_canary_present_in_output_bytes"] = NESTED_CANARY_V1 in output_bytes

        # fastDICOMstructure re-inspection of the output.
        post_structure = fds.read_buffer(output_bytes, fidelity="lossless")
        try:
            blocking = [d for d in post_structure.diagnostics if d.severity != "info"]
            record["output_reparses_with_fds_cleanly"] = blocking == []
            top_patient_id_el = post_structure.get(_TAG_PATIENT_ID)
            record["output_top_level_patient_id_value"] = (
                top_patient_id_el.value.rstrip(b"\x00").decode("ascii", "replace")
                if top_patient_id_el is not None else None
            )
            seq_el = post_structure.get(_TAG_OTHER_PATIENT_IDS_SEQ)
            record["output_sequence_still_present"] = seq_el is not None
            nested_after = []
            if seq_el is not None and seq_el.is_sequence:
                for item in seq_el.items():
                    for element in item:
                        if element.tag == _TAG_PATIENT_ID:
                            nested_after.append(element.value.rstrip(b"\x00").decode("ascii", "replace"))
            record["output_nested_patient_id_values"] = nested_after
            record["output_nested_canary_survives_structurally"] = (
                NESTED_CANARY_V1.decode() in nested_after
            )
        finally:
            post_structure.close()

        # Independent pydicom re-inspection.
        try:
            import pydicom
            dataset = pydicom.dcmread(io.BytesIO(output_bytes))
            record["pydicom_parses_output"] = True
            record["pydicom_top_level_patient_name_present"] = "PatientName" in dataset
            record["pydicom_top_level_patient_id"] = str(dataset.get("PatientID", ""))
            other_ids_seq = dataset.get((0x0010, 0x1002))
            nested_pydicom = []
            if other_ids_seq is not None:
                for seq_item in other_ids_seq.value:
                    if "PatientID" in seq_item:
                        nested_pydicom.append(str(seq_item.PatientID))
            record["pydicom_nested_patient_id_values"] = nested_pydicom
            record["pydicom_nested_canary_survives"] = NESTED_CANARY_V1.decode() in nested_pydicom
        except Exception as error:  # noqa: BLE001
            record["pydicom_parses_output"] = False
            record["pydicom_error"] = repr(error)

    return record


# ---------------------------------------------------------------------------
# Validation 2
# ---------------------------------------------------------------------------


def run_validation2() -> dict:
    record: dict = {"validation": 2, "transfer_syntax": "Implicit VR Little Endian",
                     "nested_canary": NESTED_CANARY_V2.decode()}
    source_bytes = build_validation2_fixture()
    record["source_bytes_len"] = len(source_bytes)
    record["source_bytes_sha256"] = _sha256(source_bytes)

    # --- Pre-transform structural observation.
    pre_structure = fds.read_buffer(source_bytes, fidelity="lossless")
    try:
        diagnostics = [
            {"severity": d.severity, "message": d.message, "tag": d.tag}
            for d in pre_structure.diagnostics
        ]
        record["pretransform_diagnostics"] = diagnostics
        record["pretransform_blocking_diagnostic_count"] = len(
            [d for d in pre_structure.diagnostics if d.severity != "info"]
        )
        seq_element = pre_structure.get(_TAG_OTHER_PATIENT_IDS_SEQ)
        record["pretransform_sequence_element_found"] = seq_element is not None
        record["pretransform_sequence_is_sequence"] = bool(seq_element and seq_element.is_sequence)
        record["pretransform_sequence_vr"] = seq_element.vr if seq_element is not None else None
        opaque_value = None
        if seq_element is not None and not seq_element.is_sequence:
            opaque_value = seq_element.value
        record["pretransform_opaque_value_len"] = len(opaque_value) if opaque_value else None
        record["pretransform_canary_present_in_opaque_value"] = (
            bool(opaque_value) and NESTED_CANARY_V2 in opaque_value
        )
        record["pretransform_is_modified"] = pre_structure.is_modified
    finally:
        pre_structure.close()

    # --- The actual production entry point.
    process_error = None
    result = None
    try:
        result = transform.process(source_bytes)
    except Exception as error:  # noqa: BLE001
        process_error = repr(error)
    record["transform_process_succeeded"] = result is not None
    record["transform_process_error"] = process_error

    if result is not None:
        output_bytes = result.output_bytes
        record["output_bytes_len"] = len(output_bytes)
        record["output_bytes_sha256"] = _sha256(output_bytes)
        record["elements_touched"] = result.elements_touched
        record["private_elements_removed"] = result.private_elements_removed

        record["top_patient_name_absent_from_output_bytes"] = TOP_PATIENT_NAME not in output_bytes
        record["nested_canary_present_in_output_bytes"] = NESTED_CANARY_V2 in output_bytes

        post_structure = fds.read_buffer(output_bytes, fidelity="lossless")
        try:
            blocking = [d for d in post_structure.diagnostics if d.severity != "info"]
            record["output_reparses_with_fds_cleanly"] = blocking == []
            record["output_transfer_syntax_uid"] = post_structure.transfer_syntax_uid
            seq_el = post_structure.get(_TAG_OTHER_PATIENT_IDS_SEQ)
            record["output_sequence_element_found"] = seq_el is not None
            record["output_sequence_is_sequence"] = bool(seq_el and seq_el.is_sequence)
            record["output_sequence_vr"] = seq_el.vr if seq_el is not None else None
        finally:
            post_structure.close()

        try:
            import pydicom
            dataset = pydicom.dcmread(io.BytesIO(output_bytes))
            record["pydicom_parses_output"] = True
            record["pydicom_top_level_patient_name_present"] = "PatientName" in dataset
            other_ids_el = dataset.get((0x0010, 0x1002))
            record["pydicom_other_patient_ids_element_present"] = other_ids_el is not None
            record["pydicom_other_patient_ids_vr"] = other_ids_el.VR if other_ids_el is not None else None
            nested_pydicom = []
            pydicom_raw_contains_canary = False
            if other_ids_el is not None:
                if other_ids_el.VR == "SQ":
                    for seq_item in other_ids_el.value:
                        if "PatientID" in seq_item:
                            nested_pydicom.append(str(seq_item.PatientID))
                else:
                    raw = other_ids_el.value
                    raw_bytes = bytes(raw) if not isinstance(raw, (bytes, bytearray)) else raw
                    pydicom_raw_contains_canary = NESTED_CANARY_V2 in raw_bytes
            record["pydicom_nested_patient_id_values_if_expanded_as_sq"] = nested_pydicom
            record["pydicom_raw_opaque_value_contains_canary"] = pydicom_raw_contains_canary
            record["pydicom_canary_observed_by_any_means"] = (
                (NESTED_CANARY_V2.decode() in nested_pydicom) or pydicom_raw_contains_canary
            )
        except Exception as error:  # noqa: BLE001
            record["pydicom_parses_output"] = False
            record["pydicom_error"] = repr(error)

    return record


def main() -> int:
    results = {
        "gateway_commit": _git_rev(_REPO_ROOT),
        "structure_commit": _git_rev(_REPO_ROOT.parent / "fastDICOMstructure"),
        "validation1": run_validation1(),
        "validation2": run_validation2(),
    }
    out_path = Path(__file__).resolve().parent / "adversarial_validation_a_results.json"
    out_path.write_text(json.dumps(results, indent=2, default=str))
    print(json.dumps(results, indent=2, default=str))
    print(f"\nWrote {out_path}")
    return 0


def _git_rev(repo: Path) -> str:
    import subprocess
    try:
        return subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=repo, capture_output=True, text=True, check=True,
        ).stdout.strip()
    except Exception:  # noqa: BLE001
        return "unknown"


if __name__ == "__main__":
    sys.exit(main())
