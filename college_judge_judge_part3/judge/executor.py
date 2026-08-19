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
    raise ValueError("Unsupported language.")


def _run_command(language: str) -> list[str]:
    if language == "python":
        return [
            *settings.python_command,
            "-I",
            "-B",
            "/workspace/main.py",
        ]
    if language in {"c", "cpp"}:
        return ["/workspace/program"]
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
                command=_run_command(job.language),
                stdin_data=test.input_data,
                timeout_ms=job.time_limit_ms,
                memory_mb=job.memory_limit_mb,
                output_limit=settings.run_output_bytes,
            )
            verdict = _case_verdict(result, test.expected_output)
            points = float(test.points) if verdict == "AC" else 0.0
            score += points

            # Private outputs and errors are deliberately never persisted.
            actual_output = result.stdout if test.is_public else None
            expected_output = test.expected_output if test.is_public else None
            stderr = _sanitize(result.stderr, 8192) if test.is_public and result.stderr else None

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
