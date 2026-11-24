"""Join two videos with a transition. The transition frames are composited here (numpy/OpenCV),
so every type gets the same controls: how much of each clip it covers, and an easing curve."""
from __future__ import annotations

import math
import subprocess
from pathlib import Path
from typing import Callable

import cv2
import numpy as np

from .pipeline import PipelineError, has_audio, probe_duration, run

TYPES = ("fade", "black", "white", "morph", "blur", "zoom", "wipe", "slide", "circle")
DIRECTIONS = ("left", "right", "up", "down")
EASINGS: dict[str, Callable[[float], float]] = {
    "linear": lambda p: p,
    "in": lambda p: p * p,
    "out": lambda p: 1 - (1 - p) * (1 - p),
    "inout": lambda p: p * p * (3 - 2 * p),
    "inout_strong": lambda p: 16 * p ** 5 if p < 0.5 else 1 - ((-2 * p + 2) ** 5) / 2,
}
DIPS = {"black": 0, "white": 255}


def video_meta(path: Path) -> dict:
    out = run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries",
               "stream=width,height,avg_frame_rate,r_frame_rate", "-of", "csv=p=0", str(path)]).strip().split(",")
    try:
        w, h = int(out[0]), int(out[1])
    except (ValueError, IndexError):
        raise PipelineError("הקובץ אינו סרטון תקין")
    fps = 0.0
    for frac in out[2:4][::-1]:  # prefer the average rate; fall back to the nominal one
        try:
            a, b = frac.split("/")
            if float(b) > 0 and float(a) > 0:
                fps = float(a) / float(b)
                break
        except ValueError:
            continue
    if not 1 <= fps <= 120:
        fps = 30.0
    return {"w": w, "h": h, "fps": round(fps, 3), "duration": round(probe_duration(path), 3), "audio": has_audio(path)}


# ---------------------------------------------------------------- compositing

class _Maps:
    """Coordinate grids, built once per job."""

    def __init__(self, w: int, h: int):
        self.w, self.h = w, h
        self.x = np.tile(np.linspace(0, 1, w, dtype=np.float32), (h, 1))
        self.y = np.tile(np.linspace(0, 1, h, dtype=np.float32)[:, None], (1, w))
        cx, cy = (w - 1) / 2, (h - 1) / 2
        gx, gy = np.meshgrid(np.arange(w, dtype=np.float32), np.arange(h, dtype=np.float32))
        self.gx, self.gy = gx, gy
        d = np.sqrt((gx - cx) ** 2 + (gy - cy) ** 2)
        self.dist = d / d.max()
        scale = min(1.0, 480 / max(w, h))  # optical flow runs on a small copy
        self.fw, self.fh = max(16, int(w * scale)), max(16, int(h * scale))
        self.dis = cv2.DISOpticalFlow_create(cv2.DISOPTICAL_FLOW_PRESET_MEDIUM)


def _mask_blend(a: np.ndarray, b: np.ndarray, m: np.ndarray) -> np.ndarray:
    m3 = m[:, :, None]
    return (a.astype(np.float32) * (1 - m3) + b.astype(np.float32) * m3).astype(np.uint8)


def _zoomed(img: np.ndarray, factor: float) -> np.ndarray:
    h, w = img.shape[:2]
    m = cv2.getRotationMatrix2D(((w - 1) / 2, (h - 1) / 2), 0, factor)
    return cv2.warpAffine(img, m, (w, h), flags=cv2.INTER_LINEAR, borderMode=cv2.BORDER_REFLECT)


def _flow(mp: _Maps, src: np.ndarray, dst: np.ndarray) -> np.ndarray:
    f = mp.dis.calc(src, dst, None)
    f = cv2.resize(f, (mp.w, mp.h), interpolation=cv2.INTER_LINEAR)
    f[:, :, 0] *= mp.w / mp.fw
    f[:, :, 1] *= mp.h / mp.fh
    return f


