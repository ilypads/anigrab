import asyncio
import subprocess
from dataclasses import dataclass
from enum import Enum
from typing import AsyncIterator


class MullvadStatus(Enum):
    CONNECTED = "connected"
    CONNECTING = "connecting"
    DISCONNECTED = "disconnected"
    ERROR = "error"


@dataclass
class MullvadState:
    status: MullvadStatus
    server: str | None = None
    ip: str | None = None
    message: str = ""


class MullvadClient:
    """Wrapper around the Mullvad CLI."""

    async def get_status(self) -> MullvadState:
        """Get current Mullvad connection status."""
        try:
            proc = await asyncio.create_subprocess_exec(
                "mullvad", "status",
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE
            )
            stdout, stderr = await proc.communicate()
            output = stdout.decode().strip().lower()

            if output.startswith("connected"):
                # Parse server info from output
                lines = stdout.decode().strip().split("\n")
                server = None
                ip = None
                for line in lines:
                    if "relay:" in line.lower():
                        server = line.split(":")[-1].strip()
                    if "ipv4:" in line.lower():
                        ip = line.split(":")[-1].strip()
                return MullvadState(
                    status=MullvadStatus.CONNECTED,
                    server=server,
                    ip=ip,
                    message="Connected to Mullvad VPN"
                )
            elif "connecting" in output:
                return MullvadState(
                    status=MullvadStatus.CONNECTING,
                    message="Connecting to Mullvad VPN..."
                )
            else:
                return MullvadState(
                    status=MullvadStatus.DISCONNECTED,
                    message="Disconnected from Mullvad VPN"
                )
        except FileNotFoundError:
            return MullvadState(
                status=MullvadStatus.ERROR,
                message="Mullvad CLI not found. Is it installed?"
            )
        except Exception as e:
            return MullvadState(
                status=MullvadStatus.ERROR,
                message=f"Error checking Mullvad status: {e}"
            )

    async def connect(self) -> AsyncIterator[MullvadState]:
        """Connect to Mullvad VPN, yielding status updates."""
        # Check if already connected
        state = await self.get_status()
        if state.status == MullvadStatus.CONNECTED:
            yield state
            return

        # Initiate connection
        yield MullvadState(
            status=MullvadStatus.CONNECTING,
            message="Initiating Mullvad connection..."
        )

        try:
            proc = await asyncio.create_subprocess_exec(
                "mullvad", "connect",
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE
            )
            await proc.communicate()

            # Wait for connection with timeout
            for _ in range(30):  # 30 second timeout
                await asyncio.sleep(1)
                state = await self.get_status()
                yield state
                if state.status == MullvadStatus.CONNECTED:
                    return
                if state.status == MullvadStatus.ERROR:
                    return

            yield MullvadState(
                status=MullvadStatus.ERROR,
                message="Connection timeout - Mullvad failed to connect"
            )
        except Exception as e:
            yield MullvadState(
                status=MullvadStatus.ERROR,
                message=f"Failed to connect: {e}"
            )

    async def disconnect(self) -> MullvadState:
        """Disconnect from Mullvad VPN."""
        try:
            proc = await asyncio.create_subprocess_exec(
                "mullvad", "disconnect",
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE
            )
            await proc.communicate()
            await asyncio.sleep(1)
            return await self.get_status()
        except Exception as e:
            return MullvadState(
                status=MullvadStatus.ERROR,
                message=f"Failed to disconnect: {e}"
            )


mullvad = MullvadClient()
