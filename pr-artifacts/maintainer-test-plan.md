# Collection Download/Install Test Plan

## Unit / White-Box Tests

Run:

```bash
uv run ruff check __meta__.py api.py collection_helpers.py download.py install.py var.py tests/test_collection_helpers.py tests/test_reset_collection_state.py scripts/native_archive_worker.py scripts/reset_collection_state.py
uv run ruff format --check __meta__.py api.py collection_helpers.py download.py install.py var.py tests/test_collection_helpers.py tests/test_reset_collection_state.py scripts/native_archive_worker.py scripts/reset_collection_state.py
python -m py_compile __meta__.py api.py collection_helpers.py download.py install.py var.py scripts/native_archive_worker.py scripts/reset_collection_state.py
uv run python -m unittest tests.test_collection_helpers tests.test_reset_collection_state -v
```

Expected result:

- all modules compile
- intended final-branch Python files pass lint and formatting checks
- all helper/reset tests pass

Covered behavior:

- collection link parsing and safe display text handling
- plugin setting coercion
- completed download reconciliation from MO2 metadata and archive filenames
- stale `.unfinished` cleanup and restart-boundary classification
- duplicate `Download again?`, `Already Started`, and `Already Queued` prompt
  handling
- quota/rate-limit detection and retry messaging
- archive member parsing from `7z l -slt`
- safe headless install layouts for `.zip`, `.7z`, and `.rar`
- FOMOD detection and manual-choice guide generation
- no-GUI fallback behavior for ambiguous layouts and missing archive adapters
- unsafe archive path rejection
- native archive-worker request/result handling
- activation/install completion choice behavior

## Live / Black-Box Probe

Environment used for the current validation:

- MO2 2.5.2
- Skyrim Special Edition through Steam/Proton
- Linux host: CachyOS
- Nexus collection A: `xxsqm4`, revision `99`
- Name: `Immersive & Adult`
- Collection A file entries: `559`
- Nexus collection B: `8vdyr1`, revision `12`
- Name: `Sexy Statues`
- Collection B file entries: `75`

Validated probes:

- safe `.7z` archive installed headlessly into a named MO2 mod container
- safe `.zip` archive installed headlessly into a named MO2 mod container
- safe `.rar` archive installed headlessly into a named MO2 mod container
- Proton direct archive subprocess failure fell back to the native archive worker
- headless installs completed without opening MO2 Quick Install or FOMOD UI
- quota/rate-limit state stopped queueing and remained restartable
- `xxsqm4` repair pass over reconciled downloaded state completed
  `559/559` installed/root-handled with `0` failed/skipped
- `8vdyr1` completed `75/75` installed/root-handled with `0` failed/skipped
  when stacked after the larger collection

Measured probe timings:

- `.7z`: `Acquisitive Soul Gems Multithreaded`, 200ms
- `.zip`: `Companions Questline Tweaks`, 118ms
- `.rar`: `JK's High Hrothgar`, 163ms

Full collection timings are intentionally left for the final reset proof. Record
download, install, activation, and total elapsed time for each target collection
there so the PR describes measured behavior, not estimates.

## End-To-End Acceptance

Before requesting final upstream review, run one clean proof:

1. Reset the test MO2 instance for the target collections.
2. Download collection A.
3. Install collection A.
4. Activate collection A mods/plugins.
5. Download collection B.
6. Install collection B.
7. Activate collection B mods/plugins.
8. Run final audit:
   - expected collection entries
   - completed downloads
   - installed/root-handled entries
   - active mod containers
   - active plugins
   - missing/blocked entries with reasons
9. Record final timings:
   - collection id and revision
   - collection file entries
   - download elapsed time
   - install elapsed time
   - activation/audit elapsed time
   - total elapsed time
   - unexpected blockers or manual entries

The PR is ready only when the final audit shows no unexpected missing downloads,
no unexpected uninstalled entries, and no expected inactive mod containers.
