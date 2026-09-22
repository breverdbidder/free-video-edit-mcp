"""MCP server: free FFmpeg-based video editing tools for AI agents."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from mcp.server import MCPServer

from free_video_edit_mcp import __version__
from free_video_edit_mcp import color as color_mod
from free_video_edit_mcp import docs as docs_mod
from free_video_edit_mcp.ffmpeg_runner import ensure_parent_dir, run_ffmpeg as ffmpeg_exec
from free_video_edit_mcp.media import get_media_root, probe_file, resolve_path
from free_video_edit_mcp.timeline import (
    Timeline,
    add_marker_to_sidecar,
    compose_timeline_to_file,
    list_markers_from_sidecar,
    save_timeline,
)

SERVER_VERSION = __version__

mcp = MCPServer(
    name="free-video-edit-mcp",
    version=SERVER_VERSION,
    instructions=(
        "Free open-source MCP for programmatic video editing via FFmpeg/ffprobe. "
        "Prefer structured tools (trim_clip, concat_clips, compose_timeline, apply_lut, "
        "verify_edit). Use run_ffmpeg as an escape hatch with argv lists (never shell strings). "
        "Default media root is FREE_VIDEO_EDIT_ROOT or ~/video-projects."
    ),
)

# In-memory render job presets (also persisted under media root when set)
_RENDER_JOBS: dict[str, dict[str, Any]] = {}

PRESETS: dict[str, dict[str, Any]] = {
    "h264_mp4": {
        "ext": ".mp4",
        "video": ["-c:v", "libx264", "-preset", "medium", "-crf", "23"],
        "audio": ["-c:a", "aac", "-b:a", "192k"],
        "extra": ["-movflags", "+faststart"],
    },
    "prores": {
        "ext": ".mov",
        "video": ["-c:v", "prores_ks", "-profile:v", "3"],
        "audio": ["-c:a", "pcm_s16le"],
        "extra": [],
    },
    "webm": {
        "ext": ".webm",
        "video": ["-c:v", "libvpx-vp9", "-b:v", "0", "-crf", "32"],
        "audio": ["-c:a", "libopus", "-b:a", "128k"],
        "extra": [],
    },
}


def _jobs_path() -> Path:
    p = get_media_root() / "render_jobs.json"
    return p


def _load_jobs() -> None:
    path = _jobs_path()
    if path.exists():
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                _RENDER_JOBS.update(data)
        except json.JSONDecodeError:
            pass


def _save_jobs() -> None:
    path = _jobs_path()
    ensure_parent_dir(path)
    path.write_text(json.dumps(_RENDER_JOBS, indent=2), encoding="utf-8")


_load_jobs()


@mcp.tool()
def get_version() -> dict[str, Any]:
    """Return ffmpeg/ffprobe versions and this MCP server version."""
    info = docs_mod.get_ffmpeg_version_info()
    return {
        "ok": info["ok"],
        "server_version": SERVER_VERSION,
        "media_root": str(get_media_root()),
        **info,
    }


@mcp.tool()
def probe_media(path: str, allow_any_path: bool = False) -> dict[str, Any]:
    """Probe a media file with ffprobe. Returns streams, duration, resolution, codecs, and raw JSON."""
    return probe_file(path, allow_any_path=allow_any_path)


@mcp.tool()
def search_ffmpeg_docs(query: str, limit: int = 20) -> dict[str, Any]:
    """Search installed FFmpeg filters/encoders/decoders help listings for a query string."""
    return docs_mod.search_docs(query, limit=limit)


@mcp.tool()
def get_ffmpeg_docs(name: str, kind: str = "auto") -> dict[str, Any]:
    """Get detailed help for a filter, encoder, decoder, muxer, or demuxer by name (matches live build)."""
    return docs_mod.get_docs(name, kind=kind)


@mcp.tool()
def get_whats_new() -> dict[str, Any]:
    """Brief notes for this server version plus a summary of notable FFmpeg build flags."""
    return docs_mod.whats_new(SERVER_VERSION)


@mcp.tool()
def run_ffmpeg(
    args: list[str],
    working_directory: str | None = None,
    overwrite: bool = False,
    allow_any_path: bool = False,
    timeout_seconds: float = 600,
) -> dict[str, Any]:
    """Run ffmpeg with an argv argument list (NOT a shell string). Prefer structured edit tools when possible.

    Safety: output paths outside the media workspace are blocked unless allow_any_path=true.
    Uses -y only when overwrite=true; otherwise -n. Never uses shell=True.
    """
    if not args:
        return {"ok": False, "error": "args must be a non-empty list of strings"}

    # Scan for output-like trailing path and validate
    # Heuristic: last non-flag token that looks like a path is the output
    candidates = [a for a in args if not a.startswith("-") and ("/" in a or a.endswith((".mp4", ".mov", ".mkv", ".webm", ".wav", ".mp3", ".cube", ".png", ".jpg")))]
    try:
        for c in candidates[-1:]:
            resolve_path(c, must_exist=False, allow_any_path=allow_any_path)
            ensure_parent_dir(resolve_path(c, must_exist=False, allow_any_path=allow_any_path))
        # Validate inputs that exist as prior -i values
        for i, a in enumerate(args):
            if a == "-i" and i + 1 < len(args):
                resolve_path(args[i + 1], must_exist=True, allow_any_path=allow_any_path)
    except (PermissionError, FileNotFoundError, OSError) as exc:
        return {"ok": False, "error": str(exc)}

    cwd = working_directory
    if cwd:
        try:
            cwd_path = resolve_path(cwd, must_exist=True, allow_any_path=True)
            cwd = str(cwd_path)
        except FileNotFoundError as exc:
            return {"ok": False, "error": str(exc)}
    else:
        cwd = str(get_media_root())

    result = ffmpeg_exec(
        args,
        working_directory=cwd,
        timeout=timeout_seconds,
        overwrite=overwrite,
    )
    return {
        "ok": result["ok"],
        "exit_code": result["exit_code"],
        "argv": result["argv"],
        "cwd": result["cwd"],
        "stderr_tail": result["stderr_tail"],
        "stdout_tail": result["stdout_tail"],
        "output_candidates": candidates[-1:] if candidates else [],
    }


@mcp.tool()
def trim_clip(
    input_path: str,
    output_path: str,
    start: float = 0.0,
    end: float | None = None,
    duration: float | None = None,
    stream_copy: bool = False,
    overwrite: bool = True,
    allow_any_path: bool = False,
) -> dict[str, Any]:
    """Trim a clip to start/end or start+duration and write output_path."""
    try:
        src = resolve_path(input_path, must_exist=True, allow_any_path=allow_any_path)
        out = resolve_path(output_path, must_exist=False, allow_any_path=allow_any_path)
    except (PermissionError, FileNotFoundError) as exc:
        return {"ok": False, "error": str(exc)}

    ensure_parent_dir(out)
    if end is not None and duration is not None:
        return {"ok": False, "error": "provide end OR duration, not both"}

    args: list[str] = ["-ss", str(start), "-i", str(src)]
    if end is not None:
        dur = max(0.0, float(end) - float(start))
        args.extend(["-t", str(dur)])
    elif duration is not None:
        args.extend(["-t", str(duration)])

    if stream_copy:
        args.extend(["-c", "copy"])
    else:
        args.extend(["-c:v", "libx264", "-preset", "veryfast", "-crf", "23", "-c:a", "aac", "-b:a", "192k"])
    args.append(str(out))

    result = ffmpeg_exec(args, overwrite=overwrite)
    probe = probe_file(str(out), allow_any_path=allow_any_path) if result["ok"] and out.exists() else None
    return {
        "ok": result["ok"],
        "input": str(src),
        "output": str(out),
        "start": start,
        "end": end,
        "duration_requested": duration if duration is not None else (None if end is None else float(end) - float(start)),
        "exit_code": result["exit_code"],
        "stderr_tail": result["stderr_tail"],
        "probe_summary": (probe or {}).get("summary"),
    }


@mcp.tool()
def concat_clips(
    inputs: list[str],
    output_path: str,
    method: str = "demuxer",
    overwrite: bool = True,
    allow_any_path: bool = False,
) -> dict[str, Any]:
    """Concatenate clips. method='demuxer' (same codec, stream copy) or 'filter' (re-encode, more robust)."""
    if not inputs or len(inputs) < 1:
        return {"ok": False, "error": "inputs must contain at least one path"}
    try:
        paths = [resolve_path(p, must_exist=True, allow_any_path=allow_any_path) for p in inputs]
        out = resolve_path(output_path, must_exist=False, allow_any_path=allow_any_path)
    except (PermissionError, FileNotFoundError) as exc:
        return {"ok": False, "error": str(exc)}

    ensure_parent_dir(out)

    if method == "demuxer":
        list_file = out.with_suffix(out.suffix + ".concat.txt")
        lines = []
        for p in paths:
            # concat demuxer requires escaped single quotes
            escaped = str(p).replace("'", r"'\''")
            lines.append(f"file '{escaped}'")
        list_file.write_text("\n".join(lines) + "\n", encoding="utf-8")
        args = ["-f", "concat", "-safe", "0", "-i", str(list_file), "-c", "copy", str(out)]
        result = ffmpeg_exec(args, overwrite=overwrite)
        return {
            "ok": result["ok"],
            "method": "demuxer",
            "inputs": [str(p) for p in paths],
            "output": str(out),
            "concat_list": str(list_file),
            "exit_code": result["exit_code"],
            "stderr_tail": result["stderr_tail"],
        }

    # filter concat
    tl = {
        "name": "concat_tmp",
        "clips": [{"path": str(p), "start": 0.0, "end": None, "track": 0} for p in paths],
        "width": 1280,
        "height": 720,
        "fps": 30,
    }
    # Prefer source resolution from first clip if available
    first = probe_file(str(paths[0]), allow_any_path=allow_any_path)
    if first.get("ok") and first.get("summary", {}).get("video"):
        v = first["summary"]["video"]
        if v.get("width") and v.get("height"):
            tl["width"] = v["width"]
            tl["height"] = v["height"]

    composed = compose_timeline_to_file(
        tl, str(out), reencode=True, overwrite=overwrite, allow_any_path=allow_any_path
    )
    composed["method"] = "filter"
    composed["inputs"] = [str(p) for p in paths]
    return composed


@mcp.tool()
def compose_timeline(
    timeline: dict[str, Any],
    output_path: str,
    save_as: str | None = None,
    reencode: bool = True,
    overwrite: bool = True,
    allow_any_path: bool = False,
) -> dict[str, Any]:
    """Render a JSON timeline (clips with path, start/in, end/out, track) to one output file.

    Timeline schema: {name, clips:[{path,start,end,track,label}], width, height, fps, markers}.
    Track 0 clips are concatenated in order. Optional save_as persists the timeline JSON.
    """
    # Normalize in/out aliases
    clips = []
    for c in timeline.get("clips") or []:
        cc = dict(c)
        if "in" in cc and "start" not in cc:
            cc["start"] = cc.pop("in")
        if "out" in cc and "end" not in cc:
            cc["end"] = cc.pop("out")
        clips.append(cc)
    timeline = {**timeline, "clips": clips}

    if save_as:
        tl_obj = Timeline.from_dict({**timeline, "name": save_as})
        path = save_timeline(tl_obj)
    else:
        path = None

    result = compose_timeline_to_file(
        timeline,
        output_path,
        reencode=reencode,
        overwrite=overwrite,
        allow_any_path=allow_any_path,
    )
    if path:
        result["timeline_saved"] = str(path)
    return result


@mcp.tool()
def add_marker(
    target: str,
    time: float,
    name: str,
    note: str = "",
    color: str = "yellow",
    allow_any_path: bool = False,
) -> dict[str, Any]:
    """Add a marker to a sidecar JSON next to a media or timeline file (FFmpeg has no built-in markers)."""
    try:
        return add_marker_to_sidecar(
            target, time, name, note=note, color=color, allow_any_path=allow_any_path
        )
    except (PermissionError, OSError) as exc:
        return {"ok": False, "error": str(exc)}


@mcp.tool()
def list_markers(target: str, allow_any_path: bool = False) -> dict[str, Any]:
    """List markers from the sidecar JSON associated with a media or timeline file."""
    try:
        return list_markers_from_sidecar(target, allow_any_path=allow_any_path)
    except (PermissionError, OSError) as exc:
        return {"ok": False, "error": str(exc)}


@mcp.tool()
def generate_lut(
    output_path: str,
    size: int = 33,
    exposure: float = 0.0,
    contrast: float = 1.0,
    saturation: float = 1.0,
    temperature: float = 0.0,
    title: str = "free-video-edit-mcp",
    allow_any_path: bool = False,
) -> dict[str, Any]:
    """Generate a .cube 3D LUT from simple creative params (exposure stops, contrast, saturation, temperature).

    This is a free approximation for grading workflows — not DaVinci Resolve color management.
    """
    try:
        result = color_mod.generate_cube_lut(
            output_path,
            size=size,
            exposure=exposure,
            contrast=contrast,
            saturation=saturation,
            temperature=temperature,
            title=title,
            allow_any_path=allow_any_path,
        )
        validation = color_mod.validate_cube_file(result["path"])
        result["validation"] = validation
        return result
    except (PermissionError, ValueError, OSError) as exc:
        return {"ok": False, "error": str(exc)}


@mcp.tool()
def apply_lut(
    input_path: str,
    lut_path: str,
    output_path: str,
    interp: str = "tetrahedral",
    overwrite: bool = True,
    allow_any_path: bool = False,
) -> dict[str, Any]:
    """Apply a .cube LUT to a clip using ffmpeg lut3d."""
    try:
        return color_mod.apply_lut_to_clip(
            input_path,
            lut_path,
            output_path,
            interp=interp,
            overwrite=overwrite,
            allow_any_path=allow_any_path,
        )
    except (PermissionError, FileNotFoundError, OSError) as exc:
        return {"ok": False, "error": str(exc)}


@mcp.tool()
def set_render_job(
    job_id: str,
    input_path: str,
    output_path: str | None = None,
    preset: str = "h264_mp4",
    extra_args: list[str] | None = None,
) -> dict[str, Any]:
    """Define an export/render job with a preset: h264_mp4, prores, or webm."""
    if preset not in PRESETS:
        return {"ok": False, "error": f"unknown preset '{preset}'", "available": list(PRESETS)}
    job = {
        "job_id": job_id,
        "input_path": input_path,
        "output_path": output_path,
        "preset": preset,
        "extra_args": extra_args or [],
        "status": "queued",
    }
    _RENDER_JOBS[job_id] = job
    _save_jobs()
    return {"ok": True, "job": job, "presets": list(PRESETS)}


@mcp.tool()
def run_render(
    job_id: str,
    overwrite: bool = True,
    allow_any_path: bool = False,
) -> dict[str, Any]:
    """Run a previously defined render job and return exit code + output probe summary."""
    job = _RENDER_JOBS.get(job_id)
    if not job:
        return {"ok": False, "error": f"unknown job_id '{job_id}'", "known": list(_RENDER_JOBS)}

    preset = PRESETS[job["preset"]]
    try:
        src = resolve_path(job["input_path"], must_exist=True, allow_any_path=allow_any_path)
        if job.get("output_path"):
            out = resolve_path(job["output_path"], must_exist=False, allow_any_path=allow_any_path)
        else:
            out = src.with_name(src.stem + f"_{job['preset']}" + preset["ext"])
            out = resolve_path(str(out), must_exist=False, allow_any_path=allow_any_path)
    except (PermissionError, FileNotFoundError) as exc:
        job["status"] = "failed"
        _RENDER_JOBS[job_id] = job
        _save_jobs()
        return {"ok": False, "error": str(exc), "job": job}

    ensure_parent_dir(out)
    args = ["-i", str(src), *preset["video"], *preset["audio"], *preset["extra"], *(job.get("extra_args") or []), str(out)]
    result = ffmpeg_exec(args, overwrite=overwrite)
    job["status"] = "done" if result["ok"] else "failed"
    job["output_path"] = str(out)
    job["exit_code"] = result["exit_code"]
    _RENDER_JOBS[job_id] = job
    _save_jobs()

    probe = probe_file(str(out), allow_any_path=allow_any_path) if result["ok"] and out.exists() else None
    return {
        "ok": result["ok"],
        "job": job,
        "exit_code": result["exit_code"],
        "stderr_tail": result["stderr_tail"],
        "output": str(out),
        "probe_summary": (probe or {}).get("summary"),
    }


@mcp.tool()
def verify_edit(
    output_path: str,
    expected_duration: float | None = None,
    duration_tolerance: float = 0.35,
    expected_width: int | None = None,
    expected_height: int | None = None,
    expected_clip_count: int | None = None,
    min_duration: float | None = None,
    max_duration: float | None = None,
    allow_any_path: bool = False,
) -> dict[str, Any]:
    """Compare an output file against an expected edit plan (duration, resolution). Returns pass/fail + diffs.

    This is the deterministic verification bridge: agents can assert plan vs probe results.
    expected_clip_count is recorded for plan bookkeeping (single rendered file cannot recover clip count).
    """
    try:
        probe = probe_file(output_path, allow_any_path=allow_any_path)
    except (PermissionError, FileNotFoundError) as exc:
        return {"ok": False, "pass": False, "error": str(exc)}

    if not probe.get("ok"):
        return {"ok": False, "pass": False, "error": "probe failed", "probe": probe}

    summary = probe["summary"]
    diffs: list[dict[str, Any]] = []
    passed = True

    dur = summary.get("duration")
    if expected_duration is not None:
        if dur is None:
            passed = False
            diffs.append({"field": "duration", "expected": expected_duration, "actual": None, "reason": "missing"})
        elif abs(dur - expected_duration) > duration_tolerance:
            passed = False
            diffs.append(
                {
                    "field": "duration",
                    "expected": expected_duration,
                    "actual": dur,
                    "delta": dur - expected_duration,
                    "tolerance": duration_tolerance,
                }
            )

    if min_duration is not None and (dur is None or dur < min_duration):
        passed = False
        diffs.append({"field": "min_duration", "expected_gte": min_duration, "actual": dur})
    if max_duration is not None and (dur is None or dur > max_duration):
        passed = False
        diffs.append({"field": "max_duration", "expected_lte": max_duration, "actual": dur})

    video = summary.get("video") or {}
    if expected_width is not None and video.get("width") != expected_width:
        passed = False
        diffs.append({"field": "width", "expected": expected_width, "actual": video.get("width")})
    if expected_height is not None and video.get("height") != expected_height:
        passed = False
        diffs.append({"field": "height", "expected": expected_height, "actual": video.get("height")})

    return {
        "ok": True,
        "pass": passed,
        "output": summary.get("path"),
        "summary": summary,
        "diffs": diffs,
        "expected_clip_count": expected_clip_count,
        "note": "clip_count cannot be recovered from a single rendered file; use for plan bookkeeping only",
    }


def main(argv: list[str] | None = None) -> None:
    """Start the MCP server.

    Transports (mcp Python SDK 2.x):
      - stdio (default): Cursor, Claude Desktop, and other local MCP hosts
      - streamable-http: remote / ChatGPT-style connectors (prefer this over sse)
      - sse: legacy HTTP+SSE transport

    Examples:
      python -m free_video_edit_mcp
      python -m free_video_edit_mcp --transport streamable-http --host 127.0.0.1 --port 8765
      python -m free_video_edit_mcp --transport http --host 0.0.0.0 --port 8765
    """
    import argparse

    parser = argparse.ArgumentParser(
        prog="free-video-edit-mcp",
        description="Free FFmpeg video-edit MCP server (stdio or streamable HTTP)",
    )
    parser.add_argument(
        "--transport",
        choices=("stdio", "streamable-http", "http", "sse"),
        default="stdio",
        help="MCP transport. 'http' is an alias for 'streamable-http' (ChatGPT-friendly).",
    )
    parser.add_argument("--host", default="127.0.0.1", help="Bind host for HTTP transports")
    parser.add_argument("--port", type=int, default=8765, help="Bind port for HTTP transports")
    parser.add_argument(
        "--path",
        default="/mcp",
        help="URL path for streamable-http (default /mcp) or SSE mount path",
    )
    args = parser.parse_args(argv)

    transport = args.transport
    if transport == "http":
        transport = "streamable-http"

    if transport == "stdio":
        mcp.run(transport="stdio")
        return

    if transport == "streamable-http":
        mcp.run(
            transport="streamable-http",
            host=args.host,
            port=args.port,
            streamable_http_path=args.path,
        )
        return

    # sse (legacy; prefer streamable-http for new remote clients)
    mcp.run(
        transport="sse",
        host=args.host,
        port=args.port,
        sse_path=args.path if args.path != "/mcp" else "/sse",
    )


if __name__ == "__main__":
    main()
