#!/usr/bin/env python3
"""
bookmark_sync.py — Sync Chrome & Edge bookmarks via a Git repo.

Usage:
    python3 bookmark_sync.py export [--browser chrome|edge|both] [--dry-run]
    python3 bookmark_sync.py import [--browser chrome|edge|both] [--dry-run]
    python3 bookmark_sync.py sync   [--browser chrome|edge|both] [--dry-run] [--no-commit]
    python3 bookmark_sync.py status
"""

import argparse
import copy
import json
import os
import shutil
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path
from urllib.parse import urlparse, urlunparse

# ── Browser bookmark file locations (macOS) ─────────────────────────────────

BROWSER_PATHS = {
    "chrome": Path.home() / "Library" / "Application Support" / "Google" / "Chrome",
    "edge": Path.home() / "Library" / "Application Support" / "Microsoft Edge",
}

REPO_DIR = Path(__file__).resolve().parent
BOOKMARKS_JSON = REPO_DIR / "bookmarks.json"
BOOKMARKS_MD = REPO_DIR / "bookmarks.md"
BACKUPS_DIR = REPO_DIR / "backups"

# Root folder keys in Chromium bookmark JSON
ROOT_FOLDERS = ["bookmark_bar", "other", "synced"]
ROOT_FOLDER_NAMES = {
    "bookmark_bar": "Bookmarks Bar",
    "other": "Other Bookmarks",
    "synced": "Mobile Bookmarks",
}


# ── Helpers ──────────────────────────────────────────────────────────────────

def find_profiles(browser_base: Path) -> list[Path]:
    """Find all profile directories that contain a Bookmarks file."""
    profiles = []
    if not browser_base.exists():
        return profiles
    for entry in sorted(browser_base.iterdir()):
        if entry.is_dir() and (entry / "Bookmarks").is_file():
            profiles.append(entry / "Bookmarks")
    return profiles


def read_chromium_bookmarks(path: Path) -> dict:
    """Read and parse a Chromium Bookmarks JSON file."""
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def normalize_url(url: str) -> str:
    """Normalize a URL for deduplication."""
    parsed = urlparse(url)
    scheme = parsed.scheme.lower()
    netloc = parsed.netloc.lower()
    path = parsed.path.rstrip("/") or "/"
    return urlunparse((scheme, netloc, path, parsed.params, parsed.query, ""))


def flatten_bookmarks(node: dict, folder_path: str = "") -> list[dict]:
    """Recursively flatten a Chromium bookmark tree into a list."""
    results = []
    if node.get("type") == "url":
        results.append({
            "title": node.get("name", ""),
            "url": node.get("url", ""),
            "folder": folder_path,
            "date_added": node.get("date_added", ""),
        })
    elif node.get("type") == "folder":
        name = node.get("name", "")
        child_path = f"{folder_path}/{name}" if folder_path else name
        for child in node.get("children", []):
            results.extend(flatten_bookmarks(child, child_path))
    return results


def extract_all_bookmarks(bookmarks_data: dict) -> list[dict]:
    """Extract all bookmarks from a Chromium bookmark JSON structure."""
    results = []
    roots = bookmarks_data.get("roots", {})
    for key in ROOT_FOLDERS:
        root_node = roots.get(key)
        if root_node:
            root_name = ROOT_FOLDER_NAMES.get(key, key)
            if root_node.get("type") == "folder":
                for child in root_node.get("children", []):
                    results.extend(flatten_bookmarks(child, root_name))
            else:
                results.extend(flatten_bookmarks(root_node, root_name))
    return results


# ── Core operations ──────────────────────────────────────────────────────────

def export_bookmarks(browsers: list[str]) -> tuple[list[dict], dict]:
    """Export bookmarks from specified browsers. Returns (flat_list, stats)."""
    all_bookmarks = []
    stats = {}

    for browser in browsers:
        base = BROWSER_PATHS.get(browser)
        if not base:
            print(f"  ⚠  Unknown browser: {browser}")
            continue
        profiles = find_profiles(base)
        if not profiles:
            print(f"  ⚠  No bookmark profiles found for {browser}")
            continue

        browser_bookmarks = []
        for profile_path in profiles:
            profile_name = profile_path.parent.name
            data = read_chromium_bookmarks(profile_path)
            bookmarks = extract_all_bookmarks(data)
            for bm in bookmarks:
                bm["source"] = f"{browser}/{profile_name}"
            browser_bookmarks.extend(bookmarks)
            print(f"  ✓  {browser}/{profile_name}: {len(bookmarks)} bookmarks")

        stats[browser] = len(browser_bookmarks)
        all_bookmarks.extend(browser_bookmarks)

    return all_bookmarks, stats


