"""Post-remediation publication evidence refresh -- see
POST_REMEDIATION_EVIDENCE_REFRESH.md for the full narrative.

This is NOT M6, NOT a new milestone, and NOT a general validation expansion.
It is a bounded confirmatory bridge between the frozen M1-M5 evidence (which
tested the pre-remediation gateway) and the corrected implementation
described in ADVERSARIAL_REMEDIATION_A.md.

Three pieces, run independently via this module's CLI:

  m2  -- re-runs the M2 bare-host boundary against the corrected four-
         scenario set (scenarios_publication_refresh.CORRECTED_SCENARIOS),
         reusing m2.py's own observation apparatus (strace, log/filesystem
         scanning) unchanged, via its additive `scenario_fns` parameter.
  m3  -- same, for the M3 read-only-container boundary via m3.py.
  m5  -- ONE bounded positive confirmation that the corrected gateway's
         transform -> STOW-RS -> WADO-RS path, exercised against the real
         Healthcare API DICOM store M5 already established, still submits
         and durably stores only the approved representation -- now using
         a fixture with a *nested* targeted PatientID (the case
         ADVERSARIAL_VALIDATION_A.md's Validation 1 found unprotected
         before remediation), not just top-level tags.

M4 is deliberately NOT implemented here -- see
POST_REMEDIATION_EVIDENCE_REFRESH.md section F for why (it would require
building and pushing a new container image and deploying a new Cloud Run
revision to a live service, which this task treats as requiring explicit
confirmation rather than silent execution).

The M5 piece runs the corrected application code (transform.process +
sink.store) in-process via FastAPI's TestClient rather than through a
redeployed Cloud Run revision -- the currently-deployed revision still runs
the pre-remediation code, so hitting it would not test the fix at all. This
means the M5 refresh below re-confirms the transform/store/retrieve
mechanics and the corrected policy's behavior through real durable storage;
it does not re-confirm Cloud Run-specific ingress-boundary properties
(those remain as characterized by the original, unmodified M4 evidence).
See POST_REMEDIATION_EVIDENCE_REFRESH.md section G for the full caveat,
including the identity-separation difference from the original M5 setup.

Evidence is written under docs/publication_refresh/ -- a new location,
clearly distinct from docs/m{2,3,4,5}_evidence/ (untouched).
"""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import os
import struct
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

from . import fixtures, m2, m3, m5
from . import scenarios as sc
from . import scenarios_publication_refresh as spr

_REPO_ROOT = Path(__file__).resolve().parents[3]
_EVIDENCE_DIR = _REPO_ROOT / "docs" / "publication_refresh"


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _wrap(milestone: str, boundary: str, relationship: str, evidence: dict) -> dict:
    """Adds the identifying/contextual envelope
    POST_REMEDIATION_EVIDENCE_REFRESH.md commits to for every new artifact,
    around whatever `evidence` the underlying harness already produced.
    """
    return {
        "kind": "post_remediation_confirmatory_evidence",
        "not_a_new_milestone": True,
        "milestone_refreshed": milestone,
        "boundary": boundary,
        "gateway_commit": evidence.get("gateway_commit") or m2._git_rev(_REPO_ROOT),
        "structure_commit": evidence.get("structure_commit") or m2._git_rev(m2._structure_repo_root()),
        "execution_timestamp_utc": _now_iso(),
        "relationship_to_original_milestone_evidence": relationship,
        "result": evidence,
    }


# ---------------------------------------------------------------------------
# M2 / M3 refresh -- reuse the frozen harnesses' own apparatus unchanged,
# with only the scenario set swapped for the corrected one.
# ---------------------------------------------------------------------------


def refresh_m2(*, use_strace: bool = True) -> dict:
    evidence = m2.run(use_strace=use_strace, scenario_fns=spr.CORRECTED_SCENARIOS)
    return _wrap(
        milestone="M2",
        boundary="bare host process (strace-observed)",
        relationship=(
            "Same observation apparatus (m2.py) and same Scenarios A/B/C as "
            "docs/M2_PERSISTENCE_BOUNDARY_VALIDATION.md's frozen evidence. "
            "Scenario D is replaced with the corrected acceptance-boundary "
            "check (scenarios_publication_refresh.run_scenario_d_corrected); "
            "the frozen M2 evidence and its Scenario D (expecting HTTP 500) "
            "are unchanged and remain historically accurate for the "
            "implementation they were generated against."
        ),
        evidence=evidence,
    )


