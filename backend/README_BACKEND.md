# College Code Judge — Part 1: Backend API

This package contains only the backend layer. The frontend, judge sandbox,
report generator, Linux security configuration, and Conda `environment.yml`
will be delivered as separate parts.

## Included

- FastAPI API-only application
- SQLite by default; PostgreSQL-compatible through `DATABASE_URL`
- Argon2 password hashing
- Server-side session authentication
- CSRF protection for every state-changing authenticated request
- Student/admin role checks
- Admin APIs for users, problems, and public/private tests
- Student APIs for problems and source submissions
- Private-test redaction in submission results
- Upload size, extension, UTF-8, path, and null-byte validation
- Security headers and basic per-IP rate limiting

## Current submission behavior

A valid source submission is stored in the database with status `QUEUED`.
No uploaded code is executed by this backend. The later judge-worker part will
claim queued submissions and run them in a separate Linux sandbox.

## Directory placement

Place this folder at:

```text
college_code_judge/backend/
```

The later parts will be placed beside it:

```text
college_code_judge/
├── backend/
├── frontend/       # Part 2
├── judge/          # Later part
├── deployment/     # Later part
└── environment.yml # Conda part
```

## Main entry point

After the Conda environment is delivered and created, start the backend from
inside this directory using:

```bash
python run.py
```

The default API address will be:

```text
http://SERVER_LOCAL_IP:8000
```

## First administrator

After dependencies are installed, create the first administrator using:

```bash
python scripts/create_admin.py --username admin
```

The password is entered securely and is not placed in command history.
