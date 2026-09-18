"""Post-remediation scenario definitions -- see
POST_REMEDIATION_EVIDENCE_REFRESH.md.

This is NOT a new milestone and NOT a replacement for scenarios.py.
Scenarios A, B, and C are reused *unchanged* from scenarios.py. Only
Scenario D is redefined here, against the exact same fixture
(`fixtures.build_unmodified_implicit_vr_fixture`) scenarios.py's own
`run_scenario_d` already uses, to check the corrected gateway's
acceptance-boundary behavior instead of the historical internal-write-
failure behavior.

Historical Scenario D (scenarios.run_scenario_d) checked:
    unmodified Implicit VR -> write_bytes() Unsupported -> uncaught
    exception -> HTTP 500
This remains historically true for the implementation frozen at M2-M4 and
is NOT changed here or anywhere else.

Corrected Scenario D (run_scenario_d_corrected, below) checks:
    unmodified Implicit VR -> rejected at the acceptance boundary before
    any policy mutation or serialization -> HTTP 400
    (reason=unsupported_transfer_syntax), for both /dicom and /dicom/store,
    with sink.store() structurally unreachable in the latter case (see
    ADVERSARIAL_REMEDIATION_A.md for why this holds by construction, not
    just by observation).

`scenarios.ALL_SCENARIOS` is not modified and does not import from this
module -- the frozen milestone reproduction commands documented in
docs/M2_PERSISTENCE_BOUNDARY_VALIDATION.md / docs/M3_CONTAINER_VALIDATION.md
/ docs/M4_CLOUD_RUN_VALIDATION.md keep working exactly as documented,
unaffected by this file's existence.
"""

from __future__ import annotations

import json

from . import fixtures
from . import scenarios as sc


def run_scenario_d_corrected(base_url: str, run_id: str) -> tuple[sc.ScenarioOutcome, bytes]:
    scenario_id = "D"
    canary = fixtures.run_canary(run_id, scenario_id)
    body = fixtures.build_unmodified_implicit_vr_fixture(run_id, scenario_id)

    start = sc.now_ts()
    status, headers, response_body = sc.http_request(
        f"{base_url}/dicom", body, {"Content-Type": "application/dicom"},
    )
    store_status, _store_headers, store_response_body = sc.http_request(
        f"{base_url}/dicom/store", body, {"Content-Type": "application/dicom"},
    )
    end = sc.now_ts()

    text = response_body.decode("utf-8", "replace")
    store_text = store_response_body.decode("utf-8", "replace")

    def _reason(raw_text: str) -> str | None:
        try:
            return json.loads(raw_text).get("reason")
        except (json.JSONDecodeError, AttributeError):
            return None

    checks = {
        "http_status_400": status == 400,
        "content_type_not_dicom": headers.get("content-type") != "application/dicom",
        "reason_is_unsupported_transfer_syntax": _reason(text) == "unsupported_transfer_syntax",
        "no_traceback_in_response": "Traceback" not in text and 'File "' not in text,
        "no_dicom_element_values_in_response": canary.decode("ascii") not in text,
        # /dicom/store must fail the same way, for the same reason, before
        # ever reaching sink.store() -- which is guaranteed structurally
        # (app.py raises/returns on RejectedInput before sink.store is
        # referenced at all), confirmed live here by the response shape
        # rather than by mocking, since this scenario runs against a real
        # separate process/container, not an in-process test double.
        "store_endpoint_http_status_400": store_status == 400,
        "store_endpoint_reason_is_unsupported_transfer_syntax":
            _reason(store_text) == "unsupported_transfer_syntax",
        "store_endpoint_no_dicom_element_values_in_response": canary.decode("ascii") not in store_text,
    }
    result = "PASS" if all(checks.values()) else "FAIL"
    outcome = sc.ScenarioOutcome(
        name="acceptance_boundary_rejection",
        scenario_id=scenario_id,
        description="Valid, unmodified Implicit VR Little Endian input (no targeted patient "
                     "tags, no private elements) -- the corrected gateway now rejects this at "
                     "the acceptance boundary (reason=unsupported_transfer_syntax) before any "
                     "policy mutation or serialization is attempted, for both /dicom and "
                     "/dicom/store, rather than reaching the historical internal "
                     "write-Unsupported/500 path scenarios.run_scenario_d still checks for. "
                     "See ADVERSARIAL_REMEDIATION_A.md.",
        result=result, http_status=status, window_start=start, window_end=end,
        canary_in_application_logs=False, canary_in_observed_application_artifacts=False,
        unexpected_filesystem_writes=[], checks=checks,
    )
    return outcome, canary


# Reuses scenarios.py's A/B/C unchanged; only D is corrected.
CORRECTED_SCENARIOS = (sc.run_scenario_a, sc.run_scenario_b, sc.run_scenario_c, run_scenario_d_corrected)
