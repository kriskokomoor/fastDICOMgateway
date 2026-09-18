"""FastAPI app exposing POST /dicom -- the M1 stateless gateway endpoint.

See README.md for what this proves and does not prove.
"""

from __future__ import annotations

import os
import time
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request, Response
from fastapi.responses import JSONResponse

from . import __version__, sink, transform
from .logging import get_logger

logger = get_logger(__name__)

DICOM_MEDIA_TYPE = "application/dicom"


@asynccontextmanager
async def _lifespan(_app: FastAPI):
    # M4 (docs/M4_CLOUD_RUN_VALIDATION.md "Runtime filesystem posture"):
    # one-time, request-independent characterization of the execution
    # environment -- never anything derived from a request. Cloud Run
    # supplies a writable /tmp by platform default regardless of what this
    # image declares (unlike `docker run --read-only`, which M3 verified
    # leaves /tmp non-writable) -- this distinguishes "the platform offers
    # a writable path" from "the application uses one" (it doesn't; see
    # transform.py, unchanged since M1.1). K_SERVICE/K_REVISION/
    # K_CONFIGURATION are Cloud Run's own standard env vars (unset,
    # logged as "" elsewhere) -- not secrets.
    logger.info(
        "runtime_startup uid=%d gid=%d cwd=%s tmp_exists=%s tmp_writable=%s "
        "k_service=%s k_revision=%s k_configuration=%s",
        os.getuid(), os.getgid(), os.getcwd(),
        os.path.isdir("/tmp"), os.access("/tmp", os.W_OK),
        os.environ.get("K_SERVICE", ""), os.environ.get("K_REVISION", ""),
        os.environ.get("K_CONFIGURATION", ""),
    )
    yield


app = FastAPI(title="fastDICOMgateway", version=__version__, lifespan=_lifespan)


@app.post("/dicom")
async def receive_dicom(request: Request) -> Response:
    # Read the raw body directly as bytes. No multipart/UploadFile parsing
    # is used anywhere in this project, so nothing stages the request to
    # disk -- Starlette accumulates a plain (non-multipart) body's ASGI
    # receive() chunks in memory. See README.md "Persistence constraint".
    body = await request.body()
    start = time.monotonic()

    try:
        result = transform.process(body)
    except transform.RejectedInput as rejection:
        elapsed_ms = (time.monotonic() - start) * 1000
        logger.info(
            "dicom_request_rejected input_bytes=%d reason=%s diagnostic_count=%d "
            "diagnostic_severities=%s elapsed_ms=%.2f",
            len(body),
            rejection.rejection.reason,
            rejection.rejection.diagnostic_count,
            ",".join(rejection.rejection.diagnostic_severities),
            elapsed_ms,
        )
        return JSONResponse(
            status_code=400,
            content={"status": "rejected", "reason": rejection.rejection.reason},
        )
    except Exception:
        elapsed_ms = (time.monotonic() - start) * 1000
        # logger.exception logs the exception's type/location/traceback,
        # never local variable contents -- see transform.py's exceptions,
        # none of which ever carry DICOM element values in their message.
        logger.exception(
            "dicom_request_failed input_bytes=%d elapsed_ms=%.2f",
            len(body),
            elapsed_ms,
        )
        return JSONResponse(status_code=500, content={"status": "error"})

    elapsed_ms = (time.monotonic() - start) * 1000
    logger.info(
        "dicom_request_transformed input_bytes=%d output_bytes=%d elements_touched=%d "
        "private_elements_removed=%d elapsed_ms=%.2f",
        result.input_byte_count,
        result.output_byte_count,
        result.elements_touched,
        result.private_elements_removed,
        elapsed_ms,
    )
    return Response(content=result.output_bytes, media_type=DICOM_MEDIA_TYPE)


