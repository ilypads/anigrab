"""
Anime name cleaning module.
Uses anitopy for parsing anime filenames and extracting metadata.
"""

import os
import re
import shutil
from pathlib import Path

import anitopy


def detect_anime_name(raw_name: str) -> str:
    """
    Clean a torrent/folder name to extract the anime title using anitopy.
    """
    parsed = anitopy.parse(raw_name)
    title = parsed.get('anime_title', '')

    if not title:
        # Fallback: strip extension and basic cleanup
        title = re.sub(r'\.(mkv|mp4|avi|m4v|webm)$', '', raw_name, flags=re.IGNORECASE)
        title = re.sub(r'\[[^\]]+\]', '', title)  # Remove brackets
        title = re.sub(r'\([^)]+\)', '', title)   # Remove parentheses
        title = re.sub(r'\s+', ' ', title).strip(' -_.')

    return title.strip()


def clean_episode_name(filename: str, show_name: str = None) -> str:
    """
    Clean an episode filename while preserving episode numbering.
    If show_name is provided, uses it as the base name.
    """
    parsed = anitopy.parse(filename)
    ext = parsed.get('file_extension', '')
    if ext:
        ext = f'.{ext}'

    episode_num = parsed.get('episode_number')

    if show_name and episode_num:
        # Handle multi-episode (e.g., "01-03")
        if isinstance(episode_num, list):
            episode_num = episode_num[0]
        return f"{show_name} - {str(episode_num).zfill(2)}{ext}"

    # Fall back to general cleaning
    cleaned = parsed.get('anime_title', detect_anime_name(filename))
    if episode_num:
        if isinstance(episode_num, list):
            episode_num = episode_num[0]
        return f"{cleaned} - {str(episode_num).zfill(2)}{ext}"

    return f"{cleaned}{ext}"


def rename_folder(old_path: str, new_name: str) -> str:
    """
    Rename a folder to the new name.
    Returns the new path.
    """
    old_path = Path(old_path)
    if not old_path.exists():
        raise FileNotFoundError(f"Folder not found: {old_path}")

    if not old_path.is_dir():
        raise ValueError(f"Not a directory: {old_path}")

    # Sanitize name for filesystem
    safe_name = sanitize_filename(new_name)
    new_path = old_path.parent / safe_name

    if new_path.exists() and new_path != old_path:
        raise FileExistsError(f"Target folder already exists: {new_path}")

    if new_path != old_path:
        old_path.rename(new_path)

    return str(new_path)


def rename_files_in_folder(folder_path: str, new_show_name: str) -> list[tuple[str, str]]:
    """
    Rename media files inside a folder using the clean show name.
    Returns list of (old_name, new_name) tuples.
    """
    folder = Path(folder_path)
    if not folder.exists():
        raise FileNotFoundError(f"Folder not found: {folder}")

    media_extensions = {'.mkv', '.mp4', '.avi', '.m4v', '.webm', '.ass', '.srt', '.sub'}
    renamed = []

    for file in sorted(folder.iterdir()):
        if file.is_file() and file.suffix.lower() in media_extensions:
            new_name = clean_episode_name(file.name, new_show_name)
            if new_name != file.name:
                new_path = folder / new_name
                if not new_path.exists():
                    file.rename(new_path)
                    renamed.append((file.name, new_name))

    return renamed


def sanitize_filename(name: str) -> str:
    """Remove/replace characters that are invalid in filenames."""
    # Replace invalid characters
    invalid_chars = '<>:"/\\|?*'
    for char in invalid_chars:
        name = name.replace(char, '')

    # Replace multiple spaces with single space
    name = re.sub(r'\s+', ' ', name)

    # Remove leading/trailing spaces and dots
    name = name.strip(' .')

    return name


