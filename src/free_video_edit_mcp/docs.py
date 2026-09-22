"""FFmpeg help/docs helpers for the installed build."""

from __future__ import annotations

from typing import Any

from free_video_edit_mcp.ffmpeg_runner import find_binary, run_argv, run_ffmpeg


def get_ffmpeg_version_info() -> dict[str, Any]:
    ff = find_binary("ffmpeg")
    fp = find_binary("ffprobe")
    ff_v = run_argv([ff, "-version"])
    fp_v = run_argv([fp, "-version"])
    ff_first = (ff_v.get("stdout") or "").splitlines()[:6]
    fp_first = (fp_v.get("stdout") or "").splitlines()[:3]
    config = ""
    for line in ff_first:
        if "configuration:" in line:
            config = line.split("configuration:", 1)[-1].strip()
            break
    return {
        "ok": ff_v["ok"] and fp_v["ok"],
        "ffmpeg_path": ff,
        "ffprobe_path": fp,
        "ffmpeg_version_lines": ff_first,
        "ffprobe_version_lines": fp_first,
        "configuration": config,
    }


def _help_text(topic: str) -> str:
    """Return help text for -h topic (e.g. filter=scale, encoder=libx264)."""
    result = run_argv([find_binary("ffmpeg"), "-hide_banner", "-h", topic])
    # ffmpeg prints help to stdout or stderr depending on build
    text = (result.get("stdout") or "") + "\n" + (result.get("stderr") or "")
    return text.strip()


def get_docs(name: str, kind: str = "auto") -> dict[str, Any]:
    """
    Get help for a filter, encoder, decoder, or muxer by name.
    kind: auto | filter | encoder | decoder | muxer | demuxer
    """
    name = name.strip()
    if not name:
        return {"ok": False, "error": "name is required"}

    kinds_to_try: list[str]
    if kind == "auto":
        kinds_to_try = ["filter", "encoder", "decoder", "muxer", "demuxer"]
    else:
        kinds_to_try = [kind]

    for k in kinds_to_try:
        topic = f"{k}={name}"
        text = _help_text(topic)
        # Heuristic: real help mentions the name and is longer than a short error
        if name.lower() in text.lower() and len(text) > 80 and "Unknown" not in text[:200]:
            return {"ok": True, "kind": k, "name": name, "help": text[:12000]}
        if f"{k}={name}" in text or f"Filter {name}" in text:
            return {"ok": True, "kind": k, "name": name, "help": text[:12000]}

    # Last resort: dump filter help list snippet
    listing = _help_text("filter=help")
    return {
        "ok": False,
        "name": name,
        "kind_tried": kinds_to_try,
        "error": f"No detailed help found for '{name}'. Try search_ffmpeg_docs.",
        "hint": listing[:500],
    }


def search_docs(query: str, limit: int = 20) -> dict[str, Any]:
    """Search filter/encoder names and short descriptions for a query."""
    q = query.strip().lower()
    if not q:
        return {"ok": False, "error": "query is required"}

    hits: list[dict[str, str]] = []

    # Filters
    filters_out = run_argv([find_binary("ffmpeg"), "-hide_banner", "-filters"])
    filt_text = (filters_out.get("stdout") or "") + (filters_out.get("stderr") or "")
    for line in filt_text.splitlines():
        if q in line.lower():
            hits.append({"category": "filter", "line": line.strip()})
            if len(hits) >= limit:
                break

    # Encoders
    if len(hits) < limit:
        enc = run_argv([find_binary("ffmpeg"), "-hide_banner", "-encoders"])
        enc_text = (enc.get("stdout") or "") + (enc.get("stderr") or "")
        for line in enc_text.splitlines():
            if q in line.lower():
                hits.append({"category": "encoder", "line": line.strip()})
                if len(hits) >= limit:
                    break

    # Decoders
    if len(hits) < limit:
        dec = run_argv([find_binary("ffmpeg"), "-hide_banner", "-decoders"])
        dec_text = (dec.get("stdout") or "") + (dec.get("stderr") or "")
        for line in dec_text.splitlines():
            if q in line.lower():
                hits.append({"category": "decoder", "line": line.strip()})
                if len(hits) >= limit:
                    break

    return {"ok": True, "query": query, "count": len(hits), "hits": hits[:limit]}


def whats_new(server_version: str) -> dict[str, Any]:
    info = get_ffmpeg_version_info()
    flags = info.get("configuration") or ""
    # Summarize a few notable build flags
    notable = []
    for flag in [
        "--enable-libx264",
        "--enable-libx265",
        "--enable-libvpx",
        "--enable-libaom",
        "--enable-libsvtav1",
        "--enable-gpl",
        "--enable-libopus",
        "--enable-libvorbis",
        "--enable-libmp3lame",
        "--enable-libzimg",
    ]:
        if flag in flags:
            notable.append(flag)
    return {
        "ok": True,
        "server_version": server_version,
        "notes": [
            "Public FFmpeg tool-surface MCP (not affiliated with Blackmagic Design).",
            "Deterministic JSON results from every edit tool for agent verification.",
            "LUT generate/apply via .cube + lut3d (creative approx, not Resolve CMS).",
            "Timeline compose is sequential track-0 concat with optional re-encode.",
            "Markers stored in sidecar JSON (FFmpeg has no Resolve-style markers).",
        ],
        "ffmpeg_version_lines": info.get("ffmpeg_version_lines"),
        "notable_build_flags": notable,
        "configuration_length": len(flags),
    }