def refresh_m3(*, image: str = "fastdicom-gateway:publication-refresh", build: bool = True,
               read_only: bool = True) -> dict:
    evidence = m3.run(image=image, build=build, read_only=read_only, scenario_fns=spr.CORRECTED_SCENARIOS)
    return _wrap(
        milestone="M3",
        boundary="read-only, non-root container (docker diff-observed)",
        relationship=(
            "Same observation apparatus (m3.py) and same Scenarios A/B/C as "
            "docs/M3_CONTAINER_VALIDATION.md's frozen evidence. Scenario D is "
            "replaced with the corrected acceptance-boundary check. The "
            "frozen M3 evidence and its Scenario D (expecting HTTP 500) are "
            "unchanged and remain historically accurate for the "
            "implementation they were generated against."
        ),
        evidence=evidence,
    )


# ---------------------------------------------------------------------------
# M5 positive refresh -- one bounded, real-Healthcare-API confirmation that
# the corrected recursive policy survives the durable-storage path,
# exercised in-process (see module docstring for why, not via a redeployed
# Cloud Run revision).
# ---------------------------------------------------------------------------

# Distinct from every UID used by the original M5 evidence
# (docs/M5_APPROVED_PERSISTENCE_VALIDATION.md's ...10000000000001/2/3) and
# from ADVERSARIAL_VALIDATION_A.md's fixtures -- this refresh's own instance
# must not collide with, overwrite, or be confused with either.
_REFRESH_STUDY_INSTANCE_UID = b"1.2.826.0.1.3680043.8.498.30000000000001"
_REFRESH_SERIES_INSTANCE_UID = b"1.2.826.0.1.3680043.8.498.30000000000002"
_REFRESH_SOP_INSTANCE_UID = b"1.2.826.0.1.3680043.8.498.30000000000003"
_REFRESH_SOP_CLASS_UID = b"1.2.840.10008.5.1.4.1.1.7"  # Secondary Capture Image Storage

# Top-level values reuse the exact established M2-M5 canaries on purpose:
# m5.py's own _structural_snapshot() (reused below, not reimplemented) has
# these specific values hardcoded into its presence checks
# (fixtures.PATIENT_NAME / PATIENT_ID / PATIENT_BIRTH_DATE / DEMO_PATIENT_ID)
# -- using different top-level values here would silently make those checks
# always read "absent" regardless of what actually happened. The genuinely
# new thing this refresh tests is the *nested* PatientID below, which gets
# its own distinct, unmistakably-synthetic value and its own dedicated
# (non-_structural_snapshot) verification.
_TOP_PATIENT_NAME = fixtures.PATIENT_NAME
_TOP_PATIENT_ID = fixtures.PATIENT_ID
_TOP_PATIENT_BIRTH_DATE = fixtures.PATIENT_BIRTH_DATE
_NESTED_PATIENT_ID_CANARY = b"REFRESH_NESTED_ID_DO_NOT_PERSIST"
_PRIVATE_VALUE = b"REFRESH_PRIVATE_CONTROL"
_PIXEL_DATA = bytes(range(64))
_ROWS = 8
_COLUMNS = 8


def _u16(v: int) -> bytes:
    return struct.pack("<H", v)


def _u32(v: int) -> bytes:
    return struct.pack("<I", v)


def _tag(g: int, e: int) -> bytes:
    return _u16(g) + _u16(e)


def _element_short(g: int, e: int, vr: str, value: bytes) -> bytes:
    if len(value) % 2 != 0:
        value += b"\x00"
    return _tag(g, e) + vr.encode("ascii") + _u16(len(value)) + value


def _element_long(g: int, e: int, vr: str, value: bytes) -> bytes:
    if len(value) % 2 != 0:
        value += b"\x00"
    return _tag(g, e) + vr.encode("ascii") + b"\x00\x00" + _u32(len(value)) + value


def _element_sq(g: int, e: int, item_bytes: bytes) -> bytes:
    return _tag(g, e) + b"SQ" + b"\x00\x00" + _u32(len(item_bytes)) + item_bytes


