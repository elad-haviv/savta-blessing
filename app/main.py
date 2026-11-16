"""Savta Blessing - private web app (BYOK).

The server only does media work (ffmpeg, frame ranking) and file hosting.
It never sees or stores an OpenRouter key: the browser calls OpenRouter directly.
"""
import hashlib
import hmac
import json
import os
import re
import secrets
import shutil
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from fastapi import Depends, FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from . import pipeline as pl

DATA_DIR = Path(os.environ.get("DATA_DIR", "/data")).resolve()
APP_PASSWORD = os.environ.get("APP_PASSWORD", "")
MAX_UPLOAD_MB = int(os.environ.get("MAX_UPLOAD_MB", "500"))
JOB_TTL_DAYS = int(os.environ.get("JOB_TTL_DAYS", "14"))
PUBLIC_BASE_URL = os.environ.get("PUBLIC_BASE_URL", "").rstrip("/")
SIGN_KEY = (os.environ.get("MEDIA_SIGN_KEY") or APP_PASSWORD or secrets.token_hex(16)).encode()

ALLOWED_EXT = {".mp4", ".mov", ".mkv", ".avi", ".webm", ".m4v", ".3gp", ".mpg", ".mpeg", ".wmv", ".flv"}
VOICE_MODELS = [
    {"id": "fish-audio/s2.1-pro", "label": "Fish Audio S2.1 Pro (ברירת מחדל)"},
    {"id": "fish-audio/drama-3", "label": "Fish Audio Drama 3"},
    {"id": "heygen/heygen-voice-1", "label": "HeyGen Voice"},
]
VIDEO_MODELS = [
    {"id": "heygen/avatar-iv", "label": "HeyGen Avatar IV (סנכרון שפתיים, ברירת מחדל)"},
    {"id": "bytedance/seedance-2.0", "label": "ByteDance Seedance 2.0"},
]
STT_MODEL = os.environ.get("STT_MODEL", "elevenlabs/scribe-v2")
ANALYSIS_MODEL = os.environ.get("ANALYSIS_MODEL", "google/gemini-3.8-flash")

# The page may talk only to this server and to OpenRouter: this is what keeps the
# user's key from being sent anywhere else, even if a script were ever injected.
CSP = ("default-src 'none'; script-src 'self' 'unsafe-inline'; style-src 'self' 'unsafe-inline'; "
       "img-src 'self' data: blob:; media-src 'self' blob: data: https:; "
       "connect-src 'self' https://openrouter.ai; base-uri 'none'; form-action 'none'; "
       "frame-ancestors 'none'")

app = FastAPI(title="Savta Blessing", docs_url=None, redoc_url=None, openapi_url=None)
security = HTTPBasic(auto_error=False)
pool = ThreadPoolExecutor(max_workers=2)
locks: dict[str, threading.Lock] = {}
locks_guard = threading.Lock()
SAFE_NAME = re.compile(r"^[A-Za-z0-9_.-]+$")
HIDDEN = ("state.json",)


# ------------------------------------------------------------------ auth

COOKIE = "savta_auth"
COOKIE_TTL = 30 * 24 * 3600

LOGIN_HTML = """<!doctype html><html lang="he" dir="rtl"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>כניסה</title>
<style>body{font-family:system-ui,sans-serif;background:#16181d;color:#eee;display:flex;min-height:100vh;align-items:center;justify-content:center;margin:0}
form{display:flex;flex-direction:column;gap:12px;width:min(320px,90vw)}input,button{font-size:18px;padding:12px;border-radius:8px;border:1px solid #444;background:#23262d;color:#eee}
button{background:#3b82f6;border:0;font-weight:600}.e{color:#f87171;min-height:1.2em}</style></head><body>
<form method="post" action="/login"><h2>ברכת סבתא</h2><input type="password" name="password" placeholder="סיסמה" autofocus required>
<button type="submit">כניסה</button><div class="e">__ERR__</div></form></body></html>"""
LOGIN_CSP = "default-src 'none'; style-src 'unsafe-inline'; form-action 'self'; base-uri 'none'; frame-ancestors 'none'"


def _cookie_value(exp: int) -> str:
    return f"{exp}.{hmac.new(SIGN_KEY, f'auth/{exp}'.encode(), hashlib.sha256).hexdigest()}"