def _morph(mp: _Maps, a: np.ndarray, b: np.ndarray, p: float) -> np.ndarray:
    """Each picture is bent part of the way toward the other along the motion between them, then mixed."""
    ga = cv2.cvtColor(cv2.resize(a, (mp.fw, mp.fh), interpolation=cv2.INTER_AREA), cv2.COLOR_BGR2GRAY)
    gb = cv2.cvtColor(cv2.resize(b, (mp.fw, mp.fh), interpolation=cv2.INTER_AREA), cv2.COLOR_BGR2GRAY)
    fab, fba = _flow(mp, ga, gb), _flow(mp, gb, ga)
    wa = cv2.remap(a, mp.gx - p * fab[:, :, 0], mp.gy - p * fab[:, :, 1], cv2.INTER_LINEAR, borderMode=cv2.BORDER_REPLICATE)
    wb = cv2.remap(b, mp.gx - (1 - p) * fba[:, :, 0], mp.gy - (1 - p) * fba[:, :, 1], cv2.INTER_LINEAR, borderMode=cv2.BORDER_REPLICATE)
    return cv2.addWeighted(wa, 1 - p, wb, p, 0)


def blend(mp: _Maps, kind: str, direction: str, a: np.ndarray, b: np.ndarray, p: float) -> np.ndarray:
    """One transition frame: p=0 is all A, p=1 is all B."""
    if p <= 0:
        return a
    if p >= 1:
        return b
    if kind == "fade":
        return cv2.addWeighted(a, 1 - p, b, p, 0)
    if kind == "morph":
        return _morph(mp, a, b, p)
    if kind == "blur":
        s = mp.w / 28
        sa, sb = p * s, (1 - p) * s
        a2 = cv2.GaussianBlur(a, (0, 0), sa) if sa > 0.4 else a
        b2 = cv2.GaussianBlur(b, (0, 0), sb) if sb > 0.4 else b
        return cv2.addWeighted(a2, 1 - p, b2, p, 0)
    if kind == "zoom":
        return cv2.addWeighted(_zoomed(a, 1 + 0.7 * p), 1 - p, _zoomed(b, 1 + 0.7 * (1 - p)), p, 0)
    if kind == "wipe":
        coord = {"left": 1 - mp.x, "right": mp.x, "up": 1 - mp.y, "down": mp.y}[direction]
        soft = 0.08
        return _mask_blend(a, b, np.clip((p * (1 + soft) - coord) / soft, 0, 1))
    if kind == "circle":
        soft = 0.06
        return _mask_blend(a, b, np.clip((p * (1 + soft) - mp.dist) / soft, 0, 1))
    if kind == "slide":  # B pushes A out of the frame
        out = np.empty_like(a)
        if direction in ("left", "right"):
            s = min(mp.w, max(0, int(round(p * mp.w))))
            if direction == "left":
                out[:, :mp.w - s], out[:, mp.w - s:] = a[:, s:], b[:, :s]
            else:
                out[:, s:], out[:, :s] = a[:, :mp.w - s], b[:, mp.w - s:]
        else:
            s = min(mp.h, max(0, int(round(p * mp.h))))
            if direction == "up":
                out[:mp.h - s], out[mp.h - s:] = a[s:], b[:s]
            else:
                out[s:], out[:s] = a[:mp.h - s], b[mp.h - s:]
        return out
    raise PipelineError("סוג מעבר לא מוכר")


# ---------------------------------------------------------------- the job

def _fit(w: int, h: int, fps: float) -> str:
    return (f"fps={fps},scale={w}:{h}:force_original_aspect_ratio=decrease,"
            f"pad={w}:{h}:(ow-iw)/2:(oh-ih)/2,setsar=1")


