"""M2 persistence-boundary validation harness.

Launches a real `fastdicom_gateway` server as its own OS process (wrapped
in `strace -f` when available), drives it over real HTTP with
canary-bearing synthetic DICOM fixtures, and then checks -- from outside
that process -- whether any source canary reached an application-created
log or filesystem artifact before policy transformation.

This module is deliberately a standalone tool, not part of the request
path it observes: it never imports `fastdicom_gateway.app`/`transform`
into its own process for the observed server (it must run as a genuinely
separate process to be traceable), though it does import `transform`'s
sibling `fastdicomstructure` binding to verify Scenario A's output.

See docs/M2_PERSISTENCE_BOUNDARY_VALIDATION.md for the hypothesis, scope,
non-claims, and how to read a result. Run with:

    python -m fastdicom_gateway.validation.m2
"""

from __future__ import annotations

import argparse
import contextlib
import json
import os
import shutil
import signal
import socket
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
import uuid
from dataclasses import dataclass
from pathlib import Path

from . import scenarios as sc
from . import strace_events as se

_REPO_ROOT = Path(__file__).resolve().parents[3]
_STRACE_STRING_SIZE = 8192
_READY_TIMEOUT_S = 20.0
_SHUTDOWN_TIMEOUT_S = 10.0
_MTIME_EPSILON_S = 2.0  # clock-resolution safety margin for the artifact scan

# Re-exported for readability at call sites in this module.
_now_ts = sc.now_ts
_sha256_12 = sc.sha256_12
_structure_repo_root = sc.structure_repo_root
_http_request = sc.http_request
_epoch_for_ts = sc.epoch_for_ts


def _find_free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def _default_structure_lib(structure_repo: Path) -> Path | None:
    for build_dir in ("build", "build-release"):
        candidate = structure_repo / build_dir / "libfastdicomstructure_c.so"
        if candidate.is_file():
            return candidate
    return None


def _git_rev(repo: Path) -> str:
    try:
        out = subprocess.run(
            ["git", "-C", str(repo), "rev-parse", "HEAD"],
            capture_output=True, text=True, timeout=5, check=True,
        )
        return out.stdout.strip()
    except Exception:
        return "unknown"


# ---------------------------------------------------------------------------
# Server process management
# ---------------------------------------------------------------------------


@dataclass
class ServerHandle:
    process: subprocess.Popen
    port: int
    stdout_path: Path
    stderr_path: Path
    trace_path: Path | None
    strace_available: bool
    launch_ts: str
    ready_ts: str = ""


def _launch_server(workspace: Path, port: int, strace_string_size: int, use_strace: bool) -> ServerHandle:
    structure_repo = _structure_repo_root()
    env = dict(os.environ)
    env.setdefault("FASTDICOMSTRUCTURE_REPO", str(structure_repo))
    if "FASTDICOMSTRUCTURE_LIB" not in env:
        lib = _default_structure_lib(structure_repo)
        if lib is not None:
            env["FASTDICOMSTRUCTURE_LIB"] = str(lib)

    stdout_path = workspace / "server.stdout.log"
    stderr_path = workspace / "server.stderr.log"
    trace_path = workspace / "server.strace.log"

    server_cmd = [
        sys.executable, "-m", "uvicorn", "fastdicom_gateway.app:app",
        "--host", "127.0.0.1", "--port", str(port), "--log-level", "info",
    ]

    strace_path = shutil.which("strace") if use_strace else None
    if strace_path:
        cmd = [
            strace_path, "-f", "-tt", "-yy", "-s", str(strace_string_size),
            "-e", "trace=%file,write", "-o", str(trace_path), "--",
        ] + server_cmd
    else:
        cmd = server_cmd
        trace_path = None

    launch_ts = _now_ts()
    with open(stdout_path, "wb") as out, open(stderr_path, "wb") as err:
        process = subprocess.Popen(
            cmd, cwd=str(_REPO_ROOT), env=env, stdout=out, stderr=err,
            start_new_session=True,
        )
    return ServerHandle(
        process=process, port=port, stdout_path=stdout_path, stderr_path=stderr_path,
        trace_path=trace_path, strace_available=strace_path is not None, launch_ts=launch_ts,
    )


