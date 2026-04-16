# 🔖 My Bookmarks

A Git-based bookmark manager that syncs Chrome and Edge bookmarks into a single, deduplicated repository.

### 🌐 [**Browse Bookmarks →**](https://sendtoshailesh.github.io/my-bookmarks/)

## Features

- **[Web UI](https://sendtoshailesh.github.io/my-bookmarks/)** — modern single-page app with sidebar navigation, search, card/list views, favicons, and dark/light theme
- **Export** bookmarks from Chrome and Edge
- **Deduplicate** by URL (normalized — trailing slashes, case-insensitive host)
- **Store** as both JSON (structured, diffable) and Markdown (browsable on GitHub)
- **Import** merged bookmarks back into browsers (with automatic backup)
- **Git integration** — auto-commits changes on sync

## Quick Start

```bash
# Export bookmarks from both browsers, deduplicate, and commit
python3 bookmark_sync.py sync

# Export only (no import back to browsers)
python3 bookmark_sync.py export

# Import repo bookmarks back into browsers
python3 bookmark_sync.py import

# Preview what would happen (no changes made)
python3 bookmark_sync.py sync --dry-run

# Target a specific browser
python3 bookmark_sync.py sync --browser chrome
python3 bookmark_sync.py sync --browser edge
```

## Commands

| Command   | Description |
|-----------|-------------|
| `export`  | Read bookmarks from browsers → deduplicate → write `bookmarks.json` + `bookmarks.md` |
| `import`  | Read `bookmarks.json` from repo → write back to browser bookmark files |
| `sync`    | Export + Import + Git commit (full bidirectional sync) |
| `status`  | Show bookmark counts per browser and repo stats |

## Flags

| Flag | Description |
|------|-------------|
| `--browser chrome\|edge\|both` | Target specific browser (default: `both`) |
| `--dry-run` | Preview changes without writing any files |
| `--no-commit` | Skip Git commit after sync |

## Files

| File | Description |
|------|-------------|
| `bookmarks.json` | Canonical merged bookmarks in Chromium JSON format |
| `bookmarks.md` | Human-readable Markdown view (auto-generated) |
| `bookmark_sync.py` | The sync tool |
| `backups/` | Browser backup files created before import (gitignored) |
| `sync-bookmarks.sh` | One-click sync script with macOS notifications |

## ⚡ One-Click Sync (macOS)

### Option 1: Shell Script
```bash
./sync-bookmarks.sh           # sync both browsers
./sync-bookmarks.sh chrome    # Chrome only
./sync-bookmarks.sh edge      # Edge only
```
Shows a macOS notification with results ✅

### Option 2: Keyboard Shortcut
The repo includes a macOS Quick Action (`Sync Bookmarks`) installed at `~/Library/Services/`.

To assign a keyboard shortcut:
1. Open **System Settings → Keyboard → Keyboard Shortcuts → Services → General**
2. Find **Sync Bookmarks** and assign a shortcut (e.g. `⌃⌥⌘B`)

You can also run it from any app's menu: **App Name → Services → Sync Bookmarks**

### Option 3: Web UI
Press **R** (or click 🔄) in the [web app](https://sendtoshailesh.github.io/my-bookmarks/) to refresh.
Press **N** (or click ➕) to add a bookmark directly from the browser.

## Requirements

- Python 3.9+
- macOS (Chrome/Edge bookmark paths are macOS-specific)
- Close Chrome/Edge before running `import` or `sync`

## How It Works

1. Reads bookmark JSON files from Chrome and Edge (both use Chromium format)
2. Flattens the folder hierarchy into a list of `{url, title, folder_path}`
3. Normalizes URLs and removes duplicates (first occurrence wins)
4. Rebuilds the folder hierarchy and writes `bookmarks.json` + `bookmarks.md`
5. On import, backs up browser files and writes the merged bookmarks back
6. Auto-commits changes to Git with a descriptive message
