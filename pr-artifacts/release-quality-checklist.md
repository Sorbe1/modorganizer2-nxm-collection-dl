# Release Quality Checklist

Use this checklist before opening the clean final PR.

## Design

- The README describes the final user-visible model, not the abandoned
  GUI-driver-first flow.
- The PR description states the problem and final architecture directly.
- The design note explains the boundaries:
  - manifest
  - download-state
  - download-queue
  - archive-plan
  - headless-install
  - archive-worker
  - FOMOD/manual install
  - activation-audit
- Any Proton/Linux path handling is contained inside adapter code, not spread
  through install orchestration.
- GUI automation is framed as a fallback for unavoidable FOMOD/manual cases, not
  the normal install path.

## Code

- Safe `.zip`, `.7z`, and `.rar` archives install headlessly.
- Real FOMOD/manual archives stay on the MO2 installer path.
- Ambiguous archives and archive-adapter failures become reported manual
  blockers, not Quick Install fallback.
- Download resume reconciles disk before spending Nexus API quota.
- Duplicate download prompts are declined or acknowledged without creating
  numbered duplicate archives.
- Quota/rate-limit stops are restartable and do not continue queueing.
- Activation is audited after installation instead of inferred.
- No transient probe code, local build products, or abandoned retry paths are
  included in the clean branch.

## Tests

- Intended final-branch Python files pass `ruff check` and `ruff format --check`.
- Unit tests cover each new helper boundary and every bug class found during
  live testing.
- Regression tests exist for:
  - duplicate download prompts
  - already-started/already-queued prompts
  - stale `.unfinished` files
  - quota/rate-limit stops
  - archive layout classification
  - FOMOD detection
  - no-GUI fallback for ambiguous/archive-adapter failures
  - unsafe archive members
  - native archive-worker request handling
  - activation/install completion choices
- Compile and unit tests pass from a clean checkout.

## Live Proof

- The test plan names the exact collection(s), revision(s), MO2 version, and
  runtime environment used.
- A clean reset run downloads the target collection without duplicate dialog
  storms.
- Reset proof tooling does not retain bulky full-copy backups of deleted
  collection mods/downloads and requires an explicit target MO2 base path.
- Safe archive installs do not show Quick Install or steal focus.
- Real FOMOD/manual blockers are reported clearly.
- Final audit shows expected downloads, installed containers, active mods, active
  plugins, and any blocked entries with reasons.
- Full collection timings are captured for each tested collection: download,
  install, activation/audit, total elapsed time, and expected blockers.

## PR Polish

- The branch contains only delivered artifacts from the clean manifest.
- Commit history reads as intentional implementation steps.
- The PR body emphasizes the designed architecture, tested behavior,
  professional code quality, and complete deliverable set.
- The PR body includes summary, review notes, verification, and follow-up.
- Maintainer outreach drafts exist for the introductory/access request and the
  PR review request.
- Screenshots/log snippets are used only when they help prove behavior.
- Any stale exploratory PR is closed after the replacement PR is verified, if it
  is still open.
- At the end, GitHub retains only the final intentional branch for this work.
  The old exploratory branch is deleted only after the clean PR is verified and
  no needed work remains there.

## Final DONE Sweep

Run this only after live proof and PR preparation are otherwise complete:

- Re-read the README, PR body, design note, test plan, release checklist,
  clean-branch manifest, and maintainer emails as one deliverable.
- Remove stale language, placeholders, estimates, and temporary caveats that
  were only valid before final proof.
- Replace pending timing placeholders with measured collection timings.
- Confirm the PR body tells one coherent story: designed architecture,
  professional code quality, regression coverage, live validation, and clear
  remaining limitations.
- Re-run lint, format, compile, unit tests, and the final live audit after any
  DONE-sweep edits.