def _wait_ready(handle: ServerHandle) -> bool:
    deadline = time.time() + _READY_TIMEOUT_S
    url = f"http://127.0.0.1:{handle.port}/healthz"
    while time.time() < deadline:
        if handle.process.poll() is not None:
            return False
        try:
            status, _headers, _body = _http_request(url, None, {})
            if status == 200:
                handle.ready_ts = _now_ts()
                return True
        except (urllib.error.URLError, ConnectionError, OSError):
            pass
        time.sleep(0.1)
    return False


def _shutdown_server(handle: ServerHandle) -> None:
    if handle.process.poll() is not None:
        return
    with contextlib.suppress(ProcessLookupError):
        os.killpg(os.getpgid(handle.process.pid), signal.SIGTERM)
    try:
        handle.process.wait(timeout=_SHUTDOWN_TIMEOUT_S)
    except subprocess.TimeoutExpired:
        with contextlib.suppress(ProcessLookupError):
            os.killpg(os.getpgid(handle.process.pid), signal.SIGKILL)
        handle.process.wait(timeout=_SHUTDOWN_TIMEOUT_S)


# Scenario definitions (ScenarioOutcome, run_scenario_a/b/c/d) live in
# scenarios.py, shared with m3.py -- see that module's docstring.

# ---------------------------------------------------------------------------
# Evidence collection: logs, syscalls, filesystem scan
# ---------------------------------------------------------------------------


def _scan_logs_for_canaries(handle: ServerHandle, canaries: list[bytes]) -> dict[str, list[str]]:
    hits: dict[str, list[str]] = {}
    for path in (handle.stdout_path, handle.stderr_path):
        try:
            data = path.read_bytes()
        except OSError:
            continue
        for canary in canaries:
            if canary in data:
                hits.setdefault(str(path), []).append(_sha256_12(canary))
    return hits


def _bucket_events_by_window(events: list[se.Event], windows: list[tuple[str, str, str]]) -> dict[str, list[se.Event]]:
    """windows: list of (scenario_id, start, end), assumed in chronological
    order and non-overlapping. Events before the first window's start are
    'startup'; events after the last window's end are 'shutdown'."""
    buckets: dict[str, list[se.Event]] = {sid: [] for sid, _s, _e in windows}
    buckets["startup"] = []
    buckets["shutdown"] = []
    if not windows:
        buckets["shutdown"] = list(events)
        return buckets
    first_start = windows[0][1]
    last_end = windows[-1][2]
    for event in events:
        if event.timestamp < first_start:
            buckets["startup"].append(event)
            continue
        if event.timestamp > last_end:
            buckets["shutdown"].append(event)
            continue
        placed = False
        for sid, start, end in windows:
            if start <= event.timestamp <= end:
                buckets[sid].append(event)
                placed = True
                break
        if not placed:
            # Falls between two scenario windows (idle gap) -- attribute to
            # 'shutdown' bucket's neighbour is ambiguous, so keep it visible
            # under a dedicated key rather than silently dropping it.
            buckets.setdefault("between_scenarios", []).append(event)
    return buckets


def _scanned_directories(workspace: Path) -> list[tuple[str, Path, list[str]]]:
    """The bounded, documented set of areas M2 scans for canary leakage, and
    what is pruned from each before scanning -- see
    docs/M2_PERSISTENCE_BOUNDARY_VALIDATION.md "Harness vs. application
    persistence" for why each exclusion is there. Two of these are the
    same class of confound: they are harness/test-tooling source code that
    legitimately contains the literal canary constants as Python source
    (`validation/fixtures.py`, `tests/conftest.py` and this suite's own
    `tests/test_m2_validation.py`) -- whichever process is the *first* to
    import one of those modules in this environment compiles a
    __pycache__/*.pyc embedding those constants in its bytecode co_consts.
    That's harness/test-runner self-caching, not something the traced
    gateway subprocess ever executes (it imports only `app`/`transform`/
    `logging`, never `validation` or `tests`)."""
    structure_repo = _structure_repo_root()
    return [
        (
            "gateway_repo", _REPO_ROOT,
            [
                ".git", ".venv", ".pytest_cache",
                os.path.join("src", "fastdicom_gateway", "validation"),
                "tests",
            ],
        ),
        ("structure_repo", structure_repo, [".git", "build", "build-release"]),
        ("system_temp_dir", Path(tempfile.gettempdir()), [str(workspace)]),
    ]


