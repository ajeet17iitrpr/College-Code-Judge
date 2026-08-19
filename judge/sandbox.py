from __future__ import annotations

import math
import os
import resource
import signal
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path

import psutil

from .config import settings


@dataclass(frozen=True)
class ProcessResult:
    return_code: int | None
    stdout: str
    stderr: str
    runtime_ms: int
    memory_kb: int
    timed_out: bool = False
    memory_limited: bool = False
    output_limited: bool = False
    process_limited: bool = False
    system_error: str = ""


def sandbox_available() -> tuple[bool, str]:
    bwrap = Path(settings.bubblewrap_binary)
    if not bwrap.exists():
        return False, "Bubblewrap executable was not found."
    if not os.access(bwrap, os.X_OK):
        return False, "Bubblewrap is not executable."
    return True, ""


def _add_ro_bind(command: list[str], source: Path, destination: Path | None = None) -> None:
    if not source.exists():
        return
    destination = destination or source
    command.extend(["--ro-bind", str(source), str(destination)])


def _runtime_roots() -> list[Path]:
    candidates = [
        Path("/usr"),
        Path("/bin"),
        Path("/lib"),
        Path("/lib64"),
        Path("/sbin"),
        Path(sys.prefix),
        Path(sys.base_prefix),
        Path(settings.python_command[0]).parent.parent,
        Path(settings.c_compiler[0]).parent.parent,
        Path(settings.cpp_compiler[0]).parent.parent,
        Path(settings.java_command[0]).parent.parent,
        Path(settings.javac_compiler[0]).parent.parent,
    ]
    unique: list[Path] = []
    seen: set[str] = set()
    for candidate in candidates:
        try:
            resolved = candidate.resolve()
        except OSError:
            resolved = candidate
        key = str(resolved)
        if candidate.exists() and key not in seen:
            seen.add(key)
            unique.append(candidate)
    return unique


def build_sandbox_command(workspace: Path, inner_command: list[str]) -> list[str]:
    available, reason = sandbox_available()
    if not available:
        raise RuntimeError(reason)

    command = [
        settings.bubblewrap_binary,
        "--die-with-parent",
        "--new-session",
        "--unshare-user",
        "--unshare-pid",
        "--unshare-net",
        "--unshare-ipc",
        "--unshare-uts",
        "--clearenv",
        "--tmpfs",
        "/proc",
        "--tmpfs",
        "/tmp",
        "--dir",
        "/dev",
    ]

    # Bind only a few harmless device nodes. The host/container filesystem is
    # never mounted as a whole.
    for device in ("null", "zero", "random", "urandom"):
        source = Path("/dev") / device
        if source.exists():
            command.extend(["--dev-bind", str(source), str(source)])

    for runtime_root in _runtime_roots():
        _add_ro_bind(command, runtime_root)

    for runtime_file in (
        Path("/etc/ld.so.cache"),
        Path("/etc/ld.so.conf"),
        Path("/etc/ld.so.conf.d"),
        Path("/etc/localtime"),
    ):
        _add_ro_bind(command, runtime_file)

    safe_path = ":".join(
        dict.fromkeys(
            [
                str(Path(settings.python_command[0]).parent),
                str(Path(settings.c_compiler[0]).parent),
                str(Path(settings.cpp_compiler[0]).parent),
                str(Path(settings.java_command[0]).parent),
                str(Path(settings.javac_compiler[0]).parent),
                "/usr/bin",
                "/bin",
            ]
        )
    )

    # Java/JDK needs its native runtime libraries inside the cleared
    # Bubblewrap environment. Apply these variables only to Java/javac so
    # C, C++ and Python keep their existing environment unchanged.
    java_env: list[str] = []

    if inner_command:
        try:
            inner_executable = str(Path(inner_command[0]).resolve())
        except OSError:
            inner_executable = inner_command[0]

        java_executables = {
            str(Path(settings.java_command[0]).resolve()),
            str(Path(settings.javac_compiler[0]).resolve()),
        }

        if inner_executable in java_executables:
            java_home = Path(settings.javac_compiler[0]).resolve().parent.parent

            java_ld_path = ":".join(
                [
                    str(java_home / "lib"),
                    str(java_home / "lib" / "server"),
                    str(Path(sys.prefix) / "lib"),
                ]
            )

            java_env = [
                "--setenv",
                "JAVA_HOME",
                str(java_home),
                "--setenv",
                "LD_LIBRARY_PATH",
                java_ld_path,
            ]

    command.extend(
        [
            "--bind",
            str(workspace),
            "/workspace",
            "--chdir",
            "/workspace",
            "--setenv",
            "HOME",
            "/tmp",
            "--setenv",
            "TMPDIR",
            "/tmp",
            "--setenv",
            "PATH",
            safe_path,
            "--setenv",
            "LANG",
            "C.UTF-8",
            "--setenv",
            "LC_ALL",
            "C.UTF-8",
            "--setenv",
            "PYTHONHASHSEED",
            "0",
            *java_env,
            "--",
            *inner_command,
        ]
    )
    return command


