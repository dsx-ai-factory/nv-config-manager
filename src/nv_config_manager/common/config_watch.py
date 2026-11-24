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
"""Ask a service to restart once its configuration file has changed.

Kubernetes propagates a Secret update into a running pod, so rotated
credentials and renamed streams reach the INI without the pod being replaced.
Anything a service built from the INI at startup keeps using the values it was
given, and a NATS or Nautobot connection keeps presenting its original
credentials even across its own automatic reconnects. Rebuilding each of those
in place would mean tracking every piece of state derived from the file, in
every service, forever. Restarting replaces all of it at once.
"""

from __future__ import annotations

import configparser
import os
import random
import signal
import socket
import threading

from nv_config_manager.common.ini import (
    config_path,
    loaded_config_snapshot,
    read_config_snapshot,
)
from nv_config_manager.common.log import LogCategory, get_logger

logger = get_logger(__name__, category=LogCategory.CONFIG)

# Kubernetes takes up to about a minute to surface a Secret update in a running
# pod, so polling faster than this only burns syscalls.
DEFAULT_POLL_INTERVAL_SECONDS = 30.0

# Every replica of a service reads the same file and sees the change within the
# same window. Waiting a random part of this spreads their restarts out, which a
# Deployment rollout would otherwise have arranged. It has to be long next to
# the time a replica takes to start and become ready, or the replicas of one
# service are still likely to be down together.
DEFAULT_MAX_JITTER_SECONDS = 120.0


def _restart_delay(max_jitter: float) -> float:
    """Pick this pod's share of the restart spread.

    The containers of a pod share a hostname, so they draw the same delay and
    the pod goes unready once. Drawn per process, each container would take
    the pod out of service at a different moment in the window.

    Args:
        max_jitter: Upper bound on the delay

    Returns:
        Seconds to wait before signalling, the same for every call in this pod
    """
    # Spreading load, not cryptography.
    return random.Random(socket.gethostname()).uniform(0, max_jitter)  # noqa: S311


# How long a process gets to shut down cleanly before it is stopped outright.
# This is the grace period Kubernetes gives a container by default.
DEFAULT_SHUTDOWN_GRACE_SECONDS = 30.0

# A service bounds its own drain with this so that it finishes, and exits
# cleanly, before the grace period above runs out.
SERVICE_DRAIN_SECONDS = 25

# Distinguishes a forced stop from the clean exit a SIGTERM handler produces.
FORCED_EXIT_CODE = 1

_watch_started = threading.Lock()


def _settings(text: str | None) -> dict[str, str] | None:
    """Parse INI text the way the loader does, flattened to "section.option".

    Args:
        text: Contents of an INI file, or None when they could not be decoded

    Returns:
        The settings, or None when a service could not load this file
    """
    if text is None:
        return None
    parser = configparser.ConfigParser(interpolation=None, delimiters=("=",))
    try:
        parser.read_string(text)
    except configparser.Error:
        return None
    return {
        f"{section}.{option}": value
        for section in parser.sections()
        for option, value in parser[section].items()
    }


def _decode(raw: bytes) -> str | None:
    """Decode file contents as strictly as the loader does."""
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError:
        return None


def _differing(old: dict[str, str], new: dict[str, str]) -> list[str]:
    return sorted(name for name in old.keys() | new.keys() if old.get(name) != new.get(name))


def changed_keys(before: str, after: str) -> list[str]:
    """Name the settings that differ between two versions of an INI file.

    Only names are returned. The file holds credentials, so the values behind
    them must not reach the logs.

    Args:
        before: Contents of the previous version
        after: Contents of the current version

    Returns:
        Sorted "section.option" names whose values differ, added or removed
    """
    return _differing(_settings(before) or {}, _settings(after) or {})


