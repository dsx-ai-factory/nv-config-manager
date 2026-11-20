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
"""Tests for restarting a service when its configuration file changes."""

import configparser
import hashlib
import os
import signal
import threading
import time
from collections.abc import Callable
from pathlib import Path
from unittest.mock import patch

import pytest

from nv_config_manager.common import config_watch
from nv_config_manager.common.config.loader import clear_config_cache, load_config
from nv_config_manager.common.config_watch import _watch, changed_keys, restart_on_config_change
from nv_config_manager.common.ini import (
    loaded_config_snapshot,
    read_config_snapshot,
    remember_loaded_config,
)

ORIGINAL = """[nats]
server = nats://nats:4222
queue = nv-config-manager
password = original
"""

ROTATED = """[nats]
server = nats://nats:4222
queue = nv-config-manager
password = rotated
"""

RENAMED_STREAM = """[nats]
server = nats://nats:4222
queue = nv-config-manager
password = original
config_manager_stream = renamed
"""

COMMENTED = """# rendered by a newer template
[nats]
queue = nv-config-manager
server = nats://nats:4222

password = original
"""

NO_SECTION_HEADER = "password = no section header\n"

POLL_INTERVAL = 0.01


def _digest(contents: str) -> str:
    return hashlib.sha256(contents.encode()).hexdigest()


@pytest.fixture
def ini(tmp_path: Path) -> Path:
    """Write a starting INI file and return its path."""
    path = tmp_path / "nv-config-manager.ini"
    path.write_text(ORIGINAL)
    return path


def _watch_while(
    path: Path,
    change: Callable[[], None] | None = None,
    max_jitter: float = 0.0,
) -> list[tuple[int, int]]:
    """Start a watch, apply a change to the file, and report any signal sent.

    The watch reads its baseline as it starts, the way a service does, so the
    change has to land after it is already running.

    Args:
        path: INI file being watched
        change: Applied once the watch has taken its baseline
        max_jitter: Upper bound on the delay before signalling

    Returns:
        The (pid, signal) pairs the watch sent
    """
    signals: list[tuple[int, int]] = []
    stop = threading.Event()
    baseline_taken = threading.Event()
    read_baseline = config_watch._read_baseline

    def record_baseline(target: str) -> tuple[str, str] | None:
        """Report that the watch has finished reading the file it starts from."""
        baseline = read_baseline(target)
        baseline_taken.set()
        return baseline

    with (
        patch(
            "nv_config_manager.common.config_watch.os.kill",
            side_effect=lambda pid, sig: signals.append((pid, sig)),
        ),
        patch("nv_config_manager.common.config_watch._read_baseline", side_effect=record_baseline),
    ):
        watcher = threading.Thread(
            target=_watch,
            args=(str(path), POLL_INTERVAL, max_jitter, stop),
            daemon=True,
        )
        watcher.start()

        # Changing the file before the watch has read it would make the new
        # contents its baseline, and the change would go unnoticed.
        assert baseline_taken.wait(timeout=5.0)
        if change is not None:
            change()

        # A watch with nothing to act on never returns, so give it a bounded
        # chance to react and then take it down.
        deadline = time.monotonic() + 1.0
        while not signals and time.monotonic() < deadline:
            time.sleep(POLL_INTERVAL)
        stop.set()
        watcher.join(timeout=5.0)

    return signals


def test_a_rotated_password_stops_the_process(ini: Path) -> None:
    """A new credential only reaches an open connection by way of a restart."""
    signals = _watch_while(ini, change=lambda: ini.write_text(ROTATED))

    assert signals == [(os.getpid(), signal.SIGTERM)]


def test_a_renamed_stream_stops_the_process(ini: Path) -> None:
    """Consumers bind their stream at startup, so a rename needs a restart too."""
    signals = _watch_while(ini, change=lambda: ini.write_text(RENAMED_STREAM))

    assert signals == [(os.getpid(), signal.SIGTERM)]


def test_an_unchanged_file_leaves_the_process_alone(ini: Path) -> None:
    """Nothing changed, so nothing should be disturbed."""
    assert _watch_while(ini) == []


def test_an_identical_rewrite_is_not_a_change(ini: Path, tmp_path: Path) -> None:
    """Kubernetes and Vault Agent rewrite these files on their own schedules.

    The replacement arrives with a new inode and new timestamps, so watching
    file metadata would restart a service that has nothing new to read.
    """

    def rewrite_with_the_same_contents() -> None:
        replacement = tmp_path / "replacement.ini"
        replacement.write_text(ORIGINAL)
        os.replace(replacement, ini)

    signals = _watch_while(ini, change=rewrite_with_the_same_contents)

    assert ini.read_text() == ORIGINAL
    assert signals == []


