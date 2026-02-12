import os
from dataclasses import dataclass
from pathlib import Path

# Load .env file if it exists
env_file = Path(__file__).parent / ".env"
if env_file.exists():
    with open(env_file) as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                key, value = line.split("=", 1)
                os.environ.setdefault(key.strip(), value.strip())


@dataclass
class Config:
    # Web server
    host: str = "0.0.0.0"
    port: int = 9242

    # qBittorrent Web API
    qbt_host: str = "localhost"
    qbt_port: int = 8081
    qbt_username: str = "admin"
    qbt_password: str = "adminadmin"

    # Download directory (where qBittorrent saves files)
    download_dir: str = ""

    # Path to qBittorrent executable (for restart functionality)
    qbt_executable: str = ""

    @classmethod
    def from_env(cls) -> "Config":
        # Try to find qBittorrent executable
        qbt_exe = os.getenv("QBT_EXECUTABLE", "")
        if not qbt_exe:
            # Check common locations
            home = os.path.expanduser("~")
            candidates = [
                f"{home}/Applications/qbittorrent.AppImage",
                f"{home}/.local/bin/qbittorrent",
                "/usr/bin/qbittorrent",
                "qbittorrent",
            ]
            for candidate in candidates:
                if os.path.isfile(candidate):
                    qbt_exe = candidate
                    break

        return cls(
            host=os.getenv("ANIGRAB_HOST", "0.0.0.0"),
            port=int(os.getenv("ANIGRAB_PORT", "9242")),
            qbt_host=os.getenv("QBT_HOST", "localhost"),
            qbt_port=int(os.getenv("QBT_PORT", "8081")),
            qbt_username=os.getenv("QBT_USERNAME", "admin"),
            qbt_password=os.getenv("QBT_PASSWORD", "adminadmin"),
            download_dir=os.getenv("DOWNLOAD_DIR", ""),
            qbt_executable=qbt_exe,
        )


config = Config.from_env()
