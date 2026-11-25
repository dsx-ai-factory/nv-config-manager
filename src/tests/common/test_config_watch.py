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
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import MagicMock, patch

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
    only: tuple[str, ...] | None = None,
) -> list[tuple[int, int]]:
    """Start a watch, apply a change to the file, and report any signal sent.

    The watch reads its baseline as it starts, the way a service does, so the
    change has to land after it is already running.

    Args:
        path: INI file being watched
        change: Applied once the watch has taken its baseline
        max_jitter: Upper bound on the delay before signalling
        only: Names of the settings the watch restarts for, or None for all

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
            kwargs={"only": only},
            daemon=True,
        )
        watcher.start()

        try:
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
        finally:
            # Stopped while os.kill is still patched: a watch left running by a
            # failed assertion would otherwise signal the test process itself.
            stop.set()
            watcher.join(timeout=5.0)
            assert not watcher.is_alive()

    return signals


def test_a_rotated_password_stops_the_process(ini: Path) -> None:
    """A new credential only reaches an open connection by way of a restart."""
    signals = _watch_while(ini, change=lambda: ini.write_text(ROTATED))

    assert signals == [(os.getpid(), signal.SIGTERM)]


def test_a_renamed_stream_stops_the_process(ini: Path) -> None:
    """Consumers bind their stream at startup, so a rename needs a restart too."""
    signals = _watch_while(ini, change=lambda: ini.write_text(RENAMED_STREAM))

    assert signals == [(os.getpid(), signal.SIGTERM)]


def test_a_service_restarts_only_for_the_settings_it_reads_at_startup(ini: Path) -> None:
    """A service that reads a setting on each use gains nothing from a restart for it."""
    only = ("nats.config_manager_stream",)

    assert _watch_while(ini, change=lambda: ini.write_text(ROTATED), only=only) == []
    assert _watch_while(ini, change=lambda: ini.write_text(RENAMED_STREAM), only=only) == [
        (os.getpid(), signal.SIGTERM)
    ]


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


def test_the_restart_is_spread_across_replicas() -> None:
    """Every replica sees the change at once, so they must not all exit at once."""
    with patch(
        "nv_config_manager.common.config_watch.socket.gethostname",
        side_effect=[f"render-api-{replica}" for replica in range(20)],
    ):
        delays = {config_watch._restart_delay(120.0, _digest(ROTATED)) for _ in range(20)}

    assert len(delays) == 20
    assert all(0 <= delay <= 120.0 for delay in delays)


def test_the_containers_of_a_pod_restart_together() -> None:
    """A pod whose containers drew separate delays would go unready once for each."""
    with patch("nv_config_manager.common.config_watch.socket.gethostname", return_value="dhcp-0"):
        delays = {config_watch._restart_delay(120.0, _digest(ROTATED)) for _ in range(3)}

    assert len(delays) == 1


def test_each_change_draws_a_new_delay_for_the_pod() -> None:
    """Replicas whose delays once fell close together must not overlap on every change."""
    with patch("nv_config_manager.common.config_watch.socket.gethostname", return_value="dhcp-0"):
        first = config_watch._restart_delay(120.0, _digest(ROTATED))
        second = config_watch._restart_delay(120.0, _digest(RENAMED_STREAM))

    assert first != second


def test_containers_that_notice_at_different_times_signal_at_the_same_time(ini: Path) -> None:
    """Each container polls on its own phase, so they count from the file's change."""
    changed_at = ini.stat().st_mtime
    signal_times = set()
    with patch("nv_config_manager.common.config_watch._restart_delay", return_value=40.0):
        for noticed_after in (1.0, 12.0, 29.0):
            now = changed_at + noticed_after
            with patch("nv_config_manager.common.config_watch.time.time", return_value=now):
                wait = config_watch._seconds_until_restart(str(ini), 30.0, 120.0, "digest")
            signal_times.add(round(now + wait, 6))

    assert signal_times == {round(changed_at + 30.0 + 40.0, 6)}


def test_a_change_noticed_late_is_not_delayed_further(ini: Path) -> None:
    """A file that changed long ago must not wait, or wait longer than a fresh delay."""
    changed_at = ini.stat().st_mtime
    with patch("nv_config_manager.common.config_watch._restart_delay", return_value=40.0):
        with patch(
            "nv_config_manager.common.config_watch.time.time", return_value=changed_at + 600.0
        ):
            assert config_watch._seconds_until_restart(str(ini), 30.0, 120.0, "digest") == 0.0
        with patch(
            "nv_config_manager.common.config_watch.time.time", return_value=changed_at - 600.0
        ):
            assert config_watch._seconds_until_restart(str(ini), 30.0, 120.0, "digest") == 70.0


def test_the_watch_waits_out_its_delay_before_signalling(ini: Path) -> None:
    """The wait computed for this pod is the one the watch sleeps for."""
    with patch(
        "nv_config_manager.common.config_watch._seconds_until_restart", return_value=0.0
    ) as wait:
        signals = _watch_while(ini, change=lambda: ini.write_text(ROTATED), max_jitter=20.0)

    wait.assert_called_once_with(str(ini), POLL_INTERVAL, 20.0, _digest(ROTATED))
    assert signals == [(os.getpid(), signal.SIGTERM)]


