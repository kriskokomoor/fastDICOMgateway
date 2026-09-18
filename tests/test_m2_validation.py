"""Tests for the M2 persistence-boundary validation harness itself.

Two tiers:

* Unit tests against fixtures.py and strace_events.py -- fast, deterministic,
  no subprocess/strace involved. These always run.
* One end-to-end smoke test that actually runs
  `fastdicom_gateway.validation.m2.run()` -- launches a real gateway
  subprocess under strace and exercises all four scenarios. Skipped when
  `strace` isn't on PATH or the fastDICOMstructure shared library can't be
  located, so it degrades gracefully in an environment that can't support
  it rather than failing the whole suite.

See docs/M2_PERSISTENCE_BOUNDARY_VALIDATION.md for what a PASS here does
and does not prove.
"""

from __future__ import annotations

import shutil

import pytest

from fastdicom_gateway.validation import fixtures as fx
from fastdicom_gateway.validation import m2
from fastdicom_gateway.validation import scenarios_publication_refresh as spr
from fastdicom_gateway.validation import strace_events as se


# ---------------------------------------------------------------------------
# fixtures.py
# ---------------------------------------------------------------------------


def test_success_fixture_contains_all_fixed_canaries():
    body = fx.build_success_fixture("RUN1", "A")
    assert fx.PATIENT_NAME in body
    assert fx.PATIENT_ID in body
    assert fx.PATIENT_BIRTH_DATE in body
    assert fx.run_canary("RUN1", "A") in body


def test_success_fixture_is_explicit_vr_and_parses(monkeypatch):
    fastdicomstructure = pytest.importorskip("fastdicom_gateway.transform").fds
    body = fx.build_success_fixture("RUN1", "A")
    structure = fastdicomstructure.read_buffer(body, fidelity="lossless")
    try:
        assert structure.transfer_syntax_uid == "1.2.840.10008.1.2.1"
        blocking = [d for d in structure.diagnostics if d.severity != "info"]
        assert blocking == []
    finally:
        structure.close()


def test_malformed_fixture_has_no_dicom_magic():
    body = fx.build_malformed_fixture("RUN1", "B")
    assert body[128:132] != b"DICM"
    assert fx.run_canary("RUN1", "B") in body


def test_rejected_fixture_is_truncated_success_fixture():
    full = fx.build_success_fixture("RUN1", "C")
    truncated = fx.build_rejected_fixture("RUN1", "C")
    assert len(truncated) < len(full)
    assert truncated == full[:-50]


def test_rejected_fixture_produces_a_blocking_non_info_diagnostic():
    fastdicomstructure = pytest.importorskip("fastdicom_gateway.transform").fds
    body = fx.build_rejected_fixture("RUN1", "C")
    structure = fastdicomstructure.read_buffer(body, fidelity="lossless")
    try:
        blocking = [d for d in structure.diagnostics if d.severity != "info"]
        assert blocking, "expected at least one non-info diagnostic from truncation"
    finally:
        structure.close()


def test_implicit_vr_fixture_has_no_targeted_tags_and_is_unsupported_to_write():
    """Pins the exact mechanism Scenario D relies on: an unmodified
    Implicit VR structure with no targeted tags reaches write_bytes()
    unmodified and gets FDS_STATUS_UNSUPPORTED -- not a gateway bug, a
    documented fastDICOMstructure round-trip-contract case."""
    transform = pytest.importorskip("fastdicom_gateway.transform")
    fds = transform.fds
    body = fx.build_unmodified_implicit_vr_fixture("RUN1", "D")
    structure = fds.read_buffer(body, fidelity="lossless")
    try:
        assert structure.transfer_syntax_uid == "1.2.840.10008.1.2"
        assert not structure.is_explicit_vr
        assert (0x0010, 0x0010) not in structure
        assert (0x0010, 0x0020) not in structure
        assert (0x0010, 0x0030) not in structure
        touched, private_removed = transform._apply_demo_policy(structure)
        assert touched == 0
        assert private_removed == 0
        assert not structure.is_modified
        with pytest.raises(fds.FdsError):
            structure.write_bytes()
    finally:
        structure.close()


def test_run_canary_is_unique_per_run_and_scenario():
    a1 = fx.run_canary("RUN1", "A")
    a2 = fx.run_canary("RUN2", "A")
    b1 = fx.run_canary("RUN1", "B")
    assert len({a1, a2, b1}) == 3


# ---------------------------------------------------------------------------
# strace_events.py -- deterministic parsing against embedded sample text,
# no real strace invocation needed.
# ---------------------------------------------------------------------------

