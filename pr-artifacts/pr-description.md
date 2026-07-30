# Improve collection download/install reliability

## Summary

This PR makes collection handling deterministic and resumable across download,
install, and activation. The main change is to stop treating MO2 dialogs as the
primary control surface for ordinary archives: completed downloads are
reconciled from disk first, safe archives are installed headlessly, and MO2
installer UI is reserved for real FOMOD/manual cases.

The branch is intended as a cohesive maintainable update, not a pile of local
workarounds. It includes the final design note, user-facing README updates,
focused unit/functional tests, support scripts for live proof, and maintainer
review notes so the behavior and boundaries are explicit.

The product goal is practical one-click collection parity with the Vortex
installer flow for MO2 users, while improving the parts that hurt in large real
runs: restartability, quota preservation, reduced focus stealing, and a clear
final audit.

## What changed

- Reconcile collection download state before queueing Nexus requests, including
  completed archives, stale zero-byte `.unfinished` files, duplicate prompts,
  quota/rate-limit stops, and restart-required MO2 queue state.
- Pause proactively when MO2's visible Nexus API quota counters approach the
  hourly or daily safety floor, preserving restartability and avoiding the final
  request cliff.
- Avoid duplicate download storms by queueing each missing Nexus file once per
  run and never choosing `Download again?` automatically in collection mode.
- Continue queueing through stale zero-byte laggards during the active MO2 batch
  instead of deleting/requeueing them. Recovery is deferred until the batch
  drains or the run reaches a restartable quota boundary, which avoids duplicate
  prompt storms and preserves throughput.
- Add a headless install route for safe `.zip`, `.7z`, and `.rar` archives.
  These archives are inspected, extracted into a named MO2 mod container, and
  given Nexus metadata without opening Quick Install or FOMOD dialogs.
- Add a narrow native archive-worker boundary for environments where MO2's
  embedded Python cannot spawn archive tools directly, such as Proton/Linux.
  The installer only depends on JSON `list` and `extract` results.
- Keep FOMOD handling bounded and explicit: real FOMODs use MO2's installer UI,
  default pages can be advanced automatically, and unresolved manual choices are
  reported instead of guessed.
- Report ambiguous archives and archive-adapter failures as manual blockers
  instead of falling back to MO2 Quick Install as hidden recovery.
- Split install and activation into separate phases with a final audit of
  expected downloads, installed mod containers, active mods, and active plugins.
- Reconcile collection plugins through the profile `plugins.txt` file instead
  of MO2's live plugin state API during automatic activation. This avoids
  plugin-not-found modals when MO2 has not refreshed its in-memory plugin model
  after newly created mod containers.
- Update the README to describe the deterministic download model, headless
  archive install path, platform expectations, and focused contributor checks.
- Include a reset-state utility used by the live proof and regression tests; it
  deletes bulky collection-owned payloads and retains only small audit artifacts.

## Review notes

- The detailed design document is included as
  `pr-artifacts/collection-download-install-redesign-20260728.md`. It records
  the intended boundaries, download/install/activation rules, test strategy, and
  live probe results used to shape this implementation.
- The deliverable set is meant to be reviewable as a production-quality change:
  runtime code, regression tests, reset/probe support scripts, README updates,
  a maintainer test plan, and outreach/PR notes are kept together with abandoned
  exploratory artifacts excluded from the clean branch.
- Scope is roughly `+14.9K/-0.35K` lines across the intended final branch:
  - `+14.0K/-0.34K` runtime/test/script lines, including download reconciliation,
    install planning, headless archive install, activation audit, and settings
  - `+0.86K/-0.01K` README, design, test-plan, release-checklist,
    clean-branch, outreach, and PR notes
- The direct archive path is intentionally small: classify archive members,
  extract to a temporary directory, move the proven install payload into the
  target MO2 mod directory, then write `meta.ini`.
- The archive worker is an adapter, not a second installer. Windows can use
  direct `7z.exe`; Proton/Linux can run the same list/extract contract through a
  host worker.
- GUI automation remains only for dialogs that cannot be avoided: real FOMOD
  pages, known safe confirmation prompts, and known error/report dialogs.
- Existing plugin settings are kept compatible where possible; the ZIP-only
  headless setting remains as a legacy fallback for the new archive setting.

## Verification