def needs_cleaning(name: str) -> bool:
    """Check if a folder/file name has tags that need cleaning using anitopy."""
    parsed = anitopy.parse(name)

    # If anitopy found any of these, the name has tags that should be cleaned
    has_tags = any([
        parsed.get('release_group'),
        parsed.get('video_resolution'),
        parsed.get('video_term'),
        parsed.get('audio_term'),
        parsed.get('source'),
        parsed.get('file_checksum'),
    ])

    if has_tags:
        return True

    # Additional check: looks like scene naming (many dots, no spaces)
    if name.count('.') > 3 and ' ' not in name:
        return True

    return False


ROMAN_NUMERALS = {
    'XIII': 13, 'XII': 12, 'XI': 11, 'X': 10,
    'IX': 9, 'VIII': 8, 'VII': 7, 'VI': 6,
    'V': 5, 'IV': 4, 'III': 3, 'II': 2, 'I': 1,
}


def extract_base_name_and_season(folder_name: str) -> tuple[str, int | None]:
    """
    Extract base show name and season from names with Roman numeral suffixes.

    Examples:
        "Overlord IV" -> ("Overlord", 4)
        "Konosuba II" -> ("Konosuba", 2)
        "Attack on Titan" -> ("Attack on Titan", None)

    Returns (base_name, season) tuple. Season is None if no Roman numeral found.
    """
    # Pattern to match Roman numeral at end of name (with optional brackets/parens after)
    roman_pattern = r'^(.+?)\s+(XIII|XII|XI|IX|VIII|VII|VI|IV|III|II|X|V|I)(?:\s*[\[(].*)?$'
    match = re.match(roman_pattern, folder_name)

    if match:
        base_name = match.group(1).strip()
        numeral = match.group(2)
        season = ROMAN_NUMERALS.get(numeral)
        return base_name, season

    return folder_name, None


def detect_season_number(folder_name: str) -> int:
    """Extract season number from folder name using anitopy. Returns 1 if not found."""
    parsed = anitopy.parse(folder_name)
    season = parsed.get('anime_season')

    if season:
        # Handle array (e.g., S1+S2 returns ['1', '2'])
        if isinstance(season, list):
            season = season[0]
        try:
            return int(season)
        except (ValueError, TypeError):
            pass

    # Fallback: Check for Roman numerals (anitopy doesn't handle these)
    roman_pattern = r'\b(XIII|XII|XI|IX|VIII|VII|VI|IV|III|II|X|V|I)(?:\b|[^a-zA-Z]|$)'
    roman_match = re.search(roman_pattern, folder_name)
    if roman_match:
        numeral = roman_match.group(1)
        if numeral in ROMAN_NUMERALS:
            return ROMAN_NUMERALS[numeral]

    # Fallback: Check for ordinal words
    ordinal_words = {
        'second': 2, 'third': 3, 'fourth': 4, 'fifth': 5,
        'sixth': 6, 'seventh': 7, 'eighth': 8, 'ninth': 9, 'tenth': 10,
        '2nd': 2, '3rd': 3, '4th': 4, '5th': 5,
        '6th': 6, '7th': 7, '8th': 8, '9th': 9, '10th': 10,
    }
    name_lower = folder_name.lower()
    for word, num in ordinal_words.items():
        if word in name_lower:
            return num

    return 1  # Default to season 1


