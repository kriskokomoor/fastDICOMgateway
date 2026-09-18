"""M5 approved-persistence validation harness.

Submits a synthetic, canary-bearing DICOM instance to the deployed
gateway's `POST /dicom/store`, then -- using an *independent* identity
(this process's own `gcloud`-derived Application Default Credentials,
never the gateway's runtime service account) -- retrieves the exact
stored instance back out of the Healthcare API DICOM store via WADO-RS
and compares source vs. transformed (computed locally via the same
production `transform.process()`, not re-implemented) vs. retrieved.

Also scans Cloud Run logs (reusing m4.py's query/classify functions) and
Healthcare API Cloud Audit Logs for the test window.

See docs/M5_APPROVED_PERSISTENCE_VALIDATION.md for the hypothesis, scope,
non-claims, and how to read a result. Run with:

    python -m fastdicom_gateway.validation.m5 --project <project> \\
        --dataset fastdicom-m5 --dicom-store approved-dicom
"""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

from . import fixtures, m4
from . import scenarios as sc

_REPO_ROOT = Path(__file__).resolve().parents[3]
_LOG_PROPAGATION_WAIT_S = 15.0


def _run(cmd: list[str], **kwargs) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, capture_output=True, text=True, **kwargs)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _access_token() -> str:
    """This validation process's own identity -- whatever `gcloud` is
    authenticated as locally -- never the gateway's runtime service
    account. See docs/M5_APPROVED_PERSISTENCE_VALIDATION.md "IAM design"
    for why those are deliberately kept separate."""
    result = _run(["gcloud", "auth", "print-access-token"])
    if result.returncode != 0:
        raise RuntimeError(f"gcloud auth print-access-token failed: {result.stderr.strip()}")
    return result.stdout.strip()


# ---------------------------------------------------------------------------
# WADO-RS retrieval + multipart parsing
# ---------------------------------------------------------------------------


def _parse_single_part_multipart(content_type: str, body: bytes) -> bytes:
    """Extracts the one DICOM part from a single-instance WADO-RS
    multipart/related response. Not a general MIME parser -- this project
    only ever retrieves exactly one instance per call."""
    boundary_marker = None
    for piece in content_type.split(";"):
        piece = piece.strip()
        if piece.lower().startswith("boundary="):
            boundary_marker = piece.split("=", 1)[1].strip('"')
    if boundary_marker is None:
        raise RuntimeError(f"no boundary in response Content-Type: {content_type!r}")
    delimiter = f"--{boundary_marker}".encode("ascii")
    parts = body.split(delimiter)
    # parts[0] is preamble (empty/whitespace); parts[1] is our one part;
    # remainder is the closing delimiter tail.
    part = parts[1]
    header_end = part.index(b"\r\n\r\n")
    content = part[header_end + 4:]
    return content.rstrip(b"\r\n-")  # trailing CRLF before the next delimiter


def retrieve_instance(
    project: str, location: str, dataset: str, dicom_store: str,
    study_uid: str, series_uid: str, sop_uid: str,
) -> bytes:
    import requests  # already a production dependency (sink.py)

    url = (
        f"https://healthcare.googleapis.com/v1/projects/{project}/locations/{location}"
        f"/datasets/{dataset}/dicomStores/{dicom_store}/dicomWeb/studies/{study_uid}"
        f"/series/{series_uid}/instances/{sop_uid}"
    )
    response = requests.get(
        url,
        headers={
            "Authorization": f"Bearer {_access_token()}",
            # transfer-syntax=* -- retrieve in whatever syntax it's
            # stored as, avoiding transcoding (see task's "prefer
            # retrieving with its stored transfer syntax").
            "Accept": 'multipart/related; type="application/dicom"; transfer-syntax=*',
        },
        timeout=30,
    )
    if response.status_code != 200:
        raise RuntimeError(f"WADO-RS retrieve failed: HTTP {response.status_code}")
    return _parse_single_part_multipart(response.headers.get("Content-Type", ""), response.content)


# ---------------------------------------------------------------------------
# Attribute comparison
# ---------------------------------------------------------------------------


