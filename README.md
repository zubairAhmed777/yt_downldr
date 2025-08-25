---
title: YouTube Downloader API
emoji: 🎬
colorFrom: blue
colorTo: purple
sdk: docker
pinned: false
---

FastAPI backend for YouTube downloads (pytubefix). Endpoints:
- `GET /`            → plain text OK
- `GET /health`      → {"ok": true}
- `POST /api/predict/download` → body: {"data": ["<youtube-url>"]}
