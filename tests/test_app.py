"""HTTP-level tests against the FastAPI app.

Covers M1-AC1 (valid ingest), M1-AC6 (malformed rejection over HTTP),
M1-AC7 (source-level no-persistence check), and M1-AC8 (safe logs).
"""

from __future__ import annotations

import inspect
import logging

from fastapi.testclient import TestClient

from conftest import (
    MALFORMED_INPUT,
    PATIENT_BIRTH_DATE,
    PATIENT_ID,
    PATIENT_NAME,
    build_valid_dicom,
)
from fastdicom_gateway import app as app_module
from fastdicom_gateway import sink, transform
from fastdicom_gateway.app import DICOM_MEDIA_TYPE, app

client = TestClient(app)


def test_valid_post_returns_transformed_dicom() -> None:
    """M1-AC1: a valid synthetic DICOM object POSTed to /dicom gets an
    HTTP success response, application/dicom content type, and a
    non-empty transformed payload.
    """
    response = client.post(
        "/dicom",
        content=build_valid_dicom(),
        headers={"Content-Type": "application/dicom"},
    )
    assert response.status_code == 200
    assert response.headers["content-type"] == DICOM_MEDIA_TYPE
    assert len(response.content) > 0


def test_response_payload_reflects_the_fixed_policy() -> None:
    response = client.post(
        "/dicom",
        content=build_valid_dicom(),
        headers={"Content-Type": "application/dicom"},
    )
    assert response.status_code == 200
    assert PATIENT_NAME not in response.content
    assert PATIENT_ID not in response.content
    assert PATIENT_BIRTH_DATE not in response.content
    assert b"DEMO" in response.content


def test_malformed_post_is_rejected_without_a_dicom_result() -> None:
    """M1-AC6: malformed/non-DICOM bytes get a 4xx response, no DICOM
    result is produced, and no exception traceback or request content is
    exposed to the client.
    """
    response = client.post(
        "/dicom",
        content=MALFORMED_INPUT,
        headers={"Content-Type": "application/dicom"},
    )
    assert 400 <= response.status_code < 500
    assert response.headers["content-type"] != DICOM_MEDIA_TYPE
    body_text = response.text
    assert MALFORMED_INPUT.decode("ascii") not in body_text
    assert "Traceback" not in body_text
    assert "File \"" not in body_text  # no Python traceback frames leaked


def test_empty_post_is_rejected() -> None:
    response = client.post("/dicom", content=b"", headers={"Content-Type": "application/dicom"})
    assert 400 <= response.status_code < 500


def test_healthz() -> None:
    response = client.get("/healthz")
    assert response.status_code == 200


# ---------------------------------------------------------------------------
# M1-AC7 -- source-level review that the request path never calls a
# *filesystem*-shaped persistence API. M1 used `os.memfd_create` (an
# anonymous RAM-backed file description, opened through `/proc/self/fd/<n>`)
# as a reviewed exception to adapt fastDICOMstructure's then path-only
# write API to an in-memory result. M1.1 removed that adapter in favor of
# a true in-memory write API (`Structure.write_bytes`, see transform.py's
# `_write_to_bytes`), so `memfd_create` and `/proc/self/fd` are no longer
# expected anywhere in the request path and are now part of this
# forbidden list rather than an exception to it.
#
# M5 added `sink.py`, whose entire purpose is one *intentional* durable
# persistence operation -- an authenticated HTTPS POST of already-
# transformed bytes to the Healthcare API DICOM store (see
# docs/M5_APPROVED_PERSISTENCE_VALIDATION.md). That's not a token on this
# list (it's not filesystem-shaped, tempfile-shaped, or database-shaped),
# so scanning `sink` too still enforces exactly M1-AC7's original claim
# for M5: no *filesystem* staging anywhere, even on the one endpoint that
# now does something durable.
# ---------------------------------------------------------------------------