def _structural_snapshot(dicom_bytes: bytes) -> dict:
    """Independent-oracle snapshot via pydicom (never used on the
    production path -- see transform.py's docstring) plus a pixel-data
    hash. Booleans/hashes only -- never the raw canary values themselves,
    consistent with the rest of this validation family's evidence
    conventions."""
    import pydicom

    dataset = pydicom.dcmread(pydicom_bytesio(dicom_bytes))
    patient_name = str(dataset.get("PatientName", ""))
    patient_id = str(dataset.get("PatientID", ""))
    birth_date = str(dataset.get("PatientBirthDate", ""))
    has_private = any(tag.is_private for tag in dataset.keys())
    pixel_data = dataset.get("PixelData", b"")
    return {
        "patient_name_present": fixtures.PATIENT_NAME.decode() in patient_name,
        "patient_id_value": patient_id,
        "patient_id_is_demo": patient_id == fixtures.DEMO_PATIENT_ID.decode(),
        "patient_id_is_source_value": patient_id == fixtures.PATIENT_ID.decode(),
        "birth_date_present": fixtures.PATIENT_BIRTH_DATE.decode() in birth_date,
        "private_element_present": has_private,
        "pixel_data_sha256": _sha256(bytes(pixel_data)),
        "study_instance_uid": str(dataset.get("StudyInstanceUID", "")),
        "series_instance_uid": str(dataset.get("SeriesInstanceUID", "")),
        "sop_instance_uid": str(dataset.get("SOPInstanceUID", "")),
    }


def pydicom_bytesio(data: bytes):
    import io
    return io.BytesIO(data)


# ---------------------------------------------------------------------------
# Orchestration
# ---------------------------------------------------------------------------


