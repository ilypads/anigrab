#!/usr/bin/env python3
"""
CLI tool for cleaning anime folder/file names and fetching cover images.

Usage:
    # Interactive mode - process all folders in directory
    python cli.py /path/to/anime

    # Single folder
    python cli.py "/path/to/[MTBB] The Apothecary Diaries S1 (BD 1080p)"

    # Dry run - preview changes without applying
    python cli.py --dry-run /path/to/anime

    # Auto mode - no prompts, use auto-detected names
    python cli.py --auto /path/to/anime

    # Auto mode with dry run
    python cli.py --auto --dry-run /path/to/anime

    # Skip image fetching
    python cli.py --no-images /path/to/anime

    # Plex restructure - reorganize for Plex naming convention
    # Detects movies vs series automatically
    # Series: Show Name (Year)/Season XX/Show Name (Year) - sXXeXX.ext
    # Movies: Movie Name (Year)/Movie Name (Year).ext (moved to Movies folder)
    python cli.py --plex-rename /path/to/anime

    # Plex restructure with auto mode
    python cli.py --plex-rename --auto /path/to/anime

    # Plex restructure with custom movies directory
    python cli.py --plex-rename --movies-dir /path/to/movies /path/to/anime
"""

import argparse
import asyncio
import sys
from pathlib import Path

from cleaner import (
    detect_anime_name, needs_cleaning, rename_folder, rename_files_in_folder,
    sanitize_filename, detect_season_number, restructure_for_plex, needs_plex_restructure,
    detect_media_type, restructure_for_plex_movie, extract_base_name_and_season
)
from config import config
from images import search_anilist, download_image


class Colors:
    """ANSI color codes for terminal output."""
    RESET = "\033[0m"
    BOLD = "\033[1m"
    DIM = "\033[2m"
    RED = "\033[31m"
    GREEN = "\033[32m"
    YELLOW = "\033[33m"
    BLUE = "\033[34m"
    MAGENTA = "\033[35m"
    CYAN = "\033[36m"


def print_header(text: str):
    print(f"\n{Colors.BOLD}{Colors.CYAN}{'=' * 60}{Colors.RESET}")
    print(f"{Colors.BOLD}{Colors.CYAN}{text}{Colors.RESET}")
    print(f"{Colors.BOLD}{Colors.CYAN}{'=' * 60}{Colors.RESET}")


def print_change(label: str, old: str, new: str):
    print(f"  {Colors.DIM}{label}:{Colors.RESET}")
    print(f"    {Colors.RED}- {old}{Colors.RESET}")
    print(f"    {Colors.GREEN}+ {new}{Colors.RESET}")


def print_info(text: str):
    print(f"  {Colors.BLUE}ℹ{Colors.RESET} {text}")


def print_success(text: str):
    print(f"  {Colors.GREEN}✓{Colors.RESET} {text}")


def print_warning(text: str):
    print(f"  {Colors.YELLOW}⚠{Colors.RESET} {text}")


def print_error(text: str):
    print(f"  {Colors.RED}✗{Colors.RESET} {text}")


def get_folders_missing_images(path: Path, recursive: bool = False) -> list[Path]:
    """Get list of folders missing cover.jpg."""
    if path.is_file():
        print_error(f"Path is a file, not a directory: {path}")
        return []

    if not path.exists():
        print_error(f"Path does not exist: {path}")
        return []

    folders = []

    def scan_dir(dir_path: Path, depth: int = 0):
        """Recursively scan for folders missing cover.jpg."""
        for item in sorted(dir_path.iterdir()):
            if item.is_dir():
                # Check if folder has media files but no cover
                has_media = any(
                    f.suffix.lower() in {'.mkv', '.mp4', '.avi', '.m4v', '.webm'}
                    for f in item.iterdir() if f.is_file()
                )
                has_cover = (item / "cover.jpg").exists()

                if has_media and not has_cover:
                    folders.append(item)

                # Recurse if enabled
                if recursive and depth < 3:
                    scan_dir(item, depth + 1)

    # Check path itself
    has_media = any(
        f.suffix.lower() in {'.mkv', '.mp4', '.avi', '.m4v', '.webm'}
        for f in path.iterdir() if f.is_file()
    )
    has_cover = (path / "cover.jpg").exists()
    if has_media and not has_cover:
        folders.append(path)

    # Scan subfolders
    scan_dir(path, depth=0)

    return folders