def detect_episode_info(filename: str) -> tuple[int | None, int | None]:
    """
    Extract season and episode number from filename using anitopy.
    Returns (season, episode) tuple. Values may be None if not found.
    """
    try:
        parsed = anitopy.parse(filename)
        season = parsed.get('anime_season')
        episode = parsed.get('episode_number')
    except (AttributeError, Exception):
        # anitopy can fail on simple filenames, use fallbacks
        parsed = {}
        season = None
        episode = None

    # Handle arrays (multi-season or multi-episode)
    if isinstance(season, list):
        season = season[0]
    if isinstance(episode, list):
        episode = episode[0]

    # Fallback: Try regex for S##E## format (pre-renamed Plex files)
    if not episode:
        match = re.search(r'[Ss](\d{1,2})[Ee](\d{1,3})', filename)
        if match:
            season = match.group(1)
            episode = match.group(2)

    # Fallback: Simple format like "01 - Title.mkv" or "01_Title.mkv" at start of filename
    if not episode:
        # Remove extension first
        name_no_ext = re.sub(r'\.(mkv|mp4|avi|m4v|webm|ass|srt|sub|ssa)$', '', filename, flags=re.IGNORECASE)
        # Match episode number at the start: "01 - Title", "01_Title", "01. Title"
        match = re.match(r'^(\d{1,3})\s*[-_\.]\s*', name_no_ext)
        if match:
            episode = match.group(1)

    # Convert to int
    try:
        season = int(season) if season else None
    except (ValueError, TypeError):
        season = None

    try:
        episode = int(episode) if episode else None
    except (ValueError, TypeError):
        episode = None

    return (season, episode)


def create_plex_episode_name(show_name: str, year: int | None, season: int, episode: int, ext: str) -> str:
    """Create a Plex-compatible episode filename."""
    if year:
        return f"{show_name} ({year}) - s{season:02d}e{episode:02d}{ext}"
    else:
        return f"{show_name} - s{season:02d}e{episode:02d}{ext}"


def find_existing_show_folder(parent_dir: Path, show_name: str) -> Path | None:
    """
    Search for an existing show folder with the same base name.
    Matches folders like "Show Name (2017)" when searching for "Show Name".
    Returns the existing folder path if found, None otherwise.
    """
    if not parent_dir.exists():
        return None

    # Normalize the show name for comparison
    show_name_lower = show_name.lower().strip()

    for item in parent_dir.iterdir():
        if not item.is_dir():
            continue

        folder_name = item.name

        # Check for exact match
        if folder_name.lower() == show_name_lower:
            return item

        # Check for match with year suffix: "Show Name (YYYY)"
        year_match = re.match(r'^(.+?)\s*\((\d{4})\)$', folder_name)
        if year_match:
            base_name = year_match.group(1).strip().lower()
            if base_name == show_name_lower:
                return item

    return None


