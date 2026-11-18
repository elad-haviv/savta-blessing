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
AUDIO_EXT = {".mp3", ".wav", ".m4a", ".aac", ".ogg", ".opus", ".flac", ".wma", ".amr"}
IMAGE_EXT = {".jpg", ".jpeg", ".png", ".webp", ".bmp", ".heic", ".heif"}
SUB_EXT = {".srt", ".vtt", ".ass", ".ssa"}
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
<form method="post" action="/login"><h2>ברכת סבתא</h2><input type="hidden" name="next" value="__NEXT__"><input type="password" name="password" placeholder="סיסמה" autofocus required>
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


PAGES = ("/", "/talk", "/frames")


def _login_page(err: str = "", status: int = 401, next_path: str = "/") -> HTMLResponse:
    nxt = next_path if next_path in PAGES else "/"
    return HTMLResponse(LOGIN_HTML.replace("__ERR__", err).replace("__NEXT__", nxt), status_code=status,
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
        return _login_page(next_path=request.url.path)
    return JSONResponse({"detail": exc.detail}, status_code=exc.status_code)


@app.post("/login")
def login(password: str = Form(""), next: str = Form("/")):  # noqa: A002 - form field name
    nxt = next if next in PAGES else "/"
    if not APP_PASSWORD or not hmac.compare_digest(password.encode(), APP_PASSWORD.encode()):
        return _login_page("סיסמה שגויה", 401, nxt)
    r = RedirectResponse(nxt, status_code=303)
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
        mutate(job_id, status="analyzing", step="מחלץ אודיו")
        if not pl.has_audio(src):
            raise pl.PipelineError("בקובץ אין רצועת קול")
        duration = pl.probe_duration(src)
        pl.extract_audio(src, d)
        if src.suffix.lower() in AUDIO_EXT:
            mutate(job_id, duration=round(duration, 1), frames=[], status="ready", step="")
            return
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
    if ext not in ALLOWED_EXT | AUDIO_EXT:
        raise HTTPException(400, f"סוג קובץ לא נתמך ({ext or 'ללא סיומת'}). נתמכים: {', '.join(sorted(ALLOWED_EXT | AUDIO_EXT))}")
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
             "kind": "audio" if ext in AUDIO_EXT else "video", "status": "queued", "step": "ממתין"}
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
    if st.get("kind") == "audio":
        raise HTTPException(400, "המקור הוא קובץ אודיו, אין ממנו פריימים. אפשר להעלות תמונה")
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


@app.post("/api/jobs/{job_id}/image", dependencies=[Depends(require_auth)])
async def add_image(job_id: str, file: UploadFile = File(...)):
    """Upload a still photo to use as the face image, in addition to (or instead of) video frames."""
    st = load(job_id)
    if st["status"] != "ready":
        raise HTTPException(409, "העיבוד עדיין לא הסתיים")
    ext = Path(file.filename or "").suffix.lower()
    if ext not in IMAGE_EXT:
        raise HTTPException(400, f"סוג תמונה לא נתמך ({ext or 'ללא סיומת'})")
    if sum(1 for f in st.get("frames", []) if f["name"].startswith("frame_u")) >= 20:
        raise HTTPException(400, "אפשר להעלות עד 20 תמונות")
    data = await file.read(25 * 1024 * 1024 + 1)
    if len(data) > 25 * 1024 * 1024:
        raise HTTPException(413, "התמונה גדולה מדי (עד 25MB)")
    d = jobdir(job_id)
    uid = uuid.uuid4().hex[:6]
    raw, name = d / f"upload_{uid}{ext}", f"frame_u{uid}.jpg"
    raw.write_bytes(data)
    try:
        pl.normalize_image(raw, d / name)
    except pl.PipelineError:
        raise HTTPException(400, "לא הצלחתי לקרוא את התמונה")
    finally:
        raw.unlink(missing_ok=True)
    item = {"name": name, "t": None, "score": 0, "uploaded": True}
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


class SplitReq(BaseModel):
    name: str
    max_len: float = Field(ge=4, le=120)


@app.post("/api/jobs/{job_id}/split", dependencies=[Depends(require_auth)])
def split(job_id: str, req: SplitReq):
    """Cut a staged blessing audio into short chunks (at pauses) for models with a clip-length limit."""
    d = jobdir(job_id)
    if not SAFE_NAME.match(req.name) or not req.name.startswith("stage_") or not (d / req.name).is_file():
        raise HTTPException(404)
    try:
        return {"chunks": pl.split_audio(d / req.name, d, req.max_len, f"stage_{uuid.uuid4().hex[:8]}")}
    except pl.PipelineError as e:
        raise HTTPException(400, str(e))


