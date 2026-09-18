"""Healthcare API DICOMweb STOW-RS persistence sink -- introduced in M5.

This is the gateway's *only* intentional durable-persistence operation:
an authenticated HTTPS POST of already-transformed, in-memory DICOM bytes
to a Cloud Healthcare API DICOM store's STOW-RS endpoint
(`.../dicomWeb/studies`). Nothing here writes to a filesystem path --
the multipart/related request body is built and sent entirely in memory
(see `_multipart_body`).

Authentication uses Application Default Credentials
(`google.auth.default()`) -- on Cloud Run this resolves to the runtime
service account via the metadata server automatically; no key file, no
token in an environment variable, nothing written to disk. See
docs/M5_APPROVED_PERSISTENCE_VALIDATION.md "Authentication design".

This module deliberately knows nothing about DICOM structure -- it takes
opaque bytes plus the three UID strings the caller already extracted
(transform.py's TransformResult) for the receipt, and never inspects the
bytes itself. Keeping it this way is what keeps fastDICOMstructure the
only DICOM engine on the production path (see transform.py's docstring).
"""

from __future__ import annotations

import os
import uuid
from dataclasses import dataclass
from functools import lru_cache

import google.auth
import google.auth.exceptions
import requests
from google.auth.transport.requests import AuthorizedSession

_HEALTHCARE_SCOPES = ["https://www.googleapis.com/auth/cloud-healthcare"]
_DICOMWEB_STUDIES_URL_TEMPLATE = (
    "https://healthcare.googleapis.com/v1/projects/{project}/locations/{location}"
    "/datasets/{dataset}/dicomStores/{store}/dicomWeb/studies"
)
_REQUEST_TIMEOUT_S = 30


class ConfigurationError(RuntimeError):
    """Raised when the required FASTDICOM_HEALTHCARE_* environment
    variables aren't set -- a deployment/configuration problem, not a
    per-request failure."""


class PersistenceFailed(Exception):
    """The durable STOW-RS submission itself failed -- distinct from
    transform.RejectedInput (bad input) and from a plain internal error
    (bug in this process). Carries only safe, caller-controlled fields;
    see StoreFailure and app.py's handler for why the downstream response
    body is never logged."""

    def __init__(self, failure: "StoreFailure"):
        super().__init__(f"{failure.reason} (status_code={failure.status_code})")
        self.failure = failure


@dataclass(frozen=True)
class StoreFailure:
    """Safe-to-log summary of a failed store attempt. Deliberately does
    not carry the downstream response body -- see module docstring and
    docs/M5_APPROVED_PERSISTENCE_VALIDATION.md "Failure semantics" for why
    a Healthcare API error body isn't logged without being inspected
    first."""

    reason: str  # one of a small fixed set -- see `store()`
    status_code: int | None


@dataclass(frozen=True)
class StoreResult:
    """Safe persistence receipt -- structural identifiers only, echoed
    back from what the caller already told us it intended to store (see
    transform.py's TransformResult), never re-derived from a downstream
    response body."""

    study_instance_uid: str
    series_instance_uid: str
    sop_instance_uid: str


def _dicomweb_studies_url() -> str:
    try:
        project = os.environ["FASTDICOM_HEALTHCARE_PROJECT"]
        dataset = os.environ["FASTDICOM_HEALTHCARE_DATASET"]
        store = os.environ["FASTDICOM_HEALTHCARE_DICOM_STORE"]
    except KeyError as error:
        raise ConfigurationError(f"missing required environment variable: {error}") from error
    location = os.environ.get("FASTDICOM_HEALTHCARE_LOCATION", "us-central1")
    return _DICOMWEB_STUDIES_URL_TEMPLATE.format(
        project=project, location=location, dataset=dataset, store=store,
    )


@lru_cache(maxsize=1)
def _session() -> AuthorizedSession:
    """Application Default Credentials, resolved once per process and
    refreshed automatically per-request by AuthorizedSession -- never a
    manually-fetched token stored anywhere. Cached because credential
    resolution (a metadata-server round trip on Cloud Run) is not free;
    correctness doesn't depend on caching, only latency does."""
    credentials, _project = google.auth.default(scopes=_HEALTHCARE_SCOPES)
    return AuthorizedSession(credentials)


def _multipart_body(dicom_bytes: bytes) -> tuple[bytes, str]:
    """A single-part multipart/related body per the DICOMweb STOW-RS
    transaction (PS3.18 6.6.1) -- built entirely in memory, never
    staged to a path."""
    boundary = uuid.uuid4().hex
    body = (
        f"--{boundary}\r\n"
        f"Content-Type: application/dicom\r\n\r\n"
    ).encode("ascii") + dicom_bytes + f"\r\n--{boundary}--\r\n".encode("ascii")
    content_type = f'multipart/related; type="application/dicom"; boundary={boundary}'
    return body, content_type


def store(
    dicom_bytes: bytes, *, study_instance_uid: str, series_instance_uid: str, sop_instance_uid: str,
) -> StoreResult:
    """Submits `dicom_bytes` (already transformed, already
    self-verified -- see transform.process()) to the configured Healthcare
    API DICOM store via STOW-RS. Raises PersistenceFailed on any
    non-success outcome; never returns a StoreResult unless the Healthcare
    API itself reported success.
    """
    # Every failure mode below -- missing configuration, credential
    # resolution, the HTTP call itself, an unexpected status -- raises
    # PersistenceFailed. That is deliberate: app.py's caller has exactly
    # one exception type to handle for "transform succeeded, persistence
    # did not" (see docs/M5_APPROVED_PERSISTENCE_VALIDATION.md "Failure
    # semantics"), so nothing here can fall through uncaught into
    # Starlette's own default exception handling, which would log its own
    # uncontrolled traceback outside this module's safe-logging contract.
    try:
        url = _dicomweb_studies_url()
        body, content_type = _multipart_body(dicom_bytes)
        response = _session().post(
            url, data=body, headers={"Content-Type": content_type}, timeout=_REQUEST_TIMEOUT_S,
        )
    except ConfigurationError as error:
        raise PersistenceFailed(StoreFailure(reason="configuration_error", status_code=None)) from error
    except requests.RequestException as error:
        raise PersistenceFailed(StoreFailure(reason="request_error", status_code=None)) from error
    except google.auth.exceptions.GoogleAuthError as error:
        raise PersistenceFailed(StoreFailure(reason="credentials_unavailable", status_code=None)) from error

    # STOW-RS success is 200 (all instances stored) or 202 (partial
    # success -- N/A here, exactly one instance per request, so treated
    # as failure: a single-instance request has no meaningful "partial").
    if response.status_code != 200:
        raise PersistenceFailed(StoreFailure(reason="store_rejected", status_code=response.status_code))

    return StoreResult(
        study_instance_uid=study_instance_uid,
        series_instance_uid=series_instance_uid,
        sop_instance_uid=sop_instance_uid,
    )