def get_folders_for_plex(path: Path, recursive: bool = False, force: bool = False) -> list[Path]:
    """Get list of folders that need Plex restructuring."""
    if path.is_file():
        print_error(f"Path is a file, not a directory: {path}")
        return []

    if not path.exists():
        print_error(f"Path does not exist: {path}")
        return []

    folders = []
    media_extensions = {'.mkv', '.mp4', '.avi', '.m4v', '.webm'}

    def scan_dir(dir_path: Path, depth: int = 0):
        """Recursively scan for folders needing Plex restructure."""
        for item in sorted(dir_path.iterdir()):
            if item.is_dir():
                # Check if has media files and needs restructuring (or force)
                has_media = any(
                    f.suffix.lower() in media_extensions
                    for f in item.iterdir() if f.is_file()
                )
                if has_media and (force or needs_plex_restructure(str(item))):
                    folders.append(item)

                # Recurse if enabled
                if recursive and depth < 3:
                    scan_dir(item, depth + 1)

    # Check path itself
    has_media = any(
        f.suffix.lower() in media_extensions
        for f in path.iterdir() if f.is_file()
    )
    if has_media and (force or needs_plex_restructure(str(path))):
        folders.append(path)

    # Scan subfolders
    scan_dir(path, depth=0)

    # Sort by depth (deepest first)
    folders.sort(key=lambda p: len(p.parts), reverse=True)

    return folders


def get_folders_to_process(path: Path, recursive: bool = False) -> list[Path]:
    """Get list of folders that need processing.

    Returns folders sorted by depth (deepest first) so that child folders
    are processed before their parents. This prevents path invalidation
    when a parent folder is renamed.
    """
    if path.is_file():
        print_error(f"Path is a file, not a directory: {path}")
        return []

    if not path.exists():
        print_error(f"Path does not exist: {path}")
        return []

    folders = []

    def scan_dir(dir_path: Path, depth: int = 0):
        """Recursively scan for folders with tags."""
        for item in sorted(dir_path.iterdir()):
            if item.is_dir():
                # Recurse into subdirectories FIRST if recursive mode
                # This ensures we add children before parents
                if recursive and depth < 3:  # Limit depth to prevent infinite recursion
                    scan_dir(item, depth + 1)
                # Then add the folder itself if it needs cleaning
                if needs_cleaning(item.name):
                    folders.append(item)

    # If path itself has tags, process it directly
    if needs_cleaning(path.name):
        # First scan inside if recursive (children first)
        if recursive:
            scan_dir(path, depth=1)
        # Then add the parent
        folders.append(path)
    else:
        # Scan for subfolders with tags
        scan_dir(path, depth=0)

    # Sort by path depth (deepest first) to ensure children are processed before parents
    folders.sort(key=lambda p: len(p.parts), reverse=True)

    return folders


async def process_images_only(
    folder: Path,
    dry_run: bool = False,
    auto: bool = False
) -> bool:
    """
    Only fetch cover image for a folder (no renaming).
    Returns True if image was downloaded (or would be in dry run).
    """
    print_header(f"Processing: {folder.name}")

    cover_path = folder / "cover.jpg"
    poster_path = folder / "poster.jpg"

    # Use folder name for search
    search_name = folder.name

    # Search AniList
    results = await search_anilist(search_name)

    if not results:
        print_warning("No results found on AniList")
        if not auto:
            manual_query = input(f"  {Colors.BOLD}Enter anime name to search (or 's' to skip):{Colors.RESET} ").strip()
            if manual_query and manual_query.lower() != 's':
                results = await search_anilist(manual_query)
                if results:
                    print_success(f"Found {len(results)} result(s)")

    if not results:
        print_info("Skipped - no results")
        return False

    if auto:
        best = results[0]
        if dry_run:
            print_info(f"Would download cover for: {best.display_title} ({best.year})")
        else:
            if best.best_cover:
                success = await download_image(best.best_cover, str(cover_path), str(poster_path))
                if success:
                    print_success(f"Downloaded cover for: {best.display_title}")
                    print_info("Also saved as poster.jpg for Plex")
                else:
                    print_error("Failed to download cover")
            else:
                print_warning("No cover image available")
        return True
    else:
        # Interactive: show options
        print(f"\n  {Colors.BOLD}AniList results:{Colors.RESET}")
        for i, result in enumerate(results[:5], 1):
            year = f"({result.year})" if result.year else ""
            eps = f"{result.episodes} eps" if result.episodes else ""
            print(f"    {i}. {result.display_title} {year} {eps}")

        choice = input(f"\n  {Colors.BOLD}Select (1-5, or 's' to skip):{Colors.RESET} ").strip()

        if choice.lower() != 's' and choice.isdigit():
            idx = int(choice) - 1
            if 0 <= idx < len(results):
                selected = results[idx]
                if dry_run:
                    print_info(f"Would download cover for: {selected.display_title}")
                elif selected.best_cover:
                    success = await download_image(selected.best_cover, str(cover_path), str(poster_path))
                    if success:
                        print_success(f"Downloaded cover for: {selected.display_title}")
                        print_info("Also saved as poster.jpg for Plex")
                        return True
                    else:
                        print_error("Failed to download cover")
        else:
            print_info("Skipped")

    return False