def restructure_for_plex(
    folder_path: str,
    show_name: str,
    year: int | None,
    season: int | None = None,
    library_dir: str | None = None
) -> dict:
    """
    Restructure a folder to Plex-compatible format.

    Converts from:
        /Show Name S1/Show - 01.mkv

    To:
        /Show Name (Year)/Season 01/Show Name (Year) - s01e01.mkv

    If an existing show folder is found (e.g., "Show Name (2017)"), new seasons
    will be added to that folder instead of creating a new one.

    Args:
        folder_path: Path to the source folder
        show_name: Clean show name
        year: Release year (optional)
        season: Season number (optional, will be detected if not provided)
        library_dir: Target library directory. If provided, show folder will be
                     created here instead of in the source folder's parent.

    Returns dict with results.
    """
    folder = Path(folder_path)
    if not folder.exists():
        raise FileNotFoundError(f"Folder not found: {folder}")

    results = {
        "show_folder_renamed": False,
        "season_folder_created": False,
        "files_renamed": [],
        "new_show_path": None,
        "existing_show_used": False,
        "errors": []
    }

    # Sanitize show name
    safe_show_name = sanitize_filename(show_name)

    # Check if folder is already a Season folder (rename files in place)
    is_season_folder = bool(re.match(r'^[Ss]eason\s*\d+$', folder.name))

    media_extensions = {'.mkv', '.mp4', '.avi', '.m4v', '.webm'}
    subtitle_extensions = {'.ass', '.srt', '.sub', '.ssa'}
    all_extensions = media_extensions | subtitle_extensions

    if is_season_folder:
        # Already in a Season folder
        # Get season from folder name if not provided
        folder_season_match = re.search(r'[Ss]eason\s*(\d+)', folder.name)
        folder_season = int(folder_season_match.group(1)) if folder_season_match else 1

        if season is None:
            season = folder_season

        # Find the show folder (traverse up past any nested Season folders)
        show_folder = folder.parent
        while show_folder.parent != show_folder:
            if re.match(r'^[Ss]eason\s*\d+$', show_folder.name):
                show_folder = show_folder.parent
            else:
                break

        # Collect files to process
        files_to_process = []
        for file in sorted(folder.iterdir()):
            if file.is_file() and file.suffix.lower() in all_extensions:
                _, episode = detect_episode_info(file.name)
                if episode:
                    files_to_process.append((file, episode))
                else:
                    results["errors"].append(f"Could not detect episode number: {file.name}")

        if not files_to_process:
            results["errors"].append("No media files with detectable episode numbers found")
            return results

        # Check if we need to move files to a different structure
        # This happens when:
        # 1. season != folder_season (e.g., season=4 but folder is "Season 01")
        # 2. Parent folder has Roman numeral suffix (e.g., "Overlord IV") - needs consolidation
        _, parent_roman = extract_base_name_and_season(show_folder.name)
        need_restructure = (season != folder_season) or (parent_roman is not None)

        if need_restructure:
            # Need to move files to proper structure
            # Find or create the proper show folder
            # Use library_dir if provided, otherwise go up from show folder
            if library_dir:
                base_dir = Path(library_dir)
                if not base_dir.exists():
                    base_dir.mkdir(parents=True, exist_ok=True)
            else:
                base_dir = show_folder.parent  # Go up from "Overlord IV" to "Overlord"

            if year:
                target_show_name = f"{safe_show_name} ({year})"
            else:
                target_show_name = safe_show_name

            # Check if target show folder exists
            target_show_path = find_existing_show_folder(base_dir, safe_show_name)
            if target_show_path is None:
                target_show_path = base_dir / target_show_name
                target_show_path.mkdir(parents=True, exist_ok=True)
                results["show_folder_renamed"] = True

            # Create proper season folder
            season_folder_name = f"Season {season:02d}"
            target_season_path = target_show_path / season_folder_name
            if not target_season_path.exists():
                target_season_path.mkdir()
                results["season_folder_created"] = True

            results["new_show_path"] = str(target_show_path)

            # Move and rename files
            for file, episode in files_to_process:
                ext = file.suffix
                new_filename = create_plex_episode_name(safe_show_name, year, season, episode, ext)
                new_file_path = target_season_path / new_filename

                if new_file_path.exists() and new_file_path != file:
                    results["errors"].append(f"Target exists: {new_filename}")
                    continue

                try:
                    shutil.move(str(file), str(new_file_path))
                    results["files_renamed"].append({
                        "old": file.name,
                        "new": new_filename
                    })
                except Exception as e:
                    results["errors"].append(f"Failed to move {file.name}: {e}")

            # Clean up empty folders
            try:
                if not any(folder.iterdir()):
                    folder.rmdir()
                if not any(show_folder.iterdir()):
                    show_folder.rmdir()
            except Exception:
                pass

            return results

        # Simple case: just rename files in place
        results["new_show_path"] = str(show_folder)

        for file, episode in files_to_process:
            ext = file.suffix
            new_filename = create_plex_episode_name(safe_show_name, year, season, episode, ext)
            new_file_path = folder / new_filename

            if new_file_path.exists() and new_file_path != file:
                results["errors"].append(f"Target exists: {new_filename}")
                continue

            try:
                if file != new_file_path:
                    shutil.move(str(file), str(new_file_path))
                    results["files_renamed"].append({
                        "old": file.name,
                        "new": new_filename
                    })
            except Exception as e:
                results["errors"].append(f"Failed to move {file.name}: {e}")

        return results

    # Get parent directory (where show folders live)
    # Use library_dir if provided, otherwise use folder's parent
    if library_dir:
        parent_dir = Path(library_dir)
        if not parent_dir.exists():
            parent_dir.mkdir(parents=True, exist_ok=True)
    else:
        parent_dir = folder.parent

    # First, check if an existing show folder exists
    existing_show = find_existing_show_folder(parent_dir, safe_show_name)

    if existing_show and existing_show != folder:
        # Use the existing show folder
        target_show_path = existing_show
        results["existing_show_used"] = True
        # Extract the year from existing folder name for episode naming
        year_match = re.match(r'^(.+?)\s*\((\d{4})\)$', existing_show.name)
        if year_match:
            year = int(year_match.group(2))
        new_show_folder_name = existing_show.name
    else:
        # Create new show folder name with year
        if year:
            new_show_folder_name = f"{safe_show_name} ({year})"
        else:
            new_show_folder_name = safe_show_name
        target_show_path = parent_dir / new_show_folder_name

    # Detect season from folder name if not provided
    if season is None:
        season = detect_season_number(folder.name)

    season_folder_name = f"Season {season:02d}"

    # Collect files to rename
    files_to_process = []
    for file in sorted(folder.iterdir()):
        if file.is_file() and file.suffix.lower() in all_extensions:
            _, episode = detect_episode_info(file.name)
            if episode:
                files_to_process.append((file, episode))
            else:
                results["errors"].append(f"Could not detect episode number: {file.name}")

    if not files_to_process:
        results["errors"].append("No media files with detectable episode numbers found")
        return results

    # Handle special cases for target path
    if folder.name == new_show_folder_name:
        # Folder already has correct name, just need season subfolder
        target_show_path = folder
    elif folder.parent.name == new_show_folder_name:
        # Already inside a correctly named show folder
        target_show_path = folder.parent

    # Create show folder if needed
    if not target_show_path.exists():
        target_show_path.mkdir(parents=True)
        results["show_folder_renamed"] = True

    # Create season folder
    season_path = target_show_path / season_folder_name
    if not season_path.exists():
        season_path.mkdir()
        results["season_folder_created"] = True

    results["new_show_path"] = str(target_show_path)

    # Move and rename files
    for file, episode in files_to_process:
        ext = file.suffix
        new_filename = create_plex_episode_name(safe_show_name, year, season, episode, ext)
        new_file_path = season_path / new_filename

        if new_file_path.exists() and new_file_path != file:
            results["errors"].append(f"Target exists: {new_filename}")
            continue

        try:
            if file != new_file_path:
                # Use shutil.move to handle cross-filesystem moves
                shutil.move(str(file), str(new_file_path))
                results["files_renamed"].append({
                    "old": file.name,
                    "new": new_filename
                })
        except Exception as e:
            results["errors"].append(f"Failed to move {file.name}: {e}")

    # Clean up original folder if different from target
    if folder != target_show_path and folder.exists():
        remaining = list(folder.iterdir())
        # Remove if empty
        if not remaining:
            try:
                folder.rmdir()
                results["original_folder_removed"] = True
            except Exception:
                pass
        else:
            # Check if only non-media files remain (thumbnails, nfo, etc.)
            # These are safe to leave behind or move
            non_media_only = all(
                not f.suffix.lower() in all_extensions
                for f in remaining if f.is_file()
            )
            if non_media_only:
                # Try to remove non-media files and the folder
                try:
                    shutil.rmtree(folder)
                    results["original_folder_removed"] = True
                except Exception:
                    # Leave it if we can't remove
                    pass

    return results


