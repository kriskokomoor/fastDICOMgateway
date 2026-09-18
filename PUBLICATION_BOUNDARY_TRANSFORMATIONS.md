# Publication-boundary transformations — fastDICOMgateway

Derived from internally qualified commit `b333a42cb65ab1fa7a1ae5f4946f896050b79ec7`
(qualified software-tree manifest SHA-256 `6f1cff7fd6229da5c53e8756fa1e0cfa7e3700820a5244801c2260d89a617a72`).

| Path | Category | Reason | Executable semantics changed? |
|---|---|---|---|
| `.github/workflows/ci.yml` | D — public-facing CI correction | Both `Checkout fastDICOMstructure`/`Checkout fastDICOMattrs` steps already checked out unpinned default-branch heads (a pre-existing characteristic, not introduced by this transformation). Added an explicit `TODO(release)` comment recording that these should pin exact public release tags once they exist, rather than silently leaving the gap undocumented or guessing a pin. No step, job, or checkout target was changed. | No. |
| `RELEASE_PROVENANCE.md` | C — public provenance document | New file; explains internal→public relationship, content-equivalence proof, and compatible upstream releases. | No. |
| `PUBLICATION_BOUNDARY_TRANSFORMATIONS.md` (this file) | C — public provenance document | Records this exact transformation set. | No. |

No personal-identity removal (category A) was needed — the internal hygiene scan and prior
release-preparation redaction already removed all personal email, GCP identifiers, and local paths
from this component's tracked tree (see the internal repository's own commit history for that
prior, already-applied redaction). No production code, test, HTTP behavior, DICOM transformation
behavior, or persistence behavior was touched anywhere in this transformation set.
