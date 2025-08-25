import os, traceback, socket, subprocess, shlex
from urllib.parse import quote
from urllib.error import URLError

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, FileResponse, PlainTextResponse

from pytubefix import YouTube

app = FastAPI(title="YT Downloader (FastAPI)")

# CORS: allow calls from your extension
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Persistent dir on Spaces, fallback to /tmp
root = "/data" if os.path.isdir("/data") else "/tmp"
DOWNLOAD_DIR = os.path.join(root, "youtube_downloads")
os.makedirs(DOWNLOAD_DIR, exist_ok=True)

def _silent_progress(*_args, **_kwargs):  # avoid unicode progress bar logs
    pass

def download_with_pytube(url: str):
    yt = YouTube(url, on_progress_callback=_silent_progress)
    stream = yt.streams.get_highest_resolution()
    if stream is None or not getattr(stream, "is_progressive", False):
        stream = (yt.streams.filter(progressive=True, file_extension="mp4")
                  .order_by("resolution").desc().first())
    if stream is None:
        raise RuntimeError("No progressive MP4 stream found.")
    fp = stream.download(output_path=DOWNLOAD_DIR)
    title = yt.title or os.path.basename(fp)
    return os.path.abspath(fp), title

def download_with_ytdlp(url: str):
    """
    Robust fallback using yt-dlp + ffmpeg (handles DASH, merges A/V).
    Outputs MP4 to DOWNLOAD_DIR.
    """
    outtmpl = os.path.join(DOWNLOAD_DIR, "%(title).200B.%(ext)s")
    # Best video+audio; merge to mp4; geo-bypass; sane timeout
    cmd = f'yt-dlp -f "bv*+ba/b" --merge-output-format mp4 --geo-bypass --socket-timeout 15 -o {shlex.quote(outtmpl)} {shlex.quote(url)}'
    res = subprocess.run(cmd, shell=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    if res.returncode != 0:
        raise RuntimeError(f"yt-dlp failed:\n{res.stdout[-1000:]}")
    # pick newest file as the result
    newest = max(
        (os.path.join(DOWNLOAD_DIR, f) for f in os.listdir(DOWNLOAD_DIR)),
        key=lambda p: os.path.getmtime(p)
    )
    title = os.path.splitext(os.path.basename(newest))[0]
    return os.path.abspath(newest), title

def rel_file(abs_path: str) -> str:
    return f"/file={quote(abs_path)}"

@app.get("/", response_class=PlainTextResponse)
async def root_ok():
    return "OK. POST /download {url} or POST /api/predict/download {data:[url]}. You can also GET /download?url=..."

@app.get("/health")
async def health():
    return {"ok": True}

@app.get("/diag")
async def diag():
    """
    Quick DNS/TCP diagnostic from inside the container.
    """
    info = {}
    try:
        info["getaddrinfo"] = socket.getaddrinfo("www.youtube.com", 443)
        s = socket.create_connection(("www.youtube.com", 443), timeout=3.0)
        s.close()
        info["tcp443"] = "ok"
    except Exception as e:
        info["error"] = f"{type(e).__name__}: {e}"
    return info

def _safe_download(url: str):
    """
    Try pytube first (progressive stream). If it raises a URLError (DNS/TLS) or
    any other error, fallback to yt-dlp which is generally more robust.
    """
    try:
        return download_with_pytube(url)
    except URLError:
        return download_with_ytdlp(url)
    except Exception:
        return download_with_ytdlp(url)

@app.post("/download")
async def download_plain(payload: dict):
    try:
        url = (payload.get("url") or "").strip()
        if not url:
            return JSONResponse({"error": "No URL provided"}, status_code=400)
        fp, title = _safe_download(url)
        return {"title": title, "file": fp, "public_url": rel_file(fp),
                "status": "Downloaded successfully"}
    except Exception as e:
        traceback.print_exc()
        return JSONResponse({"error": f"{type(e).__name__}: {e}"}, status_code=500)

@app.get("/download")
async def download_plain_get(url: str = ""):
    try:
        url = (url or "").strip()
        if not url:
            return JSONResponse({"error": "No URL provided"}, status_code=400)
        fp, title = _safe_download(url)
        return {"title": title, "file": fp, "public_url": rel_file(fp),
                "status": "Downloaded successfully (GET)"}
    except Exception as e:
        traceback.print_exc()
        return JSONResponse({"error": f"{type(e).__name__}: {e}"}, status_code=500)

@app.post("/api/predict/download")
async def download_gradio(payload: dict):
    """
    Accepts:
      {"data": ["<youtube-url>"]}  or  {"url": "<youtube-url>"}
    Returns:
      {"data": [ {name, path, url}, "Status", "/file=<encoded-abs-path>" ]}
    """
    try:
        url = None
        if isinstance(payload.get("data"), list) and payload["data"]:
            url = payload["data"][0]
        if not url and "url" in payload:
            url = payload["url"]
        url = (url or "").strip()
        if not url:
            return JSONResponse({"error":"Bad payload. Use {data:[<url>]} or {url:<url>}","data":[]}, status_code=400)

        fp, title = _safe_download(url)
        info = {"name": os.path.basename(fp), "path": fp, "url": rel_file(fp)}
        return {"data": [info, f"Downloaded '{title}' successfully!", rel_file(fp)]}
    except Exception as e:
        traceback.print_exc()
        return JSONResponse({"error": f"{type(e).__name__}: {e}",
                             "data":[None, f"Error: {type(e).__name__}: {e}"]}, status_code=500)

def _norm(p: str) -> str:
    if not p: raise HTTPException(status_code=400, detail="Missing file path")
    if not p.startswith("/"): p = "/" + p
    ap = os.path.abspath(p)
    if not ap.startswith(os.path.abspath(DOWNLOAD_DIR)):
        raise HTTPException(status_code=403, detail="Forbidden path")
    if not os.path.exists(ap):
        raise HTTPException(status_code=404, detail="File not found")
    return ap

@app.get("/file={abs_path:path}")
async def serve_file_legacy(abs_path: str):
    ap = _norm(abs_path)
    return FileResponse(ap, filename=os.path.basename(ap), media_type="application/octet-stream")

@app.get("/file/{abs_path:path}")
async def serve_file_alt(abs_path: str):
    ap = _norm(abs_path)
    return FileResponse(ap, filename=os.path.basename(ap), media_type="application/octet-stream")