def detect_media_type(folder_name: str, file_count: int = 0) -> str:
    """
    Detect if content is a movie or series based on folder name and file count.
    Returns 'movie' or 'series'.
    """
    name_lower = folder_name.lower()

    # Movie indicators in name
    movie_keywords = [
        'movie', 'movies', 'film', 'gekijouban', 'gekijō-ban',
        'the movie', 'motion picture'
    ]

    for keyword in movie_keywords:
        if keyword in name_lower:
            return 'movie'

    # If only 1-3 files and no season/episode indicators, likely a movie
    if file_count > 0 and file_count <= 3:
        # Check for episode indicators
        has_episode_indicator = bool(re.search(
            r'\b(s\d{1,2}|season|ep|episode|e\d{2,})\b',
            name_lower
        ))
        if not has_episode_indicator:
            return 'movie'

    return 'series'


def restructure_for_plex_movie(
    folder_path: str,
    movie_name: str,
    year: int | None,
    movies_dir: str
) -> dict:
    """
    Restructure a folder containing a movie to Plex-compatible format.

    Converts from:
        /Download Folder/[Group] Movie Name.mkv

    To:
        /Movies Dir/Movie Name (Year)/Movie Name (Year).mkv

    Returns dict with results.
    """
    folder = Path(folder_path)
    movies_base = Path(movies_dir)

    if not folder.exists():
        raise FileNotFoundError(f"Folder not found: {folder}")

    results = {
        "movie_folder_created": False,
        "file_renamed": None,
        "new_movie_path": None,
        "errors": []
    }

    # Sanitize movie name
    safe_movie_name = sanitize_filename(movie_name)

    # Create movie folder name with year
    if year:
        movie_folder_name = f"{safe_movie_name} ({year})"
    else:
        movie_folder_name = safe_movie_name

    media_extensions = {'.mkv', '.mp4', '.avi', '.m4v', '.webm'}

    # Find the movie file
    movie_files = [
        f for f in folder.iterdir()
        if f.is_file() and f.suffix.lower() in media_extensions
    ]

    if not movie_files:
        results["errors"].append("No movie files found")
        return results

    if len(movie_files) > 1:
        # If multiple files, take the largest one (likely the movie, not extras)
        movie_file = max(movie_files, key=lambda f: f.stat().st_size)
    else:
        movie_file = movie_files[0]

    # Create target folder
    target_folder = movies_base / movie_folder_name
    if not target_folder.exists():
        target_folder.mkdir(parents=True)
        results["movie_folder_created"] = True

    # Create new filename
    ext = movie_file.suffix
    new_filename = f"{movie_folder_name}{ext}"
    new_file_path = target_folder / new_filename

    # Move the file
    try:
        if movie_file != new_file_path:
            shutil.move(str(movie_file), str(new_file_path))
            results["file_renamed"] = {
                "old": movie_file.name,
                "new": new_filename
            }
    except Exception as e:
        results["errors"].append(f"Failed to move movie: {e}")
        return results

    results["new_movie_path"] = str(target_folder)

    # Clean up original folder if empty or only has non-media files
    remaining = list(folder.iterdir())
    if not remaining:
        try:
            folder.rmdir()
        except Exception:
            pass
    else:
        # Check if only non-media files remain
        non_media_only = all(
            not f.suffix.lower() in media_extensions
            for f in remaining if f.is_file()
        )
        if non_media_only:
            try:
                shutil.rmtree(folder)
            except Exception:
                pass

    return results