def _watch(
    path: str,
    poll_interval: float,
    max_jitter: float,
    stop: threading.Event,
    baseline: tuple[str, str] | None = None,
    shutdown_grace: float = DEFAULT_SHUTDOWN_GRACE_SECONDS,
) -> None:
    """Poll until the file's settings change, then signal a shutdown.

    Args:
        path: File to watch
        poll_interval: Seconds between checks
        max_jitter: Upper bound on the delay before signalling
        stop: Set this to abandon the watch without touching the process
        baseline: Digest and text already parsed at startup. A later read of
            the file is not a safe baseline: the process is still using the
            earlier copy, so a change in between would never restart it.
        shutdown_grace: Seconds the process gets to exit after the signal
    """
    if baseline is None:
        baseline = _read_baseline(path)
    digest, contents = baseline if baseline is not None else (None, "")
    settings = _settings(contents) or {}
    rejected: str | None = None

    while not stop.wait(poll_interval):
        # One read supplies the digest, the parse and the logged names, so they
        # all describe the same version of the file.
        snapshot = read_config_snapshot(path)
        if snapshot is None or snapshot[0] == digest:
            # An unreadable file is treated as a transient mount problem rather
            # than a change, so a service is never restarted for losing sight of
            # its configuration.
            continue

        current, raw = snapshot
        new_settings = _settings(_decode(raw))
        if new_settings is None:
            # Restarting would trade a working process for one that cannot
            # load its configuration, in every service at once. Keep running on
            # the last good file until a valid one arrives.
            if current != rejected:
                rejected = current
                logger.error(
                    "Configuration file %s changed but cannot be parsed, "
                    "keeping the configuration already loaded",
                    path,
                )
            continue

        changed = _differing(settings, new_settings)
        if not changed:
            # Comments, whitespace or ordering moved. Nothing a service reads
            # is different, so adopt this version and keep watching.
            digest = current
            continue

        logger.info(
            "Configuration file %s changed, restarting to pick it up. Changed settings: %s",
            path,
            ", ".join(changed),
        )
        if stop.wait(_restart_delay(max_jitter)):
            return

        # A value that changed and changed back during the delay leaves nothing
        # to pick up, so look again before disturbing the process.
        latest = read_config_snapshot(path)
        if latest is not None:
            latest_settings = _settings(_decode(latest[1]))
            if latest_settings is not None and not _differing(settings, latest_settings):
                logger.info("Configuration file %s changed back, not restarting", path)
                digest = latest[0]
                continue

        # Each service already shuts down cleanly on SIGTERM, the same way it
        # would if Kubernetes had replaced the pod, so reuse that path instead
        # of adding a second kind of exit.
        os.kill(os.getpid(), signal.SIGTERM)

        # A container's main process is PID 1, and the kernel drops a signal
        # sent to PID 1 unless the process installed a handler for it. A
        # service without one would log the change and carry on with the old
        # configuration, so stop it the way Kubernetes stops a container that
        # outlives its grace period.
        if stop.wait(shutdown_grace):
            return
        logger.warning(
            "Still running %.0fs after SIGTERM, exiting without a clean shutdown", shutdown_grace
        )
        os._exit(FORCED_EXIT_CODE)
        return


def _read_baseline(path: str) -> tuple[str, str] | None:
    """Read the file as the baseline for a process that has not parsed it yet."""
    snapshot = read_config_snapshot(path)
    if snapshot is None:
        return None
    digest, raw = snapshot
    # Only setting names are ever derived from this text, so a byte the loader
    # would reject is replaced here rather than ending the watch.
    return digest, raw.decode("utf-8", errors="replace")


def restart_on_config_change(
    poll_interval: float = DEFAULT_POLL_INTERVAL_SECONDS,
    max_jitter: float = DEFAULT_MAX_JITTER_SECONDS,
    shutdown_grace: float = DEFAULT_SHUTDOWN_GRACE_SECONDS,
) -> bool:
    """Watch the unified INI file and stop this process when it changes.

    Call once while a long-lived service starts up. The watch runs on a daemon
    thread so it suits the synchronous and asyncio services equally and does not
    hold up interpreter shutdown.

    Args:
        poll_interval: Seconds between checks of the file
        max_jitter: Upper bound on the delay before shutting down
        shutdown_grace: Seconds the process gets to exit cleanly before it is
            stopped outright. Pass more than the default for a service whose
            own shutdown is allowed to take longer.

    Returns:
        True when a watch was started, False when one was already running
    """
    if not _watch_started.acquire(blocking=False):
        return False

    path = config_path()
    # A service that has not loaded the INI yet reads it after this returns, so
    # the baseline has to be taken here rather than on the watch thread, which
    # could run after that load and adopt a newer file as its starting point.
    baseline = loaded_config_snapshot(path) or _read_baseline(path)
    threading.Thread(
        target=_watch,
        args=(path, poll_interval, max_jitter, threading.Event(), baseline, shutdown_grace),
        name="config-watch",
        daemon=True,
    ).start()
    logger.info("Watching %s for configuration changes", path)
    return True
