import asyncio
import json
import os
import re
import time
import urllib.parse
import uuid
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import AsyncIterator

import aiohttp
from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.base import BaseHTTPMiddleware

from config import config
from constants import VIDEO_EXTENSIONS, SUBTITLE_EXTENSIONS, MEDIA_EXTENSIONS
from mullvad import mullvad, MullvadStatus
from qbittorrent import qbittorrent, QBTStatus

app = FastAPI(title="AniGrab")


class NoCacheStaticMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request, call_next):
        response = await call_next(request)
        if request.url.path.startswith("/static/"):
            response.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
        return response


app.add_middleware(NoCacheStaticMiddleware)

# Mount static files
app.mount("/static", StaticFiles(directory="static"), name="static")


# ============== Job Queue System ==============

@dataclass
class QueuedJob:
    """Represents a queued download job."""
    id: str
    url: str
    title: str
    status: str = "pending"  # pending, downloading, complete, error, cancelled
    progress: float = 0.0
    torrent_hash: str | None = None
    folder_path: str | None = None
    error_message: str | None = None
    added_at: float = field(default_factory=time.time)
    started_at: float | None = None
    completed_at: float | None = None

    def to_dict(self):
        return asdict(self)


# Queue state
job_queue: list[QueuedJob] = []
queue_lock = asyncio.Lock()
queue_active = False
queue_task: asyncio.Task | None = None
queue_sse_clients: list[asyncio.Queue] = []


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


# ============== Queue Endpoints ==============

@app.get("/api/queue")
async def get_queue():
    """Get current queue state."""
    async with queue_lock:
        return {
            "success": True,
            "processing": queue_active,
            "jobs": [job.to_dict() for job in job_queue]
        }


@app.post("/api/queue/add")
async def add_to_queue(request: Request):
    """Add a new job to the queue."""
    body = await request.json()
    url = body.get("url", "")
    title = body.get("title", "")

    if not url:
        return {"success": False, "error": "No URL provided"}

    # Auto-detect title from URL if not provided
    if not title:
        if "nyaa.si" in url:
            # Try to extract title from nyaa URL
            title = url.split("/")[-1] if "/" in url else "Unknown"
        else:
            title = "Queued Download"

    job = QueuedJob(
        id=str(uuid.uuid4()),
        url=url,
        title=title
    )

    async with queue_lock:
        job_queue.append(job)

    # Broadcast update
    await broadcast_queue_update("job_added", job)

    return {"success": True, "job": job.to_dict()}


@app.post("/api/queue/remove")
async def remove_from_queue(request: Request):
    """Remove a job from the queue."""
    body = await request.json()
    job_id = body.get("id", "")

    if not job_id:
        return {"success": False, "error": "No job ID provided"}

    async with queue_lock:
        for i, job in enumerate(job_queue):
            if job.id == job_id:
                if job.status == "downloading":
                    return {"success": False, "error": "Cannot remove job that is currently downloading"}
                removed = job_queue.pop(i)
                await broadcast_queue_update("job_removed", removed)
                return {"success": True, "removed": removed.to_dict()}

    return {"success": False, "error": "Job not found"}


@app.post("/api/queue/retry")
async def retry_job(request: Request):
    """Retry a failed or cancelled job by resetting it to pending."""
    body = await request.json()
    job_id = body.get("id", "")

    if not job_id:
        return {"success": False, "error": "No job ID provided"}

    async with queue_lock:
        for job in job_queue:
            if job.id == job_id:
                if job.status not in ("error", "cancelled"):
                    return {"success": False, "error": "Can only retry failed or cancelled jobs"}
                job.status = "pending"
                job.progress = 0
                job.error_message = None
                job.started_at = None
                job.completed_at = None
                job.torrent_hash = None
                await broadcast_queue_update("job_retry", job)
                return {"success": True, "job": job.to_dict()}

    return {"success": False, "error": "Job not found"}


