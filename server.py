import asyncio
import json
import os
import re
import urllib.parse
from pathlib import Path
from typing import AsyncIterator

import aiohttp
from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

from config import config
from mullvad import mullvad, MullvadStatus
from qbittorrent import qbittorrent, QBTStatus
from cleaner import (
    detect_anime_name, rename_folder, rename_files_in_folder,
    detect_season_number, restructure_for_plex, find_existing_show_folder,
    detect_media_type, restructure_for_plex_movie, needs_cleaning,
    needs_plex_restructure, clean_episode_name, sanitize_filename,
    detect_episode_info, create_plex_episode_name
)
from anilist import search_anilist, lookup_metadata

app = FastAPI(title="AniGrab")

# Mount static files
app.mount("/static", StaticFiles(directory="static"), name="static")


def sse_message(event: str, data: dict) -> str:
    """Format a Server-Sent Event message."""
    return f"event: {event}\ndata: {json.dumps(data)}\n\n"


@app.get("/", response_class=HTMLResponse)
async def index():
    """Serve the main page."""
    with open("templates/index.html") as f:
        return HTMLResponse(content=f.read())


@app.get("/api/status")
async def get_status():
    """Get current status of Mullvad and qBittorrent."""
    mullvad_state = await mullvad.get_status()
    qbt_state = await qbittorrent.get_status()
    dht_nodes = await qbittorrent.get_dht_nodes() if qbt_state.connected else 0

    return {
        "mullvad": {
            "status": mullvad_state.status.value,
            "server": mullvad_state.server,
            "ip": mullvad_state.ip,
            "message": mullvad_state.message
        },
        "qbittorrent": {
            "connected": qbt_state.connected,
            "version": qbt_state.version,
            "message": qbt_state.message,
            "dht_nodes": dht_nodes
        }
    }


@app.post("/api/parse-link")
async def parse_link(request: Request):
    """Parse a nyaa.si or magnet link and return media info."""
    body = await request.json()
    url = body.get("url", "").strip()

    if not url:
        return {"success": False, "error": "No URL provided"}

    # Validate URL
    if not _is_valid_link(url):
        return {
            "success": False,
            "error": "Invalid link. Please provide a nyaa.si URL or magnet link."
        }

    # Try to extract info
    info = await _extract_media_info(url)
    return {"success": True, "info": info, "url": url}


@app.post("/api/cancel")
async def cancel_download(request: Request):
    """Cancel a download and delete the torrent from qBittorrent."""
    body = await request.json()
    torrent_hash = body.get("hash", "").strip()

    if not torrent_hash:
        return {"success": False, "error": "No torrent hash provided"}

    success, message = await qbittorrent.delete_torrent(torrent_hash, delete_files=True)
    return {"success": success, "message": message}


@app.post("/api/disconnect-vpn")
async def disconnect_vpn():
    """Disconnect from Mullvad VPN."""
    state = await mullvad.disconnect()
    return {
        "success": state.status.value == "disconnected",
        "status": state.status.value,
        "message": state.message
    }


@app.post("/api/restart-qbittorrent")
async def restart_qbittorrent():
    """Restart qBittorrent to fix DHT issues."""
    try:
        if not config.qbt_executable:
            return {
                "success": False,
                "message": "qBittorrent executable not found. Set QBT_EXECUTABLE env var.",
                "dht_nodes": 0
            }

        # Kill existing qBittorrent process
        kill_proc = await asyncio.create_subprocess_exec(
            "pkill", "-f", "qbittorrent",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE
        )
        await kill_proc.communicate()

        # Reset the qBittorrent client state (old session is now invalid)
        await qbittorrent.reset()

        # Wait a moment for it to fully stop
        await asyncio.sleep(2)

        # Start qBittorrent in background (use DEVNULL to fully detach)
        start_proc = await asyncio.create_subprocess_exec(
            config.qbt_executable,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
            stdin=asyncio.subprocess.DEVNULL,
            start_new_session=True
        )

        # Wait for it to start up
        await asyncio.sleep(4)

        # Check if it's running and connected
        qbt_state = await qbittorrent.get_status()
        if qbt_state.connected:
            # Wait a bit more for DHT to initialize
            await asyncio.sleep(3)
            dht_nodes = await qbittorrent.get_dht_nodes()
            return {
                "success": True,
                "message": f"qBittorrent restarted (DHT: {dht_nodes} nodes)",
                "dht_nodes": dht_nodes
            }
        else:
            return {
                "success": False,
                "message": "qBittorrent restarted but not responding yet. Try again.",
                "dht_nodes": 0
            }
    except Exception as e:
        return {
            "success": False,
            "message": f"Failed to restart qBittorrent: {e}",
            "dht_nodes": 0
        }