def _cookie_ok(v: str | None) -> bool:
    try:
        exp, sig = (v or "").split(".", 1)
        good = hmac.new(SIGN_KEY, f"auth/{exp}".encode(), hashlib.sha256).hexdigest()
        return int(exp) > time.time() and hmac.compare_digest(sig, good)
    except Exception:
        return False


def _login_page(err: str = "", status: int = 401) -> HTMLResponse:
    return HTMLResponse(LOGIN_HTML.replace("__ERR__", err), status_code=status,
                        headers={"Content-Security-Policy": LOGIN_CSP, "Cache-Control": "no-store"})


def require_auth(request: Request, creds: HTTPBasicCredentials = Depends(security)):
    if not APP_PASSWORD:
        raise HTTPException(503, "APP_PASSWORD לא הוגדר בשרת - האפליקציה חסומה עד שיוגדר")
    if creds is not None and hmac.compare_digest(creds.password.encode(), APP_PASSWORD.encode()):
        return
    if _cookie_ok(request.cookies.get(COOKIE)):
        return
    if request.method == "GET" and "text/html" in request.headers.get("accept", ""):
        raise HTTPException(401, headers={"X-Login": "1"})
    raise HTTPException(401, "נדרשת סיסמה")


@app.exception_handler(HTTPException)
async def _http_exc(request: Request, exc: HTTPException):
    if exc.status_code == 401 and exc.headers and exc.headers.get("X-Login"):
        return _login_page()
    return JSONResponse({"detail": exc.detail}, status_code=exc.status_code)


@app.post("/login")
def login(password: str = Form("")):
    if not APP_PASSWORD or not hmac.compare_digest(password.encode(), APP_PASSWORD.encode()):
        return _login_page("סיסמה שגויה", 401)
    r = RedirectResponse("/", status_code=303)
    r.set_cookie(COOKIE, _cookie_value(int(time.time()) + COOKIE_TTL), max_age=COOKIE_TTL,
                 httponly=True, secure=True, samesite="strict", path="/")
    return r


# ------------------------------------------------------------------ job state

def jobdir(job_id: str) -> Path:
    if not re.fullmatch(r"[0-9a-f]{32}", job_id):
        raise HTTPException(404, "ג'וב לא נמצא")
    d = DATA_DIR / "jobs" / job_id
    if not d.is_dir():
        raise HTTPException(404, "ג'וב לא נמצא")
    return d


def _lock(job_id: str) -> threading.Lock:
    with locks_guard:
        return locks.setdefault(job_id, threading.Lock())


def load(job_id: str) -> dict:
    return json.loads((jobdir(job_id) / "state.json").read_text("utf-8"))


def mutate(job_id: str, fn=None, **fields) -> dict:
    with _lock(job_id):
        p = jobdir(job_id) / "state.json"
        st = json.loads(p.read_text("utf-8"))
        st.update(fields)
        if fn:
            fn(st)
        tmp = p.with_suffix(".tmp")
        tmp.write_text(json.dumps(st, ensure_ascii=False), "utf-8")
        tmp.replace(p)
        return st


# ------------------------------------------------------------------ files + signed links

def sign(job_id: str, name: str, ttl: int = 3 * 3600) -> str:
    exp = int(time.time()) + ttl
    sig = hmac.new(SIGN_KEY, f"{job_id}/{name}/{exp}".encode(), hashlib.sha256).hexdigest()
    return f"{PUBLIC_BASE_URL}/m/{job_id}/{name}?exp={exp}&sig={sig}"


def _file(job_id: str, name: str) -> FileResponse:
    if not SAFE_NAME.match(name) or name in HIDDEN or name.startswith("source") or name.startswith("."):
        raise HTTPException(404)
    p = jobdir(job_id) / name
    if not p.is_file():
        raise HTTPException(404)
    return FileResponse(p)


@app.get("/m/{job_id}/{name}")
def signed_media(job_id: str, name: str, exp: int, sig: str):
    """Unauthenticated, signed, time-limited link so a video provider can fetch a still or audio file."""
    good = hmac.new(SIGN_KEY, f"{job_id}/{name}/{exp}".encode(), hashlib.sha256).hexdigest()
    if exp < time.time() or not hmac.compare_digest(good, sig):
        raise HTTPException(403, "קישור לא תקף")
    if not name.startswith(("frame_", "stage_")):
        raise HTTPException(404)
    return _file(job_id, name)