def _scan_filesystem_for_canaries(
    workspace: Path, run_start_epoch: float, canaries: dict[str, bytes],
) -> list[dict]:
    """Walks the bounded, documented set of directories for files modified
    at or after run_start_epoch, and searches their raw bytes for every
    canary. Returns a list of {path, area, mtime, canary_matches}."""
    threshold = run_start_epoch - _MTIME_EPSILON_S
    hits: list[dict] = []
    for area, root, excludes in _scanned_directories(workspace):
        if not root.is_dir():
            continue
        exclude_paths = [str(root / e) if not os.path.isabs(e) else e for e in excludes]
        for dirpath, dirnames, filenames in os.walk(root):
            dirnames[:] = [
                d for d in dirnames
                if os.path.join(dirpath, d) not in exclude_paths and d not in (".git",)
            ]
            for filename in filenames:
                full_path = os.path.join(dirpath, filename)
                try:
                    stat_result = os.stat(full_path)
                except OSError:
                    continue
                if stat_result.st_mtime < threshold:
                    continue
                try:
                    data = Path(full_path).read_bytes()
                except OSError:
                    continue
                matches = [name for name, value in canaries.items() if value in data]
                if matches:
                    hits.append({
                        "path": full_path, "area": area,
                        "mtime": stat_result.st_mtime, "canary_matches": matches,
                    })
    return hits


# ---------------------------------------------------------------------------
# Orchestration
# ---------------------------------------------------------------------------


