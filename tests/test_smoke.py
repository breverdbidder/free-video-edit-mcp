"""Smoke tests: generate tiny media, trim, concat, LUT, verify."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

# Ensure package importable when run without install
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from free_video_edit_mcp import __version__
from free_video_edit_mcp.color import generate_cube_lut, validate_cube_file
from free_video_edit_mcp.ffmpeg_runner import find_binary, run_ffmpeg
from free_video_edit_mcp.media import probe_file
from free_video_edit_mcp.server import (
    concat_clips,
    compose_timeline,
    generate_lut,
    get_version,
    get_whats_new,
    run_render,
    search_ffmpeg_docs,
    set_render_job,
    trim_clip,
    verify_edit,
    add_marker,
    list_markers,
)


@pytest.fixture(scope="module")
def media_root(tmp_path_factory):
    root = tmp_path_factory.mktemp("video-projects")
    os.environ["FREE_VIDEO_EDIT_ROOT"] = str(root)
    # Reload jobs against new root
    from free_video_edit_mcp import server as srv

    srv._RENDER_JOBS.clear()
    return root


def _gen_clip(path: Path, color: str, duration: float = 2.0, freq: int = 440) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    args = [
        "-f",
        "lavfi",
        "-i",
        f"color=c={color}:s=320x240:d={duration}:r=30",
        "-f",
        "lavfi",
        "-i",
        f"sine=frequency={freq}:duration={duration}",
        "-c:v",
        "libx264",
        "-pix_fmt",
        "yuv420p",
        "-c:a",
        "aac",
        "-shortest",
        str(path),
    ]
    result = run_ffmpeg(args, overwrite=True)
    assert result["ok"], result["stderr_tail"]


@pytest.fixture(scope="module")
def clips(media_root: Path):
    a = media_root / "a.mp4"
    b = media_root / "b.mp4"
    _gen_clip(a, "blue", 2.0, 440)
    _gen_clip(b, "red", 2.0, 880)
    return a, b


def test_binaries_present():
    assert find_binary("ffmpeg")
    assert find_binary("ffprobe")


def test_get_version(media_root):
    info = get_version()
    assert info["ok"]
    assert info["server_version"] == __version__
    assert info["ffmpeg_path"]


def test_probe_and_trim(clips, media_root):
    a, _ = clips
    probe = probe_file(str(a), allow_any_path=True)
    assert probe["ok"]
    assert probe["summary"]["duration"] == pytest.approx(2.0, abs=0.3)
    assert probe["summary"]["video"]["width"] == 320

    out = media_root / "a_trim.mp4"
    result = trim_clip(
        str(a),
        str(out),
        start=0.0,
        duration=1.0,
        overwrite=True,
        allow_any_path=True,
    )
    assert result["ok"], result.get("stderr_tail")
    v = verify_edit(
        str(out),
        expected_duration=1.0,
        duration_tolerance=0.4,
        expected_width=320,
        expected_height=240,
        allow_any_path=True,
    )
    assert v["pass"], v


def test_concat_filter(clips, media_root):
    a, b = clips
    out = media_root / "ab.mp4"
    result = concat_clips(
        [str(a), str(b)],
        str(out),
        method="filter",
        overwrite=True,
        allow_any_path=True,
    )
    assert result["ok"], result.get("stderr_tail")
    v = verify_edit(
        str(out),
        min_duration=3.5,
        max_duration=4.8,
        expected_clip_count=2,
        allow_any_path=True,
    )
    assert v["pass"], v


def test_compose_timeline(clips, media_root):
    a, b = clips
    out = media_root / "timeline_out.mp4"
    result = compose_timeline(
        {
            "name": "demo",
            "width": 320,
            "height": 240,
            "fps": 30,
            "clips": [
                {"path": str(a), "start": 0.0, "end": 1.0, "track": 0},
                {"path": str(b), "start": 0.0, "end": 1.0, "track": 0},
            ],
        },
        str(out),
        save_as="demo",
        overwrite=True,
        allow_any_path=True,
    )
    assert result["ok"], result.get("stderr_tail")
    assert result.get("clip_count") == 2
    v = verify_edit(str(out), min_duration=1.6, max_duration=2.6, allow_any_path=True)
    assert v["pass"], v


def test_lut_generate_apply(clips, media_root):
    a, _ = clips
    lut = media_root / "warm.cube"
    gen = generate_lut(
        str(lut),
        size=17,
        exposure=0.2,
        contrast=1.1,
        saturation=1.2,
        temperature=25,
        allow_any_path=True,
    )
    assert gen["ok"]
    assert validate_cube_file(gen["path"])["ok"]

    out = media_root / "a_graded.mp4"
    from free_video_edit_mcp.server import apply_lut as apply_lut_tool

    applied = apply_lut_tool(
        str(a), str(lut), str(out), overwrite=True, allow_any_path=True
    )
    assert applied["ok"], applied.get("stderr_tail")
    assert out.exists()


def test_markers(media_root, clips):
    a, _ = clips
    r = add_marker(str(a), 0.5, "intro", note="start", allow_any_path=True)
    assert r["ok"]
    listed = list_markers(str(a), allow_any_path=True)
    assert listed["count"] >= 1
    assert listed["markers"][0]["name"] == "intro"


def test_render_job(clips, media_root):
    a, _ = clips
    out = media_root / "a_export.mp4"
    job = set_render_job("j1", str(a), str(out), preset="h264_mp4")
    assert job["ok"]
    result = run_render("j1", overwrite=True, allow_any_path=True)
    assert result["ok"], result.get("stderr_tail")
    assert Path(result["output"]).exists()


def test_docs_search(media_root):
    hits = search_ffmpeg_docs("scale", limit=5)
    assert hits["ok"]
    assert hits["count"] >= 1
    news = get_whats_new()
    assert news["ok"]
    assert news["server_version"] == __version__


def test_verify_fail(clips, media_root):
    a, _ = clips
    v = verify_edit(str(a), expected_duration=99.0, duration_tolerance=0.1, allow_any_path=True)
    assert v["ok"]
    assert v["pass"] is False
    assert v["diffs"]
