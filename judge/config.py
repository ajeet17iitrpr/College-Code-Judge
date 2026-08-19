from __future__ import annotations

import os
import shlex
import shutil
import sys
from dataclasses import dataclass
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parent.parent
BACKEND_DIR = PROJECT_ROOT / "backend"


def _command_from_env(name: str, fallback: str) -> tuple[str, ...]:
    raw = os.getenv(name, fallback).strip()
    parts = shlex.split(raw)
    if not parts:
        raise RuntimeError(f"{name} is empty.")
    executable = shutil.which(parts[0]) or parts[0]
    return (str(Path(executable).resolve()), *parts[1:])


@dataclass(frozen=True)
class JudgeSettings:
    redis_url: str = os.getenv("REDIS_URL", "redis://127.0.0.1:6379/0")
    celery_result_backend: str = os.getenv(
        "CELERY_RESULT_BACKEND",
        "redis://127.0.0.1:6379/1",
    )
    queue_name: str = os.getenv("JUDGE_QUEUE", "judge")

    bubblewrap_binary: str = str(
        Path(shutil.which(os.getenv("BWRAP_BINARY", "bwrap")) or "bwrap").resolve()
    )
    python_command: tuple[str, ...] = (str(Path(sys.executable).resolve()),)
    c_compiler: tuple[str, ...] = _command_from_env("CC", "gcc")
    cpp_compiler: tuple[str, ...] = _command_from_env("CXX", "g++")
    java_command: tuple[str, ...] = _command_from_env("JAVA", "java")
    javac_compiler: tuple[str, ...] = _command_from_env("JAVAC", "javac")

    compile_timeout_ms: int = int(os.getenv("COMPILE_TIMEOUT_MS", "20000"))
    compile_memory_mb: int = int(os.getenv("COMPILE_MEMORY_MB", "1024"))
    compile_output_bytes: int = int(os.getenv("COMPILE_OUTPUT_BYTES", str(256 * 1024)))
    run_output_bytes: int = int(os.getenv("RUN_OUTPUT_BYTES", str(1024 * 1024)))
    max_processes: int = int(os.getenv("JUDGE_MAX_PROCESSES", "64"))
    stale_submission_seconds: int = int(os.getenv("STALE_SUBMISSION_SECONDS", "900"))
    queued_scan_limit: int = int(os.getenv("QUEUED_SCAN_LIMIT", "100"))


settings = JudgeSettings()