_SAMPLE_STRACE = """\
100 10:00:00.100000 openat(AT_FDCWD, "/usr/lib/libc.so.6", O_RDONLY|O_CLOEXEC) = 3</usr/lib/libc.so.6>
100 10:00:00.200000 openat(AT_FDCWD, "/tmp/leak.txt", O_WRONLY|O_CREAT|O_TRUNC, 0644) = 5</tmp/leak.txt>
100 10:00:00.250000 write(5</tmp/leak.txt>, "SAMPLE_WRITE_PAYLOAD", 21) = 21
100 10:00:00.300000 write(4<socket:[9999]>, "HTTP/1.1 200 OK\\r\\n", 18) = 18
100 10:00:00.350000 unlink("/tmp/leak.txt") = 0
100 10:00:00.400000 openat(AT_FDCWD, "/tmp/readonly.txt", O_RDONLY) = 6</tmp/readonly.txt>
101 10:00:00.500000 <... openat resumed>) = 7</tmp/interrupted>
"""


def test_parser_finds_write_capable_open():
    summary = se.parse_strace_text(_SAMPLE_STRACE)
    opens = se.write_capable_opens(summary)
    assert len(opens) == 1
    assert opens[0].path == "/tmp/leak.txt"


def test_parser_does_not_flag_read_only_open():
    summary = se.parse_strace_text(_SAMPLE_STRACE)
    opens = se.write_capable_opens(summary)
    assert all(e.path != "/tmp/readonly.txt" for e in opens)


def test_parser_classifies_socket_vs_file_writes():
    summary = se.parse_strace_text(_SAMPLE_STRACE)
    writes = [e for e in summary.events if e.syscall == "write"]
    targets = {se.classify_fd_target(e.fd_target) for e in writes}
    assert targets == {"path", "socket"}


def test_mutating_events_includes_open_and_unlink():
    summary = se.parse_strace_text(_SAMPLE_STRACE)
    mutating = se.mutating_events(summary)
    syscalls = {e.syscall for e in mutating}
    assert syscalls == {"openat", "unlink"}


def test_unfinished_resumed_lines_are_counted_not_silently_dropped():
    summary = se.parse_strace_text(_SAMPLE_STRACE)
    assert summary.unparsed_lines == 1
    assert summary.parsed_lines == 6


def test_bucket_events_by_window_separates_startup_from_scenario():
    summary = se.parse_strace_text(_SAMPLE_STRACE)
    opens = se.write_capable_opens(summary)
    windows = [("A", "10:00:00.150000", "10:00:00.280000")]
    buckets = m2._bucket_events_by_window(opens, windows)
    assert len(buckets["A"]) == 1
    assert buckets["A"][0].path == "/tmp/leak.txt"
    assert buckets["startup"] == []


# ---------------------------------------------------------------------------
# End-to-end smoke test
# ---------------------------------------------------------------------------

_STRACE_MISSING = shutil.which("strace") is None


def _structure_lib_missing() -> bool:
    try:
        import fastdicom_gateway.transform  # noqa: F401
    except Exception:
        return True
    return False


@pytest.mark.skipif(_STRACE_MISSING, reason="strace not available on PATH")
@pytest.mark.skipif(_structure_lib_missing(), reason="fastDICOMstructure binding not importable")
def test_m2_end_to_end_run_passes_with_corrected_scenario_d():
    """This is the live CI gate on the *current* gateway's M2 boundary
    behavior, so it must test *current*, not historical, expectations.

    Historical Scenario D (scenarios.run_scenario_d, still used verbatim by
    scenarios.ALL_SCENARIOS and by the frozen
    docs/M2_PERSISTENCE_BOUNDARY_VALIDATION.md evidence this test file does
    not touch) expected an unmodified Implicit VR object to reach
    write_bytes()'s Unsupported path and surface as HTTP 500 -- true for
    the implementation frozen at that milestone, pinned unconditionally by
    test_implicit_vr_fixture_has_no_targeted_tags_and_is_unsupported_to_write
    above, which calls fastDICOMstructure directly and is unaffected by
    this change.

    Since ADVERSARIAL_REMEDIATION_A.md, the *gateway* rejects Implicit VR at
    the acceptance boundary (HTTP 400, reason=unsupported_transfer_syntax)
    before ever reaching that code path, so the corrected Scenario D
    (scenarios_publication_refresh.run_scenario_d_corrected) is what a live
    end-to-end run against current code must satisfy. See
    POST_REMEDIATION_EVIDENCE_REFRESH.md for the full before/after and why
    the frozen M2 artifact was not, and should not be, edited to match.
    """
    evidence = m2.run(use_strace=True, scenario_fns=spr.CORRECTED_SCENARIOS)
    assert evidence["overall_result"] == "PASS"
    assert len(evidence["scenarios"]) == 4
    for scenario in evidence["scenarios"]:
        assert scenario["result"] == "PASS"
        assert scenario["canary_in_application_logs"] is False
        assert scenario["canary_in_observed_application_artifacts"] is False
    assert evidence["filesystem_scan_hits"] == []
