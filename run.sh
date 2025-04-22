#!/bin/bash

# Determine if we're in production or development
if [ "$FLASK_ENV" = "production" ] || [ "$IS_SPACES" = "True" ]; then
    echo "Starting TTS Arena in production mode with gunicorn..."
    # Use optimal gunicorn settings for 2 vCPU
    gunicorn app:app \
        --bind 0.0.0.0:${PORT:-7860} \
        --workers 5 \
        --threads 2 \
        --worker-class gevent \
        --worker-connections 1000 \
        --timeout 120 \
        --keepalive 5 \
        --max-requests 1000 \
        --max-requests-jitter 50 \
        --preload
else
    echo "Starting TTS Arena in development mode..."
    python app.py
fi 