def run(
    *,
    port: int | None = None,
    use_strace: bool = True,
    workspace_dir: str | None = None,
    scenario_fns: tuple | None = None,
) -> dict:
    # `scenario_fns` defaults to the original, frozen `sc.ALL_SCENARIOS` --
    # every existing caller (including the M2 milestone's own reproduction
    # command and test_m2_validation.py's historical-behavior tests) is
    # unaffected. It exists solely so
    # POST_REMEDIATION_EVIDENCE_REFRESH.md's corrected Scenario D
    # (validation/scenarios_publication_refresh.py) can reuse this same
    # observation apparatus -- launch, strace, log/filesystem scanning --
    # without duplicating it or mutating this frozen module's own default
    # scenario set.
    scenario_fns = scenario_fns or sc.ALL_SCENARIOS
    run_id = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime()) + "-" + uuid.uuid4().hex[:8]
    workspace = Path(workspace_dir) if workspace_dir else Path(tempfile.mkdtemp(prefix="fastdicom_gateway_m2_"))
    workspace.mkdir(parents=True, exist_ok=True)
    port = port or _find_free_port()

    handle = _launch_server(workspace, port, _STRACE_STRING_SIZE, use_strace)
    scenario_outcomes: list[sc.ScenarioOutcome] = []
    windows: list[tuple[str, str, str]] = []
    run_canaries: dict[str, bytes] = {}
    strace_note = None
    base_url = f"http://127.0.0.1:{handle.port}"

    try:
        ready = _wait_ready(handle)
        if not ready:
            raise RuntimeError(
                "gateway server did not become ready within the timeout -- see "
                f"{handle.stdout_path} / {handle.stderr_path}"
            )

        for scenario_fn in scenario_fns:
            outcome, canary = scenario_fn(base_url, run_id)
            scenario_outcomes.append(outcome)
            windows.append((outcome.scenario_id, outcome.window_start, outcome.window_end))
            run_canaries[f"run_canary_{outcome.scenario_id}"] = canary
            time.sleep(0.1)  # let async log writes for this request flush before the next one

        time.sleep(0.2)
    finally:
        _shutdown_server(handle)

    # --- Logging evidence ---------------------------------------------
    all_canaries = dict(sc.fixed_canaries())
    all_canaries.update(run_canaries)

    log_hits = _scan_logs_for_canaries(handle, list(all_canaries.values()))
    log_text = ""
    for path in (handle.stdout_path, handle.stderr_path):
        with contextlib.suppress(OSError):
            log_text += path.read_text(errors="replace")
    # Informational only -- a traceback in server-side logs for a 500 is
    # normal operational behavior (see app.py's `except Exception` branch,
    # which deliberately uses logger.exception for this). What matters is
    # whether a canary is *inside* one, which the per-scenario
    # canary_in_application_logs check below already covers (it scans the
    # full log text, tracebacks included).
    traceback_present_in_logs = "Traceback (most recent call last)" in log_text

    # --- Syscall evidence -----------------------------------------------
    strace_summary = None
    buckets: dict[str, list[se.Event]] = {}
    if handle.strace_available and handle.trace_path and handle.trace_path.exists():
        strace_summary = se.parse_strace_file(str(handle.trace_path))
        mutating = se.mutating_events(strace_summary)
        buckets = _bucket_events_by_window(mutating, windows)
    else:
        strace_note = "strace was not available or produced no trace; filesystem-write " \
                       "accounting relies solely on the post-run directory scan (see Limitations)."

    for outcome in scenario_outcomes:
        events = buckets.get(outcome.scenario_id, [])
        outcome.unexpected_filesystem_writes = [
            {"path": e.path, "syscall": e.syscall, "timestamp": e.timestamp, "args": e.args}
            for e in events
        ]

    # Non-request-time buckets (interpreter/library startup, shutdown, and
    # any idle gap between scenarios) are reported for transparency even
    # though they don't gate a scenario's PASS/FAIL -- see M2-AC5 ("request-
    # time filesystem writes are identified and classified rather than
    # ignored"); this is what makes that classification honest rather than
    # just silently dropping non-request-time activity.
    non_scenario_events = {
        bucket: [
            {"path": e.path, "syscall": e.syscall, "timestamp": e.timestamp, "args": e.args}
            for e in events
        ]
        for bucket, events in buckets.items()
        if bucket in ("startup", "shutdown", "between_scenarios") and events
    }

    # --- Filesystem artifact scan ----------------------------------------
    run_start_epoch = _epoch_for_ts(handle.launch_ts)
    fs_hits = _scan_filesystem_for_canaries(workspace, run_start_epoch, all_canaries)

    # --- Attribute logging/filesystem findings back onto scenarios -------
    for outcome in scenario_outcomes:
        scenario_canary_name = f"run_canary_{outcome.scenario_id}"
        relevant_names = {"patient_name", "patient_id", "patient_birth_date", scenario_canary_name}
        for path_key, matched_hashes in log_hits.items():
            relevant_hashes = {_sha256_12(all_canaries[n]) for n in relevant_names}
            if relevant_hashes & set(matched_hashes):
                outcome.canary_in_application_logs = True
                outcome.notes.append(f"canary found in {path_key}")
        for hit in fs_hits:
            if hit["mtime"] < _epoch_for_ts(outcome.window_start) - _MTIME_EPSILON_S:
                continue
            if hit["mtime"] > _epoch_for_ts(outcome.window_end) + _MTIME_EPSILON_S:
                continue
            relevant = {"patient_name", "patient_id", "patient_birth_date", scenario_canary_name}
            if relevant & set(hit["canary_matches"]):
                outcome.canary_in_observed_application_artifacts = True
                outcome.notes.append(f"canary found in application-created artifact {hit['path']}")
        if outcome.unexpected_filesystem_writes:
            # The gateway is expected to make zero filesystem writes while
            # handling a request (see README.md's persistence claim); any
            # write-capable open() during a scenario's window is significant
            # on its own, independent of whether a canary is later found in
            # that file's *final* on-disk content -- a file re-opened with
            # O_TRUNC by a later request would otherwise only implicate its
            # last writer even though strace shows every request touched it.
            outcome.result = "FAIL"
            outcome.notes.append(
                f"{len(outcome.unexpected_filesystem_writes)} request-time write-capable "
                "filesystem open(s) observed (see unexpected_filesystem_writes)"
            )
        if outcome.canary_in_application_logs or outcome.canary_in_observed_application_artifacts:
            outcome.result = "FAIL"
        if strace_note:
            outcome.notes.append(strace_note)

    overall_result = "PASS" if all(o.result in ("PASS", "NOT_EXERCISED") for o in scenario_outcomes) else "FAIL"

    scanned_areas = [
        {"area": area, "root": str(root), "excluded": excludes}
        for area, root, excludes in _scanned_directories(workspace)
    ]

    evidence = {
        "milestone": "M2",
        "run_id": run_id,
        "gateway_commit": _git_rev(_REPO_ROOT),
        "structure_commit": _git_rev(_structure_repo_root()),
        "environment": {
            "os": f"{os.uname().sysname} {os.uname().release}" if hasattr(os, "uname") else sys.platform,
            "python": sys.version.split()[0],
            "strace_available": handle.strace_available,
            "strace_version": _strace_version() if handle.strace_available else None,
        },
        "workspace": str(workspace),
        "scanned_areas": scanned_areas,
        "strace_parse": (
            {
                "total_lines": strace_summary.total_lines,
                "parsed_lines": strace_summary.parsed_lines,
                "unparsed_lines": strace_summary.unparsed_lines,
            }
            if strace_summary is not None
            else None
        ),
        "non_request_time_filesystem_writes": non_scenario_events,
        "traceback_present_in_logs": traceback_present_in_logs,
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
        "filesystem_scan_hits": fs_hits,
        "overall_result": overall_result,
    }
    (workspace / "m2_result.json").write_text(json.dumps(evidence, indent=2, default=str))
    return evidence