@app.get("/api/active-download")
async def get_active_download():
    """Check if there's an active download in qBittorrent."""
    try:
        info = await qbittorrent.get_active_download()
        if info:
            return {
                "active": True,
                "hash": info.hash,
                "name": info.name,
                "progress": round(info.progress * 100, 1),
                "status": info.status.value
            }
        return {"active": False}
    except Exception:
        return {"active": False}


@app.get("/api/progress")
async def progress_stream(hash: str):
    """
    Resume monitoring an existing torrent by hash.
    Used when user switches back to app after it went to background.
    """
    return StreamingResponse(
        _progress_generator(hash),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no"
        }
    )


async def _progress_generator(torrent_hash: str) -> AsyncIterator[str]:
    """Generate SSE events for monitoring an existing torrent."""
    # Check if torrent exists
    info = await qbittorrent.get_torrent_info(torrent_hash)
    if info is None:
        yield sse_message("not_found", {"message": "Torrent not found"})
        return

    # Monitor the torrent
    async for info in qbittorrent.monitor_torrent(torrent_hash, interval=1.0):
        progress_data = {
            "name": info.name,
            "hash": info.hash,
            "progress": round(info.progress * 100, 1),
            "status": info.status.value,
            "speed": _format_speed(info.download_speed),
            "size": _format_size(info.size),
            "downloaded": _format_size(info.downloaded),
            "eta": _format_eta(info.eta),
            "message": info.message
        }

        if info.status == QBTStatus.COMPLETE:
            # Add folder path for post-processing (from qBittorrent's actual save location)
            if info.content_path:
                progress_data["folder_path"] = info.content_path
            elif config.download_dir:
                progress_data["folder_path"] = os.path.join(config.download_dir, info.name)
            yield sse_message("complete", progress_data)
            return
        elif info.status == QBTStatus.ERROR:
            yield sse_message("error", {"message": info.message or "Download error"})
            return
        else:
            yield sse_message("progress", progress_data)


@app.get("/api/download")
async def download_stream(url: str):
    """
    Start download process with SSE streaming updates.

    Events:
    - status: General status update
    - mullvad: Mullvad connection status
    - qbittorrent: qBittorrent status
    - progress: Download progress
    - complete: Download finished
    - error: An error occurred
    """
    # Decode URL if it's still encoded
    decoded_url = urllib.parse.unquote(url)
    return StreamingResponse(
        _download_generator(decoded_url),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no"
        }
    )


