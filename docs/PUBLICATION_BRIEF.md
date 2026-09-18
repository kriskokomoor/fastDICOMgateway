# Publication Brief

Working material for a longer technical write-up about this project. Not a full article — a brief
to draft one from. Nothing here has been published anywhere.

> **S1.8 note.** This brief and the M1–M5 evidence it draws on describe the gateway's original
> imperative fixed-policy implementation. S1.8 (see
> [`S1_8_STRUCTURE_POLICY_INTEGRATION_REPORT.md`](S1_8_STRUCTURE_POLICY_INTEGRATION_REPORT.md))
> later replaced that implementation with the same policy expressed declaratively through
> `fastdicomstructure.policy.apply()`, proven byte-identical to the imperative version this brief
> describes. Any future draft should cite S1.8 for the current architecture, not just M1–M5.

## Working thesis

> The safest place to remove sensitive metadata may be before the infrastructure that persists it
> ever receives the source representation.

## Demonstrated system

`fastDICOMgateway` receives a DICOM object over HTTP, parses and policy-transforms it entirely in
memory using `fastDICOMstructure` (a C++ structural DICOM library), reparses the output as a
self-check, and — in its final form — submits only the approved, post-policy instance to a GCP
Healthcare API DICOM store. The mechanism was established locally (M1/M1.1), then tested across
four progressively stronger boundaries: a bare host process, a read-only non-root container, Google
Cloud Run's managed execution environment, and finally the durable Healthcare API store itself —
each checked independently for whether any trace of the original source object leaked into logs,
the filesystem, or durable storage.

## Evidence arc

- **M1/M1.1** — proved the mechanism: parse → policy → write, entirely in memory, with a true
  in-memory serialization API (no filesystem-shaped workaround) added to the underlying library
  along the way.
- **M2** — proved it on a bare host process: `strace`-observed, zero filesystem writes, zero
  canaries in logs, across four request scenarios including a deliberately induced internal
  failure.
- **M3** — proved it containerized, and went further: the container runs `--read-only` with zero
  writable mounts, which is *architectural* prevention, not just observed absence.
- **M4** — proved it survives Google's own managed ingress, checking two distinct log streams
  Cloud Run generates (the container's own output, and Cloud Run's separate auto-generated
  per-request log) that a naive check might conflate or miss one of.
- **M5** — proved the actual persistence claim: independent retrieval from a real durable store
  shows the approved representation, not the source one, for every policy-governed attribute.

## Central result

| Attribute | Source | Transformed | Stored (retrieved independently) |
|---|---|---|---|
| PatientName | present | absent | absent |
| PatientID | source value | `DEMO` | `DEMO` |
| PatientBirthDate | present | absent | absent |
| Private element | present | absent | absent |
| Pixel Data | hash A | hash A | hash A |

## Methods story: a validation harness that can't fail is weak evidence

Every milestone from M2 onward included a **negative control** — a deliberate, temporary,
never-committed sabotage of the system under test, specifically to prove the validation harness
would actually catch a real violation rather than reflexively reporting PASS:

- M2's negative control found a real gap in the harness itself: a file overwritten by a later
  request only implicated its *last* writer under naive content-matching, even though independent
  syscall evidence showed every request had touched it.
- M3's negative control found the same class of gap in a different mechanism (`docker diff` only
  reports a path's *first* observed change-kind).
- M4's negative control found a log-attribution bug: matching a log entry to a scenario by canary
  name alone (rather than by time window) over-attributed hits across scenario boundaries.
- M5's negative control proved the durable-state checker itself works: a deliberately
  source-bearing object, stored directly (bypassing the gateway), was correctly flagged as a
  violation.

Three of those four negative controls found a bug in the *validation harness*, not the system under
test — which is the point. A checker that has never been shown capable of failing has not
demonstrated anything about the system it's checking.

## Engineering insight

The result is specific to DICOM, but the underlying principle isn't:

> Move policy ahead of persistence when the cost of ever letting the source representation reach
> the persistence layer is high enough to justify proving — not assuming — that it doesn't.

## Limitations

See the [README](../README.md)'s "Claims and non-claims" and each milestone report's own
"Explicit non-claims" section (indexed in [`EVIDENCE_INDEX.md`](EVIDENCE_INDEX.md)). Briefly: this
is not complete de-identification, not PS3.15 or HIPAA compliance, not a production
auth/scalability story, and does not establish anything about Google-internal data handling beyond
the specific logs and storage surfaces actually queried.

## Possible article structure (headings only)

1. The problem: persistence systems as a trust boundary, not just a destination
2. The thesis and why "prove, don't assume" matters for a policy boundary
3. The system, briefly
4. The evidence arc (M1 → M5), told as escalating boundaries, not a changelog
5. The central result table
6. The methods story: negative controls, and what they found in the *harness*
7. What this doesn't prove
8. The general principle, restated

## Where this belongs

- **pysynapse** (technical/research identity) is the natural home for the canonical, full technical
  article — the evidence arc, the methods story about negative controls, and the engineering
  insight are research-identity content, not commercial-engagement content.
- **Palmer Cove** (clinical imaging / commercial engagement) is better suited to a short companion
  piece: the thesis, the central result table, and a pointer to the full technical write-up — not a
  re-derivation of the methods story.
- The two should cross-link directly to each other (companion → full article, full article → "see
  also the companion piece" if Palmer Cove's is published first or concurrently).
- GitHub (once this repository is published) is what both should link to as the primary
  evidence/reproduction source — the milestone reports and machine-readable evidence files are the
  actual proof, not something either article should try to fully re-narrate.

This repository is not being published to either site as part of this task — this section is a
recommendation for a separate, later publishing decision.
