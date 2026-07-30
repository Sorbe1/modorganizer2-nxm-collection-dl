# Clean Final Branch Manifest

## Include

Core plugin changes:

- `README.md`
- `__meta__.py`
- `api.py`
- `collection_helpers.py`
- `download.py`
- `install.py`
- `var.py`

Tests:

- `tests/test_collection_helpers.py`
- `tests/test_reset_collection_state.py`

Worker:

- `scripts/native_archive_worker.py`
- `scripts/reset_collection_state.py`

Maintainer artifacts:

- `pr-artifacts/clean-branch-manifest.md`
- `pr-artifacts/pr-description.md`
- `pr-artifacts/maintainer-test-plan.md`
- `pr-artifacts/maintainer-intro-email.md`
- `pr-artifacts/maintainer-pr-email.md`
- `pr-artifacts/collection-download-install-redesign-20260728.md`
- `pr-artifacts/release-quality-checklist.md`

## Exclude

Do not carry these into the clean final branch unless they are intentionally
rewritten:

- local package ZIPs
- local build directories
- exploratory patch-series directories
- transient live-audit logs
- one-off reset/probe scripts
- generated `__pycache__` directories
- old PR drafts that describe abandoned GUI-driver-first behavior

## Branch Procedure

1. Create the clean branch from the appropriate upstream base.
2. Replay only the included files above.
3. Run the unit/compile test plan.
4. Run the full live reset/download/install/activation proof.
5. Open the new PR using `pr-artifacts/pr-description.md`.
6. After the replacement PR is verified, close the old exploratory PR if it is
   still open.
7. Delete the old exploratory branch so GitHub retains only the final
   intentional branch for this work.