async def _download_generator(url: str) -> AsyncIterator[str]:
    """Generate SSE events for download process."""

    # Step 1: Check Mullvad connection and connect automatically if needed
    yield sse_message("status", {"step": "mullvad", "message": "Checking Mullvad VPN..."})

    mullvad_state = await mullvad.get_status()
    yield sse_message("mullvad", {
        "status": mullvad_state.status.value,
        "server": mullvad_state.server,
        "ip": mullvad_state.ip,
        "message": mullvad_state.message
    })

    if mullvad_state.status != MullvadStatus.CONNECTED:
        # Try to connect automatically
        yield sse_message("status", {"step": "mullvad", "message": "Connecting to Mullvad VPN..."})
        async for state in mullvad.connect():
            yield sse_message("mullvad", {
                "status": state.status.value,
                "server": state.server,
                "ip": state.ip,
                "message": state.message
            })
            mullvad_state = state

        if mullvad_state.status != MullvadStatus.CONNECTED:
            yield sse_message("error", {"message": f"Failed to connect to Mullvad VPN: {mullvad_state.message}"})
            return

    # Step 2: Check qBittorrent connection and DHT nodes
    yield sse_message("status", {"step": "qbittorrent", "message": "Checking qBittorrent..."})

    qbt_state = await qbittorrent.get_status()
    if not qbt_state.connected:
        # Try to start qBittorrent if we know where it is
        if config.qbt_executable:
            yield sse_message("qbittorrent", {
                "connected": False,
                "version": None,
                "message": "Starting qBittorrent..."
            })

            try:
                # Start qBittorrent (use DEVNULL to fully detach)
                await asyncio.create_subprocess_exec(
                    config.qbt_executable,
                    stdout=asyncio.subprocess.DEVNULL,
                    stderr=asyncio.subprocess.DEVNULL,
                    stdin=asyncio.subprocess.DEVNULL,
                    start_new_session=True
                )

                # Wait for it to start and become responsive
                for attempt in range(10):  # Try for up to 10 seconds
                    await asyncio.sleep(1)
                    qbt_state = await qbittorrent.get_status()
                    if qbt_state.connected:
                        break

            except Exception as e:
                yield sse_message("qbittorrent", {
                    "connected": False,
                    "version": None,
                    "message": f"Failed to start qBittorrent: {e}"
                })
                yield sse_message("error", {"message": f"Failed to start qBittorrent: {e}"})
                return

        if not qbt_state.connected:
            yield sse_message("qbittorrent", {
                "connected": False,
                "version": None,
                "message": qbt_state.message
            })
            yield sse_message("error", {"message": qbt_state.message})
            return

    # Check DHT nodes - retry a few times as DHT takes time to bootstrap
    dht_nodes = 0
    for attempt in range(6):  # Try for up to 30 seconds
        dht_nodes = await qbittorrent.get_dht_nodes()
        if dht_nodes > 0:
            break
        yield sse_message("qbittorrent", {
            "connected": True,
            "version": qbt_state.version,
            "message": f"Waiting for DHT... ({attempt + 1}/6)"
        })
        await asyncio.sleep(5)

    if dht_nodes == 0:
        yield sse_message("qbittorrent", {
            "connected": True,
            "version": qbt_state.version,
            "message": "DHT: 0 nodes - needs restart"
        })
        yield sse_message("dht_error", {
            "message": "qBittorrent has 0 DHT nodes",
            "action": "restart"
        })
        return

    yield sse_message("qbittorrent", {
        "connected": qbt_state.connected,
        "version": qbt_state.version,
        "message": f"{qbt_state.message} (DHT: {dht_nodes} nodes)"
    })

    # Step 3: Resolve magnet link if nyaa.si URL
    actual_url = url
    if "nyaa.si" in url.lower() and "magnet:" not in url.lower():
        yield sse_message("status", {"step": "resolve", "message": "Resolving torrent link..."})
        magnet = await _get_magnet_from_nyaa(url)
        if magnet:
            actual_url = magnet
            yield sse_message("status", {"step": "resolve", "message": "Magnet link resolved"})
        else:
            yield sse_message("error", {"message": "Could not get magnet link from nyaa.si"})
            return

    # Step 4: Add torrent to qBittorrent
    yield sse_message("status", {"step": "adding", "message": "Adding torrent to qBittorrent..."})

    success, message, torrent_hash = await qbittorrent.add_torrent(actual_url)

    if not success:
        yield sse_message("error", {"message": message})
        return

    yield sse_message("status", {"step": "added", "message": "Torrent added, starting download..."})

    # Step 5: Monitor download progress
    if torrent_hash:
        # Give qBittorrent time to process and fetch metadata
        await asyncio.sleep(5)

        async for info in qbittorrent.monitor_torrent(torrent_hash, interval=1.0):
            progress_data = {
                "name": info.name,
                "hash": info.hash,
                "progress": round(info.progress * 100, 1),
                "status": info.status.value,
                "speed": _format_speed(info.download_speed),
                "size": _format_size(info.size),
                "downloaded": _format_size(info.downloaded),
                "eta": _format_eta(info.eta),
                "message": info.message
            }

            if info.status == QBTStatus.COMPLETE:
                # Add folder path for post-processing (from qBittorrent's actual save location)
                if info.content_path:
                    progress_data["folder_path"] = info.content_path
                elif config.download_dir:
                    progress_data["folder_path"] = os.path.join(config.download_dir, info.name)
                yield sse_message("complete", progress_data)
                return
            elif info.status == QBTStatus.ERROR:
                yield sse_message("error", {"message": info.message or "Download error"})
                return
            else:
                yield sse_message("progress", progress_data)
    else:
        # No hash available, just report success
        yield sse_message("complete", {
            "name": "Unknown",
            "progress": 0,
            "message": "Torrent added but cannot track progress (hash unknown)"
        })


