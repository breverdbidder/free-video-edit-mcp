"""Generate and apply .cube 3D LUTs via simple creative params (not Resolve color science)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from free_video_edit_mcp.ffmpeg_runner import ensure_parent_dir, run_ffmpeg
from free_video_edit_mcp.media import resolve_path


def _clamp(v: float, lo: float = 0.0, hi: float = 1.0) -> float:
    return max(lo, min(hi, v))


def _apply_params(
    r: float,
    g: float,
    b: float,
    *,
    exposure: float,
    contrast: float,
    saturation: float,
    temperature: float,
) -> tuple[float, float, float]:
    # Exposure: stops → linear gain
    gain = 2.0 ** exposure
    r, g, b = r * gain, g * gain, b * gain

    # Simple temperature shift: warm (+) boosts R, cool (+) boosts B
    # temperature in approximate "mired-ish" units mapped to [-1, 1] style
    t = temperature / 100.0  # e.g. +20 → slight warm
    r = r * (1.0 + 0.15 * t)
    b = b * (1.0 - 0.15 * t)

    # Contrast around mid-gray 0.5
    r = (r - 0.5) * contrast + 0.5
    g = (g - 0.5) * contrast + 0.5
    b = (b - 0.5) * contrast + 0.5

    # Saturation
    luma = 0.2126 * r + 0.7152 * g + 0.0722 * b
    r = luma + (r - luma) * saturation
    g = luma + (g - luma) * saturation
    b = luma + (b - luma) * saturation

    return _clamp(r), _clamp(g), _clamp(b)


def generate_cube_lut(
    output: str,
    *,
    size: int = 33,
    exposure: float = 0.0,
    contrast: float = 1.0,
    saturation: float = 1.0,
    temperature: float = 0.0,
    title: str = "free-video-edit-mcp",
    allow_any_path: bool = False,
) -> dict[str, Any]:
    """Write a .cube LUT. Identity when all creative params are default."""
    if size < 2 or size > 65:
        raise ValueError("size must be between 2 and 65")
    if contrast <= 0:
        raise ValueError("contrast must be > 0")
    if saturation < 0:
        raise ValueError("saturation must be >= 0")

    out = resolve_path(output, must_exist=False, allow_any_path=allow_any_path)
    if out.suffix.lower() != ".cube":
        out = out.with_suffix(".cube")
    ensure_parent_dir(out)

    lines = [
        f'TITLE "{title}"',
        f"LUT_3D_SIZE {size}",
        "DOMAIN_MIN 0.0 0.0 0.0",
        "DOMAIN_MAX 1.0 1.0 1.0",
    ]

    denom = float(size - 1)
    for bi in range(size):
        for gi in range(size):
            for ri in range(size):
                r = ri / denom
                g = gi / denom
                b = bi / denom
                rr, gg, bb = _apply_params(
                    r,
                    g,
                    b,
                    exposure=exposure,
                    contrast=contrast,
                    saturation=saturation,
                    temperature=temperature,
                )
                lines.append(f"{rr:.6f} {gg:.6f} {bb:.6f}")

    out.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return {
        "ok": True,
        "path": str(out),
        "size": size,
        "params": {
            "exposure": exposure,
            "contrast": contrast,
            "saturation": saturation,
            "temperature": temperature,
        },
        "entries": size ** 3,
    }


def apply_lut_to_clip(
    input_path: str,
    lut_path: str,
    output: str,
    *,
    interp: str = "tetrahedral",
    overwrite: bool = True,
    allow_any_path: bool = False,
) -> dict[str, Any]:
    """Apply a .cube LUT with ffmpeg lut3d filter."""
    src = resolve_path(input_path, must_exist=True, allow_any_path=allow_any_path)
    lut = resolve_path(lut_path, must_exist=True, allow_any_path=allow_any_path)
    out = resolve_path(output, must_exist=False, allow_any_path=allow_any_path)
    ensure_parent_dir(out)

    # Escape path for filtergraph (replace \ and : and ')
    lut_escaped = (
        str(lut).replace("\\", "/").replace(":", "\\:").replace("'", "\\'")
    )
    vf = f"lut3d=file='{lut_escaped}':interp={interp}"
    args = [
        "-i",
        str(src),
        "-vf",
        vf,
        "-c:v",
        "libx264",
        "-preset",
        "veryfast",
        "-crf",
        "20",
        "-c:a",
        "copy",
        str(out),
    ]
    result = run_ffmpeg(args, overwrite=overwrite)
    return {
        "ok": result["ok"],
        "input": str(src),
        "lut": str(lut),
        "output": str(out),
        "exit_code": result["exit_code"],
        "stderr_tail": result["stderr_tail"],
    }


def validate_cube_file(path: str | Path) -> dict[str, Any]:
    p = Path(path)
    if not p.exists():
        return {"ok": False, "error": f"missing: {p}"}
    text = p.read_text(encoding="utf-8", errors="replace")
    size = None
    for line in text.splitlines():
        if line.startswith("LUT_3D_SIZE"):
            parts = line.split()
            if len(parts) >= 2:
                size = int(parts[1])
            break
    if size is None:
        return {"ok": False, "error": "LUT_3D_SIZE not found"}
    data_lines = [
        ln
        for ln in text.splitlines()
        if ln.strip() and not ln.strip().startswith("#") and not ln.strip()[0].isalpha()
    ]
    expected = size ** 3
    return {
        "ok": len(data_lines) == expected,
        "size": size,
        "expected_entries": expected,
        "actual_entries": len(data_lines),
        "path": str(p),
    }