@app.post("/api/jobs/{job_id}/part", dependencies=[Depends(require_auth)])
async def add_part(job_id: str, seconds: float = Form(...), url: str = Form(""), file: UploadFile | None = File(None)):
    """Store one generated clip and return the frame the next clip should start from."""
    d = jobdir(job_id)
    uid = uuid.uuid4().hex[:8]
    part = d / f"part_{uid}.mp4"
    try:
        if file is not None:
            size = 0
            with part.open("wb") as fh:
                while chunk := await file.read(1024 * 1024):
                    size += len(chunk)
                    if size > 300 * 1024 * 1024:
                        raise pl.PipelineError("קובץ הווידאו גדול מדי")
                    fh.write(chunk)
        elif url:
            pl.fetch_public_url(url, part)
        else:
            raise pl.PipelineError("לא התקבל קליפ")
        frame = f"frame_c{uid}.jpg"
        pl.chain_frame(part, seconds, d / frame)
    except pl.PipelineError as e:
        part.unlink(missing_ok=True)
        raise HTTPException(400, str(e))
    return {"name": part.name, "frame": frame}


class StitchPart(BaseModel):
    name: str
    seconds: float = Field(gt=0, le=600)


class StitchReq(BaseModel):
    parts: list[StitchPart] = Field(min_length=1, max_length=60)
    audio: str
    subs: bool = False
    sub_size: str = Field("m", pattern="^[sml]$")
    sub_box: bool = False


@app.post("/api/jobs/{job_id}/stitch", dependencies=[Depends(require_auth)])
def stitch(job_id: str, req: StitchReq):
    d = jobdir(job_id)
    names = [p.name for p in req.parts] + [req.audio]
    if any(not SAFE_NAME.match(n) or not (d / n).is_file() for n in names) \
            or any(not p.name.startswith("part_") for p in req.parts) or not req.audio.startswith("stage_"):
        raise HTTPException(404)
    subs = None
    if req.subs:
        name = load(job_id).get("subs")
        if not name or not (d / name).is_file():
            raise HTTPException(400, "אין קובץ כתוביות בג'וב הזה")
        subs = d / name
    out = f"final_{uuid.uuid4().hex[:8]}.mp4"
    try:
        pl.stitch([(d / p.name, p.seconds) for p in req.parts], d / req.audio, d / out,
                  subs=subs, sub_style=pl.subtitle_style(req.sub_size, req.sub_box))
    except pl.PipelineError as e:
        raise HTTPException(400, str(e))
    return {"name": out}


# ------------------------------------------------------------------ talking picture: image + audio + subtitles

def _ext(f: UploadFile, allowed: set[str], what: str) -> str:
    ext = Path(f.filename or "").suffix.lower()
    if ext not in allowed:
        raise HTTPException(400, f"{what}: סוג קובץ לא נתמך ({ext or 'ללא סיומת'}). נתמכים: {', '.join(sorted(allowed))}")
    return ext


def _save(f: UploadFile, dest: Path, limit_mb: int, what: str) -> Path:
    size, limit = 0, limit_mb * 1024 * 1024
    with dest.open("wb") as fh:
        while chunk := f.file.read(1024 * 1024):
            size += len(chunk)
            if size > limit:
                raise HTTPException(413, f"{what} גדול מ-{limit_mb}MB")
            fh.write(chunk)
    return dest


def _set_subs(d: Path, f: UploadFile) -> tuple[str, int]:
    ext = _ext(f, SUB_EXT, "כתוביות")
    raw = f.file.read(2 * 1024 * 1024 + 1)
    if len(raw) > 2 * 1024 * 1024:
        raise HTTPException(413, "קובץ הכתוביות גדול מ-2MB")
    try:
        return pl.prepare_subtitles(raw, ext, d)
    except pl.PipelineError as e:
        raise HTTPException(400, str(e))