@app.post("/api/post-process/detect")
async def detect_post_process(request: Request):
    """
    Detect clean name, season, media type using anitopy parser.
    Then lookup metadata from AniList for verification.
    Called after download completes.
    """
    body = await request.json()
    torrent_name = body.get("torrent_name", "")
    folder_path = body.get("folder_path", "")

    if not torrent_name:
        return {"success": False, "error": "No torrent name provided"}

    # Count media files if folder path provided
    file_count = 0
    if folder_path:
        folder = Path(folder_path)
        if folder.exists():
            media_extensions = {'.mkv', '.mp4', '.avi', '.m4v', '.webm'}
            file_count = len([
                f for f in folder.iterdir()
                if f.is_file() and f.suffix.lower() in media_extensions
            ])

    # Detect media type (movie vs series)
    media_type = detect_media_type(torrent_name, file_count)

    # Detect clean name and season using anitopy
    detected_name = detect_anime_name(torrent_name)
    detected_season = detect_season_number(torrent_name) if media_type == 'series' else None

    # Check for existing show folder (for series)
    existing_show = None
    if media_type == 'series' and folder_path:
        folder = Path(folder_path)
        existing = find_existing_show_folder(folder.parent, detected_name)
        if existing:
            existing_show = existing.name

    # Lookup metadata from AniList for verification
    anilist_results = await search_anilist(detected_name)
    matches = [r.to_dict() for r in anilist_results[:5]]

    # Get best match for auto-fill
    best_match = matches[0] if matches else None

    return {
        "success": True,
        "detected_name": detected_name,
        "detected_season": detected_season,
        "media_type": media_type,
        "existing_show": existing_show,
        "original_name": torrent_name,
        "folder_path": folder_path,
        "anilist_matches": matches,
        "best_match": best_match,
    }


@app.post("/api/post-process/apply")
async def apply_post_process(request: Request):
    """
    Apply post-processing: rename folder and files to clean names.
    """
    body = await request.json()
    folder_path = body.get("folder_path", "")
    new_name = body.get("new_name", "")

    if not folder_path or not new_name:
        return {"success": False, "error": "Missing folder_path or new_name"}

    folder = Path(folder_path)
    if not folder.exists():
        return {"success": False, "error": f"Folder not found: {folder_path}"}

    results = {
        "folder_renamed": False,
        "files_renamed": [],
        "new_path": folder_path
    }

    try:
        # Rename folder
        if folder.name != new_name:
            new_path = rename_folder(str(folder), new_name)
            folder = Path(new_path)
            results["folder_renamed"] = True
            results["new_path"] = new_path

        # Rename files inside
        renamed = rename_files_in_folder(str(folder), new_name)
        results["files_renamed"] = [{"old": old, "new": new} for old, new in renamed]

        return {"success": True, "results": results}

    except Exception as e:
        return {"success": False, "error": str(e)}


@app.post("/api/anilist/search")
async def search_anilist_metadata(request: Request):
    """Search AniList for anime metadata with a custom query."""
    body = await request.json()
    query = body.get("query", "")

    if not query:
        return {"success": False, "error": "No query provided"}

    results = await search_anilist(query)
    matches = [r.to_dict() for r in results[:10]]

    return {"success": True, "matches": matches}


