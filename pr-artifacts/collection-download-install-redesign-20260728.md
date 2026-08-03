# Collection Download/Install Redesign

## Problem

The current collection flow is too reactive. It observes MO2 dialogs after the
fact and tries to repair each condition in-place. That created overlapping retry
paths:

- queue retries
- stale `.unfinished` retries
- zero-byte placeholder retries
- duplicate prompt handling
- already-started prompt handling
- terminal recovery passes

In practice those paths can fight each other. MO2 can keep stale download state
in memory even after a zero-byte placeholder is removed, so requeueing inside the
same MO2 process can trigger storms of `Already Started`, `Already Queued`,
`Download again?`, and rename/write errors.

## Target Model

Use a deterministic desired-state loop. The main objective is complexity
reduction: small functions, explicit state transitions, and no opportunistic GUI
recovery in the hot path.

The product objective is practical one-click collection parity with the Vortex
installer flow for MO2 users, plus better restartability and auditability where
MO2 gives us the hooks to do that.

Collection work should be background-first. Queueing, reconciliation, archive
inspection, safe extraction, and activation should run without taking global
focus. Progress UI may remain visible, but plugin code must not repeatedly raise
or activate MO2 windows. Human attention is required only for explicit manual
blockers such as ambiguous FOMOD choices, quota stops, or unrecoverable archive
layout errors.

1. Manifest state is the target: each Nexus `(modId, fileId)` is either complete,
   missing, blocked by quota, or blocked by MO2 restart.
2. Completed archives on disk are authoritative. A completed archive is credited
   before any MO2 queue call.
3. MO2 queue calls are single-shot per run. If MO2 refuses with duplicate or
   already-started state and no complete archive appears, the current run stops
   cleanly with a restartable state.
4. Resume never wastes quota. On relaunch, the plugin reconciles disk first,
   removes dead zero-byte placeholders, and queues only genuinely missing files.
5. Install and activation are separate phases. Download completion does not imply
   install completion, and install completion does not imply activation.

## Architecture Objective

Rewrite the new code paths intentionally around these boundaries:

- `manifest`: parse collection metadata into expected `(modId, fileId)` records.
- `download-state`: reconcile disk state, MO2 metadata, partials, quota stops,
  and restart-required boundaries.
- `download-queue`: issue only missing download requests. It does not repair
  installs and does not continue after stale in-memory MO2 queue state.
- `archive-plan`: inspect a completed archive once and classify it as
  `headless`, `fomod`, `ambiguous`, or `manual`.
- `headless-install`: direct extract safe non-FOMOD archives into a named MO2 mod
  container and write local metadata. It does not open MO2 installer dialogs.
- `archive-worker`: perform archive list/extract behind a JSON request/result
  boundary when MO2's embedded Python cannot safely spawn native archive tools.
  Proton/Linux uses a host worker over the MO2 base directory; native Windows can
  use direct `7z.exe` or an equivalent Windows worker without changing install
  orchestration.
- `fomod-install`: only handles real FOMODs. Its default driver is just
  `Next*` then `Install`, with a bounded timeout and a manual stop on choices
  the collection did not encode.
- `manual-blocker`: report ambiguous archive layouts, archive-adapter failures,
  and root/game-directory installs explicitly. These cases do not fall back to
  Quick Install as hidden recovery.
- `activation-audit`: activate expected containers/plugins and report the final
  state.

Each boundary should be unit-testable without MO2. The MO2 black-box tests
should prove orchestration only, not compensate for unclear helper behavior.

The final PR should read as a professional deliverable: designed behavior,
focused runtime changes, regression coverage for each bug class discovered in
live testing, an operator-facing README, and maintainer notes that make the
review surface clear.

## Download Rules

- Before queueing:
  - Reconcile completed archives by metadata, archive name, and exact expected
    size where available.
  - Remove zero-byte `.unfinished` files for collection keys.
  - Do not remove nonzero partial downloads during normal preflight.

- While queueing:
  - Keep one collection download coordinator active.
  - Queue at a controlled rate, but do not issue another request for a key that
    is complete, queued this run, or waiting in MO2.
  - Use bounded queue depth for throughput, but reconciliation remains the source
    of truth.

- Duplicate dialogs:
  - `Download again?`: choose `No`. If the archive is complete, mark complete.
    If it is not complete, mark restart-required and stop this run.
  - `Already Started` / `Already Queued`: choose `OK`, wait briefly for a real
    archive or nonzero partial. If none appears, mark restart-required and stop.
  - Never choose `Yes` automatically in collection mode. Numbered duplicate
    archives are almost always a symptom of stale state and make reconciliation
    harder.

- Quota/rate limiting:
  - Detect only explicit API responses or MO2 dialogs containing quota/rate-limit
    text, not the status bar counters.
  - Stop queueing immediately.
  - Persist enough state to resume by reconciling existing archives first.
  - Show retry time when available. Retry should queue only missing files.

