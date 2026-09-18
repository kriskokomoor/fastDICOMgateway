"""M3 container persistence-boundary validation harness.

Builds (or reuses) the gateway's container image, runs it as a real
Docker container -- by default with `--read-only` and no writable mounts
at all -- drives it with the same canary-bearing synthetic DICOM fixtures
M2 uses (see scenarios.py) over real HTTP through a published port, and
checks -- from outside the container -- whether any source canary reached
the container's writable filesystem surfaces or its captured stdout/
stderr before policy transformation.

Two container-native observation mechanisms replace M2's host-side
`strace`, which cannot usefully reach inside a container's own PID/mount
namespace without weakening the container's security posture (extra
capabilities/seccomp changes) specifically for validation -- see
docs/M3_CONTAINER_VALIDATION.md "Observation methodology" for why that
tradeoff was rejected in favor of these two:

1. `--read-only` rootfs enforcement itself: this is *architectural*
   evidence, not just observed evidence -- an attempted persistent write
   fails at the kernel level (EROFS) rather than merely being unobserved.
   An attempt would surface indirectly as a Python OSError inside request
   handling -> app.py's generic `except Exception` -> 500 + a logged
   traceback, which the log scan below already covers.
2. `docker diff` against the running container, snapshotted after each
   scenario and diffed against the previous snapshot to attribute any
   change to the scenario window it appeared in -- catching anything a
   read-only rootfs doesn't rule out (e.g. a future change that adds a
   legitimate tmpfs).

See docs/M3_CONTAINER_VALIDATION.md for the hypothesis, scope, non-claims,
and how to read a result. Run with:

    python -m fastdicom_gateway.validation.m3
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
import uuid
from dataclasses import dataclass
from pathlib import Path

from . import scenarios as sc

_REPO_ROOT = Path(__file__).resolve().parents[3]
_READY_TIMEOUT_S = 30.0
_DEFAULT_TAG = "fastdicom-gateway:m3"
_CONTAINER_PORT = 8080


def _run(cmd: list[str], **kwargs) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, capture_output=True, text=True, **kwargs)


def _git_rev(repo: Path) -> str:
    try:
        out = _run(["git", "-C", str(repo), "rev-parse", "HEAD"], timeout=5)
        return out.stdout.strip() if out.returncode == 0 else "unknown"
    except Exception:
        return "unknown"


def _docker_version() -> str:
    try:
        out = _run(["docker", "--version"], timeout=5)
        return out.stdout.strip()
    except Exception:
        return "unknown"


def _find_free_port() -> int:
    import socket
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


# ---------------------------------------------------------------------------
# Image build
# ---------------------------------------------------------------------------


def build_image(tag: str = _DEFAULT_TAG, structure_repo: Path | None = None,
                 attrs_repo: Path | None = None) -> str:
    """Runs the documented, reproducible build command (see Dockerfile's
    header comment) and returns the resulting image id.

    Two sibling-checkout build contexts are required since the A0
    semantic-engine extraction: `structure` (fastDICOMstructure, pure
    Python, no build step) and `attrs` (fastDICOMattrs, compiled inside the
    image -- see Dockerfile's attrs-builder stage)."""
    structure_repo = structure_repo or sc.structure_repo_root()
    attrs_repo = attrs_repo or sc.attrs_repo_root()
    cmd = [
        "docker", "build",
        "-f", str(_REPO_ROOT / "Dockerfile"),
        "--build-context", f"structure={structure_repo}",
        "--build-context", f"attrs={attrs_repo}",
        "-t", tag,
        str(_REPO_ROOT),
    ]
    result = subprocess.run(cmd, cwd=str(_REPO_ROOT))
    if result.returncode != 0:
        raise RuntimeError(f"docker build failed (exit {result.returncode}): {' '.join(cmd)}")
    image_id = _run(["docker", "image", "inspect", tag, "--format", "{{.Id}}"]).stdout.strip()
    return image_id


# ---------------------------------------------------------------------------
# Container lifecycle
# ---------------------------------------------------------------------------


@dataclass
class ContainerHandle:
    name: str
    image: str
    port: int
    read_only: bool
    tmpfs: list[str]
    launch_ts: str


def _start_container(
    image: str, port: int, name: str, read_only: bool, tmpfs: list[str],
) -> ContainerHandle:
    cmd = [
        "docker", "run", "-d", "--rm",
        "--name", name,
        "-p", f"127.0.0.1:{port}:{_CONTAINER_PORT}",
        "--user", "1000:1000",
    ]
    if read_only:
        cmd.append("--read-only")
    for mount in tmpfs:
        cmd += ["--tmpfs", mount]
    cmd.append(image)

    launch_ts = sc.now_ts()
    result = _run(cmd)
    if result.returncode != 0:
        raise RuntimeError(f"docker run failed: {result.stderr.strip()}")
    return ContainerHandle(
        name=name, image=image, port=port, read_only=read_only, tmpfs=tmpfs, launch_ts=launch_ts,
    )


def _wait_ready(handle: ContainerHandle) -> bool:
    deadline = time.time() + _READY_TIMEOUT_S
    url = f"http://127.0.0.1:{handle.port}/healthz"
    while time.time() < deadline:
        status_check = _run(["docker", "inspect", "-f", "{{.State.Running}}", handle.name])
        if status_check.stdout.strip() != "true":
            return False
        try:
            status, _headers, _body = sc.http_request(url, None, {})
            if status == 200:
                return True
        except Exception:
            pass
        time.sleep(0.2)
    return False


def _stop_container(handle: ContainerHandle) -> None:
    _run(["docker", "stop", "-t", "5", handle.name])


def _container_logs(handle: ContainerHandle) -> tuple[str, str]:
    result = _run(["docker", "logs", handle.name])
    return result.stdout, result.stderr


def _docker_diff(handle: ContainerHandle) -> dict[str, str]:
    """{path: change_kind} where kind is one of A (added) / C (changed) /
    D (deleted), per `docker diff` output. Cumulative from container start
    -- callers wanting a per-scenario delta diff two consecutive snapshots
    themselves (see _diff_delta)."""
    result = _run(["docker", "diff", handle.name])
    changes: dict[str, str] = {}
    for line in result.stdout.splitlines():
        line = line.strip()
        if not line or " " not in line:
            continue
        kind, path = line.split(" ", 1)
        changes[path] = kind
    return changes


def _diff_delta(previous: dict[str, str], current: dict[str, str]) -> dict[str, str]:
    return {path: kind for path, kind in current.items() if previous.get(path) != kind}


def _is_regular_file(handle: ContainerHandle, path: str) -> bool:
    result = subprocess.run(
        ["docker", "exec", handle.name, "test", "-f", path], capture_output=True, timeout=10,
    )
    return result.returncode == 0


def _read_container_file(handle: ContainerHandle, path: str) -> bytes | None:
    """Best-effort content read of a changed *regular file* via `docker
    cp`, for canary-content enrichment only -- absence or failure here does
    not weaken the primary path/change-kind evidence (see module
    docstring). Skips directories: `docker cp` on a directory returns a tar
    of its entire subtree, which would make a changed directory look like
    it "contains" any canary that happens to be in a file underneath it --
    misleading as a per-path finding when that descendant file already
    appears as its own, separate docker-diff entry."""
    if not _is_regular_file(handle, path):
        return None
    result = subprocess.run(
        ["docker", "cp", f"{handle.name}:{path}", "-"], capture_output=True, timeout=10,
    )
    if result.returncode != 0:
        return None
    return result.stdout  # tar stream; searched as raw bytes below, not extracted


# ---------------------------------------------------------------------------
# Orchestration
# ---------------------------------------------------------------------------


def run(
    *,
    image: str | None = None,
    build: bool = True,
    read_only: bool = True,
    tmpfs: list[str] | None = None,
    port: int | None = None,
    scenario_fns: tuple | None = None,
) -> dict:
    # See m2.py's identical `scenario_fns` parameter for why this exists --
    # defaults to the frozen `sc.ALL_SCENARIOS`, so every existing caller is
    # unaffected; only POST_REMEDIATION_EVIDENCE_REFRESH.md's corrected
    # Scenario D run passes an override.
    scenario_fns = scenario_fns or sc.ALL_SCENARIOS
    tmpfs = tmpfs or []
    run_id = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime()) + "-" + uuid.uuid4().hex[:8]

    if build or image is None:
        tag = image or _DEFAULT_TAG
        image_id = build_image(tag)
        image_ref = tag
    else:
        image_ref = image
        image_id = _run(["docker", "image", "inspect", image, "--format", "{{.Id}}"]).stdout.strip()

    port = port or _find_free_port()
    container_name = f"fastdicom-gateway-m3-{run_id}"

    handle = _start_container(image_ref, port, container_name, read_only, tmpfs)
    scenario_outcomes: list[sc.ScenarioOutcome] = []
    run_canaries: dict[str, bytes] = {}
    diff_snapshots: dict[str, dict[str, str]] = {}
    base_url = f"http://127.0.0.1:{handle.port}"
    started_successfully = True

    try:
        ready = _wait_ready(handle)
        if not ready:
            started_successfully = False
        else:
            baseline_diff = _docker_diff(handle)
            diff_snapshots["startup"] = baseline_diff
            previous_diff = baseline_diff

            for scenario_fn in scenario_fns:
                outcome, canary = scenario_fn(base_url, run_id)
                scenario_outcomes.append(outcome)
                run_canaries[f"run_canary_{outcome.scenario_id}"] = canary
                time.sleep(0.1)

                current_diff = _docker_diff(handle)
                delta = _diff_delta(previous_diff, current_diff)
                outcome.unexpected_filesystem_writes = [
                    {"path": path, "change_kind": kind} for path, kind in delta.items()
                ]

                # Content-check every path docker diff has *ever* reported
                # changed so far (not just this scenario's delta), while the
                # container is still alive to `docker cp` from. A path
                # docker diff already marked changed by an earlier scenario
                # keeps the same change-kind on a later overwrite -- it does
                # not reappear in `delta` -- so a delta-only check would
                # miss a later scenario's canary landing in an
                # already-flagged file (see docs/M3_CONTAINER_VALIDATION.md
                # "Negative control" for the run that found this the same
                # way M2's negative control found its own analogous gap).
                relevant = {
                    "patient_name", "patient_id", "patient_birth_date",
                    f"run_canary_{outcome.scenario_id}",
                }
                for path in current_diff:
                    content = _read_container_file(handle, path)
                    if content is None:
                        continue
                    matches = [
                        name for name in relevant
                        if name in ("patient_name", "patient_id", "patient_birth_date")
                        and sc.fixed_canaries()[name] in content
                    ]
                    scenario_canary = f"run_canary_{outcome.scenario_id}"
                    if canary in content:
                        matches.append(scenario_canary)
                    if matches:
                        outcome.canary_in_observed_application_artifacts = True
                        outcome.notes.append(f"canary found in container path {path}")

                diff_snapshots[outcome.scenario_id] = current_diff
                previous_diff = current_diff

            time.sleep(0.2)
    finally:
        stdout_text, stderr_text = ("", "")
        if started_successfully:
            stdout_text, stderr_text = _container_logs(handle)
        final_diff = _docker_diff(handle) if started_successfully else {}
        _stop_container(handle)

    if not started_successfully:
        return {
            "milestone": "M3",
            "run_id": run_id,
            "gateway_commit": _git_rev(_REPO_ROOT),
            "structure_commit": _git_rev(sc.structure_repo_root()),
            "image_tag": image_ref,
            "image_id": image_id,
            "runtime": {
                "container_engine": "docker", "container_engine_version": _docker_version(),
                "read_only_rootfs": read_only, "writable_paths": tmpfs,
            },
            "error": "container did not become ready (or exited) within the timeout",
            "scenarios": [],
            "overall_result": "BLOCKED",
        }

    all_canaries = dict(sc.fixed_canaries())
    all_canaries.update(run_canaries)
    log_text = stdout_text + stderr_text
    log_hits = [name for name, value in all_canaries.items() if value in log_text.encode("utf-8", "replace")]
    canary_in_logs = len(log_hits) > 0
    traceback_present_in_logs = "Traceback (most recent call last)" in log_text

    for outcome in scenario_outcomes:
        scenario_canary_name = f"run_canary_{outcome.scenario_id}"
        relevant_names = {"patient_name", "patient_id", "patient_birth_date", scenario_canary_name}
        if relevant_names & set(log_hits):
            outcome.canary_in_application_logs = True
            outcome.notes.append("canary found in container stdout/stderr")

        if outcome.unexpected_filesystem_writes:
            outcome.result = "FAIL"
            outcome.notes.append(
                f"{len(outcome.unexpected_filesystem_writes)} filesystem change(s) newly "
                "observed via docker diff during this scenario's window"
            )
        # canary_in_observed_application_artifacts was already populated
        # in-loop above (while the container was still alive to `docker cp`
        # from) against the *cumulative* diff set, not just this scenario's
        # delta -- see the loop's comment for why that distinction matters.
        if outcome.canary_in_application_logs or outcome.canary_in_observed_application_artifacts:
            outcome.result = "FAIL"

    overall_result = "PASS" if all(o.result == "PASS" for o in scenario_outcomes) else "FAIL"

    evidence = {
        "milestone": "M3",
        "run_id": run_id,
        "gateway_commit": _git_rev(_REPO_ROOT),
        "structure_commit": _git_rev(sc.structure_repo_root()),
        "image_tag": image_ref,
        "image_id": image_id,
        "runtime": {
            "container_engine": "docker",
            "container_engine_version": _docker_version(),
            "read_only_rootfs": read_only,
            "writable_paths": tmpfs,
            "uid_gid": "1000:1000",
        },
        "traceback_present_in_logs": traceback_present_in_logs,
        "canary_in_logs": canary_in_logs,
        "scenarios": [
            {
                "name": o.name,
                "scenario_id": o.scenario_id,
                "description": o.description,
                "result": o.result,
                "http_status": o.http_status,
                "window": {"start": o.window_start, "end": o.window_end},
                "canary_in_application_logs": o.canary_in_application_logs,
                "canary_in_observed_application_artifacts": o.canary_in_observed_application_artifacts,
                "unexpected_filesystem_writes": o.unexpected_filesystem_writes,
                "checks": o.checks,
                "notes": o.notes,
            }
            for o in scenario_outcomes
        ],
        "container_filesystem_changes": [
            {"path": path, "change_kind": kind} for path, kind in final_diff.items()
        ],
        "startup_filesystem_changes": [
            {"path": path, "change_kind": kind} for path, kind in diff_snapshots.get("startup", {}).items()
        ],
        "overall_result": overall_result,
    }
    return evidence


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--image", type=str, default=None, help="reuse an existing image tag instead of building")
    parser.add_argument("--no-build", action="store_true", help="don't (re)build the image; requires --image")
    parser.add_argument("--writable", action="store_true", help="run without --read-only (for comparison)")
    parser.add_argument("--tmpfs", action="append", default=[], help="e.g. /tmp:rw,size=16m (repeatable)")
    parser.add_argument("--port", type=int, default=None)
    parser.add_argument("--out", type=str, default=None, help="write the JSON result to this path")
    args = parser.parse_args(argv)

    if args.no_build and not args.image:
        parser.error("--no-build requires --image")

    evidence = run(
        image=args.image, build=not args.no_build, read_only=not args.writable,
        tmpfs=args.tmpfs, port=args.port,
    )

    if args.out:
        Path(args.out).write_text(json.dumps(evidence, indent=2, default=str))

    print(f"M3 run {evidence['run_id']}  gateway={evidence.get('gateway_commit', '?')[:12]}  "
          f"structure={evidence.get('structure_commit', '?')[:12]}")
    print(f"image: {evidence.get('image_tag')}  id={evidence.get('image_id', '?')[:19]}")
    print(f"read_only_rootfs={evidence.get('runtime', {}).get('read_only_rootfs')}")
    for scenario in evidence.get("scenarios", []):
        print(f"  [{scenario['result']:>13}] {scenario['scenario_id']} {scenario['name']} "
              f"(http={scenario['http_status']})")
    print(f"OVERALL: {evidence['overall_result']}")
    return 0 if evidence["overall_result"] == "PASS" else 1


if __name__ == "__main__":
    sys.exit(main())
