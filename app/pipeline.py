"""Server-side media processing (ffmpeg, frames, sample picking). No network calls, no API keys:
all OpenRouter calls happen in the browser (BYOK)."""
import base64
import json
import math
import ipaddress
import os
import re
import socket
import subprocess
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path


class PipelineError(Exception):
    pass


# ---------------------------------------------------------------- ffmpeg

def run(cmd: list[str], timeout: int = 600, cwd: Path | None = None) -> str:
    p = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, cwd=cwd)
    if p.returncode != 0:
        raise PipelineError(f"{cmd[0]} נכשל: {p.stderr[-400:]}")
    return p.stdout


def probe_duration(path: Path) -> float:
    out = run(["ffprobe", "-v", "error", "-show_entries", "format=duration",
               "-of", "default=nw=1:nk=1", str(path)])
    try:
        return float(out.strip())
    except ValueError:
        raise PipelineError("לא ניתן לקרוא את אורך הקובץ - האם זה קובץ וידאו/אודיו תקין?")


def has_audio(path: Path) -> bool:
    out = run(["ffprobe", "-v", "error", "-select_streams", "a", "-show_entries",
               "stream=index", "-of", "csv=p=0", str(path)])
    return bool(out.strip())


def extract_audio(src: Path, jobdir: Path) -> tuple[Path, Path]:
    wav = jobdir / "full.wav"
    mp3 = jobdir / "full.mp3"
    run(["ffmpeg", "-y", "-i", str(src), "-vn", "-ac", "1", "-ar", "24000", str(wav)])
    run(["ffmpeg", "-y", "-i", str(wav), "-b:a", "64k", str(mp3)])
    return wav, mp3


def to_speech_mp3(src: Path, out: Path) -> Path:
    """Any audio (or the audio track of a video) as a clean mp3 a video provider will accept."""
    run(["ffmpeg", "-y", "-i", str(src), "-vn", "-ac", "1", "-ar", "44100", "-b:a", "128k", str(out)])
    return out


def cut_audio(wav: Path, start: float, end: float, out: Path) -> Path:
    cmd = ["ffmpeg", "-y", "-ss", f"{start:.2f}", "-t", f"{end - start:.2f}", "-i", str(wav),
           "-ac", "1", "-ar", "24000"]
    if out.suffix == ".mp3":
        cmd += ["-b:a", "64k"]
    run(cmd + [str(out)])
    return out


def extract_frame_at(src: Path, t: float, out: Path) -> Path:
    run(["ffmpeg", "-y", "-ss", f"{t:.2f}", "-i", str(src), "-frames:v", "1",
         "-q:v", "2", str(out)])
    if not out.exists():
        raise PipelineError("לא הצלחתי לחלץ פריים בזמן הזה")
    return out


def normalize_image(src: Path, out: Path, max_side: int = 1920) -> Path:
    """Convert any uploaded still to an upright JPEG, downscaled so the long side is at most max_side."""
    run(["ffmpeg", "-y", "-i", str(src), "-frames:v", "1",
         "-vf", f"scale='min({max_side},iw)':'min({max_side},ih)':force_original_aspect_ratio=decrease",
         "-q:v", "2", str(out)])
    if not out.exists():
        raise PipelineError("לא הצלחתי לקרוא את התמונה")
    return out


# ---------------------------------------------------------------- long videos: split, chain, stitch

def split_audio(src: Path, outdir: Path, max_len: float, prefix: str, min_len: float = 4.0) -> list[dict]:
    """Cut audio into chunks of at most max_len seconds, cutting in the middle of pauses when possible."""
    total = probe_duration(src)
    p = subprocess.run(["ffmpeg", "-i", str(src), "-af", "silencedetect=noise=-35dB:d=0.18", "-f", "null", "-"],
                       capture_output=True, text=True, timeout=600)
    starts = [float(x) for x in re.findall(r"silence_start: (-?[\d.]+)", p.stderr)]
    ends = [float(x) for x in re.findall(r"silence_end: (-?[\d.]+)", p.stderr)]
    mids = [(a + b) / 2 for a, b in zip(starts, ends)]
    cuts, t = [], 0.0
    while total - t > max_len:
        hi = min(t + max_len, total - min_len)
        cand = [m for m in mids if t + min_len <= m <= hi]
        cut = max(cand) if cand else hi
        if cut - t < min_len:
            cut = min(t + max_len, total)
        cuts.append(cut)
        t = cut
    bounds = [0.0] + cuts + [total]
    chunks = []
    for i, (a, b) in enumerate(zip(bounds, bounds[1:])):
        name = f"{prefix}_{i:02d}.mp3"
        run(["ffmpeg", "-y", "-i", str(src), "-ss", f"{a:.3f}", "-t", f"{b - a:.3f}", "-ac", "1",
             "-ar", "44100", "-b:a", "128k", str(outdir / name)])
        chunks.append({"name": name, "seconds": round(b - a, 3)})
    return chunks