def _epoch_for_ts(ts: str) -> float:
    """Converts an HH:MM:SS.ffffff wall-clock string (today's date, same
    assumption as strace_events.in_window) into a comparable epoch float."""
    struct_time = time.strptime(time.strftime("%Y-%m-%d ") + ts.split(".")[0], "%Y-%m-%d %H:%M:%S")
    frac = float("0." + ts.split(".")[1]) if "." in ts else 0.0
    return time.mktime(struct_time) + frac


def _strace_version() -> str:
    try:
        out = subprocess.run(["strace", "-V"], capture_output=True, text=True, timeout=5)
        return out.stdout.splitlines()[0] if out.stdout else "unknown"
    except Exception:
        return "unknown"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=None)
    parser.add_argument("--no-strace", action="store_true", help="skip syscall tracing")
    parser.add_argument("--workspace", type=str, default=None, help="reuse a specific workspace dir")
    parser.add_argument("--out", type=str, default=None, help="also write the JSON result to this path")
    args = parser.parse_args(argv)

    evidence = run(port=args.port, use_strace=not args.no_strace, workspace_dir=args.workspace)

    if args.out:
        Path(args.out).write_text(json.dumps(evidence, indent=2, default=str))

    print(f"M2 run {evidence['run_id']}  gateway={evidence['gateway_commit'][:12]}  "
          f"structure={evidence['structure_commit'][:12]}")
    print(f"workspace: {evidence['workspace']}")
    for scenario in evidence["scenarios"]:
        print(f"  [{scenario['result']:>13}] {scenario['scenario_id']} {scenario['name']} "
              f"(http={scenario['http_status']})")
    print(f"OVERALL: {evidence['overall_result']}")
    return 0 if evidence["overall_result"] == "PASS" else 1


if __name__ == "__main__":
    sys.exit(main())
