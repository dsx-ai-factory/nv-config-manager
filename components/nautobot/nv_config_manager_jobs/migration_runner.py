#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (c) 2024-2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
# http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
"""
Nautobot Migration Runner with PostgreSQL Advisory Locking

This script runs database migrations with distributed locking to ensure
only one pod runs migrations at a time in a multi-replica deployment.

The PostgreSQL session-level advisory lock is held for the entire duration
of the migration process to prevent race conditions.

The advisory lock only serializes pods that start concurrently. During a
rolling update each pod's init container finishes before the next pod is
created, so every pod would acquire a free lock. To run migrate, post_upgrade
and the bootstrap job once per deployment, the lock holder records the
Helm-provided NAUTOBOT_INIT_FINGERPRINT after a successful run and later pods
skip that work when it matches and no migrations are pending.
"""

import os
import subprocess
import sys
import time

import django
from django.contrib.auth import get_user_model
from django.db import connection
from django.db.migrations.executor import MigrationExecutor

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "nautobot.core.settings")
django.setup()


LOCK_ID = 20250119
MAX_WAIT = 600
POLL_INTERVAL = 5

INIT_STATE_NAME = "nautobot-init"
CREATE_INIT_STATE_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS nv_config_manager_init_state (
    name text PRIMARY KEY,
    fingerprint text NOT NULL,
    completed_at timestamptz NOT NULL DEFAULT now()
)
"""
SELECT_INIT_FINGERPRINT_SQL = "SELECT fingerprint FROM nv_config_manager_init_state WHERE name = %s"
UPSERT_INIT_FINGERPRINT_SQL = """
INSERT INTO nv_config_manager_init_state (name, fingerprint, completed_at)
VALUES (%s, %s, now())
ON CONFLICT (name) DO UPDATE
SET fingerprint = EXCLUDED.fingerprint, completed_at = EXCLUDED.completed_at
"""


def run_cmd(cmd: list[str], check: bool = True) -> subprocess.CompletedProcess:
    """Run a command and stream output."""
    print(f">>> {' '.join(cmd)}", flush=True)
    result = subprocess.run(cmd, check=check)
    return result.returncode


def set_superuser_password() -> None:
    """Set or update the superuser password."""
    user_model = get_user_model()
    username = os.environ.get("NAUTOBOT_SUPERUSER_NAME", "admin")
    password = os.environ.get("NAUTOBOT_SUPERUSER_PASSWORD", "")

    if not password:
        print("No NAUTOBOT_SUPERUSER_PASSWORD set, skipping password update.", flush=True)
        return

    user = user_model.objects.filter(username=username).first()
    if user:
        if not user.check_password(password):
            user.set_password(password)
            user.save()
            print("Password updated.", flush=True)
        else:
            print("Password unchanged.", flush=True)
    else:
        print(f"User '{username}' not found.", flush=True)


def create_api_token() -> None:
    """Create or update the API token for the superuser."""
    from nautobot.users.models import Token

    user_model = get_user_model()
    username = os.environ.get("NAUTOBOT_SUPERUSER_NAME", "admin")
    api_token_env = os.environ.get("NAUTOBOT_SUPERUSER_API_TOKEN", "")

    if not api_token_env:
        print("No NAUTOBOT_SUPERUSER_API_TOKEN set, skipping token creation.", flush=True)
        return

    api_token = api_token_env.strip()[:40]
    user = user_model.objects.filter(username=username).first()
    if user:
        _, created = Token.objects.update_or_create(
            key=api_token, defaults={"user": user, "description": "Auto-generated API token"}
        )
        print(f"Token {'created' if created else 'updated'}.", flush=True)
    else:
        print(f"User '{username}' not found for token creation.", flush=True)


def run_bootstrap_job() -> None:
    """Enable and run the bootstrap job if custom jobs are enabled."""
    from nautobot.extras.models import Job

    # Check if we should run bootstrap (based on env var set by Helm)
    if os.environ.get("NAUTOBOT_RUN_BOOTSTRAP", "false").lower() != "true":
        print("Bootstrap job disabled, skipping.", flush=True)
        return

    # Enable the bootstrap job
    job = Job.objects.filter(
        module_name="nv_config_manager_jobs.bootstrap.load_bootstrap_data", job_class_name="LoadBootstrapData"
    ).first()

    if job and not job.enabled:
        job.enabled = True
        job.save()
        print(f"Enabled job: {job}", flush=True)
    elif job:
        print(f"Job already enabled: {job}", flush=True)
    else:
        print("Bootstrap job not found in database.", flush=True)
        return

    # Run the bootstrap job
    username = os.environ.get("NAUTOBOT_SUPERUSER_NAME", "admin")

    # Temporarily unset NATS_HOST to avoid connection issues during bootstrap
    nats_host = os.environ.pop("NATS_HOST", None)
    try:
        run_cmd(
            [
                "nautobot-server",
                "runjob",
                "--local",
                "--username",
                username,
                "nv_config_manager_jobs.bootstrap.load_bootstrap_data.LoadBootstrapData",
            ],
            check=False,
        )
        print("Bootstrap job complete.", flush=True)
    finally:
        if nats_host:
            os.environ["NATS_HOST"] = nats_host


def has_pending_migrations() -> bool:
    """Return True if any migration on disk has not been applied to the database."""
    executor = MigrationExecutor(connection)
    return bool(executor.migration_plan(executor.loader.graph.leaf_nodes()))


def read_completed_fingerprint(cursor) -> str | None:
    """Return the fingerprint recorded by the last successful init run, if any."""
    cursor.execute(CREATE_INIT_STATE_TABLE_SQL)
    cursor.execute(SELECT_INIT_FINGERPRINT_SQL, [INIT_STATE_NAME])
    row = cursor.fetchone()
    return row[0] if row else None


def record_completed_fingerprint(cursor, fingerprint: str) -> None:
    """Record that init completed successfully for this fingerprint."""
    cursor.execute(UPSERT_INIT_FINGERPRINT_SQL, [INIT_STATE_NAME, fingerprint])


def main() -> int:
    print("========================================")
    print("Nautobot Migration Init Container")
    print("========================================")

    # Collect static files on every pod, regardless of lock state.
    # /opt/nautobot/static is a per-pod emptyDir volume in the default
    # deployment, so each pod must populate its own copy before the main
    # container starts serving. collectstatic is idempotent and does not
    # touch the database, so it is safe to run concurrently across pods.
    print("Collecting static files...", flush=True)
    run_cmd(["nautobot-server", "collectstatic", "--no-input"])
    print("Static files collected!", flush=True)

    print(f"Attempting to acquire PostgreSQL advisory lock (ID: {LOCK_ID})...", flush=True)

    # Keep cursor/connection open for the entire migration process
    cursor = connection.cursor()
    cursor.execute("SELECT pg_try_advisory_lock(%s)", [LOCK_ID])
    acquired = cursor.fetchone()[0]

    if acquired:
        print("=" * 40, flush=True)
        print("LOCK ACQUIRED - This pod will run migrations", flush=True)
        print("=" * 40, flush=True)

        try:
            fingerprint = os.environ.get("NAUTOBOT_INIT_FINGERPRINT", "")
            already_done = (
                bool(fingerprint) and not has_pending_migrations() and read_completed_fingerprint(cursor) == fingerprint
            )
            if already_done:
                print(
                    f"Init already completed for fingerprint {fingerprint[:12]} and no migrations are pending; "
                    "skipping migrate, post_upgrade and bootstrap.",
                    flush=True,
                )
            else:
                print("Running database migrations...", flush=True)
                run_cmd(["nautobot-server", "migrate", "--no-input"])
                print("Migrations complete!", flush=True)

            print("Creating superuser if needed...", flush=True)
            username = os.environ.get("NAUTOBOT_SUPERUSER_NAME", "admin")
            email = os.environ.get("NAUTOBOT_SUPERUSER_EMAIL", "admin@example.com")
            run_cmd(
                [
                    "nautobot-server",
                    "createsuperuser",
                    "--no-input",
                    "--username",
                    username,
                    "--email",
                    email,
                ],
                check=False,  # Don't fail if user exists
            )
            print("Superuser check complete!", flush=True)

            print("Setting superuser password...", flush=True)
            set_superuser_password()

            print("Creating API token...", flush=True)
            create_api_token()

            if not already_done:
                print("Running post_upgrade...", flush=True)
                run_cmd(["nautobot-server", "post_upgrade"])
                print("Post-upgrade complete!", flush=True)
                if fingerprint:
                    record_completed_fingerprint(cursor, fingerprint)
                    print(f"Recorded init fingerprint {fingerprint[:12]}.", flush=True)

        finally:
            # Release the lock
            cursor.execute("SELECT pg_advisory_unlock(%s)", [LOCK_ID])
            print("Lock released.", flush=True)

        print("=" * 40, flush=True)
        print("MIGRATION COMPLETED SUCCESSFULLY", flush=True)
        print("=" * 40, flush=True)

        if not already_done:
            # Run bootstrap job outside the lock
            print("Running bootstrap job...", flush=True)
            run_bootstrap_job()

        print("All init tasks complete!", flush=True)
        return 0

    else:
        # Another pod is running migrations, wait for it
        print("Lock not acquired - another pod is running migrations.", flush=True)
        print(f"Waiting up to {MAX_WAIT}s for migrations to complete...", flush=True)

        waited = 0
        while waited < MAX_WAIT:
            time.sleep(POLL_INTERVAL)
            waited += POLL_INTERVAL

            # Check if lock is still held
            cursor.execute("SELECT pg_try_advisory_lock(%s)", [LOCK_ID])
            now_acquired = cursor.fetchone()[0]

            if now_acquired:
                # We got the lock, which means the other pod finished
                cursor.execute("SELECT pg_advisory_unlock(%s)", [LOCK_ID])
                print("Other pod completed migrations. Proceeding.", flush=True)
                return 0

            print(f"Still waiting... ({waited}s / {MAX_WAIT}s)", flush=True)

        print("ERROR: Timeout waiting for migrations!", flush=True)
        return 1


if __name__ == "__main__":
    sys.exit(main())