def _apply_rlimits(memory_mb: int, cpu_limit_ms: int, output_limit: int, enforce_address_space: bool = True) -> None:
    os.setsid()
    cpu_seconds = max(1, math.ceil(cpu_limit_ms / 1000))
    resource.setrlimit(resource.RLIMIT_CPU, (cpu_seconds, cpu_seconds + 1))
    if enforce_address_space:
        address_space = max(32, memory_mb) * 1024 * 1024
        resource.setrlimit(resource.RLIMIT_AS, (address_space, address_space))
    resource.setrlimit(resource.RLIMIT_FSIZE, (output_limit, output_limit))
    resource.setrlimit(resource.RLIMIT_NOFILE, (64, 64))
    resource.setrlimit(resource.RLIMIT_CORE, (0, 0))


def _kill_process_group(pid: int) -> None:
    try:
        os.killpg(pid, signal.SIGKILL)
    except ProcessLookupError:
        return


def _tree_usage(pid: int) -> tuple[int, int]:
    try:
        root = psutil.Process(pid)
        processes = [root, *root.children(recursive=True)]
    except (psutil.NoSuchProcess, psutil.AccessDenied):
        return 0, 0

    rss = 0
    alive = 0
    for process in processes:
        try:
            rss += process.memory_info().rss
            alive += 1
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue
    return rss, alive


def _decode(data: bytes) -> str:
    return data.decode("utf-8", errors="replace")


def run_limited(
    *,
    workspace: Path,
    command: list[str],
    stdin_data: str,
    timeout_ms: int,
    memory_mb: int,
    output_limit: int,
    max_processes: int | None = None,
    enforce_address_space: bool = True,
) -> ProcessResult:
    max_processes = max_processes or settings.max_processes
    wrapped = build_sandbox_command(workspace, command)
    started = time.perf_counter()
    peak_rss = 0
    timed_out = False
    memory_limited = False
    process_limited = False

    input_bytes = stdin_data.encode("utf-8")
    with tempfile.TemporaryFile() as stdin_file, tempfile.TemporaryFile() as stdout_file, tempfile.TemporaryFile() as stderr_file:
        stdin_file.write(input_bytes)
        stdin_file.seek(0)
        try:
            process = subprocess.Popen(
                wrapped,
                stdin=stdin_file,
                stdout=stdout_file,
                stderr=stderr_file,
                cwd=str(workspace),
                env={"PATH": os.environ.get("PATH", "")},
                preexec_fn=lambda: _apply_rlimits(memory_mb, timeout_ms, output_limit, enforce_address_space),
            )

            while process.poll() is None:
                elapsed_ms = int((time.perf_counter() - started) * 1000)
                rss, process_count = _tree_usage(process.pid)
                peak_rss = max(peak_rss, rss)

                if elapsed_ms > timeout_ms:
                    timed_out = True
                    _kill_process_group(process.pid)
                    break
                if rss > memory_mb * 1024 * 1024:
                    memory_limited = True
                    _kill_process_group(process.pid)
                    break
                if process_count > max_processes:
                    process_limited = True
                    _kill_process_group(process.pid)
                    break
                time.sleep(0.01)

            try:
                process.wait(timeout=2)
            except subprocess.TimeoutExpired:
                _kill_process_group(process.pid)
                process.wait(timeout=2)

            elapsed_ms = int((time.perf_counter() - started) * 1000)
            final_rss, _ = _tree_usage(process.pid)
            peak_rss = max(peak_rss, final_rss)

            stdout_file.seek(0, os.SEEK_END)
            stdout_size = stdout_file.tell()
            stderr_file.seek(0, os.SEEK_END)
            stderr_size = stderr_file.tell()
            output_limited = stdout_size >= output_limit or stderr_size >= output_limit

            stdout_file.seek(0)
            stderr_file.seek(0)
            stdout = _decode(stdout_file.read(output_limit))
            stderr = _decode(stderr_file.read(output_limit))

            return ProcessResult(
                return_code=process.returncode,
                stdout=stdout,
                stderr=stderr,
                runtime_ms=elapsed_ms,
                memory_kb=max(0, peak_rss // 1024),
                timed_out=timed_out,
                memory_limited=memory_limited,
                output_limited=output_limited,
                process_limited=process_limited,
            )
        except Exception as exc:
            return ProcessResult(
                return_code=None,
                stdout="",
                stderr="",
                runtime_ms=int((time.perf_counter() - started) * 1000),
                memory_kb=max(0, peak_rss // 1024),
                system_error=str(exc),
            )
