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
