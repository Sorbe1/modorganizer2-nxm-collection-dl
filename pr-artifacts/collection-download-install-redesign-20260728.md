# Collection Download/Install Redesign

## Problem

The collection flow needs to be restartable and predictable at large scale. A
reactive implementation that waits for MO2 dialogs and repairs each condition in
place can drift into duplicate queue prompts, stale `.unfinished` files,
numbered duplicate archives, slow installer UI driving, and incomplete
activation state.

The desired behavior is a convergent Add Collection action: each run compares
the collection manifest to local downloads, installed MO2 mod containers,
download metadata, and profile activation, repairs safe local drift, and then
continues only the work that remains.

## Target Model

Use deterministic desired-state phases:

1. Parse the collection manifest into expected Nexus `(modId, fileId)` entries.
2. Reconcile completed archives, download metadata, partial files, duplicate
   prompts, and quota/rate-limit boundaries before queueing more Nexus requests.
3. Queue only genuinely missing downloads.
4. Install only complete archives.
5. Direct-install safe non-FOMOD archives headlessly into named MO2 mod folders.
6. Use MO2 installer UI only for confirmed FOMOD/manual archives.
7. Repair installed metadata, download metadata, priority order, active mod
   state, and plugin activation during the final sweep.

The product objective is practical one-click collection parity for MO2 users,
with better restartability and auditability than a dialog-driven flow.

## Boundaries

- `download-state`: classify completed, missing, partial, duplicate, quota
  stopped, and restart-required download state.
- `download-queue`: submit missing Nexus file requests once per run and stop
  cleanly on quota/rate-limit or stale MO2 queue state.
- `archive-plan`: classify complete archives as safe headless install, FOMOD,
  root/game-directory handled, ambiguous, or manual.
- `headless-install`: inspect and extract safe `.zip`, `.7z`, and `.rar`
  archives without opening MO2 Quick Install or FOMOD windows.
- `archive-worker`: provide a narrow JSON `list`/`extract` adapter when MO2's
  embedded Python cannot run archive tools directly, as observed under Proton.
- `fomod-install`: drive real FOMODs only with bounded default `Next`/`Install`
  automation.
- `activation-audit`: repair profile activation and plugin activation, then
  report the final state.

## Rules

- Completed archives on disk are credited before any queue call.
- Zero-byte `.unfinished` placeholders for expected files may be removed during
  preflight; nonzero partials are preserved.
- `Download again?` is answered with `No` in collection mode.
- `Already Started` and `Already Queued` prompts are acknowledged, then the run
  waits briefly for real local progress before stopping as restart-required.
- Quota/rate-limit detection is based on explicit API/dialog responses, not MO2's
  possibly stale status bar text.
- Safe archive installs do not fall back to MO2 Quick Install when preflight is
  ambiguous. Ambiguity is reported as a manual blocker.
- Headless installs write Nexus identity, file version, mod version, and Nexus
  category metadata. Replay also repairs those deterministic fields for older
  installed collection containers without reinstalling files.
- Category repair maps collection category names through MO2's `categories.dat`
  table. If MO2 cannot identify the category, unrelated MO2-local metadata
  remains user/MO2-owned.
- Empty invalid installed containers are replayed from their original archive.
  Non-empty invalid containers are kept installed but disabled during the final
  sweep because they may be tool or root payloads rather than broken game-data
  installs.

## Validation Notes

White-box tests cover download reconciliation, quota handling, duplicate prompts,
archive layout classification, unsafe archive path rejection, native archive
worker request handling, metadata repair, activation decisions, and final
postcondition auditing.

Live MO2 2.5.2 / Steam Proton validation used:

- `xxsqm4` revision `99`, `Immersive & Adult`, 559 collection file entries
- `8vdyr1` revision `12`, `Sexy Statues`, 75 collection file entries

Observed final replay results on July 29, 2026:

- `xxsqm4` replay: 559 entries installed/already present/root-handled, 0
  downloaded-but-not-installed, 553 activated collection mods, 535 plugins
  already active, 0 blocked, 0 failed/skipped entries, success summary
  auto-closed.
- Final `xxsqm4` replay repaired installed download metadata, restored version
  and category metadata, handled the root Engine Fixes preloader as already
  present, and produced 0 active invalid game-data containers.
- Post-run audits found 0 blank versions, 0 blank categories, and confirmed
  previously disabled plugins persisted as enabled with canonical filename
  casing.
- `8vdyr1` replay: 75 entries installed/already present/root-handled, 0
  downloaded-but-not-installed, 75 activated collection mods, 23 plugins already
  active, 0 blocked, success summary auto-closed.
- A final MO2 Sort pass completed and reported a missing master from collection
  content. That is a load-order/content validation finding rather than an
  installer convergence failure.

Backlog validation: run a larger collection stress test around 2000 mods after
the two target collections remain stable.
