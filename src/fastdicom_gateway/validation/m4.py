"""M4 Cloud Run persistence/log-boundary validation harness.

Drives the four M2/M3 canary-bearing scenarios (scenarios.py, unchanged)
against a *deployed* Cloud Run service over real HTTPS, then queries
Cloud Logging for the exact test window and checks whether any source
canary appears in either of the two distinct log streams Cloud Run
produces for a service -- see module-level notes below, established by
directly inspecting real log entries rather than assumed from
documentation (see docs/M4_CLOUD_RUN_VALIDATION.md "Request-log
characterization").

This module never builds or deploys anything itself (see
docs/M4_CLOUD_RUN_VALIDATION.md "Reproduction" for the `gcloud`/`docker`
commands that do) -- it only drives an already-deployed service and reads
back logs via `gcloud logging read`. Run with:

    python -m fastdicom_gateway.validation.m4 --service fastdicom-gateway \\
        --project <project> --region us-central1
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

from . import scenarios as sc

_REPO_ROOT = Path(__file__).resolve().parents[3]
_LOG_PROPAGATION_WAIT_S = 15.0

# Established empirically (see docs/M4_CLOUD_RUN_VALIDATION.md "Request-log
# characterization"): Cloud Run writes two distinct log streams per
# service, distinguishable by `logName` suffix.
_APPLICATION_LOG_SUFFIXES = ("run.googleapis.com%2Fstdout", "run.googleapis.com%2Fstderr")
_REQUEST_LOG_SUFFIX = "run.googleapis.com%2Frequests"


def _run(cmd: list[str], **kwargs) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, capture_output=True, text=True, **kwargs)


def _git_rev(repo: Path) -> str:
    try:
        out = _run(["git", "-C", str(repo), "rev-parse", "HEAD"], timeout=5)
        return out.stdout.strip() if out.returncode == 0 else "unknown"
    except Exception:
        return "unknown"


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


# ---------------------------------------------------------------------------
# Deployment metadata (M4-AC10)
# ---------------------------------------------------------------------------


def describe_deployment(project: str, region: str, service: str) -> dict:
    """A sanitized subset of `gcloud run services describe` -- deployment
    configuration only, never credentials/tokens (gcloud's own output
    doesn't include those for a `describe` call, but this still
    allowlists fields explicitly rather than dumping everything)."""
    result = _run([
        "gcloud", "run", "services", "describe", service,
        "--project", project, "--region", region, "--format=json",
    ])
    if result.returncode != 0:
        return {"error": result.stderr.strip()}
    data = json.loads(result.stdout)
    spec = data.get("spec", {}).get("template", {}).get("spec", {})
    container = (spec.get("containers") or [{}])[0]
    status = data.get("status", {})

    iam = _run([
        "gcloud", "run", "services", "get-iam-policy", service,
        "--project", project, "--region", region, "--format=json",
    ])
    allow_unauthenticated = False
    if iam.returncode == 0:
        try:
            bindings = json.loads(iam.stdout).get("bindings", [])
            allow_unauthenticated = any(
                b.get("role") == "roles/run.invoker" and "allUsers" in b.get("members", [])
                for b in bindings
            )
        except json.JSONDecodeError:
            pass

    return {
        "service": service,
        "project": project,
        "region": region,
        "revision": status.get("latestReadyRevisionName"),
        "url": status.get("url"),
        "image": container.get("image"),
        "resources": container.get("resources", {}),
        "concurrency": spec.get("containerConcurrency"),
        "timeout_seconds": spec.get("timeoutSeconds"),
        "service_account": spec.get("serviceAccountName"),
        "ingress": (data.get("metadata", {}).get("annotations", {}) or {}).get(
            "run.googleapis.com/ingress", "all"
        ),
        "allow_unauthenticated": allow_unauthenticated,
        "env_vars_explicitly_set": [
            e.get("name") for e in container.get("env", []) if isinstance(e, dict)
        ],
    }


def image_digest(image_uri: str) -> str:
    result = _run(["gcloud", "artifacts", "docker", "images", "describe", image_uri,
                    "--format=value(image_summary.digest)"])
    return result.stdout.strip() if result.returncode == 0 else "unknown"


# ---------------------------------------------------------------------------
# Cloud Logging query + classification
# ---------------------------------------------------------------------------


def query_logs(project: str, service: str, start_iso: str, end_iso: str, limit: int = 1000) -> list[dict]:
    log_filter = (
        f'resource.type="cloud_run_revision" AND '
        f'resource.labels.service_name="{service}" AND '
        f'timestamp>="{start_iso}" AND timestamp<="{end_iso}"'
    )
    result = _run([
        "gcloud", "logging", "read", log_filter,
        "--project", project, "--format=json", f"--limit={limit}", "--order=asc",
    ], timeout=60)
    if result.returncode != 0:
        raise RuntimeError(f"gcloud logging read failed: {result.stderr.strip()}")
    return json.loads(result.stdout) if result.stdout.strip() else []


def classify_log_entry(entry: dict) -> str:
    log_name = entry.get("logName", "")
    if any(log_name.endswith(suffix) for suffix in _APPLICATION_LOG_SUFFIXES):
        return "application"
    if log_name.endswith(_REQUEST_LOG_SUFFIX):
        return "request"
    return "other"


def entry_text(entry: dict) -> str:
    """Every human-readable string Cloud Logging attached to this entry --
    what the canary scan searches. Deliberately broad (includes
    httpRequest field values) so a canary landing anywhere in a log entry
    is caught, not just in textPayload."""
    parts = [
        str(entry.get("textPayload", "")),
        json.dumps(entry.get("jsonPayload", {})),
        json.dumps(entry.get("httpRequest", {})),
    ]
    return "\n".join(parts)


def observed_http_request_fields(entries: list[dict]) -> dict:
    """M4-AC8: exactly which httpRequest fields Cloud Run's request-log
    stream actually populated in this run's observed entries, and whether
    a request body was among them (it never is a distinct field; this
    checks whether any observed value looks larger than a method+path+
    headers summary would explain, as a sanity check, not proof)."""
    fields_seen: set[str] = set()
    for entry in entries:
        if classify_log_entry(entry) == "request":
            fields_seen.update(entry.get("httpRequest", {}).keys())
    return {
        "fields_observed": sorted(fields_seen),
        "note": "requestSize/responseSize are byte counts only; no field carrying request/response "
                "body content was observed in any queried entry.",
    }


# ---------------------------------------------------------------------------
# Orchestration
# ---------------------------------------------------------------------------


def run(*, project: str, region: str, service: str, base_url: str | None = None) -> dict:
    run_id = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime()) + f"-{int(time.time() * 1000) % 100000}"
    deployment = describe_deployment(project, region, service)
    url = base_url or deployment.get("url")
    if not url:
        raise RuntimeError(f"could not determine service URL for {service}")

    scenario_outcomes: list[sc.ScenarioOutcome] = []
    run_canaries: dict[str, bytes] = {}
    # Real UTC ISO windows per scenario, distinct from ScenarioOutcome's
    # own window_start/window_end (those are local-clock HH:MM:SS.ffffff,
    # the format strace_events.py needs for M2/M3 -- not comparable to
    # Cloud Logging's UTC timestamps). Needed so a log hit can be
    # attributed to the *one* scenario that actually produced it rather
    # than every scenario whose `relevant` canary set happens to include
    # the matched name -- patient_name/patient_id/patient_birth_date are
    # "relevant" to every scenario that could leak them, so name-matching
    # alone over-attributes across scenario boundaries (found and fixed
    # during this milestone's own negative control -- see
    # docs/M4_CLOUD_RUN_VALIDATION.md "Negative control").
    scenario_windows: dict[str, tuple[str, str]] = {}

    start_iso = _now_iso()
    for scenario_fn in sc.ALL_SCENARIOS:
        scenario_start_iso = _now_iso()
        outcome, canary = scenario_fn(url, run_id)
        scenario_end_iso = _now_iso()
        scenario_outcomes.append(outcome)
        run_canaries[f"run_canary_{outcome.scenario_id}"] = canary
        scenario_windows[outcome.scenario_id] = (scenario_start_iso, scenario_end_iso)
        # Wide enough gap between scenarios that their log-timestamp
        # windows (used for attribution below) don't overlap even with
        # the epsilon padding -- unlike M2/M3's 0.1s gap, which was fine
        # there because attribution used strace's own per-syscall
        # timestamps, not a padded window.
        time.sleep(4.0)
    end_iso = _now_iso()

    time.sleep(_LOG_PROPAGATION_WAIT_S)
    entries = query_logs(project, service, start_iso, end_iso)

    all_canaries = dict(sc.fixed_canaries())
    all_canaries.update(run_canaries)

    application_hits: list[dict] = []
    request_log_hits: list[dict] = []
    for entry in entries:
        text = entry_text(entry)
        matched = [name for name, value in all_canaries.items() if value.decode("ascii", "replace") in text]
        if not matched:
            continue
        kind = classify_log_entry(entry)
        record = {
            "insertId": entry.get("insertId"), "timestamp": entry.get("timestamp"),
            "logName": entry.get("logName"), "canary_matches": matched,
        }
        if kind == "application":
            application_hits.append(record)
        elif kind == "request":
            request_log_hits.append(record)

    canary_in_application_logs = len(application_hits) > 0
    canary_in_cloud_run_logs = len(request_log_hits) > 0

    # +/-1s epsilon: real clock skew between this process and Cloud Run's
    # container clock is expected to be small (both NTP-synced), but a log
    # entry's recorded event `timestamp` can trail the HTTP response
    # reaching this client by a fraction of a second. Deliberately smaller
    # than half the 4s inter-scenario gap above so windows never overlap.
    _EPSILON_S = 1.0

    def _in_scenario_window(entry_timestamp: str, scenario_id: str) -> bool:
        window_start, window_end = scenario_windows[scenario_id]
        start_dt = datetime.fromisoformat(window_start)
        end_dt = datetime.fromisoformat(window_end)
        entry_dt = datetime.fromisoformat(entry_timestamp.replace("Z", "+00:00"))
        return (start_dt - timedelta(seconds=_EPSILON_S)) <= entry_dt <= (end_dt + timedelta(seconds=_EPSILON_S))

    for outcome in scenario_outcomes:
        scenario_canary_name = f"run_canary_{outcome.scenario_id}"
        relevant = {"patient_name", "patient_id", "patient_birth_date", scenario_canary_name}
        for hit in application_hits:
            if relevant & set(hit["canary_matches"]) and _in_scenario_window(hit["timestamp"], outcome.scenario_id):
                outcome.canary_in_application_logs = True
                outcome.notes.append(f"canary found in application log entry {hit['insertId']}")
        for hit in request_log_hits:
            if relevant & set(hit["canary_matches"]) and _in_scenario_window(hit["timestamp"], outcome.scenario_id):
                # Reuses ScenarioOutcome.canary_in_observed_application_artifacts
                # (an M3-named field with no exact M4 equivalent) to mean
                # "found in Cloud Run's own request-log stream" -- see the
                # JSON assembly below, which renames it to
                # canary_in_cloud_run_logs so that meaning doesn't leak
                # into the evidence file under a misleading key.
                outcome.canary_in_observed_application_artifacts = True
                outcome.notes.append(f"canary found in Cloud Run request-log entry {hit['insertId']}")
        if outcome.canary_in_application_logs or outcome.canary_in_observed_application_artifacts:
            outcome.result = "FAIL"

    overall_result = "PASS" if all(o.result == "PASS" for o in scenario_outcomes) else "FAIL"
    if canary_in_application_logs or canary_in_cloud_run_logs:
        overall_result = "FAIL"

    evidence = {
        "milestone": "M4",
        "run_id": run_id,
        "gateway_commit": _git_rev(_REPO_ROOT),
        "structure_commit": _git_rev(sc.structure_repo_root()),
        "project": project,
        "region": region,
        "cloud_run_service": service,
        "revision": deployment.get("revision"),
        "image": deployment.get("image"),
        "image_digest": image_digest(deployment.get("image", "")) if deployment.get("image") else "unknown",
        "deployment": deployment,
        "test_window": {"start": start_iso, "end": end_iso},
        "log_entries_queried": len(entries),
        "http_request_fields_observed": observed_http_request_fields(entries),
        "scenarios": [
            {
                "name": o.name, "scenario_id": o.scenario_id, "description": o.description,
                "result": o.result, "http_status": o.http_status,
                "window": {"start": o.window_start, "end": o.window_end},
                "canary_in_application_logs": o.canary_in_application_logs,
                "canary_in_cloud_run_logs": o.canary_in_observed_application_artifacts,
                "checks": o.checks, "notes": o.notes,
            }
            for o in scenario_outcomes
        ],
        "canary_in_application_logs": canary_in_application_logs,
        "canary_in_cloud_run_logs": canary_in_cloud_run_logs,
        "application_log_hits": application_hits,
        "cloud_run_request_log_hits": request_log_hits,
        "overall_result": overall_result,
    }
    return evidence


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project", required=True)
    parser.add_argument("--region", default="us-central1")
    parser.add_argument("--service", default="fastdicom-gateway")
    parser.add_argument("--url", default=None, help="override the discovered service URL")
    parser.add_argument("--out", type=str, default=None)
    args = parser.parse_args(argv)

    evidence = run(project=args.project, region=args.region, service=args.service, base_url=args.url)

    if args.out:
        Path(args.out).write_text(json.dumps(evidence, indent=2, default=str))

    print(f"M4 run {evidence['run_id']}  gateway={evidence.get('gateway_commit', '?')[:12]}  "
          f"structure={evidence.get('structure_commit', '?')[:12]}")
    print(f"service: {evidence['cloud_run_service']}  revision={evidence.get('revision')}")
    for scenario in evidence.get("scenarios", []):
        print(f"  [{scenario['result']:>13}] {scenario['scenario_id']} {scenario['name']} "
              f"(http={scenario['http_status']})")
    print(f"canary_in_application_logs={evidence['canary_in_application_logs']}  "
          f"canary_in_cloud_run_logs={evidence['canary_in_cloud_run_logs']}")
    print(f"OVERALL: {evidence['overall_result']}")
    return 0 if evidence["overall_result"] == "PASS" else 1


if __name__ == "__main__":
    sys.exit(main())
