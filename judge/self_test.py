from __future__ import annotations

import sys
from pathlib import Path

from .config import settings
from .executor import SubmissionJob, TestSpec, execute_submission
from .sandbox import sandbox_available


def run_case(language: str, filename: str, source: str) -> None:
    outcome = execute_submission(
        SubmissionJob(
            submission_id=f"self-test-{language}",
            source_code=source,
            language=language,
            filename=filename,
            time_limit_ms=2000,
            memory_limit_mb=256,
            tests=(
                TestSpec(
                    id=1,
                    position=1,
                    input_data="12 8\n",
                    expected_output="20\n",
                    is_public=True,
                    points=10.0,
                ),
            ),
        )
    )
    if outcome.verdict != "AC" or outcome.score != 10.0:
        raise RuntimeError(
            f"{language} self-test failed: verdict={outcome.verdict}, "
            f"message={outcome.compile_message!r}, cases={outcome.cases!r}"
        )
    print(f"[PASS] {language}: AC, score 10/10")


def main() -> int:
    available, reason = sandbox_available()
    if not available:
        print(f"[FAIL] Bubblewrap: {reason}")
        return 1

    print(f"[PASS] Bubblewrap: {settings.bubblewrap_binary}")
    print(f"[INFO] Python: {' '.join(settings.python_command)}")
    print(f"[INFO] C compiler: {' '.join(settings.c_compiler)}")
    print(f"[INFO] C++ compiler: {' '.join(settings.cpp_compiler)}")
    print(f"[INFO] Java runtime: {' '.join(settings.java_command)}")
    print(f"[INFO] Java compiler: {' '.join(settings.javac_compiler)}")

    run_case(
        "python",
        "main.py",
        "a, b = map(int, input().split())\nprint(a + b)\n",
    )
    run_case(
        "c",
        "main.c",
        "#include <stdio.h>\nint main(void){long long a,b;if(scanf(\"%lld %lld\",&a,&b)!=2)return 1;printf(\"%lld\\n\",a+b);return 0;}\n",
    )
    run_case(
        "cpp",
        "main.cpp",
        "#include <iostream>\nint main(){long long a,b;if(!(std::cin>>a>>b))return 1;std::cout<<a+b<<'\\n';}\n",
    )
    run_case(
        "java",
        "Main.java",
        "import java.util.*;\npublic class Main { public static void main(String[] args) { Scanner sc=new Scanner(System.in); long a=sc.nextLong(), b=sc.nextLong(); System.out.println(a+b); } }\n",
    )
    print("All secure judge self-tests passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