# M5 (docs/M5_APPROVED_PERSISTENCE_VALIDATION.md): the only endpoint that
# performs intentional durable persistence. Same transform.process() as
# /dicom above -- no second transformation implementation -- plus one
# additional step: an authenticated STOW-RS submission of the already
# self-verified output_bytes to the configured Healthcare API DICOM
# store (sink.py). /dicom's transform-and-return behavior is completely
# unaffected by this endpoint's existence or failure modes (M4/M2/M3's
# validation harnesses keep working against it independently of
# Healthcare API availability).
@app.post("/dicom/store")
async def receive_and_store_dicom(request: Request) -> Response:
    body = await request.body()
    start = time.monotonic()

    try:
        result = transform.process(body)
    except transform.RejectedInput as rejection:
        elapsed_ms = (time.monotonic() - start) * 1000
        logger.info(
            "dicom_store_request_rejected input_bytes=%d reason=%s diagnostic_count=%d "
            "diagnostic_severities=%s elapsed_ms=%.2f",
            len(body),
            rejection.rejection.reason,
            rejection.rejection.diagnostic_count,
            ",".join(rejection.rejection.diagnostic_severities),
            elapsed_ms,
        )
        return JSONResponse(
            status_code=400,
            content={"status": "rejected", "reason": rejection.rejection.reason},
        )
    except Exception:
        elapsed_ms = (time.monotonic() - start) * 1000
        logger.exception(
            "dicom_store_request_failed input_bytes=%d elapsed_ms=%.2f", len(body), elapsed_ms,
        )
        return JSONResponse(status_code=500, content={"status": "error"})

    if not (result.study_instance_uid and result.series_instance_uid and result.sop_instance_uid):
        # Not a transform.RejectedInput (the transform itself succeeded) --
        # a store-specific requirement: STOW-RS has nothing meaningful to
        # submit an instance under without these. /dicom (which doesn't
        # need them) never hits this path.
        elapsed_ms = (time.monotonic() - start) * 1000
        logger.info(
            "dicom_store_request_rejected input_bytes=%d reason=missing_required_uids elapsed_ms=%.2f",
            len(body), elapsed_ms,
        )
        return JSONResponse(
            status_code=400,
            content={"status": "rejected", "reason": "missing_required_uids"},
        )

    try:
        stored = sink.store(
            result.output_bytes,
            study_instance_uid=result.study_instance_uid,
            series_instance_uid=result.series_instance_uid,
            sop_instance_uid=result.sop_instance_uid,
        )
    except sink.PersistenceFailed as failure:
        elapsed_ms = (time.monotonic() - start) * 1000
        # Only the safe fields sink.StoreFailure carries -- never the
        # downstream Healthcare API response body (see sink.py's
        # docstring and docs/M5_APPROVED_PERSISTENCE_VALIDATION.md
        # "Failure semantics" for why). Transform succeeded; persistence
        # did not -- reported as such, never as "stored".
        logger.error(
            "dicom_store_failed input_bytes=%d reason=%s status_code=%s elapsed_ms=%.2f",
            len(body), failure.failure.reason, failure.failure.status_code, elapsed_ms,
        )
        return JSONResponse(status_code=502, content={"status": "store_failed"})

    elapsed_ms = (time.monotonic() - start) * 1000
    logger.info(
        "dicom_stored input_bytes=%d output_bytes=%d elements_touched=%d "
        "private_elements_removed=%d study_instance_uid=%s series_instance_uid=%s "
        "sop_instance_uid=%s elapsed_ms=%.2f",
        result.input_byte_count, result.output_byte_count, result.elements_touched,
        result.private_elements_removed, stored.study_instance_uid, stored.series_instance_uid,
        stored.sop_instance_uid, elapsed_ms,
    )
    return JSONResponse(
        status_code=200,
        content={
            "status": "stored",
            "study_instance_uid": stored.study_instance_uid,
            "series_instance_uid": stored.series_instance_uid,
            "sop_instance_uid": stored.sop_instance_uid,
        },
    )


@app.get("/healthz")
async def healthz() -> dict:
    return {"status": "ok"}


# M4 finding (docs/M4_CLOUD_RUN_VALIDATION.md "Cloud Run container
# compatibility"): the exact path `/healthz` -- and only that exact path;
# `/health`, `/healthzz`, `/Healthz` etc. are unaffected -- is intercepted
# by Google's frontend infrastructure before it ever reaches this
# container on Cloud Run (confirmed empirically: Google's own branded 404
# page comes back, not this app's FastAPI 404 JSON). `/healthz` above is
# left as-is for local/M1-M3 compatibility (in-process TestClient and
# `docker run` never go through that infrastructure, so it isn't affected
# there); this is a plain additive alias for anything that needs to
# health-check the deployed Cloud Run service itself.
@app.get("/livez")
async def livez() -> dict:
    return {"status": "ok"}