# ------------------------------------------------------------------ extraction worker

def extract(job_id: str):
    d = jobdir(job_id)
    src = next(d.glob("source.*"))
    try:
        mutate(job_id, status="analyzing", step="מחלץ אודיו מהסרטון")
        if not pl.has_audio(src):
            raise pl.PipelineError("בסרטון אין רצועת קול")
        duration = pl.probe_duration(src)
        pl.extract_audio(src, d)
        mutate(job_id, duration=round(duration, 1), step="בוחר פריימים של הפנים")
        frames = pl.extract_and_rank_frames(src, d, duration)
        mutate(job_id, frames=frames, status="ready", step="")
    except Exception as e:  # noqa: BLE001 - surface any failure to the UI
        mutate(job_id, status="error", error=str(e) or e.__class__.__name__, step="")


# ------------------------------------------------------------------ API

@app.get("/api/config", dependencies=[Depends(require_auth)])
def config():
    return {"voice_models": VOICE_MODELS, "video_models": VIDEO_MODELS,
            "stt_model": STT_MODEL, "analysis_model": ANALYSIS_MODEL,
            "public_url_set": bool(PUBLIC_BASE_URL), "max_upload_mb": MAX_UPLOAD_MB}


@app.post("/api/jobs", dependencies=[Depends(require_auth)])
async def create_job(file: UploadFile = File(...)):
    ext = Path(file.filename or "").suffix.lower()
    if ext not in ALLOWED_EXT:
        raise HTTPException(400, f"סוג קובץ לא נתמך ({ext or 'ללא סיומת'}). נתמכים: {', '.join(sorted(ALLOWED_EXT))}")
    job_id = uuid.uuid4().hex
    d = DATA_DIR / "jobs" / job_id
    d.mkdir(parents=True)
    dest = d / f"source{ext}"
    size, limit = 0, MAX_UPLOAD_MB * 1024 * 1024
    with dest.open("wb") as fh:
        while chunk := await file.read(1024 * 1024):
            size += len(chunk)
            if size > limit:
                fh.close()
                shutil.rmtree(d, ignore_errors=True)
                raise HTTPException(413, f"הקובץ גדול מ-{MAX_UPLOAD_MB}MB")
            fh.write(chunk)
    state = {"id": job_id, "created": int(time.time()), "filename": file.filename,
             "status": "queued", "step": "ממתין"}
    (d / "state.json").write_text(json.dumps(state, ensure_ascii=False), "utf-8")
    pool.submit(extract, job_id)
    return {"id": job_id}


@app.get("/api/jobs/{job_id}", dependencies=[Depends(require_auth)])
def get_job(job_id: str):
    return load(job_id)


class Segment(BaseModel):
    start: float
    end: float
    text: str = ""
    speaker: str | None = None


class SuggestReq(BaseModel):
    segments: list[Segment] = Field(max_length=5000)


@app.post("/api/jobs/{job_id}/suggest", dependencies=[Depends(require_auth)])
def suggest(job_id: str, req: SuggestReq):
    """Pick clean single-speaker windows from a transcript the browser produced."""
    st = load(job_id)
    segs = [s.model_dump() for s in req.segments if s.text.strip()]
    return {"suggestions": pl.suggest_samples(segs, st.get("duration", 0.0))}


class ClipReq(BaseModel):
    start: float = Field(ge=0)
    end: float
    fmt: str = "wav"


@app.post("/api/jobs/{job_id}/clip", dependencies=[Depends(require_auth)])
def clip(job_id: str, req: ClipReq):
    st = load(job_id)
    if st["status"] != "ready":
        raise HTTPException(409, "הניתוח עדיין לא הסתיים")
    if req.fmt not in ("wav", "mp3"):
        raise HTTPException(400, "פורמט לא נתמך")
    if not (0 <= req.start < req.end <= st["duration"] + 0.5):
        raise HTTPException(400, "טווח לא תקין")
    if not 3 <= req.end - req.start <= 90:
        raise HTTPException(400, "הקטע צריך להיות בין 3 ל-90 שניות (לדגימת קול מומלץ 10-30)")
    name = f"clip_{uuid.uuid4().hex[:8]}.{req.fmt}"
    d = jobdir(job_id)
    try:
        pl.cut_audio(d / "full.wav", req.start, req.end, d / name)
    except pl.PipelineError as e:
        raise HTTPException(400, str(e))
    return {"name": name, "seconds": round(req.end - req.start, 1)}