- `uv run ruff check __meta__.py api.py collection_helpers.py download.py install.py var.py tests/test_collection_helpers.py tests/test_reset_collection_state.py scripts/native_archive_worker.py scripts/reset_collection_state.py`
- `uv run ruff format --check __meta__.py api.py collection_helpers.py download.py install.py var.py tests/test_collection_helpers.py tests/test_reset_collection_state.py scripts/native_archive_worker.py scripts/reset_collection_state.py`
- `python -m py_compile __meta__.py api.py collection_helpers.py download.py install.py var.py scripts/native_archive_worker.py scripts/reset_collection_state.py`
- `uv run python -m unittest tests.test_collection_helpers tests.test_reset_collection_state -v`
- White-box coverage includes:
  - download reconciliation for complete archives, stale `.unfinished` files,
    duplicate prompts, quota/rate-limit stops, and restart boundaries
  - installer routing for confirmed FOMODs, unknown archives, safe archive
    layouts, unsafe/archive-slip rejection, and legacy setting compatibility
  - native archive-worker request handling and Wine/Proton path adapter behavior
  - no-GUI fallback behavior when archive preflight is ambiguous or unavailable
  - activation/install completion choice behavior and reset-state helpers
- Black-box MO2 2.5.2 / Proton probes were run against Nexus collection
  `xxsqm4` revision `99` (`Immersive & Adult`, 559 collection file entries):
  - Proton direct archive subprocess failure fell back to the native archive
    worker
  - `.7z` headless install completed without MO2 installer UI in 200ms
  - `.zip` headless install completed without MO2 installer UI in 118ms
  - `.rar` headless install completed without MO2 installer UI in 163ms
  - download quota/rate-limit handling was observed against the same collection
    and now stops queueing with restartable state instead of continuing into
    duplicate dialog storms
- Black-box collection repair/stacking results:
  - `xxsqm4` revision `99`: completed `559/559` installed/root-handled with
    `0` failed/skipped; final audit reported `558` MO2 containers plus one
    explicit root/game-directory entry for the SSE Engine Fixes preloader
    archive, `558` active installed mods, `0` priority failures, and `0` MO2
    warnings.
  - `xxsqm4` revision `99`: final reset/resume proof reconciled zero-byte
    laggards across MO2 restarts, ended with `559` successful downloads and `0`
    unfinished files, then installed `479` archives headlessly and advanced
    FOMOD defaults `251` times.
  - `8vdyr1` revision `12` (`Sexy Statues`, 75 collection file entries):
    stacked after the larger collection, recovered all remaining zero-byte
    laggards across MO2 restarts, and finished `75/75` installed/root-handled
    with `0` failed/skipped, `75` active installed mods, `0` priority failures,
    and `0` MO2 warnings.
  - `egtcmf` revision `9` (`Updated Sexy Creatures Collection`, 26 collection
    file entries): original Add Collection path finished `26/26`
    installed/root-handled with `0` failed/skipped, repaired collection priority
    order, activated `26` installed mods, and repaired/enabled the one expected
    plugin without raising MO2's plugin-not-found modal.
  - `lwpfm1` revision `2` (`I&A Houses Expansion`, 13 collection file entries):
    original Add Collection path downloaded `13/13` archives, installed all
    entries headlessly, then a recovery replay finished `13/13`
    installed/root-handled with `0` failed/skipped. The recovery pass repaired
    the previously unstarred `MoonstoneCastle.esp` on disk and reported
    `1` plugin activated, `11` already active, and `0` blocked without an MO2
    plugin-not-found modal.
  - `4vz5sn` revision `7` (`Community Shaders 2026`, 53 collection file
    entries): original Add Collection replay finished `53/53`
    installed/root-handled with `0` failed/skipped. The run installed the
    NAT/TrueStorms FOMOD compatibility archive headlessly after dependency
    evidence selected `True Storms Pure - Water's Edge Fix`, repaired collection
    priority order, and activated/repaired the expected plugins without MO2
    warnings.
