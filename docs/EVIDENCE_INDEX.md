# Evidence Index

Compact index of every milestone in the `fastDICOMgateway` demonstration: what question it asked,
what it found, where the full evidence lives, and its one-line limitation. See the
[README](../README.md) for the thesis and the central source → transformed → stored result; this
index exists so a reader doesn't have to read the milestone reports sequentially to find a specific
answer.

> **S1.8 note.** M1–M5 below all observe behavior through `transform.py`'s *imperative* fixed
> policy, in effect at each milestone's own cited commit. S1.8 (gateway commit `26caca8`, see
> [`S1_8_STRUCTURE_POLICY_INTEGRATION_REPORT.md`](S1_8_STRUCTURE_POLICY_INTEGRATION_REPORT.md))
> later replaced that imperative implementation with the same policy expressed declaratively and
> executed through `fastdicomstructure.policy.apply()`, proven byte-identical to what M1–M5
> observed. M1–M5's persistence/observation findings are not re-run against S1.8; the S1.8 report
> is what qualifies the current transform implementation, not this index.

| Milestone | Question asked | Result | Evidence | Commit |
|---|---|---|---|---|
| M1 | Can a DICOM object be received over HTTP, structurally parsed, policy-transformed, and reparsed for verification — entirely in memory, using only `fastDICOMstructure`? | PASS | `src/fastdicom_gateway/{app,transform}.py`; source-level review | `7b0fe11` |
| M1.1 | Can the M1 workaround (below) be replaced with a true in-memory write API? | PASS | `fastDICOMstructure` `fds_structure_write_buffer`/`Structure.write_bytes` | `03f1352` (gateway), `4eb44cb` (structure) |
| M2 | Does the gateway, running as a bare host process, ever write source content to a filesystem artifact or application log? (`strace`-observed, four scenarios: success, malformed input, parser-rejected input, internal write failure) | PASS | [`M2_PERSISTENCE_BOUNDARY_VALIDATION.md`](M2_PERSISTENCE_BOUNDARY_VALIDATION.md), [`m2_evidence/latest_result.json`](m2_evidence/latest_result.json) | `7d5ca85` |
| M3 | Does the same hold for the gateway containerized, run `--read-only` with zero writable mounts? | PASS | [`M3_CONTAINER_VALIDATION.md`](M3_CONTAINER_VALIDATION.md), [`m3_evidence/latest_result.json`](m3_evidence/latest_result.json) | `1fc9e48` |
| M4 | Does it still hold once deployed behind Google Cloud Run's managed ingress, checking both Cloud Run's own log streams? | PASS | [`M4_CLOUD_RUN_VALIDATION.md`](M4_CLOUD_RUN_VALIDATION.md), [`m4_evidence/latest_result.json`](m4_evidence/latest_result.json) | `d597272` |
| M5 | Once the gateway durably persists an *approved* instance to a real Healthcare API DICOM store, does independent retrieval from that store show the approved representation rather than the source one? | PASS | [`M5_APPROVED_PERSISTENCE_VALIDATION.md`](M5_APPROVED_PERSISTENCE_VALIDATION.md), [`m5_evidence/latest_result.json`](m5_evidence/latest_result.json) | `ab3afb2` |

## One-line limitation per milestone

- **M1** — source-level code review only; no runtime observation of syscalls/filesystem activity
  (that's what M2 added).
- **M1.1** — a `fastDICOMstructure` API-surface fix, not a gateway behavior change; see below for
  what it actually replaced.
- **M2** — one local Linux environment, one Docker/strace toolchain; doesn't cover the deployment
  environment (reverse proxies, infrastructure logging).
- **M3** — one Docker daemon, one host; `docker diff` sees the container's own writable layer only,
  not a bind-mounted host path (none was configured).
- **M4** — one Cloud Run revision, one region, four sequential requests; not concurrent load, not
  sustained traffic.
- **M5** — one dataset, one store, one retained instance; Healthcare API Data Access audit logging
  was not enabled (confirmed by inspection, not turned on for this milestone), so `StoreInstances`/
  `RetrieveInstance` activity is `NOT OBSERVED / NOT ENABLED` in Cloud Audit Logs specifically (Admin
  Activity logs, which are always on, were checked and only show dataset/store/IAM creation).

Every milestone's full non-claims section is more detailed than the one-liner above — see each
report's own "Explicit non-claims" section before treating any of these as a broader guarantee.

## M1 → M1.1: what the fix actually replaced

M1 shipped with a known gap: `fastDICOMstructure`'s Python/C-ABI surface only exposed a path-based
write (`fds_structure_write_file`/`_with_stats`) — no in-memory buffer-write entry point, even
though its C++ core's `DICOMStructure::write(std::ostream&)` already supported it internally. M1
worked around this with `os.memfd_create`: an anonymous, RAM-only file description with no directory
entry and no path on any real filesystem, reachable only via this process's own `/proc/self/fd/<n>`
— so the write step never touched persistent storage or the `tempfile` module, but it was still a
path-shaped adapter around a path-based API, not a true in-memory write.

M1.1 closed that gap in `fastDICOMstructure` directly: `fds_structure_write_buffer`/`_with_stats`
(C ABI) and `Structure.write_bytes`/`write_bytes_with_stats` (Python) serialize into a
library-owned in-memory buffer, using the same `DICOMStructure::write(std::ostream&)` the
path-based writer already used. The gateway's `transform.py` now calls `write_bytes()` directly;
`memfd_create` and `/proc/self/fd` are gone from the request path entirely, and are forbidden tokens
(rather than a documented exception) in `tests/test_app.py`'s source-level persistence scan from
M1.1 onward.
