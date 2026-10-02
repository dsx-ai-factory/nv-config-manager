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

import os
import random
import signal
import threading
from configparser import ConfigParser

from nv_config_manager.common.ini import config_path, file_digest, loaded_config_snapshot
from nv_config_manager.common.log import LogCategory, get_logger

logger = get_logger(__name__, category=LogCategory.CONFIG)

# Kubernetes takes up to about a minute to surface a Secret update in a running
# pod, so polling faster than this only burns syscalls.
DEFAULT_POLL_INTERVAL_SECONDS = 30.0

# Every replica of a service reads the same file and sees the change within the
# same window. Waiting a random part of this spreads their restarts out, which a
# Deployment rollout would otherwise have arranged.
DEFAULT_MAX_JITTER_SECONDS = 20.0

_watch_started = threading.Lock()


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

    def flatten(text: str) -> dict[str, str]:
        parser = ConfigParser(interpolation=None, delimiters=("=",))
        try:
            parser.read_string(text)
        except Exception:  # pylint: disable=broad-exception-caught
            # A half-written file should still be reported as a change.
            return {}
        return {
            f"{section}.{option}": value
            for section in parser.sections()
            for option, value in parser[section].items()
        }

    old, new = flatten(before), flatten(after)
    return sorted(name for name in old.keys() | new.keys() if old.get(name) != new.get(name))


def _read(path: str) -> str:
    try:
        with open(path, encoding="utf-8") as handle:
            return handle.read()
    except OSError:
        return ""


def _watch(
    path: str,
    poll_interval: float,
    max_jitter: float,
    stop: threading.Event,
    baseline: tuple[str, str] | None = None,
) -> None:
    """Poll until the file's contents change, then signal a shutdown.

    Args:
        path: File to watch
        poll_interval: Seconds between checks
        max_jitter: Upper bound on the delay before signalling
        stop: Set this to abandon the watch without touching the process
        baseline: Digest and text already parsed at startup. A later read of
            the file is not a safe baseline: the process is still using the
            earlier copy, so a change in between would never restart it.
    """
    if baseline is None:
        digest = file_digest(path)
        contents = _read(path)
    else:
        digest, contents = baseline

    while not stop.wait(poll_interval):
        current = file_digest(path)
        if current is None or current == digest:
            # An unreadable file is treated as a transient mount problem rather
            # than a change, so a service is never restarted for losing sight of
            # its configuration.
            continue

        logger.info(
            "Configuration file %s changed, restarting to pick it up. Changed settings: %s",
            path,
            ", ".join(changed_keys(contents, _read(path))) or "none identified",
        )
        if stop.wait(random.uniform(0, max_jitter)):  # noqa: S311 - spreading load, not crypto
            return

        # Each service already shuts down cleanly on SIGTERM, the same way it
        # would if Kubernetes had replaced the pod, so reuse that path instead
        # of adding a second kind of exit.
        os.kill(os.getpid(), signal.SIGTERM)
        return


def restart_on_config_change(
    poll_interval: float = DEFAULT_POLL_INTERVAL_SECONDS,
    max_jitter: float = DEFAULT_MAX_JITTER_SECONDS,
) -> bool:
    """Watch the unified INI file and stop this process when it changes.

    Call once while a long-lived service starts up. The watch runs on a daemon
    thread so it suits the synchronous and asyncio services equally and does not
    hold up interpreter shutdown.

    Args:
        poll_interval: Seconds between checks of the file
        max_jitter: Upper bound on the delay before shutting down

    Returns:
        True when a watch was started, False when one was already running
    """
    if not _watch_started.acquire(blocking=False):
        return False

    path = config_path()
    threading.Thread(
        target=_watch,
        args=(path, poll_interval, max_jitter, threading.Event(), loaded_config_snapshot(path)),
        name="config-watch",
        daemon=True,
    ).start()
    logger.info("Watching %s for configuration changes", path)
    return True
