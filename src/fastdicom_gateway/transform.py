"""In-memory parse -> policy transform -> write -> verify pipeline.

fastDICOMstructure is the *only* DICOM engine used on this path -- see
README.md "Relationship to fastDICOMstructure". No pydicom import exists in
this module; pydicom, if used at all in this project, is a test-only
independent-verification tool (see tests/), never part of the
parse/transform/write path itself.

Everything here operates on in-memory bytes end to end: parsing
(`fds.read_buffer`), the fixed policy mutation, and serialization
(`Structure.write_bytes`, added in fastDICOMstructure M1.1) never touch a
filesystem path. See README.md "M1 -> M1.1" for what this replaced.
"""

from __future__ import annotations

import os
import sys
from dataclasses import dataclass
from pathlib import Path


def _add_fastdicomstructure_to_path() -> None:
    """Makes the sibling fastDICOMstructure checkout's Python package
    importable without vendoring or copying its implementation into this
    repository (see README.md "Relationship to fastDICOMstructure").

    fastDICOMstructure has a pyproject.toml, but it declares no installable
    dependency on fastDICOMattrs (a native library, not published to PyPI)
    -- its own README documents consuming it via PYTHONPATH against a
    sibling checkout, not a plain `pip install`. This project mirrors that
    same convention one level up the dependency chain: point
    FASTDICOMSTRUCTURE_REPO at a checkout (default: the sibling directory
    under the same parent as this repository) and its `python/` directory
    is added to sys.path. The fastdicomstructure package then locates its
    own compiled `libfastdicomstructure_c.so` relative to that checkout
    automatically (see its `_candidate_dirs()`), as long as the sibling
    repository has already been built -- see this README's "Prerequisites".
    """
    default_repo = Path(__file__).resolve().parents[3] / "fastDICOMstructure"
    repo = Path(os.environ.get("FASTDICOMSTRUCTURE_REPO", default_repo))
    python_dir = repo / "python"
    if str(python_dir) not in sys.path:
        sys.path.insert(0, str(python_dir))


_add_fastdicomstructure_to_path()

import fastdicomstructure as fds  # noqa: E402 -- path must be set up first
from fastdicomstructure import policy as fds_policy  # noqa: E402


@dataclass(frozen=True)
class Rejection:
    """A safe-to-log description of why input was rejected.

    Never carries DICOM element values -- only structural/diagnostic
    metadata (severity names, counts).
    """

    reason: str
    diagnostic_count: int
    diagnostic_severities: tuple[str, ...]


class RejectedInput(Exception):
    """Raised for malformed/unsupported input. Callers should respond 4xx."""

    def __init__(self, rejection: Rejection):
        super().__init__(rejection.reason)
        self.rejection = rejection


@dataclass(frozen=True)
class TransformResult:
    """Safe-to-log summary of a successful transform. No DICOM *values* --
    the three UID fields (added in M5) are structural identifiers, logged
    and returned as what a persistence receipt safely names (see sink.py,
    app.py's POST /dicom/store). This project's own synthetic fixtures
    assign these UIDs arbitrarily, with no patient association. That is a
    property of this project's own test/demo inputs, not a general
    guarantee about UIDs: this endpoint accepts arbitrary caller-supplied
    DICOM bytes, and a real object's StudyInstanceUID/SeriesInstanceUID/
    SOPInstanceUID can be institution- or patient-correlatable in practice
    (see PS3.15's treatment of UIDs among attributes requiring action in
    some de-identification profiles). Do not deploy this demo's logging
    behavior against real clinical input without re-evaluating that."""

    output_bytes: bytes
    input_byte_count: int
    output_byte_count: int
    elements_touched: int
    private_elements_removed: int
    study_instance_uid: str | None
    series_instance_uid: str | None
    sop_instance_uid: str | None


# The fixed M1 demonstration policy -- see README.md "Fixed demonstration
# policy". Deliberately small and explicit; not a general policy engine.
_TAG_PATIENT_NAME = (0x0010, 0x0010)
_TAG_PATIENT_ID = (0x0010, 0x0020)
_TAG_PATIENT_BIRTH_DATE = (0x0010, 0x0030)
_DEMO_PATIENT_ID = b"DEMO"  # even length; see Structure.set_value's padding note