def test_an_unreadable_file_is_not_a_change(ini: Path) -> None:
    """Losing sight of the file is a mount problem, not a reason to restart."""
    assert _watch_while(ini, change=ini.unlink) == []


def test_a_change_that_only_moves_comments_and_order_is_not_a_change(ini: Path) -> None:
    """A re-rendered template with the same settings gives a service nothing new."""
    assert _watch_while(ini, change=lambda: ini.write_text(COMMENTED)) == []


@pytest.mark.parametrize(
    "broken",
    [NO_SECTION_HEADER.encode(), b"[nats]\npassword = \xff\xfe\n"],
    ids=["unparseable", "not-utf8"],
)
def test_a_file_a_service_could_not_load_is_not_acted_on(
    ini: Path, broken: bytes, caplog: pytest.LogCaptureFixture
) -> None:
    """Restarting into a broken file would take every service down together."""
    with caplog.at_level("ERROR", logger=config_watch.logger.name):
        signals = _watch_while(ini, change=lambda: ini.write_bytes(broken))

    assert signals == []
    assert [r.getMessage() for r in caplog.records].count(
        f"Configuration file {ini} changed but cannot be parsed, "
        "keeping the configuration already loaded"
    ) == 1


def test_a_valid_file_after_a_broken_one_still_stops_the_process(ini: Path) -> None:
    """The watch has to survive a bad update to act on the fix that follows it."""

    def break_then_fix() -> None:
        ini.write_text(NO_SECTION_HEADER)
        time.sleep(POLL_INTERVAL * 10)
        ini.write_text(ROTATED)

    assert _watch_while(ini, change=break_then_fix) == [(os.getpid(), signal.SIGTERM)]


def test_the_restart_is_spread_across_replicas(ini: Path) -> None:
    """Every replica sees the change at once, so they must not all exit at once."""
    with patch("nv_config_manager.common.config_watch.random.uniform", return_value=0.0) as uniform:
        _watch_while(ini, change=lambda: ini.write_text(ROTATED), max_jitter=20.0)

    uniform.assert_called_once_with(0, 20.0)


def test_a_process_that_ignores_the_signal_is_stopped_outright(ini: Path) -> None:
    """A container's PID 1 drops SIGTERM unless it installed a handler for it."""
    ini.write_text(ROTATED)
    baseline = (_digest(ORIGINAL), ORIGINAL)

    with (
        patch("nv_config_manager.common.config_watch.os.kill") as kill,
        patch("nv_config_manager.common.config_watch.os._exit") as forced_exit,
    ):
        watcher = threading.Thread(
            target=_watch,
            args=(str(ini), POLL_INTERVAL, 0.0, threading.Event(), baseline, 0.0),
            daemon=True,
        )
        watcher.start()
        watcher.join(timeout=5.0)
        assert not watcher.is_alive()

    kill.assert_called_once_with(os.getpid(), signal.SIGTERM)
    forced_exit.assert_called_once_with(config_watch.FORCED_EXIT_CODE)


def test_a_process_that_shuts_down_in_time_is_not_forced(ini: Path) -> None:
    """A service with a SIGTERM handler keeps its clean exit."""
    with patch("nv_config_manager.common.config_watch.os._exit") as forced_exit:
        signals = _watch_while(ini, change=lambda: ini.write_text(ROTATED))

    assert signals == [(os.getpid(), signal.SIGTERM)]
    forced_exit.assert_not_called()


def test_changed_keys_names_the_settings_that_moved() -> None:
    """An operator needs to know why a service restarted."""
    assert changed_keys(ORIGINAL, RENAMED_STREAM) == ["nats.config_manager_stream"]
    assert changed_keys(ORIGINAL, ROTATED) == ["nats.password"]
    assert changed_keys(ORIGINAL, ORIGINAL) == []


def test_a_change_after_startup_load_still_restarts(ini: Path) -> None:
    """A rewrite after settings were loaded must not become the baseline."""
    ini.write_text(ROTATED)
    signals: list[tuple[int, int]] = []
    stop = threading.Event()
    baseline = (_digest(ORIGINAL), ORIGINAL)

    with patch(
        "nv_config_manager.common.config_watch.os.kill",
        side_effect=lambda pid, sig: signals.append((pid, sig)),
    ):
        watcher = threading.Thread(
            target=_watch,
            args=(str(ini), POLL_INTERVAL, 0.0, stop, baseline),
            daemon=True,
        )
        watcher.start()
        deadline = time.monotonic() + 1.0
        while not signals and time.monotonic() < deadline:
            time.sleep(POLL_INTERVAL)
        stop.set()
        watcher.join(timeout=5.0)

    assert signals == [(os.getpid(), signal.SIGTERM)]


