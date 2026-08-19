College Code Judge: Complete Installation

Target project root: /home/ajeet/DLD

1. Install Linux packages

For an Ubuntu/Debian-based outer container:

cd /home/ajeet/DLD
sudo bash install_system_packages.sh

When already logged in as root:

bash install_system_packages.sh

2. Create or update the Conda environment

Create a new environment:

cd /home/ajeet/DLD
conda env create -f environment.yml
conda activate college_judge

Update an existing environment:

cd /home/ajeet/DLD
conda env update -n college_judge -f environment.yml --prune
conda activate college_judge

3. Verify everything

cd /home/ajeet/DLD
conda activate college_judge
python verify_installation.py

The most important check is bubblewrap sandbox. If it fails with a namespace or permission error, the college server administrator must allow user namespaces/nested namespaces for the outer container. Do not enable unsafe direct execution as a workaround.

4. Components covered

FastAPI backend and Uvicorn server

SQLite and PostgreSQL support

Redis and Celery job queue

Python/C/C++ judge worker dependencies

Bubblewrap sandbox

HTML/PDF reports

Nginx reverse proxy

Supervisor process management for a single outer container

Backend/frontend testing tools

5. Service note

A normal Docker/LXC container often has no systemd. Use Supervisor or explicit foreground commands to run PostgreSQL, Redis, Nginx, the FastAPI backend, the frontend proxy, and the Celery worker.