def deduplicate(bookmarks: list[dict]) -> tuple[list[dict], int]:
    """Deduplicate bookmarks by normalized URL. First occurrence wins."""
    seen_urls = set()
    unique = []
    duplicates = 0

    for bm in bookmarks:
        url = bm.get("url", "")
        if not url:
            continue
        norm = normalize_url(url)
        if norm in seen_urls:
            duplicates += 1
            continue
        seen_urls.add(norm)
        unique.append(bm)

    return unique, duplicates


def build_folder_tree(bookmarks: list[dict]) -> dict:
    """Rebuild a nested folder hierarchy from flat bookmarks."""
    tree = {}
    for bm in bookmarks:
        folder = bm.get("folder", "Other Bookmarks")
        parts = [p for p in folder.split("/") if p]
        node = tree
        for part in parts:
            node = node.setdefault(part, {})
        items = node.setdefault("__items__", [])
        items.append({"title": bm["title"], "url": bm["url"]})
    return tree


def write_bookmarks_json(bookmarks: list[dict], path: Path):
    """Write bookmarks as a clean JSON file with folder hierarchy."""
    tree = build_folder_tree(bookmarks)

    def tree_to_chromium(node: dict, name: str = "") -> dict:
        result = {"name": name, "type": "folder", "children": []}
        for key, val in sorted(node.items()):
            if key == "__items__":
                for item in val:
                    result["children"].append({
                        "name": item["title"],
                        "type": "url",
                        "url": item["url"],
                    })
            else:
                result["children"].append(tree_to_chromium(val, key))
        return result

    # Build Chromium-compatible structure
    roots = {}
    root_key_map = {v: k for k, v in ROOT_FOLDER_NAMES.items()}
    for key, val in tree.items():
        chromium_key = root_key_map.get(key, None)
        if chromium_key:
            roots[chromium_key] = tree_to_chromium(val, key)
        else:
            # Put unrecognized top-level folders under "other"
            other = roots.setdefault("other", {
                "name": "Other Bookmarks", "type": "folder", "children": []
            })
            other["children"].append(tree_to_chromium(val, key))

    # Ensure all root folders exist
    for key, name in ROOT_FOLDER_NAMES.items():
        if key not in roots:
            roots[key] = {"name": name, "type": "folder", "children": []}

    output = {
        "checksum": "",
        "roots": roots,
        "version": 1,
    }

    with open(path, "w", encoding="utf-8") as f:
        json.dump(output, f, indent=2, ensure_ascii=False)


def write_bookmarks_md(bookmarks: list[dict], path: Path):
    """Write bookmarks as a human-readable Markdown file."""
    tree = build_folder_tree(bookmarks)
    lines = ["# 🔖 Bookmarks\n"]
    now = datetime.now().strftime("%Y-%m-%d %H:%M")
    lines.append(f"*Auto-generated on {now} — {len(bookmarks)} bookmarks*\n")

    def render_tree(node: dict, depth: int = 2):
        for key, val in sorted(node.items()):
            if key == "__items__":
                for item in val:
                    title = item["title"] or item["url"]
                    lines.append(f"- [{title}]({item['url']})")
                lines.append("")
            else:
                prefix = "#" * min(depth, 6)
                lines.append(f"\n{prefix} {key}\n")
                render_tree(val, depth + 1)

    render_tree(tree)

    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))