@contextmanager
def _second_write(
    write: Callable[[], object],
) -> Iterator[tuple[Callable[[], None], MagicMock]]:
    """Patch the restart delay so a second write always lands inside it.

    The watch blocks in its first scheduling call until the test has written
    the file again, so neither side depends on how the threads are scheduled:
    the second write cannot come before the watch saw the first change, nor
    after the delay has run out.

    Args:
        write: Makes the second change to the file

    Yields:
        A function to call after the first change, which waits for the watch
        to schedule it and then makes the second, and the patched delay
    """
    scheduled = threading.Event()
    released = threading.Event()
    timed_out = threading.Event()

    def schedule(*_args: object) -> float:
        scheduled.set()
        if released.wait(timeout=5.0):
            return 0.0
        # The second write never came. Keep the watch from signalling for the
        # first version, and fail the test below rather than let it pass or
        # fail on whichever version the watch happened to act on.
        timed_out.set()
        return 3600.0

    def write_again() -> None:
        try:
            assert scheduled.wait(timeout=5.0)
            write()
        finally:
            released.set()

    with patch(
        "nv_config_manager.common.config_watch._seconds_until_restart", side_effect=schedule
    ) as wait:
        yield write_again, wait
    assert not timed_out.is_set(), "the second write did not land while the delay was held open"


def test_a_second_change_during_the_delay_reschedules_from_the_newer_version(ini: Path) -> None:
    """A container that slept through the second change would signal apart from its pod."""

    with _second_write(lambda: ini.write_text(RENAMED_STREAM)) as (write_again, wait):

        def change_twice() -> None:
            ini.write_text(ROTATED)
            write_again()

        signals = _watch_while(ini, change=change_twice)

    assert [call.args[3] for call in wait.call_args_list] == [
        _digest(ROTATED),
        _digest(RENAMED_STREAM),
    ]
    assert signals == [(os.getpid(), signal.SIGTERM)]


def test_a_second_change_is_noticed_before_the_delay_runs_out(ini: Path) -> None:
    """The file is read on every poll of the delay, not only when it ends.

    A container that looked again only at the end of its own delay would still
    signal later than a neighbour that was scheduled from the newer version.
    """
    ini.write_text(ROTATED)
    first_scheduled = threading.Event()

    def schedule(_path: str, _poll: float, _jitter: float, digest: str) -> float:
        if digest == _digest(ROTATED):
            first_scheduled.set()
            return 3600.0
        return 0.0

    outcomes: list[config_watch._Hold] = []
    with patch(
        "nv_config_manager.common.config_watch._seconds_until_restart", side_effect=schedule
    ) as wait:
        holding = threading.Thread(
            target=lambda: outcomes.append(
                config_watch._hold_for_restart(
                    str(ini),
                    {"nats.password": "original"},
                    _digest(ROTATED),
                    POLL_INTERVAL,
                    0.0,
                    threading.Event(),
                )[0]
            ),
            daemon=True,
        )
        holding.start()
        assert first_scheduled.wait(timeout=5.0)
        ini.write_text(RENAMED_STREAM)
        holding.join(timeout=5.0)

    assert not holding.is_alive()
    assert outcomes == [config_watch._Hold.RESTART]
    assert [call.args[3] for call in wait.call_args_list] == [
        _digest(ROTATED),
        _digest(RENAMED_STREAM),
    ]


def test_a_change_that_is_undone_during_the_delay_does_not_restart(
    ini: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """A value that flips and flips back leaves a process nothing to pick up."""

    with (
        _second_write(lambda: ini.write_text(ORIGINAL)) as (write_again, wait),
        caplog.at_level("INFO", logger=config_watch.logger.name),
    ):

        def flip_and_flip_back() -> None:
            ini.write_text(ROTATED)
            write_again()

        signals = _watch_while(ini, change=flip_and_flip_back)

    wait.assert_called_once()
    assert signals == []
    assert f"Configuration file {ini} changed back, not restarting" in caplog.messages


@pytest.mark.parametrize(
    "replace",
    [
        lambda ini: ini.write_text(NO_SECTION_HEADER),
        lambda ini: ini.write_bytes(b"[nats]\npassword = \xff\xfe\n"),
        lambda ini: ini.unlink(),
    ],
    ids=["unparseable", "not-utf8", "missing"],
)
def test_a_change_that_breaks_during_the_delay_does_not_restart(
    ini: Path, replace: Callable[[Path], object]
) -> None:
    """A working process must not be stopped for a file it could not load."""

    with _second_write(lambda: replace(ini)) as (write_again, wait):

        def change_then_break() -> None:
            ini.write_text(ROTATED)
            write_again()

        signals = _watch_while(ini, change=change_then_break)

    wait.assert_called_once()
    assert signals == []


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