def _public_host(host: str) -> bool:
    try:
        infos = socket.getaddrinfo(host, 443, proto=socket.IPPROTO_TCP)
    except OSError:
        return False
    for info in infos:
        ip = ipaddress.ip_address(info[4][0])
        if not ip.is_global:
            return False
    return bool(infos)


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *a, **k):
        return None


def fetch_public_url(url: str, dest: Path, max_bytes: int = 300 * 1024 * 1024) -> Path:
    """Download a finished clip from a public https link. Refuses private addresses (no SSRF)."""
    opener = urllib.request.build_opener(_NoRedirect)
    for _ in range(4):
        u = urllib.parse.urlsplit(url)
        if u.scheme != "https" or not u.hostname or not _public_host(u.hostname):
            raise PipelineError("קישור הווידאו לא תקין")
        try:
            with opener.open(urllib.request.Request(url, headers={"User-Agent": "savta-blessing"}), timeout=120) as r:
                size = 0
                with dest.open("wb") as fh:
                    while chunk := r.read(1024 * 1024):
                        size += len(chunk)
                        if size > max_bytes:
                            raise PipelineError("קובץ הווידאו גדול מדי")
                        fh.write(chunk)
                return dest
        except urllib.error.HTTPError as e:
            loc = e.headers.get("Location") if e.code in (301, 302, 303, 307, 308) else None
            if not loc:
                raise PipelineError(f"הורדת הקליפ נכשלה ({e.code})")
            url = urllib.parse.urljoin(url, loc)
        except (urllib.error.URLError, TimeoutError, OSError) as e:
            raise PipelineError(f"הורדת הקליפ נכשלה ({e})")
    raise PipelineError("יותר מדי הפניות בקישור הווידאו")


def video_info(path: Path) -> tuple[int, int, float]:
    out = run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries", "stream=width,height",
               "-of", "csv=p=0", str(path)]).strip().split(",")
    try:
        return int(out[0]), int(out[1]), probe_duration(path)
    except (ValueError, IndexError):
        raise PipelineError("הקליפ שהתקבל אינו קובץ וידאו תקין")


def trim(src: Path, out: Path, start: float, end: float) -> Path:
    """Frame-accurate cut (re-encoded, so it can start anywhere, not just on a keyframe)."""
    run(["ffmpeg", "-y", "-ss", f"{start:.3f}", "-i", str(src), "-t", f"{end - start:.3f}",
         "-c:v", "libx264", "-crf", "18", "-preset", "medium", "-pix_fmt", "yuv420p",
         "-c:a", "aac", "-b:a", "192k", "-movflags", "+faststart", str(out)])
    video_info(out)
    return out


def make_thumb(video: Path, out: Path) -> Path:
    """Small poster image for the history list."""
    _, _, dur = video_info(video)
    run(["ffmpeg", "-y", "-ss", f"{min(1.0, dur / 3):.2f}", "-i", str(video), "-frames:v", "1",
         "-vf", "scale=320:-2", "-q:v", "4", str(out)])
    if not out.exists():
        raise PipelineError("לא נוצרה תמונה מוקטנת")
    return out


def chain_frame(part: Path, seconds: float, out: Path) -> Path:
    """The frame the next clip should start from: where this clip's audio ends (or its last frame).
    The container can be longer than the picture (e.g. a soundtrack that runs past the last frame),
    so step back until a frame comes out, and finally just keep the last decoded frame."""
    _, _, dur = video_info(part)
    for t in (min(seconds, dur) - 0.07, dur - 0.5, dur - 1.5, dur / 2):
        out.unlink(missing_ok=True)
        try:
            extract_frame_at(part, max(0.0, t), out)
        except PipelineError:
            continue
        if out.is_file() and out.stat().st_size > 0:
            return out
    out.unlink(missing_ok=True)
    run(["ffmpeg", "-y", "-i", str(part), "-an", "-update", "1", "-q:v", "2", str(out)])
    if not (out.is_file() and out.stat().st_size > 0):
        raise PipelineError("לא הצלחתי לחלץ פריים מהקליפ")
    return out


