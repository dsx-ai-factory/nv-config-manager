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
"""Self-contained liveness heartbeat for the config-sync sidecar.

The sync loop (``_sync_kea_configuration_async``) touches a heartbeat file
after every *completed* reconciliation attempt. The heartbeat represents
event-loop progress, NOT successful config application: a recoverable
Redis/PostgreSQL/Kea error still advances the heartbeat, while a genuinely
wedged loop stops advancing it.

The ``check-sync-heartbeat`` CLI command (used by the container's ``exec``
livenessProbe) stats this file and fails when it is stale or missing, so
kubelet only recycles the sidecar when the loop itself is stuck -- not when a
dependency is merely unreachable.
"""

from __future__ import annotations

import os
import time
from pathlib import Path

# Default location of the heartbeat file. Lives under /tmp because the sidecar
# runs with a read-only root filesystem in most deployments and /tmp is an
# emptyDir-backed writable mount.
DEFAULT_HEARTBEAT_FILE = "/tmp/config-sync-v4.heartbeat"  # nosec B108

# Default staleness threshold (seconds) for the liveness check. The loop
# advances the heartbeat roughly once per refresh-interval plus bounded
# dependency timeouts, so this is set comfortably above the worst-case
# single-iteration duration to avoid false-positive restarts.
DEFAULT_MAX_AGE_SECONDS = 90.0

# Timestamp (epoch seconds) of the last reconciliation that actually verified
# and/or applied configuration successfully. Tracked SEPARATELY from the
# loop-progress heartbeat so readiness/alerting (owned by a later change) can
# distinguish "loop alive but dependency down" from "config is fresh". Kept
# intentionally minimal here -- just module-level state.
_last_successful_reconciliation: float | None = None


def touch_heartbeat(path: str = DEFAULT_HEARTBEAT_FILE) -> None:
    """Advance the heartbeat by updating the file's mtime (creating it if needed).

    Signals that the reconcile loop completed another iteration. Must be called
    after every completed attempt, including ones where a recoverable
    dependency error occurred.
    """
    Path(path).touch(exist_ok=True)


def heartbeat_age_seconds(
    path: str = DEFAULT_HEARTBEAT_FILE, now: float | None = None
) -> float | None:
    """Return the heartbeat file's age in seconds, or ``None`` if it cannot be read.

    Any ``OSError`` counts as unreadable, not just a missing file: this backs an
    exec livenessProbe, so a permission or path error must produce the same
    clean unhealthy verdict as a missing heartbeat rather than a traceback.
    """
    try:
        mtime = os.stat(path).st_mtime
    except OSError:
        return None
    if now is None:
        now = _now()
    return now - mtime


def age_is_fresh(age: float, max_age: float = DEFAULT_MAX_AGE_SECONDS) -> bool:
    """Return ``True`` when ``age`` falls inside the acceptable heartbeat window.

    A future-dated mtime (negative age) is not fresh. It means the clock stepped
    backwards after the last touch, and accepting it would report the loop
    healthy until real time caught up -- indefinitely, if the loop is wedged. A
    live loop re-touches the file on its next iteration, which re-dates it under
    the new clock well inside the probe's failure threshold.
    """
    return 0 <= age <= max_age


def heartbeat_is_fresh(
    path: str = DEFAULT_HEARTBEAT_FILE,
    max_age: float = DEFAULT_MAX_AGE_SECONDS,
    now: float | None = None,
) -> bool:
    """Return ``True`` only when the heartbeat exists and its age is within ``max_age``."""
    age = heartbeat_age_seconds(path, now=now)
    if age is None:
        return False
    return age_is_fresh(age, max_age)


def record_successful_reconciliation(now: float | None = None) -> None:
    """Record that a reconciliation successfully verified/applied configuration."""
    global _last_successful_reconciliation
    _last_successful_reconciliation = _now() if now is None else now


def last_successful_reconciliation() -> float | None:
    """Return the epoch timestamp of the last successful reconciliation, if any."""
    return _last_successful_reconciliation


def _now() -> float:
    """Return the current epoch time (indirection kept simple for testing)."""
    return time.time()
