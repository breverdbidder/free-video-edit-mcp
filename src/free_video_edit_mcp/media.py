"""Media path validation, workspace roots, and ffprobe helpers."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from free_video_edit_mcp.ffmpeg_runner import run_ffprobe


def get_media_root() -> Path:
    """Media workspace root.

    Order: FREE_VIDEO_EDIT_ROOT env, else ~/video-projects.
    The directory is created if missing.
    """
    default = str(Path.home() / "video-projects")
    root = os.environ.get("FREE_VIDEO_EDIT_ROOT", default)
    path = Path(root).expanduser().resolve()
    path.mkdir(parents=True, exist_ok=True)
    return path


def resolve_path(
    path: str,
    *,
    must_exist: bool = False,
    allow_any_path: bool = False,
) -> Path:
    """Resolve a path. By default restrict writes/reads under media root unless allow_any_path."""
    p = Path(path).expanduser()
    if not p.is_absolute():
        p = get_media_root() / p
    p = p.resolve()

    if not allow_any_path:
        root = get_media_root()
        try:
            p.relative_to(root)
        except ValueError as exc:
            raise PermissionError(
                f"Path '{p}' is outside media workspace '{root}'. "
                "Pass allow_any_path=true to override (use with care)."
            ) from exc

    if must_exist and not p.exists():
        raise FileNotFoundError(f"Path does not exist: {p}")
    return p


def probe_file(path: str | Path, *, allow_any_path: bool = False) -> dict[str, Any]:
    p = resolve_path(str(path), must_exist=True, allow_any_path=allow_any_path)
    result = run_ffprobe(
        [
            "-v",
            "quiet",
            "-print_format",
            "json",
            "-show_format",
            "-show_streams",
            str(p),
        ]
    )
    if not result["ok"]:
        return {
            "ok": False,
            "path": str(p),
            "exit_code": result["exit_code"],
            "stderr_tail": result["stderr_tail"],
            "error": "ffprobe failed",
        }

    try:
        data = json.loads(result["stdout"] or "{}")
    except json.JSONDecodeError:
        return {
            "ok": False,
            "path": str(p),
            "error": "ffprobe returned invalid JSON",
            "stdout_tail": result["stdout_tail"],
        }

    streams = data.get("streams") or []
    fmt = data.get("format") or {}
    video = next((s for s in streams if s.get("codec_type") == "video"), None)
    audio = next((s for s in streams if s.get("codec_type") == "audio"), None)

    duration = None
    if fmt.get("duration") is not None:
        try:
            duration = float(fmt["duration"])
        except (TypeError, ValueError):
            duration = None

    summary = {
        "path": str(p),
        "duration": duration,
        "format_name": fmt.get("format_name"),
        "size_bytes": int(fmt["size"]) if fmt.get("size") else None,
        "bit_rate": int(fmt["bit_rate"]) if fmt.get("bit_rate") else None,
        "video": None,
        "audio": None,
    }
    if video:
        summary["video"] = {
            "codec": video.get("codec_name"),
            "width": video.get("width"),
            "height": video.get("height"),
            "pix_fmt": video.get("pix_fmt"),
            "avg_frame_rate": video.get("avg_frame_rate"),
            "r_frame_rate": video.get("r_frame_rate"),
            "nb_frames": video.get("nb_frames"),
        }
    if audio:
        summary["audio"] = {
            "codec": audio.get("codec_name"),
            "sample_rate": audio.get("sample_rate"),
            "channels": audio.get("channels"),
            "channel_layout": audio.get("channel_layout"),
        }

    return {
        "ok": True,
        "path": str(p),
        "summary": summary,
        "raw": data,
    }


def is_under_root(path: Path, root: Path | None = None) -> bool:
    root = root or get_media_root()
    try:
        path.resolve().relative_to(root.resolve())
        return True
    except ValueError:
        return False