def _dicom_item(content: bytes) -> bytes:
    return _tag(0xFFFE, 0xE000) + _u32(len(content)) + content


def _file_meta(ts_uid: bytes, sop_instance_uid: bytes) -> bytes:
    group_body = (
        _element_short(0x0002, 0x0002, "UI", _REFRESH_SOP_CLASS_UID)
        + _element_short(0x0002, 0x0003, "UI", sop_instance_uid)
        + _element_short(0x0002, 0x0010, "UI", ts_uid)
    )
    return _element_short(0x0002, 0x0000, "UL", _u32(len(group_body))) + group_body


def build_m5_refresh_fixture() -> bytes:
    """Explicit VR LE. Same shape as M5's original
    build_healthcare_store_fixture (fixed UIDs, minimal Image Pixel module,
    a private element) plus one addition: a nested PatientID inside
    `Other Patient IDs Sequence (0010,1002)` -- the exact construct
    ADVERSARIAL_VALIDATION_A.md's Validation 1 found the pre-remediation
    policy left untouched. This is the important difference from a plain
    re-run of M5: it specifically exercises the *corrected* recursive
    policy, not just the original top-level-only one, through the real
    durable-storage path.
    """
    ts_uid = b"1.2.840.10008.1.2.1"
    nested_patient_id = _element_short(0x0010, 0x0020, "LO", _NESTED_PATIENT_ID_CANARY)
    other_patient_ids_seq = _element_sq(0x0010, 0x1002, _dicom_item(nested_patient_id))

    dataset = b""
    dataset += _element_short(0x0008, 0x0016, "UI", _REFRESH_SOP_CLASS_UID)
    dataset += _element_short(0x0008, 0x0018, "UI", _REFRESH_SOP_INSTANCE_UID)
    dataset += _element_short(0x0008, 0x0060, "CS", b"OT")
    dataset += _element_short(0x0010, 0x0010, "PN", _TOP_PATIENT_NAME)
    dataset += _element_short(0x0010, 0x0020, "LO", _TOP_PATIENT_ID)
    dataset += _element_short(0x0010, 0x0030, "DA", _TOP_PATIENT_BIRTH_DATE)
    dataset += _element_short(0x0020, 0x000D, "UI", _REFRESH_STUDY_INSTANCE_UID)
    dataset += _element_short(0x0020, 0x000E, "UI", _REFRESH_SERIES_INSTANCE_UID)
    dataset += _element_short(0x0020, 0x0013, "IS", b"1")
    dataset += _element_short(0x0019, 0x0010, "LO", _PRIVATE_VALUE)
    dataset += other_patient_ids_seq
    dataset += _element_short(0x0028, 0x0002, "US", _u16(1))
    dataset += _element_short(0x0028, 0x0004, "CS", b"MONOCHROME2")
    dataset += _element_short(0x0028, 0x0010, "US", _u16(_ROWS))
    dataset += _element_short(0x0028, 0x0011, "US", _u16(_COLUMNS))
    dataset += _element_short(0x0028, 0x0100, "US", _u16(8))
    dataset += _element_short(0x0028, 0x0101, "US", _u16(8))
    dataset += _element_short(0x0028, 0x0102, "US", _u16(7))
    dataset += _element_short(0x0028, 0x0103, "US", _u16(0))
    dataset += _element_long(0x7FE0, 0x0010, "OB", _PIXEL_DATA)
    return b"\x00" * 128 + b"DICM" + _file_meta(ts_uid, _REFRESH_SOP_INSTANCE_UID) + dataset


