# Collection Download/Install Test Plan

## White-Box Checks

Run:

```bash
uv run ruff check __meta__.py api.py collection_helpers.py download.py install.py var.py tests/test_collection_helpers.py scripts/native_archive_worker.py
uv run ruff format --check __meta__.py api.py collection_helpers.py download.py install.py var.py tests/test_collection_helpers.py scripts/native_archive_worker.py
python -m py_compile __meta__.py api.py collection_helpers.py download.py install.py var.py scripts/native_archive_worker.py tests/test_collection_helpers.py
uv run python -m unittest tests.test_collection_helpers -v
```

Expected result:

- all modules compile
- lint and formatting checks pass for the intended deliverable files
- all collection helper tests pass

Covered behavior:

- collection link parsing and safe display text handling
- plugin setting coercion and default cadence
- completed archive reconciliation from MO2 metadata and archive filenames
- stale `.unfinished` cleanup and restart-boundary classification
- duplicate `Download again?`, `Already Started`, and `Already Queued` handling
- quota/rate-limit detection and retry messaging
- archive member parsing from `7z l -slt`
- safe headless layouts for `.zip`, `.7z`, and `.rar`
- FOMOD detection and manual-choice guide generation
- no-GUI fallback behavior for ambiguous layouts or missing archive adapters
- unsafe archive path rejection
- native archive-worker request/result handling
- installed mod metadata repair for version and Nexus category fields
- activation/install completion and postcondition audit behavior

## Live Checks

Environment used for validation:

- MO2 2.5.2
- Skyrim Special Edition through Steam/Proton
- Linux host
- Nexus collection `xxsqm4`, revision `99`, `Immersive & Adult`, 559 entries
- Nexus collection `8vdyr1`, revision `12`, `Sexy Statues`, 75 entries

Validated:

- safe `.7z`, `.zip`, and `.rar` archives installed without MO2 Quick Install UI
- Proton archive subprocess failure fell back to the native archive worker
- Add Collection replay repaired installed/download metadata and activation
- success summaries auto-closed during automatic handoff/replay
- stacked target collections converged to installed and active state
- MO2 Sort completed after convergence

Observed final replay results on July 29, 2026:

- `xxsqm4`: 559 installed/already present/root-handled, 0 downloaded but not
  installed, 558 activated collection mods, 500 plugins already active, 0 blocked
- `8vdyr1`: 75 installed/already present/root-handled, 0 downloaded but not
  installed, 75 activated collection mods, 23 plugins already active, 0 blocked

Residual validation:

- MO2 Sort reported a missing master from collection content. The installer
  correctly converged local download/install/activation state; the missing master
  should be handled as load-order/content validation outside this PR.
- A future stress pass should exercise a larger collection around 2000 mods.