- Focused post-proof gate after the `egtcmf` and `lwpfm1` fixes:
  - `python3 -m unittest tests.test_collection_helpers.SevenZipArchiveMemberPathsTests tests.test_collection_helpers.HeadlessZipInstallLayoutTests tests.test_collection_helpers.ExtractHeadlessZipArchiveTests tests.test_collection_helpers.MoveHeadlessArchivePayloadTests tests.test_collection_helpers.DownloadProgressStateTests`
  - `python3 -m unittest test_collection_helpers.SevenZipArchiveMemberPathsTests test_collection_helpers.HeadlessZipInstallLayoutTests test_collection_helpers.ExtractHeadlessZipArchiveTests test_collection_helpers.MoveHeadlessArchivePayloadTests test_collection_helpers.DownloadProgressStateTests`
  - `python3 -m py_compile collection_helpers.py install.py download.py scripts/native_archive_worker.py`
  - `python3 -m unittest tests.test_collection_helpers.SevenZipArchiveMemberPathsTests tests.test_collection_helpers.HeadlessZipInstallLayoutTests tests.test_collection_helpers.DownloadProgressStateTests`
  - `python3 -m py_compile install.py collection_helpers.py download.py scripts/native_archive_worker.py`
- Focused post-proof gate after the `4vz5sn` fixes:
  - `python3 -m unittest tests.test_collection_helpers`
  - `python3 -m unittest test_collection_helpers`
  - `python3 -m py_compile download.py collection_helpers.py install.py scripts/native_archive_worker.py`
  - `git diff --check`

## Measured timings

Current measured black-box timings are per-archive headless install probes in
MO2 2.5.2 under Proton:

| Collection | Archive type | Mod | Elapsed |
| --- | --- | --- | --- |
| `xxsqm4` rev `99` | `.7z` | `Acquisitive Soul Gems Multithreaded` | 200ms |
| `xxsqm4` rev `99` | `.zip` | `Companions Questline Tweaks` | 118ms |
| `xxsqm4` rev `99` | `.rar` | `JK's High Hrothgar` | 163ms |

Observed collection-level completion results:

| Collection | Entries | Result | Notes |
| --- | ---: | --- | --- |
| `xxsqm4` rev `99` | 559 | `559/559` installed/root-handled, `0` failed | Final reset/resume proof; one root/game-directory preloader entry |
| `8vdyr1` rev `12` | 75 | `75/75` installed/root-handled, `0` failed | Stacked after the larger collection |
| `egtcmf` rev `9` | 26 | `26/26` installed/root-handled, `0` failed | Original Add Collection replay after downloads were present; includes variant `Data` archive handling |
| `lwpfm1` rev `2` | 13 | `13/13` installed/root-handled, `0` failed | Original Add Collection proof plus recovery replay; profile plugin repair enabled `MoonstoneCastle.esp` without MO2 modal |
| `4vz5sn` rev `7` | 53 | `53/53` installed/root-handled, `0` failed | Community Shaders proof; NAT/TrueStorms FOMOD selected from profile evidence and installed headlessly |

Observed final proof timings from MO2 log timestamps:

| Scope | Started | Completed | Elapsed | Notes |
| --- | --- | --- | ---: | --- |
| `xxsqm4` download/restart proof | 22:17:28 | 22:44:31 | 27m 03s | Includes restart/resume cycles for stale zero-byte MO2 queue state |
| `xxsqm4` install + activation | 22:45:37 | 22:55:33 | 9m 56s | `479` headless archives, `79` FOMOD entries, `251` default FOMOD advances |
| `xxsqm4` total final proof | 22:17:28 | 22:55:33 | 38m 05s | `559/559`, `0` failed/skipped |
| `8vdyr1` stacked download/restart proof | 22:56:36 | 23:07:19 | 10m 43s | Stacked after `xxsqm4`; includes two restart/resume cycles for stale zero-byte MO2 queue state |
| `8vdyr1` install + activation | 23:07:27 | 23:07:56 | 29s | `75` headless archives, no FOMOD/manual entries |
| Both collections stacked | 22:17:28 | 23:07:56 | 50m 28s | `634/634` combined downloaded archives, `0` unfinished files |
| `4vz5sn` final replay | 16:24:22 | 16:24:32 | 10s | `53/53`, `0` failed/skipped; NAT/TrueStorms repaired via headless FOMOD dependency selection |
| `4vz5sn` fast replay | 16:34:37 | 16:34:45 | 8s | `53/53`, `0` failed/skipped; no install work remained, activation audit clean |

## Follow-up

- Perform a final DONE sweep over README, PR body, design note, test plan,
  checklist, manifest, and maintainer emails so the submitted branch contains no
  stale placeholders or temporary caveats.
- After the replacement PR is verified, close any stale exploratory PR if still
  open and remove the old exploratory branch.
