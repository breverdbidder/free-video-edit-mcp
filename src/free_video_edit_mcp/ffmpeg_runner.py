"""Safe subprocess wrappers for ffmpeg/ffprobe (never shell=True)."""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path
from typing import Any


DEFAULT_TIMEOUT = 600
STDERR_TAIL_CHARS = 4000


def find_binary(name: str) -> str:
    path = shutil.which(name)
    if not path:
        raise FileNotFoundError(
            f"'{name}' not found on PATH. Install FFmpeg and ensure it is available."
        )
    return path


def _tail(text: str, n: int = STDERR_TAIL_CHARS) -> str:
    if len(text) <= n:
        return text
    return text[-n:]


def run_argv(
    argv: list[str],
    *,
    working_directory: str | None = None,
    timeout: float = DEFAULT_TIMEOUT,
    env: dict[str, str] | None = None,
) -> dict[str, Any]:
    """Run a command as an argv list. Never uses shell=True."""
    if not argv:
        raise ValueError("argv must be a non-empty list")
    if any(not isinstance(a, str) for a in argv):
        raise TypeError("all argv elements must be strings")

    cwd = working_directory or os.getcwd()
    merged_env = os.environ.copy()
    if env:
        merged_env.update(env)

    try:
        proc = subprocess.run(
            argv,
            cwd=cwd,
            capture_output=True,
            text=True,
            timeout=timeout,
            env=merged_env,
            shell=False,
            check=False,
        )
        return {
            "ok": proc.returncode == 0,
            "exit_code": proc.returncode,
            "argv": argv,
            "cwd": cwd,
            "stdout": proc.stdout or "",
            "stderr": proc.stderr or "",
            "stdout_tail": _tail(proc.stdout or ""),
            "stderr_tail": _tail(proc.stderr or ""),
        }
    except subprocess.TimeoutExpired as exc:
        return {
            "ok": False,
            "exit_code": -1,
            "argv": argv,
            "cwd": cwd,
            "stdout": (exc.stdout or "") if isinstance(exc.stdout, str) else "",
            "stderr": f"Timeout after {timeout}s. "
            + ((exc.stderr or "") if isinstance(exc.stderr, str) else ""),
            "stdout_tail": "",
            "stderr_tail": f"Timeout after {timeout}s",
            "timeout": True,
        }


def run_ffmpeg(
    args: list[str],
    *,
    working_directory: str | None = None,
    timeout: float = DEFAULT_TIMEOUT,
    overwrite: bool = False,
) -> dict[str, Any]:
    """Run ffmpeg with an argument list (without the binary name)."""
    binary = find_binary("ffmpeg")
    argv = [binary, "-hide_banner"]
    if overwrite:
        argv.append("-y")
    else:
        argv.append("-n")
    argv.extend(args)
    return run_argv(argv, working_directory=working_directory, timeout=timeout)


def run_ffprobe(
    args: list[str],
    *,
    working_directory: str | None = None,
    timeout: float = 120,
) -> dict[str, Any]:
    """Run ffprobe with an argument list (without the binary name)."""
    binary = find_binary("ffprobe")
    argv = [binary, "-hide_banner", *args]
    return run_argv(argv, working_directory=working_directory, timeout=timeout)


def ensure_parent_dir(path: str | Path) -> None:
    Path(path).expanduser().resolve().parent.mkdir(parents=True, exist_ok=True)
