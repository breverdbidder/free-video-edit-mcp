"""Simple timeline model (clips, markers) persisted as JSON."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from free_video_edit_mcp.ffmpeg_runner import ensure_parent_dir, run_ffmpeg
from free_video_edit_mcp.media import get_media_root, resolve_path


@dataclass
class TimelineClip:
    path: str
    start: float = 0.0  # source in-point (seconds)
    end: float | None = None  # source out-point (seconds); None = to EOF
    track: int = 0
    label: str = ""


@dataclass
class Marker:
    time: float
    name: str
    note: str = ""
    color: str = "yellow"


@dataclass
class Timeline:
    name: str
    clips: list[TimelineClip] = field(default_factory=list)
    markers: list[Marker] = field(default_factory=list)
    width: int = 1280
    height: int = 720
    fps: float = 30.0
    audio_sample_rate: int = 48000

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Timeline":
        clips = [TimelineClip(**c) for c in data.get("clips") or []]
        markers = [Marker(**m) for m in data.get("markers") or []]
        return cls(
            name=data.get("name") or "untitled",
            clips=clips,
            markers=markers,
            width=int(data.get("width") or 1280),
            height=int(data.get("height") or 720),
            fps=float(data.get("fps") or 30.0),
            audio_sample_rate=int(data.get("audio_sample_rate") or 48000),
        )


def timeline_path(name: str) -> Path:
    safe = "".join(c if c.isalnum() or c in "-_" else "_" for c in name)
    return get_media_root() / "timelines" / f"{safe}.json"


def save_timeline(tl: Timeline) -> Path:
    path = timeline_path(tl.name)
    ensure_parent_dir(path)
    path.write_text(json.dumps(tl.to_dict(), indent=2), encoding="utf-8")
    return path


def load_timeline(name: str) -> Timeline:
    path = timeline_path(name)
    if not path.exists():
        raise FileNotFoundError(f"Timeline not found: {path}")
    data = json.loads(path.read_text(encoding="utf-8"))
    return Timeline.from_dict(data)


def markers_sidecar_path(target: str | Path) -> Path:
    p = Path(target)
    return p.with_suffix(p.suffix + ".markers.json")


def add_marker_to_sidecar(
    target: str,
    time: float,
    name: str,
    note: str = "",
    color: str = "yellow",
    *,
    allow_any_path: bool = False,
) -> dict[str, Any]:
    path = resolve_path(target, must_exist=False, allow_any_path=allow_any_path)
    side = markers_sidecar_path(path)
    ensure_parent_dir(side)
    data: dict[str, Any]
    if side.exists():
        data = json.loads(side.read_text(encoding="utf-8"))
    else:
        data = {"target": str(path), "markers": []}
    marker = {"time": float(time), "name": name, "note": note, "color": color}
    data.setdefault("markers", []).append(marker)
    data["markers"].sort(key=lambda m: m.get("time", 0))
    side.write_text(json.dumps(data, indent=2), encoding="utf-8")
    return {"ok": True, "sidecar": str(side), "marker": marker, "count": len(data["markers"])}


def list_markers_from_sidecar(
    target: str, *, allow_any_path: bool = False
) -> dict[str, Any]:
    path = resolve_path(target, must_exist=False, allow_any_path=allow_any_path)
    side = markers_sidecar_path(path)
    if not side.exists():
        return {"ok": True, "sidecar": str(side), "markers": [], "count": 0}
    data = json.loads(side.read_text(encoding="utf-8"))
    markers = data.get("markers") or []
    return {"ok": True, "sidecar": str(side), "markers": markers, "count": len(markers)}


def _clip_duration(clip: TimelineClip) -> float | None:
    if clip.end is None:
        return None
    return max(0.0, float(clip.end) - float(clip.start))


def compose_timeline_to_file(
    timeline: dict[str, Any] | Timeline,
    output: str,
    *,
    reencode: bool = True,
    overwrite: bool = True,
    allow_any_path: bool = False,
) -> dict[str, Any]:
    """Render a simple sequential timeline (track 0 clips in order) to one file."""
    tl = timeline if isinstance(timeline, Timeline) else Timeline.from_dict(timeline)
    if not tl.clips:
        return {"ok": False, "error": "timeline has no clips"}

    out = resolve_path(output, must_exist=False, allow_any_path=allow_any_path)
    ensure_parent_dir(out)

    # Sort by track then keep order; for v0.1 we concat track 0 sequentially.
    clips = [c for c in tl.clips if c.track == 0] or list(tl.clips)
    if len(clips) == 1 and not reencode:
        clip = clips[0]
        src = resolve_path(clip.path, must_exist=True, allow_any_path=allow_any_path)
        args = ["-i", str(src)]
        if clip.start:
            args = ["-ss", str(clip.start), "-i", str(src)]
        if clip.end is not None:
            dur = _clip_duration(clip)
            if dur is not None:
                args.extend(["-t", str(dur)])
        args.extend(["-c", "copy", str(out)])
        result = run_ffmpeg(args, overwrite=overwrite)
        return {
            "ok": result["ok"],
            "output": str(out),
            "method": "stream_copy_single",
            "exit_code": result["exit_code"],
            "stderr_tail": result["stderr_tail"],
            "clip_count": 1,
        }

    # Re-encode path using filter_complex concat for reliable A/V alignment.
    inputs: list[str] = []
    filter_parts: list[str] = []
    v_labels: list[str] = []
    a_labels: list[str] = []
    has_audio = True

    for i, clip in enumerate(clips):
        src = resolve_path(clip.path, must_exist=True, allow_any_path=allow_any_path)
        inputs.extend(["-i", str(src)])
        start = float(clip.start or 0.0)
        end = clip.end
        trim_v = f"[{i}:v]trim=start={start}"
        if end is not None:
            trim_v += f":end={end}"
        trim_v += (
            f",setpts=PTS-STARTPTS,scale={tl.width}:{tl.height}:force_original_aspect_ratio=decrease,"
            f"pad={tl.width}:{tl.height}:(ow-iw)/2:(oh-ih)/2,fps={tl.fps},format=yuv420p[v{i}]"
        )
        filter_parts.append(trim_v)
        v_labels.append(f"[v{i}]")

        trim_a = f"[{i}:a]atrim=start={start}"
        if end is not None:
            trim_a += f":end={end}"
        trim_a += (
            f",asetpts=PTS-STARTPTS,aresample={tl.audio_sample_rate},"
            f"aformat=sample_fmts=fltp:channel_layouts=stereo[a{i}]"
        )
        filter_parts.append(trim_a)
        a_labels.append(f"[a{i}]")

    n = len(clips)
    filter_parts.append(
        "".join(v_labels) + "".join(a_labels) + f"concat=n={n}:v=1:a=1[vout][aout]"
    )
    filter_complex = ";".join(filter_parts)

    args = [
        *inputs,
        "-filter_complex",
        filter_complex,
        "-map",
        "[vout]",
        "-map",
        "[aout]",
        "-c:v",
        "libx264",
        "-preset",
        "veryfast",
        "-crf",
        "23",
        "-c:a",
        "aac",
        "-b:a",
        "192k",
        "-movflags",
        "+faststart",
        str(out),
    ]
    result = run_ffmpeg(args, overwrite=overwrite)
    # If audio missing on some inputs, retry video-only.
    if not result["ok"] and "Stream map" in (result.get("stderr") or ""):
        has_audio = False
    if not result["ok"]:
        # Fallback: video-only concat
        filter_parts_v: list[str] = []
        v_labels = []
        for i, clip in enumerate(clips):
            start = float(clip.start or 0.0)
            end = clip.end
            trim_v = f"[{i}:v]trim=start={start}"
            if end is not None:
                trim_v += f":end={end}"
            trim_v += (
                f",setpts=PTS-STARTPTS,scale={tl.width}:{tl.height}:force_original_aspect_ratio=decrease,"
                f"pad={tl.width}:{tl.height}:(ow-iw)/2:(oh-ih)/2,fps={tl.fps},format=yuv420p[v{i}]"
            )
            filter_parts_v.append(trim_v)
            v_labels.append(f"[v{i}]")
        filter_parts_v.append("".join(v_labels) + f"concat=n={n}:v=1:a=0[vout]")
        args = [
            *inputs,
            "-filter_complex",
            ";".join(filter_parts_v),
            "-map",
            "[vout]",
            "-c:v",
            "libx264",
            "-preset",
            "veryfast",
            "-crf",
            "23",
            "-an",
            "-movflags",
            "+faststart",
            str(out),
        ]
        result = run_ffmpeg(args, overwrite=overwrite)
        has_audio = False

    return {
        "ok": result["ok"],
        "output": str(out),
        "method": "filter_concat",
        "has_audio": has_audio,
        "exit_code": result["exit_code"],
        "stderr_tail": result["stderr_tail"],
        "clip_count": n,
        "width": tl.width,
        "height": tl.height,
        "fps": tl.fps,
    }
