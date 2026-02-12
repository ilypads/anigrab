import asyncio
from dataclasses import dataclass
from enum import Enum
from typing import AsyncIterator

import aiohttp

from config import config


class QBTStatus(Enum):
    DOWNLOADING = "downloading"
    SEEDING = "seeding"
    PAUSED = "paused"
    QUEUED = "queued"
    CHECKING = "checking"
    ERROR = "error"
    COMPLETE = "complete"
    UNKNOWN = "unknown"


@dataclass
class TorrentInfo:
    name: str
    hash: str
    progress: float  # 0.0 to 1.0
    status: QBTStatus
    download_speed: int  # bytes/sec
    size: int  # bytes
    downloaded: int  # bytes
    eta: int  # seconds, -1 if unknown
    message: str = ""
    content_path: str = ""  # Full path to torrent content


@dataclass
class QBTState:
    connected: bool
    message: str
    version: str | None = None


class QBittorrentClient:
    """Client for qBittorrent Web API."""

    def __init__(self):
        self.base_url = f"http://{config.qbt_host}:{config.qbt_port}/api/v2"
        self._session: aiohttp.ClientSession | None = None
        self._authenticated = False

    async def _get_session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession()
        return self._session

    async def close(self):
        if self._session and not self._session.closed:
            await self._session.close()

    async def reset(self):
        """Reset client state after qBittorrent restart."""
        await self.close()
        self._session = None
        self._authenticated = False

    async def _login(self) -> bool:
        """Authenticate with qBittorrent."""
        session = await self._get_session()
        try:
            async with session.post(
                f"{self.base_url}/auth/login",
                data={
                    "username": config.qbt_username,
                    "password": config.qbt_password
                }
            ) as resp:
                text = await resp.text()
                self._authenticated = text.strip().lower() == "ok."
                return self._authenticated
        except Exception:
            return False

    async def get_status(self) -> QBTState:
        """Check qBittorrent connection status."""
        session = await self._get_session()
        try:
            if not self._authenticated:
                if not await self._login():
                    return QBTState(
                        connected=False,
                        message="Failed to authenticate with qBittorrent"
                    )

            async with session.get(f"{self.base_url}/app/version") as resp:
                if resp.status == 200:
                    version = await resp.text()
                    return QBTState(
                        connected=True,
                        version=version.strip(),
                        message=f"Connected to qBittorrent {version.strip()}"
                    )
                else:
                    self._authenticated = False
                    return QBTState(
                        connected=False,
                        message=f"qBittorrent returned status {resp.status}"
                    )
        except aiohttp.ClientConnectorError:
            return QBTState(
                connected=False,
                message="Cannot connect to qBittorrent. Is it running?"
            )
        except Exception as e:
            return QBTState(
                connected=False,
                message=f"Error connecting to qBittorrent: {e}"
            )

    async def get_dht_nodes(self) -> int:
        """Get the number of DHT nodes connected."""
        session = await self._get_session()
        try:
            if not self._authenticated:
                if not await self._login():
                    return 0

            async with session.get(f"{self.base_url}/transfer/info") as resp:
                if resp.status == 200:
                    data = await resp.json()
                    return data.get("dht_nodes", 0)
        except Exception:
            pass
        return 0

    async def add_torrent(self, url: str, retry: bool = True) -> tuple[bool, str, str | None]:
        """
        Add a torrent by URL (magnet or .torrent URL).
        Returns (success, message, torrent_hash).
        """
        import urllib.parse
        # Ensure URL is fully decoded (qBittorrent expects clean URLs)
        clean_url = urllib.parse.unquote(url)

        session = await self._get_session()
        try:
            if not self._authenticated:
                if not await self._login():
                    return False, "Failed to authenticate with qBittorrent", None

            data = aiohttp.FormData()
            data.add_field("urls", clean_url)
            data.add_field("paused", "false")
            if config.download_dir:
                data.add_field("savepath", config.download_dir)

            async with session.post(
                f"{self.base_url}/torrents/add",
                data=data
            ) as resp:
                if resp.status == 200:
                    text = await resp.text()
                    if "ok" in text.lower() or text.strip() == "":
                        # Try to get the hash from the magnet link
                        torrent_hash = self._extract_hash(clean_url)
                        return True, "Torrent added successfully", torrent_hash
                    else:
                        return False, f"qBittorrent rejected torrent: {text}", None
                else:
                    return False, f"Failed to add torrent: HTTP {resp.status}", None
        except (aiohttp.ServerDisconnectedError, aiohttp.ClientOSError) as e:
            # Connection lost - reset and retry once
            if retry:
                await self.reset()
                await asyncio.sleep(1)
                return await self.add_torrent(url, retry=False)
            return False, f"Error adding torrent: {e}", None
        except Exception as e:
            return False, f"Error adding torrent: {e}", None

    def _extract_hash(self, url: str) -> str | None:
        """Extract info hash from magnet URL."""
        if "magnet:" in url.lower():
            # Look for btih (BitTorrent Info Hash)
            import re
            match = re.search(r"btih:([a-fA-F0-9]{40})", url)
            if match:
                return match.group(1).lower()
            # Also check for base32 encoded hash
            match = re.search(r"btih:([A-Za-z2-7]{32})", url)
            if match:
                import base64
                try:
                    decoded = base64.b32decode(match.group(1).upper())
                    return decoded.hex().lower()
                except Exception:
                    pass
        return None

    async def delete_torrent(self, torrent_hash: str, delete_files: bool = True) -> tuple[bool, str]:
        """
        Delete a torrent from qBittorrent.
        Returns (success, message).
        """
        session = await self._get_session()
        try:
            if not self._authenticated:
                if not await self._login():
                    return False, "Failed to authenticate with qBittorrent"

            async with session.post(
                f"{self.base_url}/torrents/delete",
                data={
                    "hashes": torrent_hash,
                    "deleteFiles": "true" if delete_files else "false"
                }
            ) as resp:
                if resp.status == 200:
                    return True, "Torrent deleted successfully"
                else:
                    return False, f"Failed to delete torrent: HTTP {resp.status}"
        except Exception as e:
            return False, f"Error deleting torrent: {e}"

    async def get_torrent_info(self, torrent_hash: str) -> TorrentInfo | None:
        """Get info about a specific torrent."""
        session = await self._get_session()
        try:
            if not self._authenticated:
                if not await self._login():
                    return None

            async with session.get(
                f"{self.base_url}/torrents/info",
                params={"hashes": torrent_hash}
            ) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    if data:
                        t = data[0]
                        return TorrentInfo(
                            name=t.get("name", "Unknown"),
                            hash=t.get("hash", torrent_hash),
                            progress=t.get("progress", 0),
                            status=self._map_state(t.get("state", "")),
                            download_speed=t.get("dlspeed", 0),
                            size=t.get("size", 0),
                            downloaded=t.get("downloaded", 0),
                            eta=t.get("eta", -1),
                            content_path=t.get("content_path", "")
                        )
        except Exception:
            pass
        return None

    async def get_active_download(self) -> TorrentInfo | None:
        """Get the first active (downloading) torrent, if any."""
        session = await self._get_session()
        try:
            if not self._authenticated:
                if not await self._login():
                    return None

            # Get all torrents that are currently downloading
            async with session.get(
                f"{self.base_url}/torrents/info",
                params={"filter": "downloading"}
            ) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    if data:
                        # Return the first downloading torrent
                        t = data[0]
                        return TorrentInfo(
                            name=t.get("name", "Unknown"),
                            hash=t.get("hash", ""),
                            progress=t.get("progress", 0),
                            status=self._map_state(t.get("state", "")),
                            download_speed=t.get("dlspeed", 0),
                            size=t.get("size", 0),
                            downloaded=t.get("downloaded", 0),
                            eta=t.get("eta", -1),
                            content_path=t.get("content_path", "")
                        )
        except Exception:
            pass
        return None

    def _map_state(self, state: str) -> QBTStatus:
        """Map qBittorrent state string to QBTStatus enum."""
        state = state.lower()
        if state in ("downloading", "forcedup", "metadl", "allocating", "stalleddl"):
            return QBTStatus.DOWNLOADING
        elif state in ("uploading", "forceup", "stalledup"):
            return QBTStatus.SEEDING
        elif state in ("pauseddl", "pausedup", "paused"):
            return QBTStatus.PAUSED
        elif state in ("stoppeddl", "stoppedup"):
            return QBTStatus.PAUSED
        elif state in ("queueddl", "queuedup"):
            return QBTStatus.QUEUED
        elif state in ("checkingdl", "checkingup", "checkingresumedata"):
            return QBTStatus.CHECKING
        elif state in ("error", "missingfiles"):
            return QBTStatus.ERROR
        else:
            return QBTStatus.UNKNOWN

    async def monitor_torrent(
        self,
        torrent_hash: str,
        interval: float = 1.0
    ) -> AsyncIterator[TorrentInfo]:
        """Monitor torrent progress, yielding updates."""
        last_progress = -1.0
        stall_count = 0
        max_stalls = 5  # Report even if no progress after this many checks

        while True:
            info = await self.get_torrent_info(torrent_hash)
            if info is None:
                await asyncio.sleep(interval)
                stall_count += 1
                if stall_count > 30:  # Wait up to 30 seconds for torrent to appear
                    yield TorrentInfo(
                        name="Unknown",
                        hash=torrent_hash,
                        progress=0,
                        status=QBTStatus.ERROR,
                        download_speed=0,
                        size=0,
                        downloaded=0,
                        eta=-1,
                        message="Torrent not found in qBittorrent"
                    )
                    return
                continue

            # Yield on progress change or periodically
            if info.progress != last_progress or stall_count >= max_stalls:
                yield info
                last_progress = info.progress
                stall_count = 0
            else:
                stall_count += 1

            # Check if complete
            if info.progress >= 1.0 or info.status == QBTStatus.SEEDING:
                info.status = QBTStatus.COMPLETE
                info.message = "Download complete!"
                yield info
                return

            if info.status == QBTStatus.ERROR:
                return

            await asyncio.sleep(interval)


qbittorrent = QBittorrentClient()