def needs_plex_restructure(folder_path: str) -> bool:
    """Check if a folder needs Plex restructuring."""
    folder = Path(folder_path)

    # Skip common bonus/extras folder names
    skip_names = {
        'extras', 'extra', 'specials', 'special', 'ova', 'ovas',
        'oad', 'oads', 'ona', 'onas', 'bonus', 'bonuses',
        'nc', 'nced', 'ncop', 'creditless', 'clean',
        'pv', 'pvs', 'cm', 'menu', 'menus', 'scans',
        'featurettes', 'behind the scenes', 'interviews',
    }
    if folder.name.lower() in skip_names:
        return False

    # Check if folder name has year in parens
    has_year = bool(re.search(r'\(\d{4}\)$', folder.name))

    # Check if season subfolder exists
    has_season_folder = any(
        d.is_dir() and d.name.lower().startswith('season')
        for d in folder.iterdir()
    )

    # Check if files have CLEAN Plex naming (not just sXXeXX somewhere in a tagged filename)
    media_extensions = {'.mkv', '.mp4', '.avi', '.m4v', '.webm'}
    has_clean_plex_naming = False
    has_media = False

    for f in folder.iterdir():
        if f.is_file() and f.suffix.lower() in media_extensions:
            has_media = True
            stem = f.stem
            # Clean Plex naming: "Show Name (Year) - s##e##" or "Show Name - s##e##"
            # Should NOT have: leading brackets [Group], trailing brackets [Hash], (Quality tags)
            has_leading_bracket = stem.startswith('[')
            has_trailing_bracket = bool(re.search(r'\[[a-fA-F0-9]{6,}\]$', stem))
            has_quality_tags = bool(re.search(r'\((?:BD|WEB|1080p|720p|480p|HEVC|x265|x264|AV1|OPUS|AAC|FLAC)', stem, re.IGNORECASE))

            # Check if it matches clean plex format: ends with " - sXXeXX" or similar
            is_clean_format = bool(re.search(r' - s\d{2}e\d{2}(-e?\d{2})?$', stem, re.IGNORECASE))

            if is_clean_format and not has_leading_bracket and not has_trailing_bracket and not has_quality_tags:
                has_clean_plex_naming = True
                break

    # Needs restructure if has media but missing clean Plex structure
    if has_media:
        return not (has_year or has_season_folder or has_clean_plex_naming)

    return False


