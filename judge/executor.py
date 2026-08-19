from __future__ import annotations

import os
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

from .checker import outputs_match
from .config import settings
from .sandbox import ProcessResult, run_limited, sandbox_available


@dataclass(frozen=True)
class TestSpec:
    id: int
    position: int
    input_data: str
    expected_output: str
    is_public: bool
    points: float


@dataclass(frozen=True)
class SubmissionJob:
    submission_id: str
    source_code: str
    language: str
    filename: str
    time_limit_ms: int
    memory_limit_mb: int
    tests: tuple[TestSpec, ...]


@dataclass(frozen=True)
class ManualRunJob:
    source_code: str
    language: str
    filename: str
    stdin_data: str
    time_limit_ms: int
    memory_limit_mb: int


@dataclass(frozen=True)
class ManualRunOutcome:
    verdict: str
    compile_message: str
    stdout: str
    stderr: str
    runtime_ms: int
    memory_kb: int
    system_failure: bool = False


@dataclass(frozen=True)
class CaseOutcome:
    test_case_id: int
    position: int
    is_public: bool
    verdict: str
    runtime_ms: int
    memory_kb: int
    actual_output: str | None
    expected_output: str | None
    stderr: str | None
    points_awarded: float


@dataclass(frozen=True)
class SubmissionOutcome:
    verdict: str
    score: float
    compile_message: str
    cases: tuple[CaseOutcome, ...]
    system_failure: bool = False


LANGUAGE_FILES = {
    "python": "main.py",
    "c": "main.c",
    "cpp": "main.cpp",
    "java": "Main.java",
}


def _sanitize(message: str, limit: int = 32768) -> str:
    cleaned = message.replace("\x00", "")
    return cleaned[:limit]


def _compile_command(language: str) -> list[str]:
    if language == "python":
        return [
            *settings.python_command,
            "-I",
            "-B",
            "-m",
            "py_compile",
            "/workspace/main.py",
        ]
    if language == "c":
        return [
            *settings.c_compiler,
            "-O2",
            "-pipe",
            "-std=c17",
            "-Wall",
            "-Wextra",
            "-pedantic",
            "-fno-diagnostics-color",
            "/workspace/main.c",
            "-o",
            "/workspace/program",
        ]
    if language == "cpp":
        return [
            *settings.cpp_compiler,
            "-O2",
            "-pipe",
            "-std=c++17",
            "-Wall",
            "-Wextra",
            "-pedantic",
            "-fno-diagnostics-color",
            "/workspace/main.cpp",
            "-o",
            "/workspace/program",
        ]
    if language == "java":
        return [
            *settings.javac_compiler,
            "-J-Xms16m",
            "-J-Xmx512m",
            "-encoding",
            "UTF-8",
            "-d",
            "/workspace",
            "/workspace/Main.java",
        ]
    raise ValueError("Unsupported language.")


def _run_command(language: str, memory_limit_mb: int) -> list[str]:
    if language == "python":
        return [
            *settings.python_command,
            "-I",
            "-B",
            "/workspace/main.py",
        ]
    if language in {"c", "cpp"}:
        return ["/workspace/program"]
    if language == "java":
        heap_mb = max(16, min(512, max(16, int(memory_limit_mb * 0.60))))
        return [
            *settings.java_command,
            "-Xms16m",
            f"-Xmx{heap_mb}m",
            "-XX:MaxMetaspaceSize=128m",
            "-XX:ReservedCodeCacheSize=64m",
            "-Dfile.encoding=UTF-8",
            "-cp",
            "/workspace",
            "Main",
        ]
    raise ValueError("Unsupported language.")


def _compile_verdict(result: ProcessResult) -> tuple[str | None, str]:
    if result.system_error:
        return "SE", "Secure compilation sandbox failed."
    if result.timed_out:
        return "CE", "Compilation exceeded the allowed time."
    if result.memory_limited:
        return "CE", "Compilation exceeded the allowed memory."
    if result.output_limited:
        return "CE", "Compiler output exceeded the allowed size."
    if result.process_limited:
        return "CE", "Compilation created too many processes."
    if result.return_code != 0:
        message = result.stderr or result.stdout or "Compilation failed."
        return "CE", _sanitize(message)
    warnings = _sanitize(result.stderr or result.stdout)
    return None, warnings


def _case_verdict(result: ProcessResult, expected: str) -> str:
    if result.system_error:
        return "SE"
    if result.timed_out:
        return "TLE"
    if result.memory_limited:
        return "MLE"
    if result.output_limited:
        return "OLE"
    if result.process_limited:
        return "RE"
    if result.return_code != 0:
        return "RE"
    if outputs_match(result.stdout, expected):
        return "AC"
    return "WA"


def _overall_verdict(cases: Iterable[CaseOutcome]) -> str:
    cases = tuple(cases)
    if cases and all(case.verdict == "AC" for case in cases):
        return "AC"
    priority = ("SE", "TLE", "MLE", "OLE", "RE", "WA")
    observed = {case.verdict for case in cases}
    for verdict in priority:
        if verdict in observed:
            return verdict
    return "SE"


def _manual_runtime_verdict(result: ProcessResult) -> str:
    if result.system_error:
        return "SE"
    if result.timed_out:
        return "TLE"
    if result.memory_limited:
        return "MLE"
    if result.output_limited:
        return "OLE"
    if result.process_limited:
        return "RE"
    if result.return_code != 0:
        return "RE"
    return "OK"


