#!/usr/bin/env python3
"""
Fix nested Season folders created by a bug in the CLI.

This script finds deeply nested Season folders and moves files up to the
correct location.

Usage:
    python fix_nested_seasons.py /path/to/Overlord --dry-run
    python fix_nested_seasons.py /path/to/Overlord
"""

import argparse
import os
import re
import shutil
from pathlib import Path


def find_media_files(folder: Path, extensions: set) -> list[Path]:
    """Recursively find all media files in a folder."""
    files = []
    for item in folder.rglob("*"):
        if item.is_file() and item.suffix.lower() in extensions:
            files.append(item)
    return files


def find_deepest_season_in_chain(start: Path) -> tuple[Path, int]:
    """
    Given a path that might have nested Season folders, find the outermost
    Season folder and count how deep the nesting goes.
    """
    # Walk up to find the top Season folder
    current = start
    top_season = None

    while current.parent != current:
        if re.match(r'^[Ss]eason\s*\d+$', current.name):
            top_season = current
        current = current.parent

    if not top_season:
        return start, 0

    # Now count nested Season folders inside
    depth = 0
    check = top_season
    while True:
        season_child = None
        for item in check.iterdir():
            if item.is_dir() and re.match(r'^[Ss]eason\s*\d+$', item.name):
                season_child = item
                depth += 1
                break
        if season_child:
            check = season_child
        else:
            break

    return top_season, depth


def fix_nested_seasons(base_path: Path, dry_run: bool = False):
    """Fix nested Season folders by moving files up."""
    media_extensions = {'.mkv', '.mp4', '.avi', '.m4v', '.webm', '.ass', '.srt', '.sub'}

    # Find all Season folders at any depth
    season_folders = []
    for item in base_path.rglob("*"):
        if item.is_dir() and re.match(r'^[Ss]eason\s*\d+$', item.name):
            season_folders.append(item)

    if not season_folders:
        print("No Season folders found")
        return

    # Sort by depth (deepest first) so we don't get confused by moving
    season_folders.sort(key=lambda p: len(p.parts), reverse=True)

    # Group folders by their parent structure
    processed_tops = set()

    for season in season_folders:
        # Find the top-level Season folder in this chain
        top_season, depth = find_deepest_season_in_chain(season)

        if str(top_season) in processed_tops:
            continue
        processed_tops.add(str(top_season))

        if depth == 0:
            print(f"OK: {top_season} (no nesting)")
            continue

        print(f"\nFound nested structure: {top_season}")
        print(f"  Nesting depth: {depth} levels deep")

        # Find all media files inside
        files = find_media_files(top_season, media_extensions)

        if not files:
            print(f"  No media files found")
            continue

        print(f"  Found {len(files)} media file(s)")

        # Move files to top Season folder
        for file in files:
            target = top_season / file.name
            if file.parent == top_season:
                print(f"  Already in correct location: {file.name}")
                continue

            if target.exists():
                print(f"  WARNING: Target exists, skipping: {file.name}")
                continue

            if dry_run:
                print(f"  Would move: {file.name}")
                print(f"    From: {file.parent}")
                print(f"    To:   {top_season}")
            else:
                print(f"  Moving: {file.name}")
                shutil.move(str(file), str(target))

        # Remove empty nested Season folders
        for subfolder in sorted(top_season.rglob("*"), key=lambda p: len(p.parts), reverse=True):
            if subfolder.is_dir() and re.match(r'^[Ss]eason\s*\d+$', subfolder.name):
                try:
                    remaining = list(subfolder.iterdir())
                    if not remaining:
                        if dry_run:
                            print(f"  Would remove empty: {subfolder}")
                        else:
                            subfolder.rmdir()
                            print(f"  Removed empty: {subfolder}")
                except Exception as e:
                    print(f"  Error removing {subfolder}: {e}")


def main():
    parser = argparse.ArgumentParser(description="Fix nested Season folders")
    parser.add_argument("path", type=Path, help="Path to check/fix")
    parser.add_argument("--dry-run", "-n", action="store_true", help="Preview without making changes")

    args = parser.parse_args()
    path = args.path.resolve()

    if not path.exists():
        print(f"Path does not exist: {path}")
        return

    print(f"Scanning: {path}")
    print(f"Mode: {'Dry run' if args.dry_run else 'Live'}")
    print("=" * 60)

    fix_nested_seasons(path, args.dry_run)

    print("\n" + "=" * 60)
    if args.dry_run:
        print("Dry run complete. Run without --dry-run to apply changes.")
    else:
        print("Done!")


if __name__ == "__main__":
    main()
