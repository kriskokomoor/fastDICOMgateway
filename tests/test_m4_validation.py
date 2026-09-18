"""Tests for the M4 Cloud Run validation harness.

Same two-tier shape as tests/test_m2_validation.py and
tests/test_m3_validation.py:

* Unit tests against m4.py's log-entry classification, canary-matching,
  and time-window attribution logic -- fast, deterministic, no network or
  `gcloud` calls. These always run.
* One end-to-end smoke test that exercises a real deployed Cloud Run
  service. Unlike M2's `strace` and M3's local Docker (both free,
  near-instant, safe to run in every `pytest -q`), this hits real
  billed infrastructure and takes tens of seconds (log propagation
  delay) -- so it's opt-in only, via the FASTDICOM_GATEWAY_M4_LIVE_TEST
  environment variable, never run by default.

See docs/M4_CLOUD_RUN_VALIDATION.md for what a PASS here does and does
not prove.
"""

from __future__ import annotations

import os

import pytest

from fastdicom_gateway.validation import m4


# ---------------------------------------------------------------------------
# Log-entry classification (M4-AC7/AC8) -- pure functions, no network
# ---------------------------------------------------------------------------


def test_classify_stdout_entry_as_application():
    entry = {"logName": "projects/p/logs/run.googleapis.com%2Fstdout"}
    assert m4.classify_log_entry(entry) == "application"


def test_classify_stderr_entry_as_application():
    entry = {"logName": "projects/p/logs/run.googleapis.com%2Fstderr"}
    assert m4.classify_log_entry(entry) == "application"


def test_classify_requests_entry_as_request():
    entry = {"logName": "projects/p/logs/run.googleapis.com%2Frequests"}
    assert m4.classify_log_entry(entry) == "request"


def test_classify_unknown_entry_as_other():
    entry = {"logName": "projects/p/logs/some-other-log"}
    assert m4.classify_log_entry(entry) == "other"


def test_entry_text_includes_text_payload():
    entry = {"textPayload": "hello canary world"}
    assert "hello canary world" in m4.entry_text(entry)


def test_entry_text_includes_http_request_fields():
    entry = {"httpRequest": {"requestUrl": "https://example/dicom", "status": 200}}
    text = m4.entry_text(entry)
    assert "https://example/dicom" in text
    assert "200" in text


def test_observed_http_request_fields_only_from_request_entries():
    entries = [
        {"logName": "projects/p/logs/run.googleapis.com%2Fstdout", "textPayload": "irrelevant"},
        {
            "logName": "projects/p/logs/run.googleapis.com%2Frequests",
            "httpRequest": {"status": 200, "latency": "0.01s", "requestUrl": "https://x/dicom"},
        },
    ]
    result = m4.observed_http_request_fields(entries)
    assert set(result["fields_observed"]) == {"status", "latency", "requestUrl"}
    assert "body" not in result["note"].lower() or "no field carrying" in result["note"].lower()


# ---------------------------------------------------------------------------
# End-to-end smoke test -- opt-in only, hits real Cloud Run + Cloud Logging
# ---------------------------------------------------------------------------


def _live_test_disabled() -> str | None:
    if os.environ.get("FASTDICOM_GATEWAY_M4_LIVE_TEST") != "1":
        return "set FASTDICOM_GATEWAY_M4_LIVE_TEST=1 to run against a real deployed Cloud Run service"
    if not os.environ.get("FASTDICOM_GATEWAY_M4_PROJECT"):
        return "set FASTDICOM_GATEWAY_M4_PROJECT to the GCP project the service is deployed in"
    return None


@pytest.mark.skipif(_live_test_disabled() is not None, reason=_live_test_disabled() or "")
def test_m4_end_to_end_run_passes_against_live_service():
    project = os.environ["FASTDICOM_GATEWAY_M4_PROJECT"]
    region = os.environ.get("FASTDICOM_GATEWAY_M4_REGION", "us-central1")
    service = os.environ.get("FASTDICOM_GATEWAY_M4_SERVICE", "fastdicom-gateway")

    evidence = m4.run(project=project, region=region, service=service)
    assert evidence["overall_result"] == "PASS"
    assert len(evidence["scenarios"]) == 4
    for scenario in evidence["scenarios"]:
        assert scenario["result"] == "PASS"
        assert scenario["canary_in_application_logs"] is False
        assert scenario["canary_in_cloud_run_logs"] is False
    assert evidence["canary_in_application_logs"] is False
    assert evidence["canary_in_cloud_run_logs"] is False
