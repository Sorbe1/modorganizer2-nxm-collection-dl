# MO2 Nexus Collection Downloader

A Mod Organizer 2 plugin that lets you download Nexus Mods collections directly
within MO2. Simply paste a collection URL, pick a revision, and download the
mods you want. The long-term goal is MO2 collection install parity with, and
where possible a better experience than, the one-click Vortex collection flow:
restartable downloads, less desktop focus stealing, and clear final audit
results.

## Features
- Enter a NXM collection URL and inspect the collection metadata (name, author, description, thumbnail).
- Handle Nexus Mods "Add Collection" links from a browser or MO2's Nexus link handler.
- Choose a specific revision of the collection.
- View counts for essential, optional, external and bundled resources.
- Select optional/external items to include before downloading.
- Queue Premium downloads through MO2 while reconciling archives already present on disk.
- Detect duplicate, stale, unfinished, and quota-limited download states so collection downloads can be restarted without wasting Nexus quota.
- Install safe non-FOMOD `.zip`, `.7z`, and `.rar` archives headlessly without opening MO2 installer dialogs.
- Use a native archive worker when MO2's embedded Python cannot inspect or extract archives directly, which is common under Proton.
- Install real FOMOD/manual archives one at a time with bounded default-choice automation.
- Install repeated collection entries as separate MO2 mods with numbered suffixes.
- Optionally activate installed collection mods and plugins after installation.
- Opens mod downloads in web browser if the user does not have Premium

#### Currently Unimplemented:
- Collection author FOMOD choice replay
- Cannot auto-detect user Premium status
- Unable to implement bundled resources (Not in Nexus API documentation, will check if possible soon.)

## Installation

1. Download the [latest release](https://github.com/Furglitch/modorganizer2-nxm-collection-dl/releases/latest)
2. Extract it into your MO2 `plugins` folder
3. Enable "NXM Collections Downloader" in MO2's Plugins manager, if it's not already enabled.
4. Find it under Tools → NXM Collections Downloader

## How to Use

1. Open the plugin from the Tools menu or click Nexus Mods' "Add Collection" button.
2. Paste a collection URL (e.g., `https://www.nexusmods.com/games/skyrimspecialedition/collections/qdurkx`)
3. Choose a revision
4. Select any optional items you want
5. Download via the 'Download Collection' tool
   - If you don't have Premium, make sure to check the 'Open in Browser' option to open the mod pages in your web browser for manual downloading.
6. Install the downloaded mods in MO2 using the 'Install Downloaded Collection' tool.

The collection URL parser accepts normal Nexus collection web URLs, collection
tab URLs, and `nxm://.../collections/...` links. Links without a revision use the
latest revision returned by Nexus Mods.

When launched from Nexus Mods' Add Collection button, the plugin downloads the
collection and then starts the install pass automatically by default. Disable
`auto_install_after_download` under the Nexus Mods Collections plugin settings
if you prefer to inspect the Downloads tab and install manually.

## Download and Install Model

The plugin treats the collection metadata as the desired state. Before queueing
downloads, it reconciles the MO2 downloads directory, completed archive metadata,
and unfinished placeholders. Completed archives are credited first; only
genuinely missing files are queued through MO2. If MO2 reports quota/rate limits
or stale in-memory queue state, the run stops with a restartable status instead
of repeatedly queueing the same file.

Replaying Add Collection is the normal recovery path. Each run first reconciles
local state: archives whose exact Nexus files are already installed are marked
installed in MO2's download metadata, installed collection mod containers are
enabled, deterministic installed mod metadata is repaired from the collection
manifest, and only the remaining incomplete downloads or installs continue
through the ordinary workflow. The intended result is idempotent convergence:
whether the collection completed in one pass or several interrupted passes, the
final local state should be the same.

The install pass separates safe archive installs from real installer UI:

- Safe non-FOMOD `.zip`, `.7z`, and `.rar` archives are inspected, extracted, and
  written into named MO2 mod folders directly. This is the default path and does
  not steal desktop focus.
- If archive inspection or extraction cannot run inside MO2, the plugin uses the
  `scripts/native_archive_worker.py` adapter. Under Proton/Linux this lets host
  `7z` perform the archive work through a JSON request/result boundary.
- Confirmed FOMOD archives still use MO2's installer. When
  `auto_advance_fomod_defaults` is enabled, the driver advances visible FOMODs by
  clicking enabled `Next` buttons and then `Install`, bounded by
  `auto_advance_fomod_max_steps`.
- Ambiguous archive layouts, root/game-directory installs, and archive adapter
  failures are reported as manual blockers. They do not fall back to MO2 Quick
  Install because that path can create wrong names, duplicate prompts, and focus
  loss.

By default, the install pass checks installed collection mods in MO2, repairs
download metadata for archives it installed, restores deterministic `meta.ini`
fields such as version and category, restores collection priority order,
activates installed collection mods, and attempts to activate plugins from those
mods after the collection completes. Category repair uses MO2's own
`categories.dat` table when the collection metadata names a category; unrelated
MO2-local metadata remains user/MO2-owned. Repeated files from the same Nexus mod
use `#2`, `#3`, and later suffixes instead of silently merging into one row.

## Platform Notes

The headless archive path is intentionally narrow: the plugin needs only archive
`list` and `extract` results. On Linux/Steam Deck/Proton, those operations can be
served by the bundled native worker and host `7z`. On Windows 11, the same
orchestration should work with MO2's normal archive tools or an equivalent
Windows `7z` worker; the install logic does not depend on Proton-specific
behavior.

## Contributing

Contributions welcome! Feel free to open issues or submit pull requests.

Set up the dev environment with `uv sync`, set up pre-commit hooks, then copy the files to your MO2 plugins directory to test.

Useful focused checks for this code path:

```bash
uv run ruff check __meta__.py api.py collection_helpers.py download.py install.py var.py tests/test_collection_helpers.py scripts/native_archive_worker.py
uv run ruff format --check __meta__.py api.py collection_helpers.py download.py install.py var.py tests/test_collection_helpers.py scripts/native_archive_worker.py
python -m py_compile __meta__.py api.py collection_helpers.py download.py install.py var.py scripts/native_archive_worker.py
uv run python -m unittest tests.test_collection_helpers -v
```
