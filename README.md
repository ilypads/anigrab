# AniGrab

A local web app for triggering torrent downloads via qBittorrent through Mullvad VPN.

## Prerequisites

- Python 3.11+
- [Mullvad VPN](https://mullvad.net/) installed with CLI (`mullvad` command available)
- [qBittorrent](https://www.qbittorrent.org/) with Web UI enabled

## Setup

### 1. Enable qBittorrent Web UI

1. Open qBittorrent
2. Go to **Tools → Options → Web UI**
3. Check **"Web User Interface (Remote control)"**
4. Set port (default: 8081)
5. Set username and password
6. Click OK

### 2. Install Dependencies

```bash
cd anigrab
python -m venv venv
source venv/bin/activate  # On Windows: venv\Scripts\activate
pip install -r requirements.txt
```

### 3. Configure (Optional)

Set environment variables or edit `config.py`:

```bash
export QBT_HOST=localhost
export QBT_PORT=8081
export QBT_USERNAME=admin
export QBT_PASSWORD=your_password
export ANIGRAB_PORT=8080
export DOWNLOAD_DIR=/path/to/plex/media  # Optional
```

### 4. Run

```bash
python server.py
```

Or with uvicorn directly:

```bash
uvicorn server:app --host 0.0.0.0 --port 8080
```

### 5. Access

Open on your iPhone (same WiFi): `http://<your-computer-ip>:8080`

To find your computer's IP:
```bash
# Linux
ip addr show | grep "inet " | grep -v 127.0.0.1

# macOS
ipconfig getifaddr en0
```

## Usage

1. Copy a nyaa.si URL or magnet link
2. Open AniGrab on your phone
3. Paste the link
4. Review the media info and confirm
5. Watch the download progress

## Add to iPhone Home Screen

1. Open the URL in Safari
2. Tap the Share button
3. Select "Add to Home Screen"
4. Now it works like a native app

## Project Structure

```
anigrab/
├── config.py          # Configuration
├── mullvad.py         # Mullvad VPN integration
├── qbittorrent.py     # qBittorrent Web API client
├── server.py          # FastAPI web server
├── requirements.txt   # Python dependencies
├── templates/
│   └── index.html     # Main HTML page
└── static/
    ├── style.css      # Styles
    └── app.js         # Frontend JavaScript
```

## Running as a Service (Optional)

### systemd (Linux)

Create `/etc/systemd/system/anigrab.service`:

```ini
[Unit]
Description=AniGrab
After=network.target

[Service]
Type=simple
User=your_username
WorkingDirectory=/path/to/anigrab
Environment="QBT_PASSWORD=your_password"
ExecStart=/path/to/anigrab/venv/bin/python server.py
Restart=always

[Install]
WantedBy=multi-user.target
```

Then:
```bash
sudo systemctl enable anigrab
sudo systemctl start anigrab
```