# S1.8: the same fixed policy above, expressed declaratively and applied
# through fastdicomstructure.policy.apply() -- see fastDICOMstructure's
# docs/architecture/S1_8_GATEWAY_INTEGRATION_IMPLEMENTATION_DESIGN.md.
# Remove/Replace default to recursive=True (fastdicomstructure's own
# established default for these two operation kinds), exactly matching the
# erase_recursive/set_value_recursive scope this replaces -- see
# fastDICOMstructure's tests/python/test_policy.py::
# PolicyReproducesGatewayDemoTest, which already proves this exact
# construction byte-identical to the imperative sequence it replaces.
_GATEWAY_DEMO_POLICY = fds_policy.Policy(
    name="gateway-demo-policy",
    version="1.0.0",
    operations=(
        fds_policy.Remove(_TAG_PATIENT_NAME),
        fds_policy.Replace(_TAG_PATIENT_ID, _DEMO_PATIENT_ID),
        fds_policy.Remove(_TAG_PATIENT_BIRTH_DATE),
        fds_policy.PrivateTagPolicy(remove=True),
    ),
)

# M5: structural identifiers -- read, never written, by this project. Not
# patient data for this project's own synthetic fixtures; see the scoping
# note in TransformResult's docstring for why that does not generalize to
# arbitrary caller-supplied input.
_TAG_STUDY_INSTANCE_UID = (0x0020, 0x000D)
_TAG_SERIES_INSTANCE_UID = (0x0020, 0x000E)
_TAG_SOP_INSTANCE_UID = (0x0008, 0x0018)


def _uid_value(structure: fds.Structure, tag: tuple[int, int]) -> str | None:
    element = structure.get(tag)
    if element is None:
        return None
    # UI values are NUL-padded to an even length per PS3.5 6.4; trailing
    # NULs are not part of the UID.
    return element.value.rstrip(b"\x00").decode("ascii", "replace")


def _parse_or_reject(data: bytes) -> fds.Structure:
    """Parses `data` in memory and rejects it if the structural parser
    reports anything beyond an informational diagnostic (mirrors
    python/examples/pipeline_demo.py's structural_parse_and_inspect), or if
    it isn't Explicit VR Little Endian -- see the acceptance-scope check
    below.
    """
    try:
        structure = fds.read_buffer(data, fidelity="lossless")
    except fds.FdsError as error:
        raise RejectedInput(Rejection(
            reason="parse_failed",
            diagnostic_count=0,
            diagnostic_severities=(),
        )) from error

    # Acceptance scope (README.md "Acceptance scope"): this demonstration's
    # fixed policy has been shown -- by adversarial-experiment fixtures
    # exercised through this exact pipeline, see ADVERSARIAL_VALIDATION_A.md
    # -- to reach every structurally visible occurrence of its targeted
    # tags only for Explicit VR Little Endian input. Under Implicit VR, a
    # defined-length nested sequence is parsed as one opaque, unexpanded
    # value with no diagnostic of any kind (a documented fastDICOMstructure
    # limitation, see its docs/roundtrip-contract.md "Implicit VR Little
    # Endian"), so a targeted tag hidden inside one would be invisible to
    # every mutation below and could still reach output_bytes. Rejecting
    # Implicit VR here keeps the invariant that only input the gateway has
    # actually demonstrated sufficient structural visibility into becomes
    # eligible for persistence. This is a scope control for *this*
    # demonstration and its fixed policy, not a claim that Implicit VR is
    # unsafe in general or that this architecture requires Explicit VR --
    # dictionary-backed Implicit VR interpretation is a feasible route not
    # pursued here (see README.md "Acceptance scope").
    if not structure.is_explicit_vr:
        structure.close()
        raise RejectedInput(Rejection(
            reason="unsupported_transfer_syntax",
            diagnostic_count=0,
            diagnostic_severities=(),
        ))

    blocking = [d for d in structure.diagnostics if d.severity != "info"]
    if blocking:
        severities = tuple(d.severity for d in blocking)
        structure.close()
        raise RejectedInput(Rejection(
            reason="blocking_diagnostic",
            diagnostic_count=len(blocking),
            diagnostic_severities=severities,
        ))
    return structure