class FrameReq(BaseModel):
    t: float = Field(ge=0)


@app.post("/api/jobs/{job_id}/frame", dependencies=[Depends(require_auth)])
def add_frame(job_id: str, req: FrameReq):
    """Manually pull a still at a chosen second (when face detection finds nothing usable)."""
    st = load(job_id)
    if st["status"] != "ready":
        raise HTTPException(409, "הניתוח עדיין לא הסתיים")
    if req.t > st["duration"]:
        raise HTTPException(400, "הזמן חורג מאורך הסרטון")
    d = jobdir(job_id)
    src = next(d.glob("source.*"))
    name = f"frame_m{uuid.uuid4().hex[:6]}.jpg"
    try:
        pl.extract_frame_at(src, req.t, d / name)
    except pl.PipelineError as e:
        raise HTTPException(400, str(e))
    item = {"name": name, "t": round(req.t, 1), "score": 0}
    mutate(job_id, lambda s: s.setdefault("frames", []).append(item))
    return item


@app.post("/api/jobs/{job_id}/stage", dependencies=[Depends(require_auth)])
async def stage_audio(job_id: str, file: UploadFile = File(...)):
    """Park the generated blessing audio here so the video provider can fetch it by signed URL."""
    d = jobdir(job_id)
    ext = Path(file.filename or "").suffix.lower()
    if ext not in (".mp3", ".wav"):
        raise HTTPException(400, "רק mp3 או wav")
    data = await file.read(25 * 1024 * 1024 + 1)
    if len(data) > 25 * 1024 * 1024:
        raise HTTPException(413, "הקובץ גדול מדי")
    name = f"stage_{uuid.uuid4().hex[:8]}{ext}"
    (d / name).write_bytes(data)
    return {"name": name}


@app.get("/api/jobs/{job_id}/sign", dependencies=[Depends(require_auth)])
def sign_url(job_id: str, name: str):
    jobdir(job_id)
    if not PUBLIC_BASE_URL:
        return {"url": None}
    if not SAFE_NAME.match(name) or not name.startswith(("frame_", "stage_")) or not (jobdir(job_id) / name).is_file():
        raise HTTPException(404)
    return {"url": sign(job_id, name)}


@app.get("/api/jobs/{job_id}/files/{name}", dependencies=[Depends(require_auth)])
def get_file(job_id: str, name: str):
    return _file(job_id, name)


@app.delete("/api/jobs/{job_id}", dependencies=[Depends(require_auth)])
def delete_job(job_id: str):
    shutil.rmtree(jobdir(job_id), ignore_errors=True)
    return {"ok": True}


@app.get("/healthz")
def healthz():
    return {"ok": True}


# ------------------------------------------------------------------ housekeeping

def cleanup_old():
    root = DATA_DIR / "jobs"
    if not root.is_dir():
        return
    cutoff = time.time() - JOB_TTL_DAYS * 86400
    for d in root.iterdir():
        try:
            if d.is_dir() and d.stat().st_mtime < cutoff:
                shutil.rmtree(d, ignore_errors=True)
        except OSError:
            pass


def mark_interrupted():
    root = DATA_DIR / "jobs"
    if not root.is_dir():
        return
    for d in root.iterdir():
        p = d / "state.json"
        if not p.is_file():
            continue
        try:
            st = json.loads(p.read_text("utf-8"))
        except ValueError:
            continue
        if st.get("status") in ("queued", "analyzing"):
            st.update(status="error", error="העיבוד הופסק (השרת הופעל מחדש). העלה את הסרטון שוב.")
            p.write_text(json.dumps(st, ensure_ascii=False), "utf-8")


@app.on_event("startup")
def startup():
    (DATA_DIR / "jobs").mkdir(parents=True, exist_ok=True)
    mark_interrupted()
    cleanup_old()


static_dir = Path(__file__).parent / "static"


@app.get("/", dependencies=[Depends(require_auth)])
def index():
    html = (static_dir / "index.html").read_text("utf-8")
    return HTMLResponse(html, headers={"Content-Security-Policy": CSP, "Referrer-Policy": "no-referrer",
                                       "Cache-Control": "no-store", "X-Content-Type-Options": "nosniff"})


app.mount("/static", StaticFiles(directory=static_dir), name="static")