def refresh_m5_positive(*, project: str, region: str, dataset: str, dicom_store: str) -> dict:
    """Runs the corrected transform.process() -> sink.store() path
    in-process (FastAPI TestClient; see module docstring for why, not a
    redeployed Cloud Run revision), submits to the real Healthcare API
    DICOM store, then independently retrieves via WADO-RS using
    m5.py's own retrieve_instance()/_structural_snapshot() -- the same
    independent-oracle methodology the original M5 evidence used.
    """
    os.environ["FASTDICOM_HEALTHCARE_PROJECT"] = project
    os.environ["FASTDICOM_HEALTHCARE_LOCATION"] = region
    os.environ["FASTDICOM_HEALTHCARE_DATASET"] = dataset
    os.environ["FASTDICOM_HEALTHCARE_DICOM_STORE"] = dicom_store

    sys.path.insert(0, str(_REPO_ROOT / "src"))
    from fastapi.testclient import TestClient  # noqa: PLC0415

    from fastdicom_gateway import transform  # noqa: PLC0415
    from fastdicom_gateway.app import app  # noqa: PLC0415

    source_bytes = build_m5_refresh_fixture()
    source_snapshot = m5._structural_snapshot(source_bytes)

    # Same production transform.process() the gateway itself calls, computed
    # locally for the "transformed" comparison column -- not a second
    # implementation (identical to how m5.py's own run() does this).
    transform_result = transform.process(source_bytes)
    transformed_snapshot = m5._structural_snapshot(transform_result.output_bytes)

    client = TestClient(app)
    test_start_iso = _now_iso()
    response = client.post(
        "/dicom/store", content=source_bytes, headers={"Content-Type": "application/dicom"},
    )
    test_end_iso = _now_iso()

    result: dict = {
        "run_id": time.strftime("%Y%m%dT%H%M%SZ", time.gmtime()),
        "gateway_commit": m2._git_rev(_REPO_ROOT),
        "structure_commit": m2._git_rev(m2._structure_repo_root()),
        "project": project, "region": region, "healthcare_dataset": dataset, "dicom_store": dicom_store,
        "execution_mode": "in_process_testclient_not_cloud_run",
        "test_start_iso": test_start_iso,
        "test_end_iso": test_end_iso,
        "source_bytes": len(source_bytes),
        "transformed_bytes": len(transform_result.output_bytes),
        "store_http_status": response.status_code,
    }
    if response.status_code != 200:
        result["overall_result"] = "FAIL"
        result["error"] = f"POST /dicom/store returned {response.status_code}: {response.text[:200]!r}"
        return result

    receipt = response.json()
    result["receipt"] = receipt

    retrieved_bytes = m5.retrieve_instance(
        project, region, dataset, dicom_store,
        receipt["study_instance_uid"], receipt["series_instance_uid"], receipt["sop_instance_uid"],
    )
    retrieved_snapshot = m5._structural_snapshot(retrieved_bytes)

    # Independent pydicom inspection of the *nested* structure specifically
    # -- the property this refresh exists to establish, beyond what
    # _structural_snapshot()'s flat top-level checks already cover.
    import pydicom  # noqa: PLC0415
    retrieved_dataset = pydicom.dcmread(io.BytesIO(retrieved_bytes))
    other_ids_seq = retrieved_dataset.get((0x0010, 0x1002))
    nested_patient_ids_retrieved = (
        [str(item.PatientID) for item in other_ids_seq.value if "PatientID" in item]
        if other_ids_seq is not None else []
    )

    comparison = {
        "top_level_patient_name_present": {
            "source": source_snapshot["patient_name_present"],
            "transformed": transformed_snapshot["patient_name_present"],
            "retrieved": retrieved_snapshot["patient_name_present"],
        },
        "top_level_patient_id_is_demo": {
            "source": source_snapshot["patient_id_is_demo"],
            "transformed": transformed_snapshot["patient_id_is_demo"],
            "retrieved": retrieved_snapshot["patient_id_is_demo"],
        },
        "top_level_birth_date_present": {
            "source": source_snapshot["birth_date_present"],
            "transformed": transformed_snapshot["birth_date_present"],
            "retrieved": retrieved_snapshot["birth_date_present"],
        },
        "private_element_present": {
            "source": source_snapshot["private_element_present"],
            "transformed": transformed_snapshot["private_element_present"],
            "retrieved": retrieved_snapshot["private_element_present"],
        },
        "pixel_data_sha256": {
            "source": source_snapshot["pixel_data_sha256"],
            "transformed": transformed_snapshot["pixel_data_sha256"],
            "retrieved": retrieved_snapshot["pixel_data_sha256"],
        },
        "nested_patient_id_values_retrieved": nested_patient_ids_retrieved,
    }

    pixel_hash_match = (
        source_snapshot["pixel_data_sha256"] == transformed_snapshot["pixel_data_sha256"]
        == retrieved_snapshot["pixel_data_sha256"]
    )
    top_level_policy_correct = (
        source_snapshot["patient_name_present"] and not retrieved_snapshot["patient_name_present"]
        and not source_snapshot["patient_id_is_demo"] and retrieved_snapshot["patient_id_is_demo"]
        and source_snapshot["birth_date_present"] and not retrieved_snapshot["birth_date_present"]
        and source_snapshot["private_element_present"] and not retrieved_snapshot["private_element_present"]
    )
    nested_canary_absent_after_retrieval = (
        _NESTED_PATIENT_ID_CANARY.decode() not in nested_patient_ids_retrieved
    )
    nested_policy_correct = (
        nested_patient_ids_retrieved == ["DEMO"] and nested_canary_absent_after_retrieval
    )

    result.update({
        "source": source_snapshot,
        "transformed": transformed_snapshot,
        "retrieved": retrieved_snapshot,
        "comparison": comparison,
        "pixel_hash_match": pixel_hash_match,
        "top_level_policy_correctly_applied_end_to_end": top_level_policy_correct,
        "nested_policy_correctly_applied_end_to_end": nested_policy_correct,
        "nested_source_canary_absent_after_independent_retrieval": nested_canary_absent_after_retrieval,
        "source_canary_in_stored_object": (
            retrieved_snapshot["patient_name_present"]
            or retrieved_snapshot["patient_id_is_source_value"]
            or retrieved_snapshot["birth_date_present"]
            or retrieved_snapshot["private_element_present"]
            or (not nested_canary_absent_after_retrieval)
        ),
        "retrieved_object_parses_with_pydicom": True,
    })

    overall_pass = (
        pixel_hash_match and top_level_policy_correct and nested_policy_correct
        and not result["source_canary_in_stored_object"]
    )
    result["overall_result"] = "PASS" if overall_pass else "FAIL"
    return _wrap(
        milestone="M5",
        boundary="Healthcare API durable store (STOW-RS submit, WADO-RS retrieve); "
                 "gateway code executed in-process, not via a redeployed Cloud Run revision",
        relationship=(
            "Reuses the same Healthcare API dataset/store "
            "(fastdicom-m5/approved-dicom) and the same independent-retrieval "
            "methodology (m5.py's retrieve_instance/_structural_snapshot) as "
            "docs/M5_APPROVED_PERSISTENCE_VALIDATION.md's frozen evidence, "
            "under a fixture with a nested PatientID the original M5 fixture "
            "did not have. A new, distinct instance was stored "
            f"(SOPInstanceUID {_REFRESH_SOP_INSTANCE_UID.decode()}); the "
            "original M5 canonical instance "
            "(...10000000000001/2/3) was not touched, read, or deleted. "
            "Unlike original M5, both the STOW-RS submission and the WADO-RS "
            "retrieval here used this session's own gcloud ADC identity "
            "(project owner), not a separate dedicated gateway service "
            "account -- the identity-separation property M5 established is "
            "not re-tested by this refresh, only the transform/store/"
            "retrieve mechanics and the corrected recursive policy are."
        ),
        evidence=result,
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("milestone", choices=["m2", "m3", "m5"])
    parser.add_argument("--project", required=True)
    parser.add_argument("--region", default="us-central1")
    parser.add_argument("--dataset", default="fastdicom-m5")
    parser.add_argument("--dicom-store", default="approved-dicom")
    parser.add_argument("--out", type=str, default=None)
    args = parser.parse_args(argv)

    if args.milestone == "m2":
        evidence = refresh_m2()
    elif args.milestone == "m3":
        evidence = refresh_m3()
    else:
        evidence = refresh_m5_positive(
            project=args.project, region=args.region, dataset=args.dataset, dicom_store=args.dicom_store,
        )

    _EVIDENCE_DIR.mkdir(parents=True, exist_ok=True)
    out_path = Path(args.out) if args.out else _EVIDENCE_DIR / f"{args.milestone}_refresh_result.json"
    out_path.write_text(json.dumps(evidence, indent=2, default=str))
    print(f"Wrote {out_path}")
    inner = evidence["result"]
    print(f"OVERALL: {inner.get('overall_result')}")
    return 0 if inner.get("overall_result") == "PASS" else 1


if __name__ == "__main__":
    sys.exit(main())
