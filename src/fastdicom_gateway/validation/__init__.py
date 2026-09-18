"""M2 persistence-boundary validation harness.

Not part of the gateway's request-handling path -- this package is a
standalone evidence-collection tool that launches its own gateway
subprocess, drives it over real HTTP, and observes it from the outside
(syscalls, captured stdout/stderr, a bounded filesystem scan). See
docs/M2_PERSISTENCE_BOUNDARY_VALIDATION.md for the hypothesis, scope, and
how to interpret a run's result.

Run with: python -m fastdicom_gateway.validation.m2
"""