@app.post("/api/queue/clear")
async def clear_queue():
    """Clear completed and failed jobs from the queue."""
    async with queue_lock:
        before_count = len(job_queue)
        job_queue[:] = [j for j in job_queue if j.status in ("pending", "downloading")]
        removed_count = before_count - len(job_queue)

    await broadcast_queue_update("queue_cleared", {"removed": removed_count})
    return {"success": True, "removed": removed_count}


@app.post("/api/queue/start")
async def start_queue():
    """Start processing the queue."""
    global queue_active, queue_task

    if queue_active:
        return {"success": False, "error": "Queue is already running"}

    queue_active = True
    queue_task = asyncio.create_task(process_queue())

    return {"success": True, "message": "Queue started"}


@app.post("/api/queue/stop")
async def stop_queue():
    """Stop processing the queue (finish current job first)."""
    global queue_active

    queue_active = False
    return {"success": True, "message": "Queue will stop after current job completes"}


@app.get("/api/queue/stream")
async def queue_stream():
    """SSE stream for queue updates."""
    client_queue = asyncio.Queue()
    queue_sse_clients.append(client_queue)

    async def event_generator():
        try:
            # Send initial state as queue_update so frontend handles it consistently
            async with queue_lock:
                yield sse_message("queue_update", {
                    "processing": queue_active,
                    "jobs": [job.to_dict() for job in job_queue]
                })

            # Stream updates
            while True:
                try:
                    message = await asyncio.wait_for(client_queue.get(), timeout=30)
                    yield message
                except asyncio.TimeoutError:
                    # Send keepalive
                    yield sse_message("ping", {})
        finally:
            queue_sse_clients.remove(client_queue)

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        }
    )


async def broadcast_queue_update(event_type: str, data):
    """Broadcast an update to all connected queue SSE clients."""
    # Always send full queue state so frontend can simply re-render
    # Note: We read without lock here - caller may or may not hold the lock.
    # This is safe since we just need a snapshot for the broadcast.
    queue_state = {
        "jobs": [job.to_dict() for job in job_queue],
        "processing": queue_active,
        "event": event_type  # Include original event for debugging
    }
    message = sse_message("queue_update", queue_state)
    for client in queue_sse_clients:
        try:
            await client.put(message)
        except Exception:
            pass


async def process_queue():
    """Background task that processes queued downloads sequentially."""
    global queue_active

    while queue_active:
        # Find next pending job
        job = None
        async with queue_lock:
            for j in job_queue:
                if j.status == "pending":
                    job = j
                    job.status = "downloading"
                    job.started_at = time.time()
                    break

        if not job:
            # No pending jobs, stop the queue
            queue_active = False
            await broadcast_queue_update("queue_stopped", {"reason": "empty"})
            break

        await broadcast_queue_update("job_started", job)

        try:
            # Process this job using existing download logic
            async for event_data in _download_generator(job.url):
                # Parse the SSE event
                if isinstance(event_data, str):
                    # Parse SSE format
                    lines = event_data.strip().split('\n')
                    event_type = "unknown"
                    data = {}
                    for line in lines:
                        if line.startswith("event:"):
                            event_type = line[6:].strip()
                        elif line.startswith("data:"):
                            try:
                                data = json.loads(line[5:].strip())
                            except json.JSONDecodeError:
                                data = {"raw": line[5:].strip()}

                    # Update job based on event
                    if event_type == "progress":
                        job.progress = data.get("progress", 0)
                        if "hash" in data:
                            job.torrent_hash = data["hash"]
                        await broadcast_queue_update("job_progress", job)

                    elif event_type == "complete":
                        job.status = "complete"
                        job.progress = 100
                        job.completed_at = time.time()
                        job.folder_path = data.get("folder_path")
                        await broadcast_queue_update("job_complete", job)

                    elif event_type == "error":
                        job.status = "error"
                        job.error_message = data.get("message", "Unknown error")
                        job.completed_at = time.time()
                        await broadcast_queue_update("job_error", job)

        except Exception as e:
            job.status = "error"
            job.error_message = str(e)
            job.completed_at = time.time()
            await broadcast_queue_update("job_error", job)

        # Small delay between jobs
        await asyncio.sleep(2)

    queue_active = False


# ============== Helper Functions ==============

# (library/renaming endpoints removed)



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
