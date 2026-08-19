# College Code Judge — Part 3: Secure Judge Worker

Place the `judge` folder at:

```text
/home/ajeet/DLD/judge
```

Replace the backend queue file with:

```text
/home/ajeet/DLD/backend/app/services/submission_queue.py
```

using the file provided under:

```text
backend_replacement/app/services/submission_queue.py
```

## What this part adds

- Redis/Celery submission queue
- Recovery of queued or stale submissions
- Python syntax checking and execution
- C17 and C++17 compilation and execution
- Bubblewrap isolation with the working `--tmpfs /proc` mode
- No network access inside submissions
- Minimal read-only runtime mounts; the full container root is not exposed
- Time, memory, process-count and output-size limits
- Public/private test execution
- Private output and error redaction before database storage
- Overall verdict and score persistence

## Required Conda packages

The previously supplied `environment.yml` must include:

```yaml
- bubblewrap
- redis-server
- c-compiler
- cxx-compiler

- pip:
    - celery[redis]
    - redis
    - psutil
```

## Environment variables

Run the backend and worker with the same database and Redis settings:

```bash
export DATABASE_URL="sqlite:////home/ajeet/DLD/backend/data/judge.db"
export REDIS_URL="redis://127.0.0.1:6379/0"
export CELERY_RESULT_BACKEND="redis://127.0.0.1:6379/1"
export JUDGE_QUEUE="judge"
```

Conda normally sets `CC` and `CXX` automatically. Verify:

```bash
echo "$CC"
echo "$CXX"
```

## Secure self-test

From `/home/ajeet/DLD`:

```bash
conda activate college_judge
python -m judge.self_test
```

Expected:

```text
[PASS] python: AC, score 10/10
[PASS] c: AC, score 10/10
[PASS] cpp: AC, score 10/10
All secure judge self-tests passed.
```

## Start the services

Use three terminals for now.

### Terminal 1 — Redis

```bash
conda activate college_judge
cd /home/ajeet/DLD
./judge/start_redis.sh
```

### Terminal 2 — Judge worker

```bash
conda activate college_judge
cd /home/ajeet/DLD
./judge/start_worker.sh
```

### Terminal 3 — Backend

```bash
conda activate college_judge
cd /home/ajeet/DLD/backend
python run.py
```

The frontend remains on port 8080 as configured earlier.

## Security note

Never replace the sandbox with direct `subprocess` execution. If Bubblewrap
fails, the worker records `SE` and refuses to run the submitted program.