_FORBIDDEN_TOKENS = (
    "import tempfile",
    "NamedTemporaryFile",
    "mkstemp",
    "memfd_create",
    "/proc/self/fd",
    "sqlite3",
    "psycopg",
    "sqlalchemy",
    "import pickle",
    "open(",
)


def test_request_path_source_contains_no_persistence_apis() -> None:
    for module in (app_module, transform, sink):
        source = inspect.getsource(module)
        for token in _FORBIDDEN_TOKENS:
            assert token not in source, f"found forbidden token {token!r} in {module.__name__}"


def test_no_file_handlers_are_attached_to_the_logger() -> None:
    logger = app_module.logger
    for handler in logger.handlers:
        assert not isinstance(handler, logging.FileHandler)


# ---------------------------------------------------------------------------
# M1-AC8 -- safe logs
# ---------------------------------------------------------------------------

def test_phi_values_do_not_appear_in_logs(caplog) -> None:
    with caplog.at_level(logging.INFO, logger="fastdicom_gateway.app"):
        response = client.post(
            "/dicom",
            content=build_valid_dicom(),
            headers={"Content-Type": "application/dicom"},
        )
    assert response.status_code == 200

    captured = caplog.text
    assert "NEVER_PERSIST^KRIS" not in captured
    assert "SECRET-123456789" not in captured
    assert "19610217" not in captured
    # Sanity: safe fields are actually present, so this test would fail
    # loudly (not just vacuously pass) if logging broke.
    assert "dicom_request_transformed" in captured
    assert "input_bytes=" in captured


def test_phi_values_do_not_appear_in_logs_for_rejected_input(caplog) -> None:
    with caplog.at_level(logging.INFO, logger="fastdicom_gateway.app"):
        client.post(
            "/dicom",
            content=MALFORMED_INPUT,
            headers={"Content-Type": "application/dicom"},
        )
    assert MALFORMED_INPUT.decode("ascii") not in caplog.text


# ---------------------------------------------------------------------------
# M5 -- POST /dicom/store. sink.store() is monkeypatched throughout: these
# tests exercise app.py's own request-handling/logging behavior, not a real
# Healthcare API call (see src/fastdicom_gateway/validation/m5.py for the
# real-API evidence run).
# ---------------------------------------------------------------------------

from fastdicom_gateway.validation import fixtures as val_fixtures  # noqa: E402


def _store_fixture() -> bytes:
    return val_fixtures.build_healthcare_store_fixture("APPTEST")