@app.post("/api/post-process/plex-restructure")
async def plex_restructure(request: Request):
    """
    Restructure a folder to Plex-compatible format.
    Creates: Show Name (Year)/Season XX/Show Name (Year) - sXXeXX.ext
    """
    body = await request.json()
    folder_path = body.get("folder_path", "")
    show_name = body.get("show_name", "")
    year = body.get("year")  # Optional, from AniList
    season = body.get("season")  # Optional, will detect if not provided

    if not folder_path or not show_name:
        return {"success": False, "error": "Missing folder_path or show_name"}

    folder = Path(folder_path)
    if not folder.exists():
        return {"success": False, "error": f"Folder not found: {folder_path}"}

    try:
        # Detect season if not provided
        if season is None:
            season = detect_season_number(folder.name)

        # Auto-lookup year from AniList if not provided
        if year is None:
            try:
                matches = await search_anilist(show_name)
                if matches:
                    year = matches[0].year
            except Exception:
                pass  # Continue without year if lookup fails

        # Perform restructure - use anime_dir if configured
        result = restructure_for_plex(str(folder), show_name, year, season, config.anime_dir)

        return {"success": True, "results": result}

    except Exception as e:
        return {"success": False, "error": str(e)}


@app.post("/api/post-process/movie-restructure")
async def movie_restructure(request: Request):
    """
    Restructure a folder containing a movie to Plex-compatible format.
    Creates: Movie Name (Year)/Movie Name (Year).mkv
    """
    body = await request.json()
    folder_path = body.get("folder_path", "")
    movie_name = body.get("movie_name", "")
    year = body.get("year")  # Optional, from AniList
    movies_dir_override = body.get("movies_dir")  # Optional override

    if not folder_path or not movie_name:
        return {"success": False, "error": "Missing folder_path or movie_name"}

    # Get movies directory from override, config, or default
    movies_dir = movies_dir_override or config.movies_dir
    if not movies_dir:
        # Default to sibling "Movies" folder of anime_dir
        if config.anime_dir:
            movies_dir = str(Path(config.anime_dir).parent / "Movies")
        else:
            return {"success": False, "error": "Movies directory not configured"}

    folder = Path(folder_path)
    if not folder.exists():
        return {"success": False, "error": f"Folder not found: {folder_path}"}

    try:
        # Perform movie restructure
        result = restructure_for_plex_movie(str(folder), movie_name, year, movies_dir)

        return {"success": True, "results": result}

    except Exception as e:
        return {"success": False, "error": str(e)}


@app.post("/api/library/scan")
async def scan_library(request: Request):
    """
    Scan a directory for folders that need processing.
    Returns folders grouped by type: needs_cleaning, needs_plex_restructure.
    """
    body = await request.json()
    scan_path = body.get("path", "")
    recursive = body.get("recursive", True)
    scan_type = body.get("type", "all")  # all, cleaning, plex
    force = body.get("force", False)

    if not scan_path:
        # Default to anime_dir from config
        scan_path = config.anime_dir
        if not scan_path:
            return {"success": False, "error": "No path provided and ANIME_DIR not configured"}

    path = Path(scan_path)
    if not path.exists():
        return {"success": False, "error": f"Path not found: {scan_path}"}

    if not path.is_dir():
        return {"success": False, "error": f"Path is not a directory: {scan_path}"}

    results = {
        "path": str(path),
        "needs_cleaning": [],
        "needs_plex_restructure": [],
    }

    media_extensions = {'.mkv', '.mp4', '.avi', '.m4v', '.webm'}

    def scan_dir(dir_path: Path, depth: int = 0):
        """Recursively scan for folders needing processing."""
        max_depth = 3 if recursive else 0

        for item in sorted(dir_path.iterdir()):
            if item.is_dir():
                # Check if folder has media files
                has_media = any(
                    f.suffix.lower() in media_extensions
                    for f in item.iterdir() if f.is_file()
                )

                if has_media:
                    folder_info = {
                        "path": str(item),
                        "name": item.name,
                        "detected_name": detect_anime_name(item.name),
                    }

                    # Check what processing is needed
                    if scan_type in ("all", "cleaning") and needs_cleaning(item.name):
                        folder_info["detected_season"] = detect_season_number(item.name)
                        results["needs_cleaning"].append(folder_info.copy())

                    if scan_type in ("all", "plex") and (force or needs_plex_restructure(str(item))):
                        file_count = sum(1 for f in item.iterdir() if f.is_file() and f.suffix.lower() in media_extensions)
                        folder_info["media_type"] = detect_media_type(item.name, file_count)
                        folder_info["detected_season"] = detect_season_number(item.name) if folder_info["media_type"] == "series" else None
                        results["needs_plex_restructure"].append(folder_info.copy())

                # Recurse into subdirectories
                if depth < max_depth:
                    scan_dir(item, depth + 1)

    # Scan the directory
    scan_dir(path, depth=0)

    # Sort by depth (deepest first for cleaning operations)
    for key in ("needs_cleaning", "needs_plex_restructure"):
        results[key].sort(key=lambda x: len(Path(x["path"]).parts), reverse=True)

    return {
        "success": True,
        "results": results,
        "counts": {
            "needs_cleaning": len(results["needs_cleaning"]),
            "needs_plex_restructure": len(results["needs_plex_restructure"]),
        }
    }