def run(*, project: str, region: str, service: str, dataset: str, dicom_store: str,
        base_url: str | None = None) -> dict:
    run_id = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime()) + f"-{int(time.time() * 1000) % 100000}"
    deployment = m4.describe_deployment(project, region, service)
    url = base_url or deployment.get("url")
    if not url:
        raise RuntimeError(f"could not determine service URL for {service}")

    source_bytes = fixtures.build_healthcare_store_fixture(run_id)
    source = _structural_snapshot(source_bytes)

    # Same production transform.process() the gateway itself calls --
    # computed locally so the comparison table has a "transformed" column
    # without a second HTTP round trip. Not a duplicate implementation:
    # literally the same function.
    sys.path.insert(0, str(_REPO_ROOT / "src"))
    from fastdicom_gateway import transform  # noqa: PLC0415
    transform_result = transform.process(source_bytes)
    transformed = _structural_snapshot(transform_result.output_bytes)

    test_start_iso = _now_iso()
    status, headers, response_body = sc.http_request(
        f"{url}/dicom/store", source_bytes, {"Content-Type": "application/dicom"},
    )
    test_end_iso = _now_iso()

    result: dict = {
        "milestone": "M5",
        "run_id": run_id,
        "gateway_commit": m4._git_rev(_REPO_ROOT),
        "structure_commit": m4._git_rev(sc.structure_repo_root()),
        "project": project,
        "region": region,
        "cloud_run_service": service,
        "cloud_run_revision": deployment.get("revision"),
        "healthcare_dataset": dataset,
        "dicom_store": dicom_store,
        "gateway_service_account": deployment.get("service_account"),
        "source_bytes": len(source_bytes),
        "transformed_bytes": len(transform_result.output_bytes),
        "store_http_status": status,
    }

    if status != 200:
        result["overall_result"] = "FAIL"
        result["error"] = f"POST /dicom/store returned {status}: {response_body[:200]!r}"
        return result

    receipt = json.loads(response_body)
    result["receipt"] = receipt

    retrieved_bytes = retrieve_instance(
        project, region, dataset, dicom_store,
        receipt["study_instance_uid"], receipt["series_instance_uid"], receipt["sop_instance_uid"],
    )
    retrieved = _structural_snapshot(retrieved_bytes)

    comparison = {
        "patient_name_present": {"source": source["patient_name_present"], "transformed": transformed["patient_name_present"], "stored": retrieved["patient_name_present"]},
        "patient_id_is_demo": {"source": source["patient_id_is_demo"], "transformed": transformed["patient_id_is_demo"], "stored": retrieved["patient_id_is_demo"]},
        "birth_date_present": {"source": source["birth_date_present"], "transformed": transformed["birth_date_present"], "stored": retrieved["birth_date_present"]},
        "private_element_present": {"source": source["private_element_present"], "transformed": transformed["private_element_present"], "stored": retrieved["private_element_present"]},
        "pixel_data_sha256": {"source": source["pixel_data_sha256"], "transformed": transformed["pixel_data_sha256"], "stored": retrieved["pixel_data_sha256"]},
    }

    pixel_hash_match = (
        source["pixel_data_sha256"] == transformed["pixel_data_sha256"] == retrieved["pixel_data_sha256"]
    )
    policy_correct = (
        source["patient_name_present"] and not transformed["patient_name_present"] and not retrieved["patient_name_present"]
        and not source["patient_id_is_demo"] and transformed["patient_id_is_demo"] and retrieved["patient_id_is_demo"]
        and source["birth_date_present"] and not transformed["birth_date_present"] and not retrieved["birth_date_present"]
        and source["private_element_present"] and not transformed["private_element_present"] and not retrieved["private_element_present"]
    )
    uids_match = (
        retrieved["study_instance_uid"] == receipt["study_instance_uid"]
        and retrieved["series_instance_uid"] == receipt["series_instance_uid"]
        and retrieved["sop_instance_uid"] == receipt["sop_instance_uid"]
    )

    # --- Log scans (reuse m4.py) --------------------------------------
    time.sleep(_LOG_PROPAGATION_WAIT_S)
    all_canaries = dict(sc.fixed_canaries())
    all_canaries["run_canary"] = fixtures.run_canary(run_id, "M5")
    log_entries = m4.query_logs(project, service, test_start_iso, test_end_iso)
    canary_hits = []
    for entry in log_entries:
        text = m4.entry_text(entry)
        matched = [name for name, value in all_canaries.items() if value.decode("ascii", "replace") in text]
        if matched:
            canary_hits.append({"insertId": entry.get("insertId"), "logName": entry.get("logName"), "canary_matches": matched})
    canary_in_cloud_run_logs = len(canary_hits) > 0

    audit_entries = _run([
        "gcloud", "logging", "read",
        f'protoPayload.serviceName="healthcare.googleapis.com" AND timestamp>="{test_start_iso}" AND timestamp<="{test_end_iso}"',
        "--project", project, "--format=json", "--limit=100",
    ])
    audit_records = json.loads(audit_entries.stdout) if audit_entries.returncode == 0 and audit_entries.stdout.strip() else []
    audit_summary = [
        {
            "methodName": e.get("protoPayload", {}).get("methodName"),
            "principal": e.get("protoPayload", {}).get("authenticationInfo", {}).get("principalEmail"),
            "resourceName": e.get("protoPayload", {}).get("resourceName"),
            "timestamp": e.get("timestamp"),
        }
        for e in audit_records
    ]

    result.update({
        "source": source,
        "transformed": transformed,
        "retrieved": retrieved,
        "comparison": comparison,
        "pixel_hash_match": pixel_hash_match,
        "policy_correctly_applied_end_to_end": policy_correct,
        "uids_match_receipt": uids_match,
        "source_canary_in_stored_object": (
            retrieved["patient_name_present"]
            or retrieved["patient_id_is_source_value"]
            or retrieved["birth_date_present"]
            or retrieved["private_element_present"]
        ),
        "source_canary_in_cloud_run_logs": canary_in_cloud_run_logs,
        "cloud_run_log_canary_hits": canary_hits,
        "healthcare_data_access_audit_logging_enabled": False,
        "healthcare_audit_activity_records_in_window": audit_summary,
        "cost_accounting": {
            "healthcare_store_requests": 1,
            "healthcare_retrieve_requests": 1,
            "retained_dicom_bytes": len(transform_result.output_bytes),
        },
    })

    overall_pass = (
        pixel_hash_match and policy_correct and uids_match
        and not canary_in_cloud_run_logs and not result["source_canary_in_stored_object"]
    )
    result["overall_result"] = "PASS" if overall_pass else "FAIL"
    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project", required=True)
    parser.add_argument("--region", default="us-central1")
    parser.add_argument("--service", default="fastdicom-gateway")
    parser.add_argument("--dataset", default="fastdicom-m5")
    parser.add_argument("--dicom-store", default="approved-dicom")
    parser.add_argument("--url", default=None)
    parser.add_argument("--out", type=str, default=None)
    args = parser.parse_args(argv)

    evidence = run(
        project=args.project, region=args.region, service=args.service,
        dataset=args.dataset, dicom_store=args.dicom_store, base_url=args.url,
    )
    if args.out:
        Path(args.out).write_text(json.dumps(evidence, indent=2, default=str))

    print(f"M5 run {evidence['run_id']}")
    print(f"store: {evidence.get('store_http_status')}  receipt: {evidence.get('receipt')}")
    if "comparison" in evidence:
        for attr, values in evidence["comparison"].items():
            print(f"  {attr}: source={values['source']} transformed={values['transformed']} stored={values['stored']}")
    print(f"OVERALL: {evidence['overall_result']}")
    return 0 if evidence["overall_result"] == "PASS" else 1


if __name__ == "__main__":
    sys.exit(main())
