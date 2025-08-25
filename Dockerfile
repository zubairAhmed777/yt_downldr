FROM python:3.10-slim

# System deps: ffmpeg for muxing, CA certs for TLS, curl for quick checks
RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg ca-certificates curl && \
    update-ca-certificates && \
    rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r /app/requirements.txt

COPY main.py /app/main.py

# Create persistent download dir (Spaces mounts /data if persistence is on)
RUN mkdir -p /data/youtube_downloads

# Respect Spaces' dynamic PORT
EXPOSE 7860
CMD ["bash","-lc","uvicorn main:app --host 0.0.0.0 --port ${PORT:-7860}"]
