# Release provenance

This public source tree was constructed from an internally qualified research/development
repository's release candidate. That internal repository is not public, and this repository
shares no Git history, commits, or objects with it.

- **Internal qualification identifier:** `b333a42cb65ab1fa7a1ae5f4946f896050b79ec7`. This is a
  reference label for the internal repository's own record-keeping — it is **not a commit
  reachable in this repository**, and no attempt to resolve it here will succeed.
- **Content equivalence:** the qualified internal source tree's deterministic software-tree
  manifest hashes to `6f1cff7fd6229da5c53e8756fa1e0cfa7e3700820a5244801c2260d89a617a72`. See
  `PUBLICATION_BOUNDARY_TRANSFORMATIONS.md` for the complete, explicit list of publication-only
  changes between that tree and this one.
- **Compatible dependencies:** this reference application was qualified against fastDICOMstructure
  internal candidate `97ab708d87cf7cd9740d4f92c93b60f52291c5e9` (public manifest
  `dd52bfda7b543c7030e376a0463b332349e8368fe4716a9fb15d78af3c6337a5`) and, transitively,
  fastDICOMattrs internal candidate `24efab8a5fecc159ca88f1ad94e03b0d5dee2c61` (public manifest
  `025be34e9124582f0ff40455aeefb02a1753da42e2fe3a9acc4e04e3f7814b16`). Use the corresponding
  public releases of those two projects, identified by their own `RELEASE_PROVENANCE.md` files.
- **What is intentionally not included:** the internal repository's commit history, its earlier
  (superseded) states, and any local development-machine detail.
- **Scope reminder:** this repository is a bounded reference application/deployment example, not
  the reusable library, not a production service, and not a complete de-identification solution —
  see README.md.