@app.post("/api/library/preview")
async def preview_changes(request: Request):
    """
    Preview changes for a folder without applying them (dry-run mode).
    Returns detailed preview of what would happen.
    """
    body = await request.json()
    folder_path = body.get("folder_path", "")
    mode = body.get("mode", "standard")  # standard, plex
    show_name = body.get("show_name")
    year = body.get("year")
    season = body.get("season")
    movies_dir = body.get("movies_dir")

    if not folder_path:
        return {"success": False, "error": "No folder_path provided"}

    folder = Path(folder_path)
    if not folder.exists():
        return {"success": False, "error": f"Folder not found: {folder_path}"}

    media_extensions = {'.mkv', '.mp4', '.avi', '.m4v', '.webm'}
    subtitle_extensions = {'.ass', '.srt', '.sub', '.ssa'}
    all_extensions = media_extensions | subtitle_extensions

    # Get file count for media type detection
    file_count = sum(1 for f in folder.iterdir() if f.is_file() and f.suffix.lower() in media_extensions)

    # Detect names if not provided
    if not show_name:
        show_name = detect_anime_name(folder.name)

    media_type = detect_media_type(folder.name, file_count)

    if season is None and media_type == "series":
        season = detect_season_number(folder.name)

    safe_name = sanitize_filename(show_name)

    preview = {
        "mode": mode,
        "detected_name": show_name,
        "detected_season": season,
        "media_type": media_type,
        "folder_rename": None,
        "file_renames": [],
        "structure_changes": [],
    }

    if mode == "plex":
        # Plex restructure preview
        if year:
            new_folder_name = f"{safe_name} ({year})"
        else:
            new_folder_name = safe_name

        if media_type == "movie":
            # Movie restructure preview
            target_movies_dir = movies_dir or config.movies_dir
            if not target_movies_dir and config.anime_dir:
                target_movies_dir = str(Path(config.anime_dir).parent / "Movies")

            preview["structure_changes"].append({
                "type": "create_folder",
                "path": f"{target_movies_dir}/{new_folder_name}/"
            })

            # Find largest media file (the movie)
            movie_files = [f for f in folder.iterdir() if f.is_file() and f.suffix.lower() in media_extensions]
            if movie_files:
                movie_file = max(movie_files, key=lambda f: f.stat().st_size)
                new_filename = f"{new_folder_name}{movie_file.suffix}"
                preview["file_renames"].append({
                    "old": movie_file.name,
                    "new": new_filename,
                    "action": "move"
                })
        else:
            # Series restructure preview
            season_folder_name = f"Season {season:02d}"

            # Check for existing show folder
            existing_show = find_existing_show_folder(folder.parent, safe_name)
            if existing_show and existing_show != folder:
                preview["structure_changes"].append({
                    "type": "use_existing",
                    "path": str(existing_show)
                })
                new_folder_name = existing_show.name
                # Extract year from existing folder for episode naming
                import re
                year_match = re.match(r'^(.+?)\s*\((\d{4})\)$', existing_show.name)
                if year_match:
                    year = int(year_match.group(2))
            else:
                preview["structure_changes"].append({
                    "type": "create_folder",
                    "path": f"{folder.parent}/{new_folder_name}/"
                })

            preview["structure_changes"].append({
                "type": "create_season",
                "path": f"{new_folder_name}/{season_folder_name}/"
            })

            # Preview file renames
            for file in sorted(folder.iterdir()):
                if file.is_file() and file.suffix.lower() in all_extensions:
                    _, episode = detect_episode_info(file.name)
                    if episode:
                        new_filename = create_plex_episode_name(safe_name, year, season, episode, file.suffix)
                        preview["file_renames"].append({
                            "old": file.name,
                            "new": new_filename,
                            "action": "move"
                        })
                    else:
                        preview["file_renames"].append({
                            "old": file.name,
                            "new": None,
                            "action": "skip",
                            "reason": "Could not detect episode number"
                        })

    else:
        # Standard cleaning preview
        if folder.name != safe_name:
            preview["folder_rename"] = {
                "old": folder.name,
                "new": safe_name
            }

        # Preview file renames
        for file in sorted(folder.iterdir()):
            if file.is_file() and file.suffix.lower() in media_extensions:
                new_name = clean_episode_name(file.name, safe_name)
                if new_name != file.name:
                    preview["file_renames"].append({
                        "old": file.name,
                        "new": new_name,
                        "action": "rename"
                    })

    return {"success": True, "preview": preview}