def test_store_endpoint_returns_safe_receipt_on_success(monkeypatch) -> None:
    def _fake_store(dicom_bytes, *, study_instance_uid, series_instance_uid, sop_instance_uid):
        return sink.StoreResult(
            study_instance_uid=study_instance_uid,
            series_instance_uid=series_instance_uid,
            sop_instance_uid=sop_instance_uid,
        )

    monkeypatch.setattr(sink, "store", _fake_store)
    response = client.post(
        "/dicom/store", content=_store_fixture(), headers={"Content-Type": "application/dicom"},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "stored"
    assert body["study_instance_uid"] == val_fixtures.M5_STUDY_INSTANCE_UID.decode()
    assert body["series_instance_uid"] == val_fixtures.M5_SERIES_INSTANCE_UID.decode()
    assert body["sop_instance_uid"] == val_fixtures.M5_SOP_INSTANCE_UID.decode()
    # No source values in the receipt.
    assert val_fixtures.PATIENT_NAME.decode() not in response.text
    assert val_fixtures.PATIENT_ID.decode() not in response.text


def test_store_endpoint_reports_persistence_failure_not_success(monkeypatch) -> None:
    def _fake_store(*args, **kwargs):
        raise sink.PersistenceFailed(sink.StoreFailure(reason="store_rejected", status_code=409))

    monkeypatch.setattr(sink, "store", _fake_store)
    response = client.post(
        "/dicom/store", content=_store_fixture(), headers={"Content-Type": "application/dicom"},
    )
    assert response.status_code == 502
    assert response.json() == {"status": "store_failed"}


def test_store_endpoint_never_claims_stored_on_failure(monkeypatch) -> None:
    def _fake_store(*args, **kwargs):
        raise sink.PersistenceFailed(sink.StoreFailure(reason="request_error", status_code=None))

    monkeypatch.setattr(sink, "store", _fake_store)
    response = client.post(
        "/dicom/store", content=_store_fixture(), headers={"Content-Type": "application/dicom"},
    )
    assert response.json()["status"] != "stored"


def test_store_endpoint_rejects_malformed_input_before_touching_sink(monkeypatch) -> None:
    calls = []
    monkeypatch.setattr(sink, "store", lambda *a, **k: calls.append(1))
    response = client.post(
        "/dicom/store", content=MALFORMED_INPUT, headers={"Content-Type": "application/dicom"},
    )
    assert 400 <= response.status_code < 500
    assert calls == []  # sink.store() never called for rejected input


def test_store_endpoint_applies_the_same_fixed_policy(monkeypatch) -> None:
    """Both /dicom and /dicom/store call transform.process() -- pin that
    /dicom/store's *submitted* bytes (what sink.store() receives) already
    have the policy applied, not just the HTTP receipt."""
    captured = {}

    def _fake_store(dicom_bytes, **kwargs):
        captured["bytes"] = dicom_bytes
        return sink.StoreResult(**kwargs)

    monkeypatch.setattr(sink, "store", _fake_store)
    client.post("/dicom/store", content=_store_fixture(), headers={"Content-Type": "application/dicom"})
    assert val_fixtures.PATIENT_NAME not in captured["bytes"]
    assert val_fixtures.PATIENT_ID not in captured["bytes"]
    assert val_fixtures.DEMO_PATIENT_ID in captured["bytes"]


def test_store_endpoint_failure_does_not_log_source_canaries(monkeypatch, caplog) -> None:
    def _fake_store(*args, **kwargs):
        raise sink.PersistenceFailed(sink.StoreFailure(reason="store_rejected", status_code=409))

    monkeypatch.setattr(sink, "store", _fake_store)
    with caplog.at_level(logging.INFO, logger="fastdicom_gateway.app"):
        client.post(
            "/dicom/store", content=_store_fixture(), headers={"Content-Type": "application/dicom"},
        )
    assert val_fixtures.PATIENT_NAME.decode() not in caplog.text
    assert val_fixtures.PATIENT_ID.decode() not in caplog.text
    assert val_fixtures.PATIENT_BIRTH_DATE.decode() not in caplog.text
    assert "reason=store_rejected" in caplog.text
    assert "status_code=409" in caplog.text


def test_store_endpoint_success_does_not_log_source_canaries(monkeypatch, caplog) -> None:
    def _fake_store(dicom_bytes, *, study_instance_uid, series_instance_uid, sop_instance_uid):
        return sink.StoreResult(study_instance_uid, series_instance_uid, sop_instance_uid)

    monkeypatch.setattr(sink, "store", _fake_store)
    with caplog.at_level(logging.INFO, logger="fastdicom_gateway.app"):
        client.post(
            "/dicom/store", content=_store_fixture(), headers={"Content-Type": "application/dicom"},
        )
    assert val_fixtures.PATIENT_NAME.decode() not in caplog.text
    assert val_fixtures.PATIENT_ID.decode() not in caplog.text


def test_dicom_endpoint_unaffected_by_store_endpoint_existing() -> None:
    """M5-AC4/§28: /dicom's transform-and-return behavior must not depend
    on sink.py or Healthcare API availability at all."""
    response = client.post(
        "/dicom", content=build_valid_dicom(), headers={"Content-Type": "application/dicom"},
    )
    assert response.status_code == 200
    assert response.headers["content-type"] == DICOM_MEDIA_TYPE
