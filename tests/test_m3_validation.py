"""Tests for the M3 container persistence-boundary validation harness.

Same two-tier shape as tests/test_m2_validation.py:

* Unit tests against m3.py's `docker diff` parsing/delta logic and the
  shared scenarios.py fixtures/scoring -- fast, deterministic, no Docker
  daemon involved. These always run.
* One end-to-end smoke test that builds the real image and runs a real
  container through all four scenarios with `--read-only`. Skipped when
  `docker` isn't available/usable, so it degrades gracefully rather than
  failing the whole suite. On a cold build cache this can take a minute or
  two (compiling fastDICOMstructure); Docker layer caching makes repeat
  runs fast.

See docs/M3_CONTAINER_VALIDATION.md for what a PASS here does and does not
prove.
"""

from __future__ import annotations

import shutil
import subprocess

import pytest

from fastdicom_gateway.validation import m3
from fastdicom_gateway.validation import scenarios_publication_refresh as spr


# ---------------------------------------------------------------------------
# docker diff parsing / delta logic -- pure functions, no Docker needed
# ---------------------------------------------------------------------------


def test_diff_delta_reports_only_newly_appeared_paths():
    previous = {"/tmp": "C", "/tmp/a.bin": "A"}
    current = {"/tmp": "C", "/tmp/a.bin": "A", "/tmp/b.bin": "A"}
    delta = m3._diff_delta(previous, current)
    assert delta == {"/tmp/b.bin": "A"}


def test_diff_delta_is_empty_when_nothing_new_appears():
    """Pins the exact gap the M3 negative control found: docker diff keeps
    reporting an already-changed path with the same change-kind on a
    later overwrite, so a naive delta-only check would miss a second
    scenario's write to an already-'A'-marked file. m3.py's content-check
    loop (inside run(), while the container is still alive) is the fix --
    this test just documents why the delta alone isn't sufficient."""
    previous = {"/tmp/a.bin": "A"}
    current = {"/tmp/a.bin": "A"}  # overwritten again, same change-kind
    assert m3._diff_delta(previous, current) == {}


def test_diff_delta_from_empty_baseline():
    previous: dict[str, str] = {}
    current = {"/tmp/a.bin": "A"}
    assert m3._diff_delta(previous, current) == {"/tmp/a.bin": "A"}


# ---------------------------------------------------------------------------
# shared scenarios.py -- reused from M2's own test coverage, spot-checked
# here for the base_url-parameterized call shape m3.py depends on.
# ---------------------------------------------------------------------------


def test_scenario_functions_accept_a_base_url_not_a_handle():
    """m3.py calls scenario functions with a bare base_url string (a
    published container port), not an M2 ServerHandle -- this pins that
    contract so a future change to scenarios.py's signature is caught
    here rather than only inside a slow end-to-end container test."""
    import inspect

    from fastdicom_gateway.validation import scenarios as sc

    for fn in sc.ALL_SCENARIOS:
        params = list(inspect.signature(fn).parameters)
        assert params[:2] == ["base_url", "run_id"]


# ---------------------------------------------------------------------------
# End-to-end smoke test
# ---------------------------------------------------------------------------


def _docker_missing() -> bool:
    if shutil.which("docker") is None:
        return True
    try:
        result = subprocess.run(["docker", "info"], capture_output=True, timeout=10)
        return result.returncode != 0
    except Exception:
        return True


@pytest.mark.skipif(_docker_missing(), reason="docker not available/usable")
def test_m3_end_to_end_run_passes_read_only_with_corrected_scenario_d():
    """Live CI gate on the *current* gateway's M3 boundary behavior -- see
    test_m2_end_to_end_run_passes_with_corrected_scenario_d's docstring for
    the historical-vs-corrected Scenario D distinction (identical here:
    frozen docs/M3_CONTAINER_VALIDATION.md evidence is untouched and
    remains accurate for the pre-remediation implementation; this live run
    checks current behavior, per POST_REMEDIATION_EVIDENCE_REFRESH.md)."""
    evidence = m3.run(
        image="fastdicom-gateway:m3-pytest", build=True, read_only=True,
        scenario_fns=spr.CORRECTED_SCENARIOS,
    )
    assert evidence["overall_result"] == "PASS"
    assert evidence["runtime"]["read_only_rootfs"] is True
    assert evidence["runtime"]["writable_paths"] == []
    assert len(evidence["scenarios"]) == 4
    for scenario in evidence["scenarios"]:
        assert scenario["result"] == "PASS"
        assert scenario["canary_in_application_logs"] is False
        assert scenario["canary_in_observed_application_artifacts"] is False
        assert scenario["unexpected_filesystem_writes"] == []
    assert evidence["container_filesystem_changes"] == []
    assert evidence["canary_in_logs"] is False