## Install Rules

- Install only from complete downloaded archives.
- Direct-install every safe non-FOMOD archive headlessly. This is the normal
  path for `.zip`, `.7z`, and `.rar` archives whose layout can be proven safe.
- Do not depend on MO2 installer dialogs for safe archive installs. If direct
  archive inspection/extraction fails inside MO2, use the native archive worker
  instead of adding more GUI or drive-letter recovery code.
- If archive inspection remains unavailable, or the layout is ambiguous, record a
  manual blocker. Do not open MO2 Quick Install as fallback.
- Use MO2's installer UI only for confirmed FOMOD/manual cases.
- FOMOD automation is a simple page runner:
  - click enabled `Next` until unavailable
  - click enabled `Install`
  - short delay, target 50-100ms
  - timeout per page/window
- If a FOMOD presents multiple enabled choices where the collection does not
  encode a choice, stop with a manual report instead of guessing.
- After each install, reconcile MO2 mod containers by expected target name and
  Nexus metadata.

## Activation Rules

- After install, activate every installed collection mod that should be active.
- Reconcile the mod list again after activation and report:
  - collection entries
  - complete downloads
  - installed/root-handled entries
  - active mod containers
  - active plugins
  - blocked/missing entries with reasons

## Test Strategy

1. Unit tests for state classification:
   - complete archive with stale unfinished sibling
   - zero-byte placeholder
   - nonzero partial placeholder
   - duplicate prompt with complete archive
   - duplicate prompt without complete archive
   - already-started prompt with and without real partial
   - quota response and non-quota status-bar text
   - proactive hourly/daily quota-floor pause from MO2's visible API status
   - archive member-list parsing
   - safe headless archive layout
   - FOMOD archive classification
   - no-GUI fallback for ambiguous/archive-adapter failures
   - direct extraction payload movement
   - unsafe/archive-slip rejection

2. Integration probes:
   - single headless `.zip`, `.7z`, and `.rar` install
   - single FOMOD install with default `Next`/`Install`
   - single duplicate download prompt
   - single already-started placeholder restart/resume
   - quota exhausted, then resume after quota refresh

3. End-to-end proof:
   - reset both target collections
   - reset deletes collection-owned mod/download payloads and retains only the
     small audit manifest/profile snapshots needed to understand what changed
   - download collection A
   - install and activate collection A
   - download collection B
   - install and activate collection B
   - final audit all expected downloads/install containers/active mods

4. Backlog:
   - large 2000-mod collection stress test after the two target collections pass.
   - regenerate the pending PR series from the final architecture, not from the
     exploratory path. The review story should read as one intentional update:
     deterministic download state, headless archive install through a narrow
     archive-worker boundary, simplified FOMOD handling, and final activation
     audit. Drop abandoned retry/GUI-driving experiments from the narrative.
     Each PR should have focused regression coverage for the behavior it adds.
   - after the implementation is proven, open a second clean GitHub branch from
     the appropriate base and replay only the delivered final-design artifacts.
     Do not carry the meandering experiment history, transient probes, or
     abandoned code paths into that branch.
   - the replacement PR now contains the delivered final-design artifacts, and
     the old exploratory PR has been closed in favor of it. Any remaining old
     branch retention should be treated as temporary archaeology, not an active
     source line.

## 2026-07-28 Live Results

The first live headless `.7z` probe failed inside MO2's embedded Python with
`[WinError 6] Invalid handle` while spawning 7z. Retrying through a host native
archive worker succeeded without installer UI:

- `.7z`: `Acquisitive Soul Gems Multithreaded`, installed in 200ms
- `.zip`: `Companions Questline Tweaks`, installed in 118ms
- `.rar`: `JK's High Hrothgar`, installed in 163ms

This validates the headless archive path and makes the archive worker a required
adapter for Proton/Linux. The same architecture should remain viable on Windows
11 because the orchestration only depends on `list` and `extract` JSON results,
not on Proton-specific path behavior.

Additional black-box collection results from the same MO2/Proton environment:

- `xxsqm4` revision `99` (`Immersive & Adult`, 559 entries): a repair pass over
  the reconciled downloaded state finished `559/559` installed/root-handled,
  `0` failed/skipped. The final audit reported `558` MO2 mod containers and one
  explicit root/game-directory entry for the SSE Engine Fixes preloader archive.
- `8vdyr1` revision `12` (`Sexy Statues`, 75 entries): stacked after the larger
  collection and finished `75/75` installed/root-handled, `0` failed/skipped in
  the observed black-box run.

The submitted PR body now records the measured final proof timings that had
exact MO2 log start/end timestamps. Later stress-report evidence covers the
broader 10-collection matrix, including the documented manual/informational
FOMOD rows that remain after the current profile audit passes cleanly.
