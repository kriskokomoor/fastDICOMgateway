# Demo Release — v0.1.0-demo

## What this tag means

> A completed, evidence-backed demonstration of pre-persistence DICOM policy application through
> local, containerized, Cloud Run, and Healthcare API persistence boundaries.

It is not a production-readiness claim, a `1.0.0`, or a statement that this project is done being
worked on — only that the specific engineering sequence it set out to demonstrate (M1 through M5) is
complete, each stage independently evidence-backed, and the result is legible to a reader arriving
cold.

## Validated commits

| Repository | Commit | Role |
|---|---|---|
| `fastDICOMgateway` | `ab3afb2` | the demonstrated application (M1–M5) |
| `fastDICOMstructure` | `4eb44cb` | the only DICOM engine on the production path; unmodified since its own M1.1 API addition |

## What is demonstrated

A DICOM object received over HTTP (locally, containerized, or via Google Cloud Run) is structurally
parsed, policy-transformed, and reparsed for verification entirely in memory using
`fastDICOMstructure` — and, as of M5, an approved post-policy instance can be durably persisted to a
GCP Healthcare API DICOM store, with independent retrieval confirming the durable object reflects
the approved representation, not the source one. See the [README](../README.md)'s central
source → transformed → stored table and [`EVIDENCE_INDEX.md`](EVIDENCE_INDEX.md) for the six
boundaries this was validated at.

## What is not demonstrated

- Complete DICOM de-identification or PS3.15 conformance.
- HIPAA or other regulatory compliance.
- Production authentication/authorization, multi-tenant IAM, or a hardened ingress architecture —
  the deployed demo is intentionally unauthenticated and synthetic-data-only.
- Scalability, concurrency, or availability under load — none was tested.
- Absence of data handling by Google infrastructure outside the specific logs and storage surfaces
  this project's own validation harnesses actually queried.

See each milestone report's own "Explicit non-claims" section (linked from
[`EVIDENCE_INDEX.md`](EVIDENCE_INDEX.md)) for the complete, per-boundary list.

## What would constitute a new milestone beyond this tag

This tag is a deliberate stopping point for the *demonstration* — not a backlog. Any of the
following would be a distinct new project/question, not a continuation of this one:

- a production authentication and IAM architecture in front of the gateway;
- a real, PS3.15-conformant de-identification profile (UID remapping, HMAC pseudonymization, date
  shifting, burned-in-pixel PII detection) replacing the fixed demonstration policy;
- integration with `fastDICOMarchive`'s stateful cohort/reconstruction path, or with
  `fastDICOMattrs`'s shallow-probe layer;
- concurrency/load characterization, retry/idempotency architecture, or a bulk-ingestion path;
- infrastructure-as-code, CI/CD, or a multi-environment deployment story;
- enabling Healthcare API Data Access audit logging and building on top of it.

None of these are in scope for this release; each would need its own explicitly-scoped objective
before being started.