async def process_folder(
    folder: Path,
    dry_run: bool = False,
    auto: bool = False,
    fetch_images: bool = True
) -> bool:
    """
    Process a single folder.
    Returns True if changes were made (or would be made in dry run).
    """
    print_header(f"Processing: {folder.name}")

    # Detect clean name
    detected_name = detect_anime_name(folder.name)
    print_change("Folder name", folder.name, detected_name)

    # In interactive mode, let user edit
    if not auto:
        user_input = input(f"\n  {Colors.BOLD}Enter new name (or press Enter to accept, 's' to skip):{Colors.RESET} ").strip()
        if user_input.lower() == 's':
            print_info("Skipped")
            return False
        if user_input:
            detected_name = user_input

    # Sanitize the name
    safe_name = sanitize_filename(detected_name)
    if safe_name != detected_name:
        print_warning(f"Name sanitized: {detected_name} → {safe_name}")
        detected_name = safe_name

    # Check if folder already has correct name
    if folder.name == detected_name:
        print_info("Folder name already clean")
    elif dry_run:
        print_info(f"Would rename folder to: {detected_name}")
    else:
        try:
            new_path = rename_folder(str(folder), detected_name)
            folder = Path(new_path)
            print_success(f"Renamed folder to: {detected_name}")
        except Exception as e:
            print_error(f"Failed to rename folder: {e}")
            return False

    # Process files inside
    print(f"\n  {Colors.BOLD}Files:{Colors.RESET}")
    if dry_run:
        # Preview file renames
        media_extensions = {'.mkv', '.mp4', '.avi', '.m4v', '.webm'}
        for file in sorted(folder.iterdir()):
            if file.is_file() and file.suffix.lower() in media_extensions:
                from cleaner import clean_episode_name
                new_name = clean_episode_name(file.name, detected_name)
                if new_name != file.name:
                    print_change("File", file.name, new_name)
    else:
        try:
            renamed = rename_files_in_folder(str(folder), detected_name)
            if renamed:
                for old_name, new_name in renamed:
                    print_success(f"Renamed: {old_name} → {new_name}")
            else:
                print_info("No files needed renaming")
        except Exception as e:
            print_error(f"Failed to rename files: {e}")

    # Fetch cover image
    if fetch_images:
        print(f"\n  {Colors.BOLD}Cover image:{Colors.RESET}")
        cover_path = folder / "cover.jpg"
        poster_path = folder / "poster.jpg"  # For Plex compatibility

        if cover_path.exists():
            print_info("Cover image already exists")
        else:
            # Search AniList
            results = await search_anilist(detected_name)

            if not results:
                print_warning("No results found on AniList")
                if not auto:
                    # Prompt for manual search
                    manual_query = input(f"  {Colors.BOLD}Enter anime name to search (or 's' to skip):{Colors.RESET} ").strip()
                    if manual_query and manual_query.lower() != 's':
                        results = await search_anilist(manual_query)
                        if results:
                            print_success(f"Found {len(results)} result(s)")

            if not results:
                pass  # Skip image download
            elif auto:
                # Auto mode: use first result
                best = results[0]
                if dry_run:
                    print_info(f"Would download cover for: {best.display_title} ({best.year})")
                else:
                    if best.best_cover:
                        success = await download_image(best.best_cover, str(cover_path), str(poster_path))
                        if success:
                            print_success(f"Downloaded cover for: {best.display_title}")
                            print_info("Also saved as poster.jpg for Plex")
                        else:
                            print_error("Failed to download cover")
                    else:
                        print_warning("No cover image available")
            else:
                # Interactive: show options
                print(f"\n  {Colors.BOLD}AniList results:{Colors.RESET}")
                for i, result in enumerate(results[:5], 1):
                    year = f"({result.year})" if result.year else ""
                    eps = f"{result.episodes} eps" if result.episodes else ""
                    print(f"    {i}. {result.display_title} {year} {eps}")

                choice = input(f"\n  {Colors.BOLD}Select (1-5, or 's' to skip):{Colors.RESET} ").strip()

                if choice.lower() != 's' and choice.isdigit():
                    idx = int(choice) - 1
                    if 0 <= idx < len(results):
                        selected = results[idx]
                        if dry_run:
                            print_info(f"Would download cover for: {selected.display_title}")
                        elif selected.best_cover:
                            success = await download_image(selected.best_cover, str(cover_path), str(poster_path))
                            if success:
                                print_success(f"Downloaded cover for: {selected.display_title}")
                                print_info("Also saved as poster.jpg for Plex")
                            else:
                                print_error("Failed to download cover")
                else:
                    print_info("Skipped cover image")

    return True


