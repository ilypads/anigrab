#!/bin/bash

# Kill any process using port 8080
PID=$(lsof -t -i :9242 2>/dev/null)
if [ -n "$PID" ]; then
    echo "Killing process on port 9242 (PID: $PID)"
    kill -9 $PID 2>/dev/null
    sleep 1
fi

# Activate venv and start server
cd "$(dirname "$0")"
source venv/bin/activate
python server.py