@app.post("/api/talk", dependencies=[Depends(require_auth)])
def create_talk(image: UploadFile = File(...), audio: UploadFile = File(...), subs: UploadFile = File(...)):
    """One picture, one finished audio track and a subtitle file: everything the talk page needs, ready at once."""
    img_ext = _ext(image, IMAGE_EXT, "תמונה")
    aud_ext = _ext(audio, AUDIO_EXT | ALLOWED_EXT, "אודיו")
    _ext(subs, SUB_EXT, "כתוביות")
    job_id = uuid.uuid4().hex
    d = DATA_DIR / "jobs" / job_id
    d.mkdir(parents=True)
    try:
        uid = uuid.uuid4().hex[:6]
        raw_img = _save(image, d / f"upload_{uid}{img_ext}", 25, "התמונה")
        frame = f"frame_u{uid}.jpg"
        try:
            pl.normalize_image(raw_img, d / frame)
        except pl.PipelineError:
            raise HTTPException(400, "לא הצלחתי לקרוא את התמונה")
        finally:
            raw_img.unlink(missing_ok=True)

        src = _save(audio, d / f"source{aud_ext}", MAX_UPLOAD_MB, "האודיו")
        try:
            if not pl.has_audio(src):
                raise HTTPException(400, "בקובץ האודיו אין רצועת קול")
            staged = f"stage_{uuid.uuid4().hex[:8]}.mp3"
            pl.to_speech_mp3(src, d / staged)
            duration = pl.probe_duration(d / staged)
        except pl.PipelineError as e:
            raise HTTPException(400, f"לא הצלחתי לקרוא את האודיו: {e}")

        sub_name, cues = _set_subs(d, subs)
    except BaseException:
        shutil.rmtree(d, ignore_errors=True)
        raise
    state = {"id": job_id, "created": int(time.time()), "kind": "talk", "status": "ready",
             "filename": audio.filename, "duration": round(duration, 3),
             "frames": [{"name": frame, "t": None, "score": 0, "uploaded": True}],
             "audio": staged, "subs": sub_name, "cues": cues}
    (d / "state.json").write_text(json.dumps(state, ensure_ascii=False), "utf-8")
    return state


@app.post("/api/frames", dependencies=[Depends(require_auth)])
def create_frames(start: UploadFile = File(...), end: UploadFile | None = File(None)):
    """Start image (and optional end image) for a free-form image-to-video generation."""
    imgs = [("start", start)] + ([("end", end)] if end is not None and end.filename else [])
    exts = [_ext(f, IMAGE_EXT, "תמונת התחלה" if role == "start" else "תמונת סיום") for role, f in imgs]
    job_id = uuid.uuid4().hex
    d = DATA_DIR / "jobs" / job_id
    d.mkdir(parents=True)
    frames = []
    try:
        for (role, f), ext in zip(imgs, exts):
            uid = uuid.uuid4().hex[:6]
            raw = _save(f, d / f"upload_{uid}{ext}", 25, "התמונה")
            name = f"frame_u{uid}.jpg"
            try:
                pl.normalize_image(raw, d / name)
            except pl.PipelineError:
                raise HTTPException(400, f"לא הצלחתי לקרוא את {'תמונת ההתחלה' if role == 'start' else 'תמונת הסיום'}")
            finally:
                raw.unlink(missing_ok=True)
            frames.append({"name": name, "role": role, "t": None, "score": 0, "uploaded": True})
    except BaseException:
        shutil.rmtree(d, ignore_errors=True)
        raise
    state = {"id": job_id, "created": int(time.time()), "kind": "frames", "status": "ready", "frames": frames}
    (d / "state.json").write_text(json.dumps(state, ensure_ascii=False), "utf-8")
    return state


@app.post("/api/jobs/{job_id}/subs", dependencies=[Depends(require_auth)])
def replace_subs(job_id: str, subs: UploadFile = File(...)):
    """Swap in a corrected subtitle file; finished videos can then be re-burned without generating again."""
    name, cues = _set_subs(jobdir(job_id), subs)
    return mutate(job_id, subs=name, cues=cues)


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


def _page(name: str) -> HTMLResponse:
    html = (static_dir / name).read_text("utf-8")
    return HTMLResponse(html, headers={"Content-Security-Policy": CSP, "Referrer-Policy": "no-referrer",
                                       "Cache-Control": "no-store", "X-Content-Type-Options": "nosniff"})


@app.get("/", dependencies=[Depends(require_auth)])
def index():
    return _page("index.html")


@app.get("/talk", dependencies=[Depends(require_auth)])
def talk_page():
    return _page("talk.html")


@app.get("/frames", dependencies=[Depends(require_auth)])
def frames_page():
    return _page("frames.html")


app.mount("/static", StaticFiles(directory=static_dir), name="static")