# Test cases
if __name__ == "__main__":
    test_names = [
        "[MTBB] The Apothecary Diaries S1 (BD 1080p)",
        "[Tenrai-Sensei] KonoSuba God's Blessing on This Wonderful World [BD][1080p][HEVC 10bit x265][Dual Audio]",
        "The.All.devouring.Whale.S01.1080p.ADN.WEB-DL.AAC2.0.H.264-VARYG",
        "[Exiled-Destiny] Haibane Renmei",
        "The Eminence in Shadow (S1+S2) [BD] [AV1] [ItachiUchiha]",
        "[SubsPlease] Frieren - Beyond Journey's End - 01 (1080p) [ABC123].mkv",
        "Solo Leveling",
        "Made in Abyss Season 2",
        "Overlord IV",
        "[Judas] Konosuba II - 01.mkv",
    ]

    print("=" * 70)
    print("ANITOPY-BASED CLEANER TEST RESULTS")
    print("=" * 70)

    print("\n--- detect_anime_name() ---")
    for name in test_names:
        cleaned = detect_anime_name(name)
        print(f"  {name}")
        print(f"    -> {cleaned}\n")

    print("\n--- detect_season_number() ---")
    season_tests = [
        "Made in Abyss Season 2",
        "Overlord IV",
        "Konosuba II",
        "[MTBB] The Apothecary Diaries S1 (BD 1080p)",
        "Attack on Titan Third Season",
        "Solo Leveling",
    ]
    for name in season_tests:
        season = detect_season_number(name)
        print(f"  {name} -> Season {season}")

    print("\n--- detect_episode_info() ---")
    episode_tests = [
        "[SubsPlease] Frieren - Beyond Journey's End - 01 (1080p) [ABC123].mkv",
        "[Judas] Konosuba II - 01.mkv",
        "Show.Name.S02E15.1080p.mkv",
        "[Group] Show - 24v2.mkv",
        "Show Name Episode 10.mkv",
    ]
    for name in episode_tests:
        season, episode = detect_episode_info(name)
        print(f"  {name}")
        print(f"    -> Season: {season}, Episode: {episode}")

    print("\n--- needs_cleaning() ---")
    for name in test_names[:5]:
        needs = needs_cleaning(name)
        print(f"  {name[:50]}... -> {needs}")
