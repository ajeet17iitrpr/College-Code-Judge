# Secure Judge Worker

The worker compiles and executes Python, C and C++ submissions inside Bubblewrap. It uses the already-verified nested-container mode with `--tmpfs /proc` rather than mounting a new proc filesystem.

Run the self-test from the project root after activating the Conda environment:

```bash
source load_env.sh
python -m judge.self_test
```

Start Redis and the worker manually:

```bash
./judge/start_redis.sh
./judge/start_worker.sh
```

The complete application can be started with `./start_all.sh`.