@app.post("/api/library/batch")
async def batch_process(request: Request):
    """
    Process multiple folders in batch.
    """
    body = await request.json()
    folders = body.get("folders", [])  # List of folder configs
    mode = body.get("mode", "standard")  # standard, plex
    movies_dir = body.get("movies_dir")

    if not folders:
        return {"success": False, "error": "No folders provided"}

    results = {
        "processed": 0,
        "failed": 0,
        "skipped": 0,
        "details": []
    }

    for folder_config in folders:
        folder_path = folder_config.get("path", "")
        show_name = folder_config.get("show_name")
        year = folder_config.get("year")
        season = folder_config.get("season")

        if not folder_path:
            results["skipped"] += 1
            results["details"].append({
                "path": folder_path,
                "status": "skipped",
                "reason": "No path provided"
            })
            continue

        folder = Path(folder_path)
        if not folder.exists():
            results["failed"] += 1
            results["details"].append({
                "path": folder_path,
                "status": "failed",
                "reason": "Folder not found"
            })
            continue

        try:
            # Auto-detect if not provided
            if not show_name:
                show_name = detect_anime_name(folder.name)

            media_extensions = {'.mkv', '.mp4', '.avi', '.m4v', '.webm'}
            file_count = sum(1 for f in folder.iterdir() if f.is_file() and f.suffix.lower() in media_extensions)
            media_type = detect_media_type(folder.name, file_count)

            if season is None and media_type == "series":
                season = detect_season_number(folder.name)

            # Auto-lookup year from AniList if not provided (for Plex mode)
            if year is None and mode == "plex":
                try:
                    matches = await search_anilist(show_name)
                    if matches:
                        year = matches[0].year
                except Exception:
                    pass  # Continue without year if lookup fails

            if mode == "plex":
                # Plex restructure
                if media_type == "movie":
                    target_movies_dir = movies_dir or config.movies_dir
                    if not target_movies_dir and config.anime_dir:
                        target_movies_dir = str(Path(config.anime_dir).parent / "Movies")

                    if not target_movies_dir:
                        results["failed"] += 1
                        results["details"].append({
                            "path": folder_path,
                            "status": "failed",
                            "reason": "Movies directory not configured"
                        })
                        continue

                    result = restructure_for_plex_movie(str(folder), show_name, year, target_movies_dir)
                else:
                    result = restructure_for_plex(str(folder), show_name, year, season, config.anime_dir)

                results["processed"] += 1
                results["details"].append({
                    "path": folder_path,
                    "status": "success",
                    "result": result
                })

            else:
                # Standard cleaning
                safe_name = sanitize_filename(show_name)
                new_path = folder_path

                if folder.name != safe_name:
                    new_path = rename_folder(str(folder), safe_name)
                    folder = Path(new_path)

                renamed_files = rename_files_in_folder(str(folder), safe_name)

                results["processed"] += 1
                results["details"].append({
                    "path": folder_path,
                    "status": "success",
                    "new_path": new_path,
                    "files_renamed": len(renamed_files)
                })

        except Exception as e:
            results["failed"] += 1
            results["details"].append({
                "path": folder_path,
                "status": "failed",
                "reason": str(e)
            })

    return {"success": True, "results": results}


