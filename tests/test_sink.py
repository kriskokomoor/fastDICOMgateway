"""Unit tests for sink.py -- the Healthcare API STOW-RS persistence sink.

All Healthcare API interaction is mocked here (no real network/GCP calls
-- see docs/M5_APPROVED_PERSISTENCE_VALIDATION.md's own note that "Mock
Healthcare API only for unit tests. The actual M5 evidence run must hit
the real Healthcare API DICOM Store", which src/fastdicom_gateway/
validation/m5.py does separately).
"""

from __future__ import annotations

import os

import pytest

from fastdicom_gateway import sink


@pytest.fixture(autouse=True)
def _healthcare_env(monkeypatch):
    # Every test that reaches sink.store() also monkeypatches `_session`
    # directly (see the fakes below), so the real lru_cache-wrapped
    # `_session` (which would call the real google.auth.default()) is
    # never actually invoked here -- no cache to reset between tests.
    monkeypatch.setenv("FASTDICOM_HEALTHCARE_PROJECT", "test-project")
    monkeypatch.setenv("FASTDICOM_HEALTHCARE_DATASET", "test-dataset")
    monkeypatch.setenv("FASTDICOM_HEALTHCARE_DICOM_STORE", "test-store")


def test_dicomweb_studies_url_uses_configured_values():
    url = sink._dicomweb_studies_url()
    assert url == (
        "https://healthcare.googleapis.com/v1/projects/test-project/locations/us-central1"
        "/datasets/test-dataset/dicomStores/test-store/dicomWeb/studies"
    )


def test_dicomweb_studies_url_respects_location_override(monkeypatch):
    monkeypatch.setenv("FASTDICOM_HEALTHCARE_LOCATION", "europe-west4")
    url = sink._dicomweb_studies_url()
    assert "/locations/europe-west4/" in url


def test_missing_config_raises_configuration_error(monkeypatch):
    monkeypatch.delenv("FASTDICOM_HEALTHCARE_DATASET")
    with pytest.raises(sink.ConfigurationError):
        sink._dicomweb_studies_url()


def test_multipart_body_wraps_bytes_with_correct_content_type():
    body, content_type = sink._multipart_body(b"\x01\x02\x03")
    assert content_type.startswith('multipart/related; type="application/dicom"; boundary=')
    boundary = content_type.split("boundary=")[1]
    assert body.startswith(f"--{boundary}".encode("ascii"))
    assert b"Content-Type: application/dicom\r\n\r\n\x01\x02\x03" in body
    assert body.endswith(f"--{boundary}--\r\n".encode("ascii"))


def test_multipart_body_does_not_mutate_dicom_bytes():
    original = bytes(range(256))
    body, _content_type = sink._multipart_body(original)
    assert original in body


class _FakeResponse:
    def __init__(self, status_code: int):
        self.status_code = status_code


class _FakeSession:
    def __init__(self, status_code: int):
        self.status_code = status_code
        self.calls: list[dict] = []

    def post(self, url, data, headers, timeout):
        self.calls.append({"url": url, "data": data, "headers": headers, "timeout": timeout})
        return _FakeResponse(self.status_code)


def test_store_succeeds_on_http_200(monkeypatch):
    fake = _FakeSession(200)
    monkeypatch.setattr(sink, "_session", lambda: fake)
    result = sink.store(
        b"dicom-bytes", study_instance_uid="1.2.3", series_instance_uid="1.2.3.4",
        sop_instance_uid="1.2.3.4.5",
    )
    assert result.study_instance_uid == "1.2.3"
    assert result.series_instance_uid == "1.2.3.4"
    assert result.sop_instance_uid == "1.2.3.4.5"
    assert len(fake.calls) == 1
    assert b"dicom-bytes" in fake.calls[0]["data"]


@pytest.mark.parametrize("status_code", [400, 409, 500, 503])
def test_store_raises_persistence_failed_on_non_200(monkeypatch, status_code):
    fake = _FakeSession(status_code)
    monkeypatch.setattr(sink, "_session", lambda: fake)
    with pytest.raises(sink.PersistenceFailed) as excinfo:
        sink.store(b"x", study_instance_uid="1", series_instance_uid="2", sop_instance_uid="3")
    assert excinfo.value.failure.reason == "store_rejected"
    assert excinfo.value.failure.status_code == status_code


def test_store_raises_persistence_failed_on_request_exception(monkeypatch):
    import requests

    class _RaisingSession:
        def post(self, *args, **kwargs):
            raise requests.ConnectionError("network unreachable")

    monkeypatch.setattr(sink, "_session", _RaisingSession)
    with pytest.raises(sink.PersistenceFailed) as excinfo:
        sink.store(b"x", study_instance_uid="1", series_instance_uid="2", sop_instance_uid="3")
    assert excinfo.value.failure.reason == "request_error"


def test_store_raises_persistence_failed_on_missing_configuration(monkeypatch):
    monkeypatch.delenv("FASTDICOM_HEALTHCARE_PROJECT")
    with pytest.raises(sink.PersistenceFailed) as excinfo:
        sink.store(b"x", study_instance_uid="1", series_instance_uid="2", sop_instance_uid="3")
    assert excinfo.value.failure.reason == "configuration_error"


def test_store_raises_persistence_failed_on_credential_error(monkeypatch):
    import google.auth.exceptions

    def _raise_auth_error(*args, **kwargs):
        raise google.auth.exceptions.DefaultCredentialsError("no ADC")

    monkeypatch.setattr(sink, "_session", _raise_auth_error)
    with pytest.raises(sink.PersistenceFailed) as excinfo:
        sink.store(b"x", study_instance_uid="1", series_instance_uid="2", sop_instance_uid="3")
    assert excinfo.value.failure.reason == "credentials_unavailable"


def test_persistence_failed_message_never_contains_dicom_bytes(monkeypatch):
    """The exception's own string representation (what an un-careful
    `logger.exception(error)` might log) must never be able to carry
    DICOM content -- see docs/M5_APPROVED_PERSISTENCE_VALIDATION.md
    "Failure semantics"."""
    fake = _FakeSession(409)
    monkeypatch.setattr(sink, "_session", lambda: fake)
    canary = b"NEVER_PERSIST^KRIS"
    with pytest.raises(sink.PersistenceFailed) as excinfo:
        sink.store(canary, study_instance_uid="1", series_instance_uid="2", sop_instance_uid="3")
    assert canary not in str(excinfo.value).encode()
    assert canary not in repr(excinfo.value.failure).encode()
