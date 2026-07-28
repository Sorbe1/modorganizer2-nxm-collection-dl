# Improve collection download/install reliability

## Summary

This PR makes Nexus collection handling deterministic and resumable across
download, install, metadata repair, and activation. The main change is to stop
treating MO2 installer dialogs as the primary control surface for ordinary
archives: completed downloads are reconciled from disk first, safe archives are
installed headlessly, and MO2 installer UI is reserved for confirmed FOMOD or
manual cases.

Replaying Add Collection is now the normal recovery path. Each run repairs safe
local drift, marks exact installed downloads as installed, repairs deterministic
installed mod metadata, enables installed collection mod containers, and resumes
only incomplete work. The intended end state is the same whether a collection
finishes in one pass or after several interrupted passes.

The product goal is practical one-click collection parity for MO2 users, with
better restartability, lower Nexus quota waste, reduced desktop focus stealing,
and a clear final audit.

## What Changed

- Reconcile completed archives, stale zero-byte `.unfinished` files, duplicate
  prompts, quota/rate-limit stops, and restart-required MO2 queue state before
  queueing more Nexus requests.
- Queue each missing Nexus file once per run and avoid automatic duplicate
  downloads in collection mode.
- Add a headless install route for safe `.zip`, `.7z`, and `.rar` archives.
- Add a native archive-worker adapter for environments where MO2's embedded
  Python cannot spawn archive tools directly.
- Keep FOMOD handling bounded: real FOMODs use MO2's installer UI, default pages
  can auto-advance, and unresolved manual choices are reported.
- Report ambiguous archives and archive-adapter failures as manual blockers
  instead of falling back to MO2 Quick Install.
- Split install and activation into separate phases with a final audit of
  expected downloads, installed mod containers, active mods, and active plugins.
- Repair installed collection mod metadata for manifest-provided file version,
  mod version, and MO2 category fields mapped from collection category names.
- Repair replayed invalid empty installer outputs from their original archives,
  while disabling non-game-data collection containers that should not be active
  in the left pane.
- Repair profile `plugins.txt` for plugin enablement and canonical filename
  casing after collection activation.
- Update the README and include maintainer-facing design/test notes.

## Review Notes

- The detailed design note is in
  `pr-artifacts/collection-download-install-redesign-20260728.md`.
- The direct archive path is intentionally narrow: classify archive members,
  extract to a temporary directory, move the proven payload into the target MO2
  mod directory, then write `meta.ini`.
- The archive worker is an adapter, not a second installer. The installer only
  depends on JSON `list` and `extract` results.
- GUI automation remains only for dialogs that cannot be avoided.
- Existing settings are kept compatible where possible; the ZIP-only headless
  setting remains as a legacy fallback for the broader archive setting.

## Verification

```bash
uv run ruff check __meta__.py api.py collection_helpers.py download.py install.py var.py tests/test_collection_helpers.py scripts/native_archive_worker.py
uv run ruff format --check __meta__.py api.py collection_helpers.py download.py install.py var.py tests/test_collection_helpers.py scripts/native_archive_worker.py
python -m py_compile __meta__.py api.py collection_helpers.py download.py install.py var.py scripts/native_archive_worker.py tests/test_collection_helpers.py
uv run python -m unittest tests.test_collection_helpers -v
```

White-box coverage includes:

- download reconciliation for complete archives, stale `.unfinished` files,
  duplicate prompts, quota/rate-limit stops, and restart boundaries
- installer routing for confirmed FOMODs, unknown archives, safe archive layouts,
  unsafe archive rejection, and legacy setting compatibility
- native archive-worker request handling and Wine/Proton path adapter behavior
- installed mod metadata repair for version and Nexus category fields
- installed mod layout repair for safe single-wrapper archive roots
- no-GUI fallback behavior when archive preflight is ambiguous or unavailable
- activation/install completion and final postcondition audit behavior

Black-box MO2 2.5.2 / Steam Proton validation used:

- `xxsqm4` revision `99`, `Immersive & Adult`, 559 collection file entries
- `8vdyr1` revision `12`, `Sexy Statues`, 75 collection file entries

Observed final replay results on July 29, 2026:

- `xxsqm4`: 559 installed/already present/root-handled, 0 downloaded but not
  installed, 553 activated collection mods, 535 plugins already active, 0 blocked
- Final `xxsqm4` replay repaired metadata for installed collection containers,
  restored MO2 category/version fields, handled the root Engine Fixes preloader
  as already present, and produced 0 failed/skipped entries.
- Post-run audits found 0 active invalid game-data containers, 0 blank versions,
  0 blank categories, and confirmed previously disabled plugins persisted as
  enabled with canonical filename casing.
- `8vdyr1`: 75 installed/already present/root-handled, 0 downloaded but not
  installed, 75 activated collection mods, 23 plugins already active, 0 blocked
- MO2 Sort completed and reported a missing master from collection content. That
  is retained as a load-order/content validation finding, not treated as an
  installer convergence failure.

## Scope

Approximate final branch scope is `+15.1K/-0.4K` lines:

- runtime/plugin code: `+11.1K/-0.4K`
- regression tests: `+3.5K`
- native archive worker: `+0.15K`
- README and maintainer docs: `+0.37K`
- CI/package support: `+0.02K`

## Follow-Up

- Run a larger collection stress test around 2000 mods.
- Expand platform validation on native Windows 11.