def test_read_config_snapshot_matches_the_file(ini: Path) -> None:
    """The digest and the contents come from one read of the file."""
    assert read_config_snapshot(str(ini)) == (_digest(ORIGINAL), ORIGINAL.encode())


def test_load_config_parses_the_snapshot_it_records(
    ini: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A swap between the snapshot and the parse cannot split the two."""
    original_digest = _digest(ORIGINAL)

    def snapshot_then_swap(path: str) -> tuple[str, bytes] | None:
        snapshot = read_config_snapshot(path)
        ini.write_text(ROTATED)
        return snapshot

    monkeypatch.setenv("NV_CONFIG_MANAGER_INI", str(ini))
    monkeypatch.setattr(
        "nv_config_manager.common.config.loader.read_config_snapshot", snapshot_then_swap
    )
    clear_config_cache()

    loaded = load_config()

    assert loaded.get("nats", "password") == "original"
    assert loaded_config_snapshot(str(ini)) == (original_digest, ORIGINAL)
    assert changed_keys(ORIGINAL, ini.read_text()) == ["nats.password"]


def test_the_first_parse_stays_the_baseline(ini: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Objects built at startup hold the first version, whatever is parsed later."""
    monkeypatch.setenv("NV_CONFIG_MANAGER_INI", str(ini))
    clear_config_cache()
    load_config()

    ini.write_text(ROTATED)

    assert load_config().get("nats", "password") == "rotated"
    assert loaded_config_snapshot(str(ini)) == (_digest(ORIGINAL), ORIGINAL)


def test_config_with_invalid_bytes_fails_to_load(
    ini: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A silently replaced byte in a credential would surface as an auth failure."""
    ini.write_bytes(b"[nats]\npassword = \xff\xfe\n")
    monkeypatch.setenv("NV_CONFIG_MANAGER_INI", str(ini))
    clear_config_cache()

    with pytest.raises(UnicodeDecodeError):
        load_config()

    assert loaded_config_snapshot(str(ini)) is None


def test_unparseable_config_records_no_baseline(ini: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A file the process failed to load must not become the watcher baseline."""
    ini.write_text("password = no section header\n")
    monkeypatch.setenv("NV_CONFIG_MANAGER_INI", str(ini))
    clear_config_cache()

    with pytest.raises(configparser.MissingSectionHeaderError):
        load_config()

    assert loaded_config_snapshot(str(ini)) is None


def test_restart_baselines_on_the_loaded_snapshot(
    ini: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The watch starts from the parsed snapshot when one exists."""
    monkeypatch.setenv("NV_CONFIG_MANAGER_INI", str(ini))
    digest = _digest(ORIGINAL)
    clear_config_cache()
    remember_loaded_config(str(ini), digest, ORIGINAL)
    captured: dict[str, tuple[object, ...]] = {}

    class _Thread:
        def __init__(self, *_args: object, **kwargs: object) -> None:
            captured["args"] = kwargs["args"]  # type: ignore[assignment]

        def start(self) -> None:
            return None

    monkeypatch.setattr(config_watch.threading, "Thread", _Thread)
    try:
        assert restart_on_config_change() is True
    finally:
        if config_watch._watch_started.locked():
            config_watch._watch_started.release()

    assert captured["args"][4] == (digest, ORIGINAL)


def test_restart_baselines_before_the_service_loads(
    ini: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Without a loaded snapshot, the baseline is the file as it was at the call."""
    monkeypatch.setenv("NV_CONFIG_MANAGER_INI", str(ini))
    clear_config_cache()
    captured: dict[str, tuple[object, ...]] = {}

    class _Thread:
        def __init__(self, *_args: object, **kwargs: object) -> None:
            captured["args"] = kwargs["args"]  # type: ignore[assignment]

        def start(self) -> None:
            ini.write_text(ROTATED)

    monkeypatch.setattr(config_watch.threading, "Thread", _Thread)
    try:
        assert restart_on_config_change() is True
    finally:
        if config_watch._watch_started.locked():
            config_watch._watch_started.release()

    assert captured["args"][4] == (_digest(ORIGINAL), ORIGINAL)


def test_changed_keys_does_not_leak_the_values() -> None:
    """The file holds credentials, so only names may be logged."""
    reported = " ".join(changed_keys(ORIGINAL, ROTATED))

    assert "rotated" not in reported
    assert "original" not in reported