SUB_FONT = os.environ.get("SUB_FONT", "Noto Sans Hebrew")
SUB_SIZES = {"s": 14, "m": 18, "l": 24}  # libass units, relative to a 288-line frame


def subtitle_style(size: str = "m", box: bool = False) -> str:
    """force_style for the subtitles filter. Encoding=-1 lets libass detect right-to-left lines,
    otherwise Hebrew punctuation lands on the wrong side."""
    s = [f"FontName={SUB_FONT}", f"FontSize={SUB_SIZES.get(size, 18)}", "Bold=1", "MarginV=18", "Encoding=-1",
         "PrimaryColour=&H00FFFFFF"]
    if box:
        s += ["BorderStyle=3", "Outline=6", "Shadow=0", "OutlineColour=&H80000000", "BackColour=&H80000000"]
    else:
        s += ["BorderStyle=1", "Outline=1.6", "Shadow=0.8", "OutlineColour=&H00000000", "BackColour=&H80000000"]
    return ",".join(s)


def prepare_subtitles(raw: bytes, ext: str, outdir: Path) -> tuple[str, int]:
    """Store an uploaded subtitle file as UTF-8 (Hebrew files are often windows-1255), SRT/VTT as subs.srt,
    ASS/SSA as subs.ass. Returns the stored name and the number of cues."""
    for enc in ("utf-8-sig", "cp1255"):
        try:
            text = raw.decode(enc)
            break
        except UnicodeDecodeError:
            continue
    else:
        raise PipelineError("לא הצלחתי לקרוא את קובץ הכתוביות (קידוד לא מוכר)")
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    for old in outdir.glob("subs.*"):
        old.unlink()
    if ext in (".ass", ".ssa"):
        out = outdir / "subs.ass"
        out.write_text(text, "utf-8")
        cues = sum(1 for line in text.split("\n") if line.startswith("Dialogue:"))
    else:
        tmp = outdir / f"subs_in{ext}"
        tmp.write_text(text, "utf-8")
        out = outdir / "subs.srt"
        try:
            run(["ffmpeg", "-y", "-i", tmp.name, out.name], cwd=outdir)
        finally:
            tmp.unlink(missing_ok=True)
        cues = out.read_text("utf-8").count("-->")
    if cues == 0:
        out.unlink(missing_ok=True)
        raise PipelineError("לא נמצאו כתוביות בקובץ. צריך SRT, VTT או ASS עם זמנים")
    return out.name, cues


def stitch(parts: list[tuple[Path, float]], audio: Path, out: Path,
           subs: Path | None = None, sub_style: str = "") -> Path:
    """Join clips, each fitted to exactly its audio chunk's length, and lay the original audio on top.
    With subs, burn the subtitles into the picture in the same pass."""
    w, h, _ = video_info(parts[0][0])
    w, h = w - w % 2, h - h % 2
    cmd, fc = ["ffmpeg", "-y"], ""
    for i, (p, sec) in enumerate(parts):
        cmd += ["-i", str(p)]
        fc += (f"[{i}:v]fps=30,scale={w}:{h}:force_original_aspect_ratio=decrease,pad={w}:{h}:(ow-iw)/2:(oh-ih)/2,"
               f"setsar=1,tpad=stop_mode=clone:stop_duration=30,trim=0:{sec:.3f},setpts=PTS-STARTPTS[v{i}];")
    fc += "".join(f"[v{i}]" for i in range(len(parts))) + f"concat=n={len(parts)}:v=1:a=0"
    if subs is not None:
        # run in the job folder so the filter gets a bare file name (no path escaping inside the filtergraph)
        enc = "" if subs.suffix == ".ass" else ":charenc=UTF-8"
        style = sub_style if subs.suffix != ".ass" else "Encoding=-1"
        fc += f"[vc];[vc]subtitles=filename={subs.name}{enc}:force_style='{style}'[v]"
    else:
        fc += "[v]"
    cmd += ["-i", str(audio), "-filter_complex", fc, "-map", "[v]", "-map", f"{len(parts)}:a",
            "-c:v", "libx264", "-crf", "18", "-preset", "medium", "-pix_fmt", "yuv420p",
            "-c:a", "aac", "-b:a", "192k", "-shortest", "-movflags", "+faststart", str(out)]
    run(cmd, timeout=1800, cwd=subs.parent if subs is not None else None)
    return out


# ---------------------------------------------------------------- sample picking