async def process_plex_restructure(
    folder: Path,
    dry_run: bool = False,
    auto: bool = False,
    movies_dir: str | None = None
) -> bool:
    """
    Restructure a folder to Plex-compatible format.
    Detects if content is a movie or series and restructures accordingly.
    """
    print_header(f"Processing: {folder.name}")

    # Count media files for detection
    media_extensions = {'.mkv', '.mp4', '.avi', '.m4v', '.webm'}
    media_files = [f for f in folder.iterdir() if f.is_file() and f.suffix.lower() in media_extensions]
    file_count = len(media_files)

    # Check if folder is already a "Season XX" folder - use parent for show name
    import re
    is_season_folder = bool(re.match(r'^[Ss]eason\s*\d+', folder.name))

    if is_season_folder:
        # Traverse up to find the actual show name (not another Season folder)
        show_folder = folder.parent
        while show_folder.parent != show_folder:  # Not at root
            if re.match(r'^[Ss]eason\s*\d+', show_folder.name):
                show_folder = show_folder.parent
            else:
                break

        # Check if show_folder has a Roman numeral suffix (e.g., "Overlord IV")
        base_name, roman_season = extract_base_name_and_season(show_folder.name)

        if roman_season is not None:
            # Use the Roman numeral as the season (e.g., "Overlord IV" -> Season 4)
            detected_name = detect_anime_name(base_name)
            season = roman_season
            print_info(f"Detected Roman numeral season: {show_folder.name} -> {detected_name} Season {season}")
        else:
            # No Roman numeral, use folder name and Season folder number
            detected_name = detect_anime_name(show_folder.name)
            season_match = re.search(r'[Ss]eason\s*(\d+)', folder.name)
            season = int(season_match.group(1)) if season_match else 1
            print_info(f"Detected season folder - using parent: {detected_name} (Season {season})")

        media_type = 'series'

        # Files are already in a Season folder, just rename them in place
        print_info("Files already in Season folder - will rename in place")
    else:
        # Detect media type
        media_type = detect_media_type(folder.name, file_count)

        # Detect clean name and season
        detected_name = detect_anime_name(folder.name)
        season = detect_season_number(folder.name) if media_type == 'series' else None

        if media_type == 'movie':
            print_info(f"Detected: {detected_name} (Movie)")
        else:
            print_info(f"Detected: {detected_name} (Season {season})")

    # Search AniList for year
    results = await search_anilist(detected_name)
    year = None
    selected_result = None

    if results:
        if auto:
            selected_result = results[0]
            year = selected_result.year
            print_info(f"AniList match: {selected_result.display_title} ({year})")
        else:
            print(f"\n  {Colors.BOLD}AniList results:{Colors.RESET}")
            for i, result in enumerate(results[:5], 1):
                y = f"({result.year})" if result.year else ""
                eps = f"{result.episodes} eps" if result.episodes else ""
                print(f"    {i}. {result.display_title} {y} {eps}")

            choice = input(f"\n  {Colors.BOLD}Select for year/metadata (1-5, or 's' to skip):{Colors.RESET} ").strip()
            if choice.lower() != 's' and choice.isdigit():
                idx = int(choice) - 1
                if 0 <= idx < len(results):
                    selected_result = results[idx]
                    year = selected_result.year
    else:
        print_warning("No AniList results found (folder name will not include year)")
        if not auto:
            manual_query = input(f"  {Colors.BOLD}Enter anime name to search (or 's' to skip):{Colors.RESET} ").strip()
            if manual_query and manual_query.lower() != 's':
                results = await search_anilist(manual_query)
                if results:
                    print(f"\n  {Colors.BOLD}AniList results:{Colors.RESET}")
                    for i, result in enumerate(results[:5], 1):
                        y = f"({result.year})" if result.year else ""
                        print(f"    {i}. {result.display_title} {y}")
                    choice = input(f"\n  {Colors.BOLD}Select (1-5):{Colors.RESET} ").strip()
                    if choice.isdigit():
                        idx = int(choice) - 1
                        if 0 <= idx < len(results):
                            selected_result = results[idx]
                            year = selected_result.year

    # In interactive mode, allow editing the name
    if not auto:
        if year:
            suggested = f"{detected_name} ({year})"
        else:
            suggested = detected_name
        print_info(f"Suggested folder name: {suggested}")
        user_input = input(f"\n  {Colors.BOLD}Enter show name (or Enter to accept, 's' to skip):{Colors.RESET} ").strip()
        if user_input.lower() == 's':
            print_info("Skipped")
            return False
        if user_input:
            detected_name = user_input

    # Sanitize
    safe_name = sanitize_filename(detected_name)

    # Preview changes
    if year:
        new_folder_name = f"{safe_name} ({year})"
    else:
        new_folder_name = safe_name

    print(f"\n  {Colors.BOLD}Restructure plan:{Colors.RESET}")

    if media_type == 'movie':
        print(f"    Movie folder: {new_folder_name}/")
        print(f"    File format: {new_folder_name}.ext")
        if not movies_dir:
            # Use config.movies_dir if set, otherwise default to sibling "Movies" folder
            if config.movies_dir:
                movies_dir = config.movies_dir
            else:
                movies_dir = str(folder.parent.parent / "Movies")
        print(f"    Destination: {movies_dir}/")
    else:
        season_folder = f"Season {season:02d}"
        print(f"    Show folder: {new_folder_name}/")
        print(f"    Season folder: {season_folder}/")
        print(f"    Episode format: {safe_name}{f' ({year})' if year else ''} - s{season:02d}eXX.ext")

    if dry_run:
        print_info("Dry run - no changes made")
        return True

    # Execute restructure
    try:
        if media_type == 'movie':
            result = restructure_for_plex_movie(str(folder), safe_name, year, movies_dir)

            if result["movie_folder_created"]:
                print_success(f"Created movie folder: {new_folder_name}")
            if result["file_renamed"]:
                print_success(f"Renamed: {result['file_renamed']['old']} → {result['file_renamed']['new']}")

            if result["errors"]:
                for err in result["errors"]:
                    print_error(err)

            # Download cover image
            if selected_result and selected_result.best_cover and result["new_movie_path"]:
                movie_path = Path(result["new_movie_path"])
                cover_path = movie_path / "cover.jpg"
                poster_path = movie_path / "poster.jpg"

                if not cover_path.exists():
                    success = await download_image(
                        selected_result.best_cover,
                        str(cover_path),
                        str(poster_path)
                    )
                    if success:
                        print_success(f"Downloaded cover for: {selected_result.display_title}")
                        print_info("Also saved as poster.jpg for Plex")
        else:
            result = restructure_for_plex(str(folder), safe_name, year, season)

            if result["show_folder_renamed"]:
                print_success(f"Created show folder: {new_folder_name}")
            if result["season_folder_created"]:
                print_success(f"Created season folder: {season_folder}")

            for renamed in result["files_renamed"]:
                print_success(f"Renamed: {renamed['old']} → {renamed['new']}")

            if result["errors"]:
                for err in result["errors"]:
                    print_error(err)

            # Download cover image if we have AniList result
            if selected_result and selected_result.best_cover and result["new_show_path"]:
                show_path = Path(result["new_show_path"])
                cover_path = show_path / "cover.jpg"
                poster_path = show_path / "poster.jpg"

                if not cover_path.exists():
                    success = await download_image(
                        selected_result.best_cover,
                        str(cover_path),
                        str(poster_path)
                    )
                    if success:
                        print_success(f"Downloaded cover for: {selected_result.display_title}")
                        print_info("Also saved as poster.jpg for Plex")

        return True

    except Exception as e:
        print_error(f"Restructure failed: {e}")
        return False