def _is_valid_link(url: str) -> bool:
    """Check if URL is a valid nyaa.si link or magnet link."""
    url_lower = url.lower().strip()

    if url_lower.startswith("magnet:"):
        return "btih:" in url_lower

    if "nyaa.si" in url_lower:
        return True

    return False


async def _extract_media_info(url: str) -> dict:
    """Extract media information from URL."""
    info = {
        "title": "Unknown",
        "type": "magnet" if "magnet:" in url.lower() else "nyaa",
        "size": None,
        "seeders": None,
        "leechers": None
    }

    # Try to extract title from magnet link
    if "magnet:" in url.lower():
        match = re.search(r"dn=([^&]+)", url)
        if match:
            info["title"] = urllib.parse.unquote_plus(match.group(1))
        return info

    # For nyaa.si, try to fetch page info
    if "nyaa.si" in url.lower():
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(url, timeout=10) as resp:
                    if resp.status == 200:
                        html = await resp.text()

                        # Extract title
                        title_match = re.search(r"<h3[^>]*class=\"panel-title\"[^>]*>([^<]+)</h3>", html)
                        if title_match:
                            info["title"] = title_match.group(1).strip()

                        # Extract size, seeders, leechers from the page
                        size_match = re.search(r"<div[^>]*>File size</div>\s*<div[^>]*>([^<]+)</div>", html)
                        if size_match:
                            info["size"] = size_match.group(1).strip()

                        seeders_match = re.search(r"<span[^>]*style=\"color: green[^\"]*\"[^>]*>(\d+)</span>", html)
                        if seeders_match:
                            info["seeders"] = int(seeders_match.group(1))

                        leechers_match = re.search(r"<span[^>]*style=\"color: red[^\"]*\"[^>]*>(\d+)</span>", html)
                        if leechers_match:
                            info["leechers"] = int(leechers_match.group(1))
        except Exception:
            pass

    return info


async def _get_magnet_from_nyaa(url: str) -> str | None:
    """Extract magnet link from nyaa.si page."""
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(url, timeout=10) as resp:
                if resp.status == 200:
                    html = await resp.text()
                    match = re.search(r'href="(magnet:\?[^"]+)"', html)
                    if match:
                        return match.group(1)
    except Exception:
        pass
    return None


def _format_speed(bytes_per_sec: int) -> str:
    """Format download speed."""
    if bytes_per_sec < 1024:
        return f"{bytes_per_sec} B/s"
    elif bytes_per_sec < 1024 * 1024:
        return f"{bytes_per_sec / 1024:.1f} KB/s"
    else:
        return f"{bytes_per_sec / (1024 * 1024):.1f} MB/s"


def _format_size(bytes_val: int) -> str:
    """Format file size."""
    if bytes_val < 1024:
        return f"{bytes_val} B"
    elif bytes_val < 1024 * 1024:
        return f"{bytes_val / 1024:.1f} KB"
    elif bytes_val < 1024 * 1024 * 1024:
        return f"{bytes_val / (1024 * 1024):.1f} MB"
    else:
        return f"{bytes_val / (1024 * 1024 * 1024):.2f} GB"


def _format_eta(seconds: int) -> str:
    """Format ETA."""
    if seconds < 0:
        return "Unknown"
    if seconds < 60:
        return f"{seconds}s"
    elif seconds < 3600:
        return f"{seconds // 60}m {seconds % 60}s"
    else:
        hours = seconds // 3600
        minutes = (seconds % 3600) // 60
        return f"{hours}h {minutes}m"


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host=config.host, port=config.port)
