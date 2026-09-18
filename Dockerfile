# syntax=docker/dockerfile:1
#
# Container build, introduced in M3 and reused unchanged (aside from the
# CMD's $PORT handling, see below) for M4's Cloud Run deployment -- see
# docs/M3_CONTAINER_VALIDATION.md and docs/M4_CLOUD_RUN_VALIDATION.md.
# Three source trees are needed -- this repository, the sibling
# fastDICOMstructure checkout it depends on directly, and fastDICOMstructure's
# own dependency fastDICOMattrs (the DICOM parser/writer/ABI, extracted out
# of fastDICOMstructure at the A0 semantic-engine extraction -- see
# fastDICOMattrs' docs/architecture/ADR-001-ATTRS-NAMING-AND-LAYERING.md).
# Neither sibling is ever vendored into this repository (see README.md
# "Relationship to fastDICOMstructure"), so the build requires two named
# BuildKit build contexts pointing at those sibling checkouts. See
# docs/M3_CONTAINER_VALIDATION.md "Build design" for why this shape was
# chosen over vendoring or a parent-directory build context. Build from
# inside this repository with:
#
#   docker build -f Dockerfile \
#       --build-context structure=../fastDICOMstructure \
#       --build-context attrs=../fastDICOMattrs \
#       -t fastdicom-gateway:m3 .
#
# Both stages pin the same base image tag so the compiled C++ shared
# library (built against this image's glibc/libstdc++) and the runtime
# stage that links it stay ABI-compatible.
ARG BASE_IMAGE=python:3.12-slim-bookworm

# ---------------------------------------------------------------------------
# Stage 1: compile fastDICOMattrs' C++ core + C ABI shared library -- the
# DICOM parser/writer, extracted from fastDICOMstructure at A0.
# Tests/bench are disabled (FDS_BUILD_TESTS/FDS_BUILD_BENCH=OFF) -- this
# stage only needs to produce libfastdicomattrs_c.so, not run that
# repository's own test suite (already validated on its own). This build
# tooling never reaches the runtime stage.
# ---------------------------------------------------------------------------
FROM ${BASE_IMAGE} AS attrs-builder

RUN apt-get update && apt-get install -y --no-install-recommends \
        build-essential cmake ninja-build \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /src/fastDICOMattrs
COPY --from=attrs . .

RUN rm -rf build \
    && cmake -S . -B container-build -G Ninja \
        -DCMAKE_BUILD_TYPE=Release \
        -DFDS_BUILD_TESTS=OFF \
        -DFDS_BUILD_BENCH=OFF \
        -DFDS_BUILD_ABI=ON \
    && cmake --build container-build --parallel \
    && cmake --install container-build --prefix /opt/fastdicomattrs-install \
    && mkdir -p /opt/fastdicomattrs-install/python-package \
    && cp -r python/fastdicomattrs /opt/fastdicomattrs-install/python-package/ \
    && rm -rf /opt/fastdicomattrs-install/python-package/fastdicomattrs/__pycache__

# ---------------------------------------------------------------------------
# Stage 2: runtime image. Python + gateway + fastDICOMattrs' shared library
# + fastDICOMstructure's pure-Python policy package (no build step of its
# own since A0 -- just copied in) + only the native runtime libraries
# fastDICOMattrs needs (libstdc++6) -- no compiler, no cmake, no Git
# metadata, no test/validation tooling (see .dockerignore for what never
# reaches this image's build context at all).
# ---------------------------------------------------------------------------
FROM ${BASE_IMAGE} AS runtime

RUN apt-get update && apt-get install -y --no-install-recommends \
        libstdc++6 \
    && rm -rf /var/lib/apt/lists/*

COPY --from=attrs-builder /opt/fastdicomattrs-install/lib/ /usr/local/lib/
# Base-image-version-coupled path (see BASE_IMAGE above): python:3.12-*
# always exposes this exact site-packages location.
COPY --from=attrs-builder /opt/fastdicomattrs-install/python-package/fastdicomattrs \
    /usr/local/lib/python3.12/site-packages/fastdicomattrs
COPY --from=structure python/fastdicomstructure \
    /usr/local/lib/python3.12/site-packages/fastdicomstructure
RUN find /usr/local/lib/python3.12/site-packages -name __pycache__ -prune -exec rm -rf {} + \
    && ldconfig

WORKDIR /app
COPY pyproject.toml README.md ./
COPY src ./src
RUN pip install --no-cache-dir .

ENV FASTDICOMATTRS_LIB=/usr/local/lib/libfastdicomattrs_c.so \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

# Non-root, unprivileged: no home directory, no login shell, no writable
# ownership grants beyond what already-world-readable image files provide
# -- the application needs no writable path (see docs/M3_CONTAINER_VALIDATION.md
# "Filesystem posture").
RUN groupadd --gid 1000 appuser \
    && useradd --uid 1000 --gid appuser --no-create-home \
        --shell /usr/sbin/nologin appuser
USER appuser:appuser

EXPOSE 8080
# Shell form + `exec` (not the plain exec-form array) so Cloud Run's
# platform-supplied $PORT (see docs/M4_CLOUD_RUN_VALIDATION.md "Cloud Run
# container compatibility") is substituted at container start, while
# `exec` still replaces the shell with uvicorn as PID 1 -- without it, a
# shell-form CMD leaves /bin/sh as PID 1, which does not forward SIGTERM
# to uvicorn, breaking graceful shutdown (both `docker stop` in M3 and
# Cloud Run's scale-down signal rely on this). Falls back to 8080 when
# $PORT is unset, so `docker run` (M3) is unaffected.
CMD ["/bin/sh", "-c", "exec uvicorn fastdicom_gateway.app:app --host 0.0.0.0 --port ${PORT:-8080}"]
