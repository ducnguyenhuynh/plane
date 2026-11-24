#!/bin/bash
set -e

python manage.py wait_for_db
# Wait for migrations
python manage.py wait_for_migrations
# Run the agent queue worker
celery -A plane worker -l info -Q ${AGENT_RUN_QUEUE:-agent_runs} -c ${AGENT_WORKER_CONCURRENCY:-2} -n agent@%h