class _Reader:
    """Decoded frames of one clip, from a frame index on (or up to one), as raw BGR."""

    def __init__(self, path: Path, w: int, h: int, fps: float, start: int = 0, end: int | None = None):
        tr = f",trim=start_frame={start}" + (f":end_frame={end}" if end is not None else "")
        self.size, self.shape = w * h * 3, (h, w, 3)
        self.p = subprocess.Popen(["ffmpeg", "-v", "error", "-i", str(path), "-an", "-vf", _fit(w, h, fps) + tr,
                                   "-f", "rawvideo", "-pix_fmt", "bgr24", "-"],
                                  stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
        self.last: np.ndarray | None = None

    def next(self) -> np.ndarray | None:
        """The next frame; once the clip runs out, its last frame again (a freeze)."""
        buf = self.p.stdout.read(self.size) if self.p.stdout else b""
        if len(buf) == self.size:
            self.last = np.frombuffer(buf, np.uint8).reshape(self.shape)
        return self.last

    def close(self):
        try:
            self.p.kill()
            self.p.wait(timeout=5)
        except Exception:  # noqa: BLE001
            pass


def make(a: Path, b: Path, out: Path, kind: str, direction: str, easing: str, d_a: float, d_b: float,
         preview: bool = False, progress: Callable[[str], None] = lambda s: None) -> dict:
    """A then B, with the transition taking the last d_a seconds of A and the first d_b seconds of B.

    Dips (through black/white): A fades out over d_a, then B fades in over d_b.
    Everything else: the two clips overlap. When the two times differ, the clip with the shorter
    time stands on a frozen frame for the difference (A on its last frame, B on its first)."""
    if kind not in TYPES or direction not in DIRECTIONS or easing not in EASINGS:
        raise PipelineError("הגדרות מעבר לא תקינות")
    ma, mb = video_meta(a), video_meta(b)
    fps = min(ma["fps"], 60.0)
    w, h = ma["w"] - ma["w"] % 2, ma["h"] - ma["h"] % 2
    if preview and max(w, h) > 640:  # small and quick, just to judge the transition
        k = 640 / max(w, h)
        w, h = int(w * k) // 2 * 2, int(h * k) // 2 * 2
    na, nb = int(ma["duration"] * fps), int(mb["duration"] * fps)
    fa = max(0, min(int(round(d_a * fps)), na - 2))  # transition frames taken from the end of A
    fb = max(0, min(int(round(d_b * fps)), nb - 2))  # ... and from the start of B
    if na < 3 or nb < 3:
        raise PipelineError("אחד הסרטונים קצר מדי")
    dip = kind in DIPS
    if fa + fb == 0:
        raise PipelineError("זמן המעבר הוא אפס משני הצדדים")
    n = na - fa                                   # A plays untouched up to this frame
    total = fa + fb if dip else max(fa, fb)       # frames in the transition
    b_wait = 0 if dip else total - fb             # B stands on its first frame this long
    pad = int(2 * fps)
    a0 = max(0, n - pad) if preview else 0        # preview: only two seconds around the transition
    b1 = min(nb, fb + pad) if preview else None

    ease, mp = EASINGS[easing], _Maps(w, h)
    seg = out.with_name(out.stem + "_t.mp4")
    ra, rb = _Reader(a, w, h, fps, start=min(n, na - 1)), _Reader(b, w, h, fps, end=max(fb, 1))
    enc = subprocess.Popen(["ffmpeg", "-v", "error", "-y", "-f", "rawvideo", "-pix_fmt", "bgr24", "-s", f"{w}x{h}",
                            "-r", str(fps), "-i", "-", "-c:v", "libx264", "-crf", "12", "-preset", "veryfast",
                            "-pix_fmt", "yuv420p", str(seg)], stdin=subprocess.PIPE, stderr=subprocess.DEVNULL)
    try:
        fr_a = fr_b = None
        for i in range(total):
            if i % 10 == 0:
                progress(f"מחשב את המעבר ({int(100 * i / total)}%)")
            if dip:
                color = np.full((h, w, 3), DIPS[kind], np.uint8)
                if i < fa:
                    fr_a = ra.next()
                    frame = cv2.addWeighted(fr_a, 1 - ease((i + 1) / fa), color, ease((i + 1) / fa), 0)
                else:
                    fr_b = rb.next()
                    q = ease((i - fa) / fb)
                    frame = cv2.addWeighted(color, 1 - q, fr_b, q, 0)
            else:
                fr_a = ra.next() if i < fa or fr_a is None else fr_a
                if fr_b is None or i > b_wait:
                    fr_b = rb.next()
                frame = blend(mp, kind, direction, fr_a, fr_b, ease((i + 1) / (total + 1)))
            if frame is None or fr_a is None and fr_b is None:
                raise PipelineError("לא הצלחתי לקרוא את הפריימים של אחד הסרטונים")
            enc.stdin.write(np.ascontiguousarray(frame).tobytes())
        enc.stdin.close()
        if enc.wait(timeout=600) != 0:
            raise PipelineError("קידוד המעבר נכשל")
    except (BrokenPipeError, TypeError, cv2.error) as e:
        raise PipelineError(f"חישוב המעבר נכשל ({e})")
    finally:
        ra.close()
        rb.close()
        if enc.poll() is None:
            enc.kill()

    progress("מרכיב את הסרטון")
    fit = _fit(w, h, fps)
    parts, fc = [], ""
    if n - a0 > 0:
        fc += f"[0:v]{fit},trim=start_frame={a0}:end_frame={n},setpts=PTS-STARTPTS[va];"
        parts.append("[va]")
    fc += f"[2:v]fps={fps},setsar=1,setpts=PTS-STARTPTS[vt];"
    parts.append("[vt]")
    if (b1 if b1 is not None else nb) - fb > 0:
        fc += f"[1:v]{fit},trim=start_frame={fb}" + (f":end_frame={b1}" if b1 is not None else "") + ",setpts=PTS-STARTPTS[vb];"
        parts.append("[vb]")
    fc += "".join(parts) + f"concat=n={len(parts)}:v=1:a=0[v]"
    maps = ["-map", "[v]"]
    pre, t_fa, t_fb = (n - a0) / fps, fa / fps, fb / fps
    b_at = pre + (total - fb) / fps  # where B's own time zero lands in the result
    au = []
    if ma["audio"]:
        fade = f",afade=t=out:st={pre:.3f}:d={t_fa:.3f}" if fa else ""
        fc += f";[0:a]aresample=48000,aformat=channel_layouts=stereo,atrim=start={a0 / fps:.3f},asetpts=PTS-STARTPTS{fade}[aa]"
        au.append("[aa]")
    if mb["audio"]:
        end = f",atrim=end={b1 / fps:.3f}" if b1 is not None else ""
        fade = f",afade=t=in:st=0:d={t_fb:.3f}" if fb else ""
        fc += (f";[1:a]aresample=48000,aformat=channel_layouts=stereo{end},asetpts=PTS-STARTPTS{fade},"
               f"adelay={int(round(b_at * 1000))}:all=1[ab]")
        au.append("[ab]")
    if au:
        fc += ";" + "".join(au) + (f"amix=inputs={len(au)}:normalize=0:duration=longest[a]" if len(au) > 1 else "anull[a]")
        maps += ["-map", "[a]", "-c:a", "aac", "-b:a", "192k"]
    frames_out = (n - a0) + total + ((b1 if b1 is not None else nb) - fb)
    cmd = ["ffmpeg", "-y", "-i", str(a), "-i", str(b), "-i", str(seg), "-filter_complex", fc, *maps,
           "-c:v", "libx264", "-crf", "24" if preview else "18", "-preset", "veryfast" if preview else "medium",
           "-pix_fmt", "yuv420p", "-t", f"{frames_out / fps:.3f}", "-movflags", "+faststart", str(out)]
    try:
        run(cmd, timeout=1800)
    finally:
        seg.unlink(missing_ok=True)
    return {"transition_at": round(pre, 2), "transition_len": round(total / fps, 2), "duration": round(frames_out / fps, 2),
            "taken_a": round(t_fa, 2), "taken_b": round(t_fb, 2)}
