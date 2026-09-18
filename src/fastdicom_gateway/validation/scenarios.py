"""The four M2/M3 request scenarios, shared between both validation
harnesses so the definition of "successful transform" / "malformed input" /
"parser-rejected input" / "internal write failure" -- and what counts as a
PASS for each -- stays identical whether the gateway under test is a bare
subprocess (M2) or a container (M3). Only how the harness reaches the
gateway (a `base_url`) and how it observes persistence differs between the
two; that part lives in m2.py/m3.py, not here.
"""

from __future__ import annotations

import hashlib
import os
import sys
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path

from . import fixtures

_GATEWAY_REPO_ROOT = Path(__file__).resolve().parents[3]


def now_ts() -> str:
    """HH:MM:SS.ffffff, the same wall-clock format strace -tt emits."""
    return time.strftime("%H:%M:%S", time.localtime()) + f".{int((time.time() % 1) * 1e6):06d}"


def sha256_12(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()[:12]


def structure_repo_root() -> Path:
    default_repo = _GATEWAY_REPO_ROOT.parent / "fastDICOMstructure"
    return Path(os.environ.get("FASTDICOMSTRUCTURE_REPO", str(default_repo)))


def attrs_repo_root() -> Path:
    """fastDICOMstructure's own dependency, needed as a second Docker build
    context (see Dockerfile) since the A0 semantic-engine extraction moved
    the C++ parser/writer/ABI there -- fastDICOMstructure is now a pure
    Python consumer with nothing of its own to compile. See
    fastdicom_gateway.transform's _add_fastdicomstructure_to_path for the
    equivalent local-import convention (unchanged by this addition; that
    resolution happens inside fastDICOMstructure's own package, not here)."""
    default_repo = _GATEWAY_REPO_ROOT.parent / "fastDICOMattrs"
    return Path(os.environ.get("FASTDICOMATTRS_REPO", str(default_repo)))


def http_request(url: str, data: bytes | None, headers: dict) -> tuple[int, dict, bytes]:
    request = urllib.request.Request(
        url, data=data, headers=headers, method="POST" if data is not None else "GET",
    )
    try:
        with urllib.request.urlopen(request, timeout=10) as response:
            return response.status, dict(response.headers), response.read()
    except urllib.error.HTTPError as error:
        return error.code, dict(error.headers or {}), error.read()


@dataclass
class ScenarioOutcome:
    name: str
    scenario_id: str
    description: str
    result: str  # PASS / FAIL / NOT_EXERCISED
    http_status: int | None
    window_start: str
    window_end: str
    canary_in_application_logs: bool
    canary_in_observed_application_artifacts: bool
    unexpected_filesystem_writes: list[dict]
    checks: dict = field(default_factory=dict)
    notes: list[str] = field(default_factory=list)


def run_scenario_a(base_url: str, run_id: str) -> tuple[ScenarioOutcome, bytes]:
    scenario_id = "A"
    canary = fixtures.run_canary(run_id, scenario_id)
    body = fixtures.build_success_fixture(run_id, scenario_id)
    start = now_ts()
    status, headers, response_body = http_request(
        f"{base_url}/dicom", body, {"Content-Type": "application/dicom"},
    )
    end = now_ts()

    checks = {
        "http_status_200": status == 200,
        "content_type_application_dicom": headers.get("content-type") == "application/dicom",
        "response_nonempty": len(response_body) > 0,
        "patient_name_absent_from_output": fixtures.PATIENT_NAME not in response_body,
        "patient_id_absent_from_output": fixtures.PATIENT_ID not in response_body,
        "patient_birth_date_absent_from_output": fixtures.PATIENT_BIRTH_DATE not in response_body,
        "demo_patient_id_present_in_output": fixtures.DEMO_PATIENT_ID in response_body,
        "pixel_data_preserved": fixtures.PIXEL_DATA in response_body,
        "run_canary_present_in_output": canary in response_body,  # untouched tag -- expected, not a leak
    }
    try:
        sys.path.insert(0, str(structure_repo_root() / "python"))
        import fastdicomstructure as fds  # noqa: PLC0415

        structure = fds.read_buffer(response_body, fidelity="lossless")
        try:
            blocking = [d for d in structure.diagnostics if d.severity != "info"]
            checks["output_reparses_cleanly"] = blocking == []
        finally:
            structure.close()
    except Exception as error:  # pragma: no cover - defensive only
        checks["output_reparses_cleanly"] = False
        checks["reparse_error"] = repr(error)

    result = "PASS" if all(v for k, v in checks.items() if isinstance(v, bool)) else "FAIL"
    outcome = ScenarioOutcome(
        name="successful_transform", scenario_id=scenario_id,
        description="Valid DICOM with all three fixed patient canaries, a private element, "
                     "Pixel Data, and a per-run canary in an untouched tag.",
        result=result, http_status=status, window_start=start, window_end=end,
        canary_in_application_logs=False, canary_in_observed_application_artifacts=False,
        unexpected_filesystem_writes=[], checks=checks,
    )
    return outcome, canary


def run_scenario_b(base_url: str, run_id: str) -> tuple[ScenarioOutcome, bytes]:
    scenario_id = "B"
    canary = fixtures.run_canary(run_id, scenario_id)
    body = fixtures.build_malformed_fixture(run_id, scenario_id)
    start = now_ts()
    status, headers, response_body = http_request(
        f"{base_url}/dicom", body, {"Content-Type": "application/dicom"},
    )
    end = now_ts()

    text = response_body.decode("utf-8", "replace")
    checks = {
        "http_status_4xx": 400 <= status < 500,
        "content_type_not_dicom": headers.get("content-type") != "application/dicom",
        "no_dicom_result_produced": headers.get("content-type") != "application/dicom",
        "no_traceback_in_response": "Traceback" not in text and 'File "' not in text,
        "malformed_bytes_not_echoed": b"NOT A DICOM FILE" not in response_body,
    }
    result = "PASS" if all(checks.values()) else "FAIL"
    outcome = ScenarioOutcome(
        name="malformed_input", scenario_id=scenario_id,
        description="Non-DICOM bytes (no preamble/DICM magic) with a per-run canary embedded "
                     "as raw ASCII.",
        result=result, http_status=status, window_start=start, window_end=end,
        canary_in_application_logs=False, canary_in_observed_application_artifacts=False,
        unexpected_filesystem_writes=[], checks=checks,
    )
    return outcome, canary


def run_scenario_c(base_url: str, run_id: str) -> tuple[ScenarioOutcome, bytes]:
    scenario_id = "C"
    canary = fixtures.run_canary(run_id, scenario_id)
    body = fixtures.build_rejected_fixture(run_id, scenario_id)
    start = now_ts()
    status, headers, response_body = http_request(
        f"{base_url}/dicom", body, {"Content-Type": "application/dicom"},
    )
    end = now_ts()

    text = response_body.decode("utf-8", "replace")
    checks = {
        "http_status_4xx": 400 <= status < 500,
        "content_type_not_dicom": headers.get("content-type") != "application/dicom",
        "no_traceback_in_response": "Traceback" not in text and 'File "' not in text,
    }
    result = "PASS" if all(checks.values()) else "FAIL"
    outcome = ScenarioOutcome(
        name="parser_rejected_truncated_pixel_data", scenario_id=scenario_id,
        description="Syntactically DICOM-like input (valid preamble/File Meta) truncated "
                     "mid-Pixel-Data, producing a non-info structural diagnostic.",
        result=result, http_status=status, window_start=start, window_end=end,
        canary_in_application_logs=False, canary_in_observed_application_artifacts=False,
        unexpected_filesystem_writes=[], checks=checks,
    )
    return outcome, canary


def run_scenario_d(base_url: str, run_id: str) -> tuple[ScenarioOutcome, bytes]:
    scenario_id = "D"
    canary = fixtures.run_canary(run_id, scenario_id)
    body = fixtures.build_unmodified_implicit_vr_fixture(run_id, scenario_id)
    start = now_ts()
    status, _headers, response_body = http_request(
        f"{base_url}/dicom", body, {"Content-Type": "application/dicom"},
    )
    end = now_ts()

    text = response_body.decode("utf-8", "replace")
    checks = {
        "http_status_500": status == 500,
        "no_traceback_in_response": "Traceback" not in text and 'File "' not in text,
        "no_dicom_element_values_in_response": canary.decode("ascii") not in text,
    }
    result = "PASS" if all(checks.values()) else "FAIL"
    outcome = ScenarioOutcome(
        name="internal_write_unsupported", scenario_id=scenario_id,
        description="Valid, unmodified Implicit VR Little Endian input (no targeted patient "
                     "tags, no private elements) -- fastDICOMstructure's write() returns "
                     "Unsupported for this documented case, surfacing as a natural 500.",
        result=result, http_status=status, window_start=start, window_end=end,
        canary_in_application_logs=False, canary_in_observed_application_artifacts=False,
        unexpected_filesystem_writes=[], checks=checks,
    )
    return outcome, canary


ALL_SCENARIOS = (run_scenario_a, run_scenario_b, run_scenario_c, run_scenario_d)


def fixed_canaries() -> dict[str, bytes]:
    return {
        "patient_name": fixtures.PATIENT_NAME,
        "patient_id": fixtures.PATIENT_ID,
        "patient_birth_date": fixtures.PATIENT_BIRTH_DATE,
    }


def epoch_for_ts(ts: str) -> float:
    """Converts an HH:MM:SS.ffffff wall-clock string (today's date) into a
    comparable epoch float. Valid as long as a single validation run never
    spans midnight."""
    struct_time = time.strptime(time.strftime("%Y-%m-%d ") + ts.split(".")[0], "%Y-%m-%d %H:%M:%S")
    frac = float("0." + ts.split(".")[1]) if "." in ts else 0.0
    return time.mktime(struct_time) + frac
