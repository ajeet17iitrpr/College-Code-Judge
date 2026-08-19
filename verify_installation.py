from __future__ import annotations

import importlib
import os
import shutil
import subprocess
import sys
from pathlib import Path

REQUIRED_MODULES = [
    "fastapi", "uvicorn", "sqlalchemy", "pydantic", "multipart", "argon2",
    "celery", "redis", "psutil", "jinja2", "reportlab", "email_validator",
]
REQUIRED_COMMANDS = ["bwrap", "redis-server"]


def report(ok: bool, name: str, detail: str = "") -> bool:
    print(f"[{'PASS' if ok else 'FAIL'}] {name}{': ' + detail if detail else ''}")
    return ok


def main() -> int:
    success = True
    for module in REQUIRED_MODULES:
        try:
            importlib.import_module(module)
            success &= report(True, f"Python module {module}")
        except Exception as exc:
            success &= report(False, f"Python module {module}", str(exc))
    for command in REQUIRED_COMMANDS:
        path = shutil.which(command)
        success &= report(bool(path), command, path or "not found")
    cc = os.getenv("CC") or shutil.which("gcc")
    cxx = os.getenv("CXX") or shutil.which("g++")
    success &= report(bool(cc), "C compiler", str(cc or "not found"))
    success &= report(bool(cxx), "C++ compiler", str(cxx or "not found"))

    bwrap = shutil.which("bwrap")
    if bwrap:
        command = [
            bwrap, "--unshare-user", "--unshare-pid", "--unshare-net",
            "--unshare-ipc", "--unshare-uts", "--ro-bind", "/", "/",
            "--tmpfs", "/proc", "--tmpfs", "/tmp", "/bin/true",
        ]
        result = subprocess.run(command, capture_output=True, text=True, timeout=10)
        success &= report(result.returncode == 0, "Bubblewrap namespace test", result.stderr.strip())
    project = Path(__file__).resolve().parent
    success &= report((project / "backend" / "app" / "main.py").is_file(), "Backend files")
    success &= report((project / "frontend" / "static" / "app.js").is_file(), "Frontend files")
    success &= report((project / "judge" / "self_test.py").is_file(), "Judge files")
    return 0 if success else 1


if __name__ == "__main__":
    raise SystemExit(main())