async def main():
    parser = argparse.ArgumentParser(
        description="Clean anime folder/file names and fetch cover images",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  %(prog)s /mnt/f/Media/Anime/              Interactive mode (recursive by default)
  %(prog)s --dry-run /mnt/f/Media/Anime/    Preview changes
  %(prog)s --auto /mnt/f/Media/Anime/       Auto mode (no prompts)
  %(prog)s --images-only /path/to/anime          Only add images to folders missing them
  %(prog)s --images-only --auto /path/to/anime   Auto-add images (no prompts)
  %(prog)s --plex-rename /path/to/anime          Restructure for Plex naming
  %(prog)s --plex-rename --auto /path/to/anime   Auto Plex restructure (no prompts)
  %(prog)s --no-recursive /path/to/anime         Only process top-level folders
  %(prog)s "/path/to/[MTBB] Show Name (BD)"      Single folder
        """
    )

    parser.add_argument("path", type=Path, help="Path to anime folder or directory containing anime folders")
    parser.add_argument("--dry-run", "-n", action="store_true", help="Preview changes without applying them")
    parser.add_argument("--auto", "-a", action="store_true", help="Auto mode - no prompts, use detected names")
    parser.add_argument("--no-recursive", action="store_true", help="Don't process subfolders (recursive is on by default)")
    parser.add_argument("--no-images", action="store_true", help="Skip fetching cover images")
    parser.add_argument("--images-only", action="store_true", help="Only fetch images for folders missing cover.jpg (skip renaming)")
    parser.add_argument("--plex-rename", action="store_true", help="Restructure folders for Plex: Show (Year)/Season XX/Show (Year) - sXXeXX.ext")
    parser.add_argument("--movies-dir", type=Path, help="Directory for movies (default: sibling 'Movies' folder)")
    parser.add_argument("--force", "-f", action="store_true", help="Force processing even if folder appears already processed")

    args = parser.parse_args()

    path = args.path.resolve()

    recursive = not args.no_recursive

    print_header("AniGrab Cleaner")
    print(f"  Path: {path}")
    if args.plex_rename:
        mode_str = "Plex restructure"
    elif args.images_only:
        mode_str = "Images only"
    elif args.dry_run:
        mode_str = "Dry run"
    else:
        mode_str = "Live"
    print(f"  Mode: {mode_str}")
    print(f"  Prompts: {'Auto' if args.auto else 'Interactive'}")
    print(f"  Recursive: {'No' if args.no_recursive else 'Yes'}")
    if not args.images_only and not args.plex_rename:
        print(f"  Images: {'Skip' if args.no_images else 'Fetch'}")

    # Get folders to process
    if args.plex_rename:
        folders = get_folders_for_plex(path, recursive=recursive, force=args.force)
        if not folders:
            print_warning("No folders needing Plex restructure found")
            print_info("Folders already have year in name, Season folders, or sXXeXX naming")
            if not args.force:
                print_info("Tip: Use --force to process anyway")
            return
    elif args.images_only:
        folders = get_folders_missing_images(path, recursive=recursive)
        if not folders:
            print_warning("No folders missing cover images found")
            return
    else:
        folders = get_folders_to_process(path, recursive=recursive)
        if not folders:
            print_warning("No folders with tags found to process")
            print_info("Tip: Use --images-only to add images to already-clean folders")
            return

    print(f"\n  Found {len(folders)} folder(s) to process")

    if not args.auto and not args.dry_run:
        confirm = input(f"\n  {Colors.BOLD}Continue? [Y/n]:{Colors.RESET} ").strip().lower()
        if confirm == 'n':
            print_info("Aborted")
            return

    # Process each folder
    processed = 0
    for folder in folders:
        try:
            if args.plex_rename:
                changed = await process_plex_restructure(
                    folder,
                    dry_run=args.dry_run,
                    auto=args.auto,
                    movies_dir=str(args.movies_dir) if args.movies_dir else None
                )
            elif args.images_only:
                changed = await process_images_only(
                    folder,
                    dry_run=args.dry_run,
                    auto=args.auto
                )
            else:
                changed = await process_folder(
                    folder,
                    dry_run=args.dry_run,
                    auto=args.auto,
                    fetch_images=not args.no_images
                )
            if changed:
                processed += 1
        except KeyboardInterrupt:
            print_warning("\nInterrupted by user")
            break
        except Exception as e:
            print_error(f"Error processing {folder.name}: {e}")

    # Summary
    print_header("Summary")
    action = "would be processed" if args.dry_run else "processed"
    print(f"  {processed}/{len(folders)} folders {action}")


if __name__ == "__main__":
    asyncio.run(main())