def execute_manual(job: ManualRunJob) -> ManualRunOutcome:
    """Compile and run source once with student-provided stdin.

    This path never reads configured public/private test cases and never writes a
    Submission record. It uses the same Bubblewrap sandbox and resource limits as
    normal judging.
    """
    available, _reason = sandbox_available()
    if not available:
        return ManualRunOutcome(
            verdict="SE",
            compile_message="Secure Bubblewrap sandbox is unavailable; execution was refused.",
            stdout="",
            stderr="",
            runtime_ms=0,
            memory_kb=0,
            system_failure=True,
        )

    source_filename = LANGUAGE_FILES.get(job.language)
    if source_filename is None:
        return ManualRunOutcome("SE", "Unsupported language.", "", "", 0, 0, True)

    with tempfile.TemporaryDirectory(prefix="judge-manual-") as temp:
        workspace = Path(temp).resolve()
        source_path = workspace / source_filename
        source_path.write_text(job.source_code, encoding="utf-8", newline="\n")
        os.chmod(source_path, 0o600)

        compile_result = run_limited(
            workspace=workspace,
            command=_compile_command(job.language),
            stdin_data="",
            timeout_ms=settings.compile_timeout_ms,
            memory_mb=settings.compile_memory_mb,
            output_limit=settings.compile_output_bytes,
            enforce_address_space=job.language != "java",
        )
        compile_verdict, compile_message = _compile_verdict(compile_result)
        if compile_verdict is not None:
            return ManualRunOutcome(
                verdict=compile_verdict,
                compile_message=compile_message,
                stdout="",
                stderr=_sanitize(compile_result.stderr or "", 8192),
                runtime_ms=compile_result.runtime_ms,
                memory_kb=compile_result.memory_kb,
                system_failure=compile_verdict == "SE",
            )

        executable = workspace / "program"
        if executable.exists():
            os.chmod(executable, 0o700)

        result = run_limited(
            workspace=workspace,
            command=_run_command(job.language, job.memory_limit_mb),
            stdin_data=job.stdin_data,
            timeout_ms=job.time_limit_ms,
            memory_mb=job.memory_limit_mb,
            output_limit=settings.run_output_bytes,
            enforce_address_space=job.language != "java",
        )
        verdict = _manual_runtime_verdict(result)
        return ManualRunOutcome(
            verdict=verdict,
            compile_message=compile_message,
            stdout=_sanitize(result.stdout, settings.run_output_bytes),
            stderr=_sanitize(result.stderr, 8192),
            runtime_ms=result.runtime_ms,
            memory_kb=result.memory_kb,
            system_failure=verdict == "SE",
        )


def execute_submission(job: SubmissionJob) -> SubmissionOutcome:
    available, _reason = sandbox_available()
    if not available:
        return SubmissionOutcome(
            verdict="SE",
            score=0.0,
            compile_message="Secure Bubblewrap sandbox is unavailable; execution was refused.",
            cases=(),
            system_failure=True,
        )

    source_filename = LANGUAGE_FILES.get(job.language)
    if source_filename is None:
        return SubmissionOutcome("SE", 0.0, "Unsupported language.", (), True)

    with tempfile.TemporaryDirectory(prefix=f"judge-{job.submission_id[:8]}-") as temp:
        workspace = Path(temp).resolve()
        source_path = workspace / source_filename
        source_path.write_text(job.source_code, encoding="utf-8", newline="\n")
        os.chmod(source_path, 0o600)

        compile_result = run_limited(
            workspace=workspace,
            command=_compile_command(job.language),
            stdin_data="",
            timeout_ms=settings.compile_timeout_ms,
            memory_mb=settings.compile_memory_mb,
            output_limit=settings.compile_output_bytes,
            enforce_address_space=job.language != "java",
        )
        compile_verdict, compile_message = _compile_verdict(compile_result)
        if compile_verdict is not None:
            return SubmissionOutcome(
                verdict=compile_verdict,
                score=0.0,
                compile_message=compile_message,
                cases=(),
                system_failure=compile_verdict == "SE",
            )

        executable = workspace / "program"
        if executable.exists():
            os.chmod(executable, 0o700)

        case_outcomes: list[CaseOutcome] = []
        score = 0.0
        for test in sorted(job.tests, key=lambda item: item.position):
            result = run_limited(
                workspace=workspace,
                command=_run_command(job.language, job.memory_limit_mb),
                stdin_data=test.input_data,
                timeout_ms=job.time_limit_ms,
                memory_mb=job.memory_limit_mb,
                output_limit=settings.run_output_bytes,
                enforce_address_space=job.language != "java",
            )
            verdict = _case_verdict(result, test.expected_output)
            points = float(test.points) if verdict == "AC" else 0.0
            score += points

            # Persist all results server-side so administrators can review private cases.
            # Student APIs redact private input/output fields.
            actual_output = _sanitize(result.stdout, settings.run_output_bytes)
            expected_output = _sanitize(test.expected_output, settings.run_output_bytes)
            stderr = _sanitize(result.stderr, 8192) if result.stderr else None

            case_outcomes.append(
                CaseOutcome(
                    test_case_id=test.id,
                    position=test.position,
                    is_public=test.is_public,
                    verdict=verdict,
                    runtime_ms=result.runtime_ms,
                    memory_kb=result.memory_kb,
                    actual_output=actual_output,
                    expected_output=expected_output,
                    stderr=stderr,
                    points_awarded=points,
                )
            )

        overall = _overall_verdict(case_outcomes)
        return SubmissionOutcome(
            verdict=overall,
            score=score,
            compile_message=compile_message,
            cases=tuple(case_outcomes),
            system_failure=overall == "SE",
        )