def backup_browser_bookmarks(browser: str) -> list[Path]:
    """Backup browser bookmark files before import."""
    BACKUPS_DIR.mkdir(exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    backed_up = []

    base = BROWSER_PATHS.get(browser)
    if not base:
        return backed_up

    for profile_path in find_profiles(base):
        profile_name = profile_path.parent.name
        backup_name = f"{browser}_{profile_name}_{ts}.json"
        backup_path = BACKUPS_DIR / backup_name
        shutil.copy2(profile_path, backup_path)
        backed_up.append(backup_path)
        print(f"  💾 Backed up {browser}/{profile_name} → backups/{backup_name}")

    return backed_up


def import_bookmarks(browsers: list[str], dry_run: bool = False):
    """Import repo bookmarks back into browser bookmark files."""
    if not BOOKMARKS_JSON.exists():
        print("  ✗  bookmarks.json not found — run 'export' first")
        return

    with open(BOOKMARKS_JSON, "r", encoding="utf-8") as f:
        repo_data = json.load(f)

    for browser in browsers:
        base = BROWSER_PATHS.get(browser)
        if not base:
            continue

        profiles = find_profiles(base)
        if not profiles:
            print(f"  ⚠  No profiles found for {browser}")
            continue

        if not dry_run:
            backup_browser_bookmarks(browser)

        for profile_path in profiles:
            profile_name = profile_path.parent.name
            if dry_run:
                print(f"  [dry-run] Would write to {browser}/{profile_name}")
            else:
                # Read existing to preserve metadata (checksum, etc.)
                existing = read_chromium_bookmarks(profile_path)
                existing["roots"] = copy.deepcopy(repo_data["roots"])
                with open(profile_path, "w", encoding="utf-8") as f:
                    json.dump(existing, f, indent=2, ensure_ascii=False)
                print(f"  ✓  Imported into {browser}/{profile_name}")

    if not dry_run:
        print("\n  ⚠  Restart your browser(s) to see the updated bookmarks.")


def git_commit(message: str):
    """Stage bookmark files and commit."""
    try:
        subprocess.run(
            ["git", "add", "bookmarks.json", "bookmarks.md"],
            cwd=REPO_DIR, check=True, capture_output=True,
        )
        result = subprocess.run(
            ["git", "diff", "--cached", "--quiet"],
            cwd=REPO_DIR, capture_output=True,
        )
        if result.returncode != 0:
            subprocess.run(
                ["git", "commit", "-m", message],
                cwd=REPO_DIR, check=True, capture_output=True,
            )
            print(f"  📝 Committed: {message}")
        else:
            print("  ℹ  No changes to commit.")
    except subprocess.CalledProcessError as e:
        print(f"  ⚠  Git commit failed: {e}")


def show_status():
    """Show bookmark counts and repo stats."""
    print("\n📊 Bookmark Status\n")

    for browser, base in BROWSER_PATHS.items():
        profiles = find_profiles(base)
        if not profiles:
            print(f"  {browser}: not found")
            continue
        for profile_path in profiles:
            data = read_chromium_bookmarks(profile_path)
            bookmarks = extract_all_bookmarks(data)
            profile_name = profile_path.parent.name
            print(f"  {browser}/{profile_name}: {len(bookmarks)} bookmarks")

    if BOOKMARKS_JSON.exists():
        with open(BOOKMARKS_JSON, "r", encoding="utf-8") as f:
            repo_data = json.load(f)
        # Count URLs in repo
        count = 0
        def count_urls(node):
            nonlocal count
            if node.get("type") == "url":
                count += 1
            for child in node.get("children", []):
                count_urls(child)
        for root in repo_data.get("roots", {}).values():
            if isinstance(root, dict):
                count_urls(root)
        print(f"\n  repo (bookmarks.json): {count} bookmarks")
        mod_time = datetime.fromtimestamp(BOOKMARKS_JSON.stat().st_mtime)
        print(f"  last updated: {mod_time.strftime('%Y-%m-%d %H:%M')}")
    else:
        print("\n  repo: no bookmarks.json yet — run 'export' first")


# ── CLI ──────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="Sync Chrome & Edge bookmarks via a Git repo.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python3 bookmark_sync.py sync              # Full sync (export + import + commit)
  python3 bookmark_sync.py export             # Export only
  python3 bookmark_sync.py import             # Import repo → browsers
  python3 bookmark_sync.py sync --dry-run     # Preview without changes
  python3 bookmark_sync.py sync --browser chrome  # Chrome only
        """,
    )
    parser.add_argument("command", choices=["export", "import", "sync", "status"],
                        help="Command to run")
    parser.add_argument("--browser", choices=["chrome", "edge", "both"], default="both",
                        help="Target browser (default: both)")
    parser.add_argument("--dry-run", action="store_true",
                        help="Preview changes without writing files")
    parser.add_argument("--no-commit", action="store_true",
                        help="Skip Git commit after sync")

    args = parser.parse_args()
    browsers = ["chrome", "edge"] if args.browser == "both" else [args.browser]

    if args.command == "status":
        show_status()
        return

    if args.command in ("export", "sync"):
        print("\n📤 Exporting bookmarks...\n")
        all_bookmarks, stats = export_bookmarks(browsers)

        if not all_bookmarks:
            print("\n  ✗  No bookmarks found.")
            return

        print(f"\n  Total collected: {len(all_bookmarks)}")

        print("\n🔍 Deduplicating...\n")
        unique, dups = deduplicate(all_bookmarks)
        print(f"  Unique: {len(unique)}  |  Duplicates removed: {dups}")

        if args.dry_run:
            print(f"\n  [dry-run] Would write {len(unique)} bookmarks to bookmarks.json + bookmarks.md")
        else:
            write_bookmarks_json(unique, BOOKMARKS_JSON)
            write_bookmarks_md(unique, BOOKMARKS_MD)
            print(f"\n  ✓  Wrote bookmarks.json ({len(unique)} bookmarks)")
            print(f"  ✓  Wrote bookmarks.md")

    if args.command in ("import", "sync"):
        print("\n📥 Importing to browsers...\n")
        import_bookmarks(browsers, dry_run=args.dry_run)

    if args.command == "sync" and not args.dry_run and not args.no_commit:
        print("\n📝 Committing to Git...\n")
        now = datetime.now().strftime("%Y-%m-%d %H:%M")
        git_commit(f"sync: update bookmarks ({now})")

    print("\n✅ Done!\n")


if __name__ == "__main__":
    main()
