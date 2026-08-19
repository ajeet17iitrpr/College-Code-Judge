# College Code Judge — Final Integrated Project

A local-network programming examination system for Python, C and C++ with a secure Bubblewrap judge, student registration, administrator approval, one-time common exam code, monitoring flags, versioned submissions, manual grading and reports.

## Final features

### Student portal

- Self-registration using full name, email, enrollment number and password.
- Registration remains pending until an administrator approves it.
- Password reset and account-recovery requests require administrator approval.
- When Exam Mode is enabled, password login is followed by a six-digit code announced in the lab.
- The common code can be used once per student for each generated code version.
- A forced re-login grants the affected student one authorised reuse of the current code.
- Only one active student login is allowed. A newly activated session replaces the old session.
- Python, C and C++ editor with file upload, local autosave and repeated submissions.
- Every submission is stored as a new version; previous code is never overwritten.
- Public tests show input, expected output and student output.
- Private tests show only pass/fail information to students.
- Light and dark modes.
- Published result page and PDF download.

### Exam monitoring

- Fullscreen button and countdown display.
- Records tab switches, window blur, fullscreen exit, copy, cut, paste, right-click, F12, common developer-tool shortcuts, View Source and Print attempts.
- Records IP and browser changes detected by the backend.
- Flags do not auto-submit code and do not automatically reduce marks.
- Submitted programs have no network access because Bubblewrap uses `--unshare-net`.

Browser monitoring is a deterrent and an audit mechanism; it cannot disable the internet for the entire lab computer. Network-level controls are still required for guaranteed internet blocking.

### Administrator portal

- Exam Mode on/off switch.
- Generate or regenerate one common six-digit code and choose its validity period.
- Force re-login for one student or all students.
- Approve/reject new registrations.
- Approve/reject password-reset and account-recovery requests.
- View students by name, email and enrollment number.
- View login sessions, IP addresses, browser details, code usage, all submissions and all flags.
- Create problems and public/private test cases.
- Review complete source code and all public/private test details.
- Preserve automatic score while optionally assigning manual marks and remarks.
- Select an official submission version.
- Best, latest or administrator-selected result policy.
- Publish results to students.
- Student PDF reports and result-summary CSV.
- Audit log for administrator actions.

Passwords are stored only as Argon2 hashes. Administrators can approve resets but cannot view existing passwords.

## Project layout

```text
DLD/
├── backend/
│   ├── app/
│   ├── scripts/
│   ├── data/
│   └── run.py
├── frontend/
│   ├── static/
│   └── server.py
├── judge/
├── environment.yml
├── .env.example
├── start_all.sh
├── stop_all.sh
├── status.sh
└── verify_installation.py
```

## Apply over the staged project already in `/home/ajeet/DLD`

Extract the final package in a separate directory, then run:

```bash
cd /path/to/college_judge_final
chmod +x apply_final.sh
./apply_final.sh /home/ajeet/DLD
```

The script:

- creates a timestamped backup under `/home/ajeet/DLD/backups/`;
- replaces backend application code, frontend and judge worker;
- preserves `/home/ajeet/DLD/backend/data/judge.db`;
- preserves an existing `/home/ajeet/DLD/.env`;
- creates `.env` from `.env.example` only when it does not exist.

## Conda installation only

No direct `apt` installation is required.

```bash
cd /home/ajeet/DLD
conda env update -n college_judge -f environment.yml --prune
conda activate college_judge
```

For a new environment:

```bash
conda env create -f environment.yml
conda activate college_judge
```

## Environment configuration

```bash
cd /home/ajeet/DLD
cp .env.example .env   # only if .env does not already exist
nano .env
```

For your current container, keep:

```dotenv
APP_HOST=127.0.0.1
APP_PORT=8000
FRONTEND_HOST=0.0.0.0
FRONTEND_PORT=8080
BACKEND_URL=http://127.0.0.1:8000
DATABASE_URL=sqlite:////home/ajeet/DLD/backend/data/judge.db
DATA_DIR=/home/ajeet/DLD/backend/data
REDIS_URL=redis://127.0.0.1:6379/0
CELERY_RESULT_BACKEND=redis://127.0.0.1:6379/1
COOKIE_SECURE=false
```

Only frontend port `8080` is exposed. Backend port `8000` and Redis port `6379` remain bound to localhost.

## Database setup

### Upgrade the existing staged database

```bash
conda activate college_judge
cd /home/ajeet/DLD
source load_env.sh
python backend/scripts/migrate_final.py
```

The migration creates another database backup before changing the schema.

### Create or update the first administrator

```bash
python backend/scripts/init_db.py \
  --name "System Administrator" \
  --email "admin@example.com" \
  --enrollment "ADMIN" \
  --password "StrongAdminPass123"
```

### Optional sample problem

```bash
python backend/scripts/create_sample_problem.py
```

## Verify the installation

```bash
conda activate college_judge
cd /home/ajeet/DLD
source load_env.sh
python verify_installation.py
python -m judge.self_test
```

The Bubblewrap check must pass. The project deliberately refuses to execute submissions when the secure sandbox is unavailable.

## Start everything

```bash
conda activate college_judge
cd /home/ajeet/DLD
./start_all.sh
```

Check status:

```bash
./status.sh
```

View logs:

```bash
tail -f logs/backend.log
tail -f logs/frontend.log
tail -f logs/worker.log
tail -f logs/redis.log
```

Stop everything:

```bash
./stop_all.sh
```

## Student URL

Students connected to the institute network should open:

```text
http://172.26.1.171:8080
```

The address works only when the institute network can route to the container IP and TCP port `8080` is allowed.

## Recommended first-run order

1. Sign in as administrator.
2. Create the problems and test cases.
3. Let students register.
4. Approve the pending registrations.
5. Open **Admin → Exam**.
6. Generate the common code.
7. Enable Exam Mode.
8. Announce the code manually in the lab.
9. Review submissions, private tests and flags.
10. Enter manual marks where needed.
11. Choose the result policy and publish results.

## Security design

- Student source code never executes in the FastAPI or frontend process.
- Bubblewrap creates separate user, process, network, IPC and UTS namespaces.
- `/proc` is an empty `tmpfs`, matching the mode verified in your container.
- The runtime filesystem is read-only except for the temporary submission workspace.
- CPU, memory, process, file-descriptor and output limits are enforced.
- Expected private outputs remain in the judge/database and are redacted from student APIs.
- API authorization is checked server-side; hiding frontend buttons is never treated as security.
- Session cookies are HttpOnly, SameSite=Strict and protected by CSRF tokens.
- One active student session is enforced after complete login and exam-code verification.
- F12 blocking is not considered real protection; sensitive information is never sent to the browser.

For HTTPS behind a trusted institute reverse proxy, set `COOKIE_SECURE=true`.
