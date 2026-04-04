#!/bin/bash
# Start LM Studio server and load the vision model.
# Called by com.faceguard.lmstudio.plist at login.

LMS="${LMS:-/usr/local/bin/lms}"
MODEL="${LM_MODEL:-gemma-4-e2b-it}"
PORT="${LM_PORT:-1234}"

echo "[lmstudio-start] Starting server on port $PORT..."
"$LMS" server start --port "$PORT" &
SERVER_PID=$!

# Wait for server to be ready
for i in $(seq 1 15); do
    if curl -s "http://localhost:$PORT/v1/models" > /dev/null 2>&1; then
        echo "[lmstudio-start] Server ready after ${i}s"
        break
    fi
    echo "[lmstudio-start] Waiting for server... ($i/15)"
    sleep 1
done

# Load the model
echo "[lmstudio-start] Loading model: $MODEL"
curl -s http://localhost:$PORT/api/v1/models/load \
    -H "Content-Type: application/json" \
    -d "{
        \"model\": \"$MODEL\",
        \"context_length\": 8192,
        \"flash_attention\": true
    }"

echo "[lmstudio-start] Done."
wait $SERVER_PID