def suggest_samples(segs: list[dict], duration: float, max_len: float = 25.0,
                    min_len: float = 8.0, n: int = 3) -> list[dict]:
    """Pick windows of continuous single-speaker speech, 8-25s, most speech first."""
    if not segs:
        return [{"start": 0.0, "end": min(20.0, duration), "text": ""}]
    cands = []
    for i in range(len(segs)):
        st = segs[i]["start"]
        en = segs[i]["end"]
        sp = segs[i].get("speaker")
        texts = [segs[i]["text"]]
        speech = en - st
        for j in range(i + 1, len(segs)):
            s = segs[j]
            if s["start"] - en > 1.2 or s.get("speaker") != sp or s["end"] - st > max_len:
                break
            speech += s["end"] - s["start"]
            en = s["end"]
            texts.append(s["text"])
        dur = en - st
        if dur > max_len:  # single long segment
            en = st + max_len
            dur = max_len
        cands.append({"start": max(0.0, st - 0.1), "end": min(duration, en + 0.15),
                      "text": " ".join(texts), "score": (min(dur, max_len), speech / max(dur, 0.1))})
    good = [c for c in cands if c["end"] - c["start"] >= min_len] or cands
    good.sort(key=lambda c: c["score"], reverse=True)
    picked: list[dict] = []
    for c in good:
        if all(c["end"] <= p["start"] or c["start"] >= p["end"] for p in picked):
            picked.append(c)
        if len(picked) >= n:
            break
    for p in picked:
        p.pop("score", None)
        p["start"] = round(p["start"], 2)
        p["end"] = round(p["end"], 2)
    return picked


def text_for_range(segs: list[dict], start: float, end: float) -> str:
    parts = [s["text"] for s in segs if s["end"] > start and s["start"] < end]
    return " ".join(parts).strip()


# ---------------------------------------------------------------- frames

def extract_and_rank_frames(src: Path, jobdir: Path, duration: float, keep: int = 6) -> list[dict]:
    import cv2  # lazy: heavy import

    fdir = jobdir / "frames_all"
    fdir.mkdir(exist_ok=True)
    fps = min(1.0, 240.0 / max(duration, 1.0))
    run(["ffmpeg", "-y", "-i", str(src), "-vf", f"fps={fps:.4f},scale=-2:'min(1080,ih)'",
         "-q:v", "3", str(fdir / "f_%04d.jpg")], timeout=900)

    cdir = getattr(getattr(cv2, "data", None), "haarcascades", "") or ""
    frontal = cv2.CascadeClassifier(cdir + "haarcascade_frontalface_default.xml")
    eye = cv2.CascadeClassifier(cdir + "haarcascade_eye.xml")
    detector_ok = not frontal.empty() and not eye.empty()

    scored = []
    for i, p in enumerate(sorted(fdir.glob("f_*.jpg"))):
        t = i / fps
        img = cv2.imread(str(p))
        if img is None:
            continue
        gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
        h, w = gray.shape
        if not detector_ok:
            # no face detector available: rank by sharpness of the central area, user picks by eye
            c = gray[h // 6: 5 * h // 6, w // 4: 3 * w // 4]
            scored.append((cv2.Laplacian(c, cv2.CV_64F).var(), t, p))
            continue
        faces = frontal.detectMultiScale(gray, 1.1, 5, minSize=(max(60, w // 14),) * 2)
        if len(faces) == 0:
            continue
        x, y, fw, fh = max(faces, key=lambda f: f[2] * f[3])
        roi = gray[y:y + fh, x:x + fw]
        sharp = cv2.Laplacian(roi, cv2.CV_64F).var()
        eyes = eye.detectMultiScale(roi[: fh // 2, :], 1.1, 4)
        cx = (x + fw / 2) / w
        score = (min(fw * fh / (w * h) / 0.08, 1) * 40 + min(sharp / 150, 1) * 30
                 + (20 if len(eyes) >= 2 else 0) + 10 * (1 - abs(cx - 0.5) * 2))
        scored.append((score, t, p))

    scored.sort(reverse=True)
    chosen: list[tuple[float, float, Path]] = []
    for s in scored:
        if all(abs(s[1] - c[1]) >= 2.0 for c in chosen):
            chosen.append(s)
        if len(chosen) >= keep:
            break

    out = []
    for n, (score, t, p) in enumerate(chosen, 1):
        name = f"frame_{n}.jpg"
        (jobdir / name).write_bytes(p.read_bytes())
        out.append({"name": name, "t": round(t, 1), "score": round(score, 1)})
    for p in fdir.glob("*.jpg"):
        p.unlink()
    fdir.rmdir()
    return out