def _apply_demo_policy(structure: fds.Structure) -> tuple[int, int]:
    """Applies the fixed M1 policy in place. Returns
    (elements_touched, private_elements_removed).

    * remove every structurally visible occurrence of PatientName
      (0010,0010), at any nesting depth
    * replace every structurally visible occurrence of PatientID
      (0010,0020), at any nesting depth, with a fixed demonstration value
    * remove every structurally visible occurrence of PatientBirthDate
      (0010,0030), at any nesting depth
    * remove private elements (odd group number, any depth)
    * Pixel Data and everything else is left untouched: no code path here
      can reach Pixel Data at all (see docs/architecture.md section 8 in
      fastDICOMstructure), and nothing else is targeted.

    S1.8: applied through fastdicomstructure.policy.apply() against the
    declarative _GATEWAY_DEMO_POLICY above, rather than four direct
    imperative Structure calls. Remove/Replace's own recursive=True
    default reaches a tag nested inside a sequence item -- e.g. a standard
    `Other Patient IDs Sequence (0010,1002)` item carrying its own nested
    (0010,0020) -- the same way a top-level occurrence is reached,
    identically to the erase_recursive/set_value_recursive calls this
    replaces (proven byte-identical by fastDICOMstructure's own
    PolicyReproducesGatewayDemoTest). See ADVERSARIAL_VALIDATION_A.md for
    the original experiments establishing this recursive-reach requirement
    in the first place. This is intentionally still a fixed, three-tag
    policy, not a general de-identification profile or a dictionary-backed
    engine -- see README.md's "Fixed demonstration policy" and "Acceptance
    scope". `elements_touched` counts every individual element changed
    across the three non-private-tag operations (0 or more each), not
    just whether each operation fired at all -- matching the prior
    imperative implementation's own counting convention exactly.

    A `PolicyExecutionStatus` other than `COMPLETED` is not expected for
    this fixed, Require-free policy (none of its four operations can
    reject a `Policy`), but is treated as an internal failure here rather
    than silently returned as if it were a clean transform.
    """
    result = fds_policy.apply(structure, _GATEWAY_DEMO_POLICY)
    if result.execution is not fds_policy.PolicyExecutionStatus.COMPLETED:
        raise RuntimeError(
            f"gateway demo policy did not complete cleanly: {result.execution}"
        )
    private_removed = next(
        op.count for op in result.operations if op.kind == "private_tag_policy"
    )
    touched = sum(op.count for op in result.operations if op.kind != "private_tag_policy")
    return touched, private_removed


def _write_to_bytes(structure: fds.Structure) -> bytes:
    """Serializes `structure` to bytes entirely in memory.

    M1 adapted fastDICOMstructure's then path-only write API to an
    in-memory result via an anonymous, RAM-backed file descriptor (see
    README.md "M1 -> M1.1" for that history). fastDICOMstructure M1.1 added
    a true in-memory write entry point (`Structure.write_bytes`, backed by
    a new C ABI buffer-write function, which in turn calls the same
    `DICOMStructure::write(std::ostream&)` the path-based writer uses), so
    no filesystem-shaped adapter is needed here anymore.
    """
    return structure.write_bytes()


def _verify_output(data: bytes) -> tuple[str | None, str | None, str | None]:
    """Reparses the just-written bytes as an integrity self-check
    (M1-AC5). A blocking diagnostic here means the transform pipeline
    itself produced invalid output -- a server-side bug, not a client
    input problem -- so this raises rather than returning a status.

    Also returns (study, series, sop)_instance_uid read from that same
    reparse (M5) -- reusing this parse rather than adding a second one
    just to look them up. None for whichever tag the input didn't have;
    `/dicom` (which doesn't need them) tolerates that, `/dicom/store`
    (which does) rejects a result missing any of them.
    """
    structure = fds.read_buffer(data, fidelity="lossless")
    try:
        blocking = [d for d in structure.diagnostics if d.severity != "info"]
        if blocking:
            raise RuntimeError(
                f"transformed output failed self-verification reparse: "
                f"{len(blocking)} blocking diagnostic(s)"
            )
        return (
            _uid_value(structure, _TAG_STUDY_INSTANCE_UID),
            _uid_value(structure, _TAG_SERIES_INSTANCE_UID),
            _uid_value(structure, _TAG_SOP_INSTANCE_UID),
        )
    finally:
        structure.close()


def process(data: bytes) -> TransformResult:
    """The full in-memory parse -> policy -> write -> verify pipeline.

    Raises RejectedInput for malformed/unsupported input (caller should
    respond 4xx). Any other exception indicates an internal failure
    (caller should respond 5xx).
    """
    structure = _parse_or_reject(data)
    try:
        touched, private_removed = _apply_demo_policy(structure)
        output_bytes = _write_to_bytes(structure)
    finally:
        structure.close()

    study_uid, series_uid, sop_uid = _verify_output(output_bytes)

    return TransformResult(
        output_bytes=output_bytes,
        input_byte_count=len(data),
        output_byte_count=len(output_bytes),
        elements_touched=touched,
        private_elements_removed=private_removed,
        study_instance_uid=study_uid,
        series_instance_uid=series_uid,
        sop_instance_uid=sop_uid,
    )
