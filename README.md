# MO2 Nexus Collection Downloader

A Mod Organizer 2 plugin that lets you download Nexus Mods collections directly within MO2. Simply paste a collection URL, pick a revision, and download the mods you want.

## Features
- Enter a NXM collection URL and inspect the collection metadata (name, author, description, thumbnail).
- Handle Nexus Mods "Add Collection" links from a browser or MO2's Nexus link handler.
- Choose a specific revision of the collection.
- View counts for essential, optional, external and bundled resources.
- Select optional/external items to include before downloading.
- Queue Premium downloads through MO2 and skip archives that are already present.
- Install downloaded collection files one at a time.
- Install repeated collection entries as separate MO2 mods with numbered suffixes.
- Optionally activate installed collection mods and plugins after installation.
- Optionally accept selected default choices in visible FOMOD installers.
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

The installer processes one downloaded archive at a time and waits briefly before
starting the next archive. This avoids overlapping MO2 installer sessions while
still allowing normal MO2 installer dialogs to appear when a mod needs manual
choices. By default, the install pass checks installed collection mods in MO2
and attempts to activate plugins from those mods after the collection completes.
Collection files are installed as separate MO2 mod rows by default; repeated
files from the same Nexus mod use `#2`, `#3`, and later suffixes instead of
silently merging into one row. Collection author FOMOD choice replay is not
implemented, but `auto_advance_fomod_defaults` can optionally advance visible
FOMOD installers by accepting their selected default choices.

## Contributing

Contributions welcome! Feel free to open issues or submit pull requests.

Set up the dev environment with `uv sync`, set up pre-commit hooks, then copy the files to your MO2 plugins directory to test.
