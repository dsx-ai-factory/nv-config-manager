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
"""Launch screen - step progress and log output for simulation bringup."""

from __future__ import annotations

import json
import os
import shlex
import subprocess
import tempfile
import threading
import time
from collections import deque
from dataclasses import dataclass
from pathlib import Path
from queue import Empty, SimpleQueue
from typing import IO

from rich.text import Text
from textual import events, work
from textual.app import ComposeResult
from textual.containers import Container, Horizontal, Vertical, VerticalScroll
from textual.message import Message
from textual.widgets import Button, Input, Label, Static, Tab, Tabs
from textual.worker import Worker, WorkerState, get_current_worker

from nv_config_manager_installer.air_sim.constants import (
    CONFIG_MANAGER_NAUTOBOT_DEPLOYMENT,
    DEFAULT_AIR_FRONTEND_URL,
    DEFAULT_AIR_INTERNAL_FRONTEND_URL,
    NVCM_BOX_USER,
)
from nv_config_manager_installer.air_sim.orchestrator import (
    STEPS,
    OrchestratorCallback,
    SimOrchestrator,
    StepStatus,
)
from nv_config_manager_installer.air_sim.proxy import SOCKS_PORT, ProxyInfo
from nv_config_manager_installer.air_sim.sim_config import SimConfig
from nv_config_manager_installer.air_sim.sim_manager import AirSimulationManager

_STATUS_ICON = {
    StepStatus.PENDING: "[ ]",
    StepStatus.RUNNING: "[>]",
    StepStatus.SUCCESS: "[*]",
    StepStatus.FAILED: "[!]",
    StepStatus.SKIPPED: "[-]",
}

_COPY_ICON = "⧉"
_COPIED_ICON = "✓"
_MAX_DEPLOY_LOG_LINES = 4000
_MAX_SERVICE_LOG_LINES = 1000
_LOG_FLUSH_INTERVAL = 0.5
_MAX_LOG_DRAIN_PER_FLUSH = 250
_MAX_SERVICE_EVENTS_PER_POLL = 25
_SERVICE_POLL_INTERVAL = 10.0
_POD_POLL_INTERVAL = 15.0
_PROVISIONING_POLL_INTERVAL = 30.0
_STEP_ELAPSED_INTERVAL = 5.0
_WORKER_IDLE_INTERVAL = 0.5
_POD_READY_STATES = {"Running", "Completed", "Succeeded"}
_DHCP_ACTIVITY_KEYWORDS = (
    "DHCPDISCOVER",
    "DHCPOFFER",
    "DHCPREQUEST",
    "DHCPACK",
    "DHCPNAK",
    "DHCP4_LEASE",
    "DHCP4_PACKET",
    "DHCPSRV_CFGMGR_NEW_SUBNET4",
    "DHCP4_CONFIG_COMPLETE",
    "Generating configuration from nautobot data",
    "Validating configuration against KEA API",
    "Persisting configuration to Redis",
    "KEA DHCP4 Configuration Refresh Complete",
    "error",
    "failed",
    "warning",
)
_ZTP_SKIP_KEYWORDS = ("health", "metrics", "readiness", "livez")
_STREAM_HINTS = {
    "deploy": "Deploy output streams here. The complete log is also written to the path above.",
    "dhcp": "DHCP events appear here after install completes.",
    "ztp": "ZTP request events appear here after install completes.",
    "access": "Direct SSH appears here after SSH is ready. Browser access appears after the provider is ready.",
}
_TAB_TO_STREAM = {
    "stream-deploy": "deploy",
    "stream-dhcp": "dhcp",
    "stream-ztp": "ztp",
}
_BUFFER_LIMITS = {
    "deploy": _MAX_DEPLOY_LOG_LINES,
    "dhcp": _MAX_SERVICE_LOG_LINES,
    "ztp": _MAX_SERVICE_LOG_LINES,
}
_FOLLOW_LABEL_ON = "Follow: On"
_FOLLOW_LABEL_OFF = "Follow: Off"


@dataclass(frozen=True)
class AirProviderStatus:
    """Provider-owned labels and readiness rules used by the shared AIR dashboard."""

    display_name: str
    web_pod_prefix: str
    access_url: str
    dependent_pod_prefixes: tuple[str, ...] = ()
    excluded_web_pod_prefixes: tuple[str, ...] = ()

    def is_web_pod(self, pod: dict[str, str]) -> bool:
        """Return whether a pod is the provider's primary web workload."""
        name = pod.get("name", "")
        return name.startswith(f"{self.web_pod_prefix}-") and not name.startswith(
            self.excluded_web_pod_prefixes
        )

    @property
    def provisioning_wait_text(self) -> str:
        return f"Switches Provisioned: waiting for {self.display_name}"


DEFAULT_PROVIDER_STATUS = AirProviderStatus(
    display_name="Nautobot",
    web_pod_prefix=CONFIG_MANAGER_NAUTOBOT_DEPLOYMENT,
    access_url="https://nautobot.nvcm.air",
    dependent_pod_prefixes=(
        "nv-config-manager-nautobot-celery",
        "nv-config-manager-nautobot-celery-beat",
        "nv-config-manager-render-",
        "nv-config-manager-temporal-",
        "nv-config-manager-ztp",
        "nv-config-manager-dhcp",
    ),
    excluded_web_pod_prefixes=(
        "nv-config-manager-nautobot-celery",
        "nv-config-manager-nautobot-celery-beat",
    ),
)


def _create_deploy_log_path() -> Path:
    """Create an owner-only deployment log and return its path."""
    descriptor, path = tempfile.mkstemp(prefix="nvcm-deploy-", suffix=".log")
    os.close(descriptor)
    return Path(path)


def _copy_button(
    button_id: str,
    tooltip: str,
    *,
    classes: str = "copy-icon-btn",
    variant: str = "default",
) -> Button:
    button = Button(_COPY_ICON, id=button_id, variant=variant, classes=classes)
    button.tooltip = tooltip
    return button


def _clean_dhcp_line(line: str) -> str:
    """Extract the useful DHCP event text from Kea or refresh logs."""
    message = _json_log_message(line)
    if message:
        return message
    for marker in ("DHCP4", "DHCPSRV"):
        idx = line.find(marker)
        if idx >= 0:
            return line[idx:]
    return line


def _clean_ztp_line(line: str) -> str:
    """Extract the msg/message field from a JSON-structured ZTP log line."""
    message = _json_log_message(line)
    if message:
        return message
    return line


def _json_log_message(line: str) -> str | None:
    """Extract msg/message from a JSON-structured service log line."""
    idx = line.find("{")
    if idx >= 0:
        try:
            data = json.loads(line[idx:])
            message = data.get("msg") or data.get("message")
            return str(message) if message else None
        except json.JSONDecodeError:
            return None
    return None


def _is_interesting_dhcp_line(line: str) -> bool:
    """Return true for DHCP lines that help explain provisioning progress."""
    lowered = line.lower()
    return any(keyword in line or keyword.lower() in lowered for keyword in _DHCP_ACTIVITY_KEYWORDS)


def _is_interesting_ztp_line(line: str) -> bool:
    """Return true for ZTP access/API lines, excluding health/readiness noise."""
    lowered = line.lower()
    if any(keyword in lowered for keyword in _ZTP_SKIP_KEYWORDS):
        return False
    return " /v1/" in line or "/v1/" in line or "error" in lowered or "failed" in lowered


def _is_ready_pod(pod: dict[str, str]) -> bool:
    ready_count, _, ready_total = pod.get("ready", "").partition("/")
    return (
        bool(ready_count)
        and bool(ready_total)
        and ready_count == ready_total
        and pod.get("status") in _POD_READY_STATES
    )


def _pod_state_text(pod: dict[str, str]) -> str:
    return f"{pod.get('ready', '—')} {pod.get('status', 'Unknown')}"


def _pod_attention_text(pod: dict[str, str]) -> str:
    return f"{pod.get('name', 'unknown')} ({_pod_state_text(pod)})"


def _ssh_command(host: str, port: int, password: str) -> str:
    return (
        f"sshpass -p {shlex.quote(password)} ssh"
        f" -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null"
        f" -o PreferredAuthentications=password"
        f" -p {port} {NVCM_BOX_USER}@{host}"
    )


def _ssh_credentials(password: str) -> str:
    return f"Username: {NVCM_BOX_USER}\nPassword: {password}"


# Rough typical durations shown as hints for pending/running steps.
_TYPICAL: dict[str, str] = {
    "parse-topology": "~5s",
    "validate-images": "~10s",
    "create-sim": "~10s",
    "attach-cloud-init": "~5s",
    "start-sim": "4-6m",
    "create-ssh": "~15s",
    "wait-setup": "3-5m",
    "upload-files": "~5s",
    "run-deploy": "~20m",
    "post-deploy": "~1m",
}


def _fmt_duration(seconds: float) -> str:
    m, s = divmod(int(seconds), 60)
    return f"{m}:{s:02d}"


# ── Messages ──────────────────────────────────────────────────────────────────


class _StepUpdated(Message):
    def __init__(self, step_id: str, status: StepStatus, message: str) -> None:
        super().__init__()
        self.step_id = step_id
        self.status = status
        self.message = message


class _LogLine(Message):
    def __init__(self, line: str, stream: str = "deploy") -> None:
        super().__init__()
        self.line = line
        self.stream = stream


class _LogBatch(Message):
    def __init__(self, entries: list[tuple[str, str]]) -> None:
        super().__init__()
        self.entries = entries


class _StepElapsed(Message):
    pass


class _SshReady(Message):
    def __init__(self, host: str, port: int) -> None:
        super().__init__()
        self.host = host
        self.port = port


class _DeployStarted(Message):
    def __init__(self, host: str, port: int) -> None:
        super().__init__()
        self.host = host
        self.port = port


class _ProviderReady(Message):
    def __init__(self, host: str, port: int) -> None:
        super().__init__()
        self.host = host
        self.port = port


class _BringupComplete(Message):
    def __init__(self, success: bool, host: str, port: int) -> None:
        super().__init__()
        self.success = success
        self.host = host
        self.port = port


class _SimulationCreated(Message):
    def __init__(self, simulation_id: str) -> None:
        super().__init__()
        self.simulation_id = simulation_id


class _SocksPortChanged(Message):
    def __init__(self, socks_port: int) -> None:
        super().__init__()
        self.socks_port = socks_port


# ── Callback bridge ───────────────────────────────────────────────────────────


class _TuiCallback(OrchestratorCallback):
    def __init__(self, screen: LaunchScreen, log_file: IO[str] | None = None) -> None:
        self._screen = screen
        self._log_file = log_file

    def on_step(self, step_id: str, status: StepStatus, message: str = "") -> None:
        self._screen.post_message(_StepUpdated(step_id, status, message))
        label = dict(STEPS).get(step_id, step_id)
        suffix = f" - {message}" if message else ""
        self._screen.enqueue_log_line(f"{_STATUS_ICON[status]} {label}{suffix}", "deploy")

    def on_log(self, line: str) -> None:
        stream = "deploy"
        if line.startswith("[DHCP]"):
            stream = "dhcp"
        elif line.startswith("[ZTP]"):
            stream = "ztp"
        ui_line = line
        if stream == "dhcp":
            ui_line = _clean_dhcp_line(line)
            show_line = _is_interesting_dhcp_line(ui_line)
        elif stream == "ztp":
            ui_line = _clean_ztp_line(line)
            show_line = _is_interesting_ztp_line(ui_line)
        else:
            show_line = True
            if line.startswith("Simulation: "):
                self._screen.post_message(
                    _SimulationCreated(line.removeprefix("Simulation: ").strip())
                )
        if show_line:
            self._screen.enqueue_log_line(ui_line, stream)
        if self._log_file:
            self._log_file.write(line + "\n")
            self._log_file.flush()

    def on_ssh_ready(self, host: str, port: int) -> None:
        self._screen.post_message(_SshReady(host, port))

    def on_deploy_started(self, host: str, port: int) -> None:
        self._screen.post_message(_DeployStarted(host, port))

    def on_complete(self, success: bool, host: str = "", port: int = 0) -> None:
        self._screen.post_message(_BringupComplete(success, host, port))


# ── Step list widget ──────────────────────────────────────────────────────────


class _StepListWidget(Vertical):
    """Left panel: deployment steps with live status icons and elapsed timing."""

    def __init__(self, **kwargs: object) -> None:
        super().__init__(**kwargs)
        self._labels: dict[str, Static] = {}
        self._statuses: dict[str, StepStatus] = {}
        self._start_times: dict[str, float] = {}
        self._durations: dict[str, float] = {}
        self._running_step: str | None = None
        self._stop = threading.Event()
        self._tick_worker_lock = threading.Lock()
        self._tick_worker_running = False
        self._tick_thread: threading.Thread | None = None

    def compose(self) -> ComposeResult:
        yield Label("Steps", classes="section-title")
        yield Label("─" * 24, classes="section-divider")
        for step_id, _label in STEPS:
            w = Static(self._render_text(step_id, StepStatus.PENDING), id=f"step-{step_id}")
            self._labels[step_id] = w
            self._statuses[step_id] = StepStatus.PENDING
            yield w

    def on_unmount(self) -> None:
        self._stop.set()

    def _start_elapsed_worker(self) -> None:
        with self._tick_worker_lock:
            if self._tick_worker_running:
                return
            self._stop.clear()
            self._tick_worker_running = True
            self._tick_thread = threading.Thread(
                target=self._run_elapsed_ticks,
                name="nvcm-air-step-elapsed",
                daemon=True,
            )
            self._tick_thread.start()

    def _run_elapsed_ticks(self) -> None:
        try:
            while not self._stop.wait(_STEP_ELAPSED_INTERVAL):
                if not self._running_step:
                    break
                self.post_message(_StepElapsed())
        finally:
            with self._tick_worker_lock:
                self._tick_worker_running = False

    def on__step_elapsed(self, event: _StepElapsed) -> None:
        if self._running_step:
            self._refresh(self._running_step)
        event.stop()

    def update_step(self, step_id: str, status: StepStatus) -> None:
        self._statuses[step_id] = status
        if status == StepStatus.RUNNING:
            self._start_times[step_id] = time.monotonic()
            self._running_step = step_id
            self._start_elapsed_worker()
        else:
            if step_id in self._start_times and step_id not in self._durations:
                self._durations[step_id] = time.monotonic() - self._start_times[step_id]
            if self._running_step == step_id:
                self._running_step = None
        self._refresh(step_id)

    def _refresh(self, step_id: str) -> None:
        widget = self._labels.get(step_id)
        if widget:
            status = self._statuses.get(step_id, StepStatus.PENDING)
            widget.update(self._render_text(step_id, status))

    def _render_text(self, step_id: str, status: StepStatus) -> str:
        label = dict(STEPS).get(step_id, step_id)
        icon = _STATUS_ICON[status]

        timing = ""
        if status == StepStatus.RUNNING and step_id in self._start_times:
            elapsed = time.monotonic() - self._start_times[step_id]
            timing = f"  {_fmt_duration(elapsed)}"
        elif step_id in self._durations:
            timing = f"  {_fmt_duration(self._durations[step_id])}"

        hint = ""
        if status in (StepStatus.PENDING, StepStatus.RUNNING) and step_id in _TYPICAL:
            hint = f"  [dim](~{_TYPICAL[step_id]})[/dim]"

        return f"{icon}  {label}{timing}{hint}"


class _StreamTabsWidget(Vertical):
    """Tabbed log viewer that only renders the selected stream."""

    def __init__(self, **kwargs: object) -> None:
        super().__init__(**kwargs)
        self._buffers: dict[str, deque[str]] = {
            stream: deque(maxlen=limit) for stream, limit in _BUFFER_LIMITS.items()
        }
        self._seen_service_lines: dict[str, set[str]] = {"dhcp": set(), "ztp": set()}
        self._follow_streams: dict[str, bool] = dict.fromkeys(_BUFFER_LIMITS, True)
        self._active_stream = "deploy"

    def compose(self) -> ComposeResult:
        yield Label("Details", classes="section-title")
        yield Tabs(
            Tab("Deploy", id="stream-deploy"),
            Tab("DHCP", id="stream-dhcp"),
            Tab("ZTP", id="stream-ztp"),
            Tab("Access", id="stream-access"),
            active="stream-deploy",
            id="stream-tabs",
        )
        with Horizontal(id="stream-toolbar"):
            yield Static(_STREAM_HINTS["deploy"], id="stream-hint", classes="field-hint")
            yield Button(_FOLLOW_LABEL_ON, id="btn-stream-follow", variant="success", compact=True)
            yield Button("End", id="btn-stream-end", compact=True)
        with Container(id="stream-content"):
            with VerticalScroll(id="active-log-pane", classes="stream-log"):
                yield Static("", id="active-log", classes="stream-text", markup=False)
            with VerticalScroll(id="access-pane"):
                yield Static("Access details will appear after SSH is ready.")

    def on_mount(self) -> None:
        self._sync_visible_content()

    def on_tabs_tab_activated(self, event: Tabs.TabActivated) -> None:
        tab_id = event.tab.id or ""
        if tab_id == "stream-access":
            self._active_stream = "access"
        else:
            self._active_stream = _TAB_TO_STREAM.get(tab_id, "deploy")
        self._sync_visible_content()
        event.stop()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        button_id = event.button.id or ""
        if button_id == "btn-stream-follow":
            self._toggle_follow()
            event.stop()
        elif button_id == "btn-stream-end":
            self._follow_streams[self._active_stream] = True
            self._update_follow_button()
            self._scroll_active_end()
            event.stop()

    def append_lines(self, entries: list[tuple[str, str]]) -> None:
        active_changed = False
        for raw_line, stream in entries:
            line = raw_line.rstrip()
            if not line:
                continue
            stream = stream if stream in self._buffers else "deploy"
            if self._is_duplicate_service_line(stream, line):
                continue
            self._append_to_buffer(stream, line)
            if stream == self._active_stream:
                active_changed = True

        if active_changed:
            self._render_active_stream(follow=self._follow_streams[self._active_stream])

    def set_access_widget(self, widget: _ProxyAccessWidget) -> None:
        pane = self.query_one("#access-pane", VerticalScroll)
        try:
            existing = pane.query_one(_ProxyAccessWidget)
        except Exception:
            existing = None
        if existing is not None:
            existing.set_access(
                widget.proxy,
                widget.ssh_command,
                provider_ready=widget.provider_ready,
                provider_name=widget.provider_name,
            )
            self._sync_visible_content()
            return
        pane.remove_children()
        pane.mount(widget)
        self._sync_visible_content()

    def select_stream(self, stream: str) -> None:
        if stream != "access" and stream not in self._buffers:
            raise ValueError(f"Unknown stream: {stream}")
        self.query_one("#stream-tabs", Tabs).active = f"stream-{stream}"
        self._active_stream = stream
        self._sync_visible_content()

    def _is_duplicate_service_line(self, stream: str, line: str) -> bool:
        if stream == "deploy":
            return False
        seen = self._seen_service_lines[stream]
        return line in seen

    def _append_to_buffer(self, stream: str, line: str) -> None:
        buffer = self._buffers[stream]
        if stream != "deploy":
            seen = self._seen_service_lines[stream]
            if len(buffer) == buffer.maxlen and buffer:
                seen.discard(buffer[0])
            seen.add(line)
        buffer.append(line)

    def _sync_visible_content(self) -> None:
        hint = self.query_one("#stream-hint", Static)
        log_pane = self.query_one("#active-log-pane", VerticalScroll)
        access = self.query_one("#access-pane", VerticalScroll)
        follow = self.query_one("#btn-stream-follow", Button)
        end = self.query_one("#btn-stream-end", Button)

        hint.update(_STREAM_HINTS[self._active_stream])
        if self._active_stream == "access":
            log_pane.display = False
            access.display = True
            follow.display = False
            end.display = False
            return

        access.display = False
        log_pane.display = True
        follow.display = True
        end.display = True
        self._update_follow_button()
        self._render_active_stream(follow=self._follow_streams[self._active_stream])

    def _render_active_stream(self, *, follow: bool) -> None:
        self.query_one("#active-log", Static).update("\n".join(self._buffers[self._active_stream]))
        if follow:
            self.call_after_refresh(self._scroll_active_end)

    def _toggle_follow(self) -> None:
        if self._active_stream == "access":
            return
        enabled = not self._follow_streams[self._active_stream]
        self._follow_streams[self._active_stream] = enabled
        self._update_follow_button()
        if enabled:
            self._scroll_active_end()

    def _update_follow_button(self) -> None:
        if self._active_stream == "access":
            return
        button = self.query_one("#btn-stream-follow", Button)
        if self._follow_streams[self._active_stream]:
            button.label = _FOLLOW_LABEL_ON
            button.variant = "success"
        else:
            button.label = _FOLLOW_LABEL_OFF
            button.variant = "warning"

    def _scroll_active_end(self) -> None:
        self.query_one("#active-log-pane", VerticalScroll).scroll_end(
            animate=False,
            immediate=True,
            force=True,
            x_axis=False,
        )

    @property
    def active_lines(self) -> list[str]:
        if self._active_stream == "access":
            return []
        return list(self._buffers[self._active_stream])


class _CopyCommandPanel(Container):
    """Copyable command panel with a large click target."""

    def __init__(
        self,
        title: str,
        command: str,
        button_id: str,
        tooltip: str,
        *,
        panel_id: str,
        command_id: str,
    ) -> None:
        super().__init__(id=panel_id, classes="copy-panel")
        self._title = title
        self._command = command
        self._button_id = button_id
        self._tooltip = tooltip
        self._command_id = command_id
        self.tooltip = tooltip

    def compose(self) -> ComposeResult:
        with Horizontal(classes="copy-panel-header"):
            yield Label(self._title, classes="copy-panel-title")
            yield _copy_button(self._button_id, self._tooltip)
        yield Static(self._command, id=self._command_id, classes="proxy-cmd")

    def set_command(self, command: str) -> None:
        self._command = command
        try:
            self.query_one(f"#{self._command_id}", Static).update(command)
        except Exception:
            pass

    def on_click(self) -> None:
        self._copy_command()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == self._button_id:
            self._copy_command()
            event.stop()

    def _copy_command(self) -> None:
        self.app.copy_to_clipboard(self._command)
        button = self.query_one(f"#{self._button_id}", Button)
        button.label = _COPIED_ICON
        self.app.notify("Copied to clipboard")
        self.set_timer(1.0, lambda: self._restore_copy_button(button))

    def _restore_copy_button(self, button: Button) -> None:
        if str(button.label) == _COPIED_ICON:
            button.label = _COPY_ICON


class _SshCommandBar(Horizontal):
    """Copyable SSH command strip shown once the DSX Air worker is reachable."""

    def __init__(self, **kwargs: object) -> None:
        super().__init__(**kwargs)
        self._command = ""
        self.tooltip = "Copy SSH command"

    def compose(self) -> ComposeResult:
        yield Label("SSH", classes="ssh-badge")
        yield Static("", id="ssh-cmd", classes="ssh-cmd")
        yield _copy_button(
            "copy-ssh",
            "Copy SSH command",
            classes="copy-icon-btn ssh-copy-btn",
        )

    def set_command(self, command: str) -> None:
        self._command = command
        self.query_one("#ssh-cmd", Static).update(command)
        self.display = True

    def on_click(self, event: events.Click) -> None:
        self._copy_command()
        event.stop()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "copy-ssh":
            self._copy_command()
            event.stop()

    def _copy_command(self) -> None:
        if not self._command:
            return
        self.app.copy_to_clipboard(self._command)
        button = self.query_one("#copy-ssh", Button)
        button.label = _COPIED_ICON
        self.app.notify("Copied to clipboard")
        self.set_timer(1.0, lambda: self._restore_copy_button(button))

    def _restore_copy_button(self, button: Button) -> None:
        if str(button.label) == _COPIED_ICON:
            button.label = _COPY_ICON


class _AirLinkBar(Horizontal):
    """Copyable DSX Air simulation URL shown once the simulation exists."""

    def __init__(self, **kwargs: object) -> None:
        super().__init__(**kwargs)
        self._url = ""
        self.tooltip = "Copy DSX Air simulation link"

    def compose(self) -> ComposeResult:
        yield Label("DSX Air", classes="ssh-badge")
        yield Static("", id="air-link", classes="ssh-cmd")
        yield _copy_button(
            "copy-air-link",
            "Copy DSX Air simulation link",
            classes="copy-icon-btn ssh-copy-btn",
        )

    def set_url(self, url: str) -> None:
        self._url = url
        self.query_one("#air-link", Static).update(Text(url, style=f"link {url}"))
        self.display = True

    def on_click(self, event: events.Click) -> None:
        self._copy_url()
        event.stop()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "copy-air-link":
            self._copy_url()
            event.stop()

    def _copy_url(self) -> None:
        if not self._url:
            return
        self.app.copy_to_clipboard(self._url)
        button = self.query_one("#copy-air-link", Button)
        button.label = _COPIED_ICON
        self.app.notify("Copied to clipboard")
        self.set_timer(1.0, lambda: self._restore_copy_button(button))

    def _restore_copy_button(self, button: Button) -> None:
        if str(button.label) == _COPIED_ICON:
            button.label = _COPY_ICON


# ── Proxy access widget ───────────────────────────────────────────────────────


class _ProxyAccessWidget(Container):
    """Shows per-platform SOCKS proxy commands and a Launch Browser button."""

    def __init__(
        self,
        proxy: ProxyInfo,
        ssh_command: str,
        *,
        provider_ready: bool,
        provider_name: str,
        **kwargs: object,
    ) -> None:
        super().__init__(**kwargs)
        self._proxy = proxy
        self._ssh_command = ssh_command
        self._provider_ready = provider_ready
        self._provider_name = provider_name
        self._tunnel_proc: subprocess.Popen[bytes] | None = None

    @property
    def proxy(self) -> ProxyInfo:
        return self._proxy

    @property
    def ssh_command(self) -> str:
        return self._ssh_command

    @property
    def provider_ready(self) -> bool:
        return self._provider_ready

    @property
    def provider_name(self) -> str:
        return self._provider_name

    def compose(self) -> ComposeResult:
        p = self._proxy

        yield Label("Access", classes="subsection-label")
        yield Label(
            self._access_hint(),
            id="proxy-hint",
            classes="field-hint",
        )
        with Horizontal(id="proxy-controls") as controls:
            controls.display = self._provider_ready
            yield Label("SOCKS Port", id="socks-port-label")
            yield Input(
                value=str(p.socks_port),
                placeholder=str(SOCKS_PORT),
                id="socks-port",
            )
            launch_button = Button("Launch Browser", id="btn-launch-browser", variant="primary")
            launch_button.display = self._provider_ready
            yield launch_button

        yield Label(self._manual_label(), id="manual-commands-label", classes="subsection-label")
        yield _CopyCommandPanel(
            "OOB SSH credentials",
            _ssh_credentials(p.password),
            "copy-ssh-creds",
            "Copy OOB SSH credentials",
            panel_id="panel-ssh-creds",
            command_id="cmd-ssh-creds",
        )
        yield _CopyCommandPanel(
            "Direct SSH",
            self._ssh_command,
            "copy-ssh-direct",
            "Copy direct SSH command",
            panel_id="panel-ssh-direct",
            command_id="cmd-ssh-direct",
        )

        tunnel_label = Label(
            "SSH tunnel commands",
            id="tunnel-commands-label",
            classes="subsection-label",
        )
        tunnel_label.display = self._provider_ready
        yield tunnel_label
        ssh_unix = _CopyCommandPanel(
            "Linux / macOS - SOCKS tunnel",
            p.ssh_cmd_unix(),
            "copy-ssh-unix",
            "Copy Linux/macOS SOCKS tunnel",
            panel_id="panel-ssh-unix",
            command_id="cmd-ssh-unix",
        )
        ssh_unix.display = self._provider_ready
        yield ssh_unix
        ssh_win = _CopyCommandPanel(
            "Windows OpenSSH - SOCKS tunnel",
            p.ssh_cmd_windows(),
            "copy-ssh-win",
            "Copy Windows SOCKS tunnel",
            panel_id="panel-ssh-win",
            command_id="cmd-ssh-win",
        )
        ssh_win.display = self._provider_ready
        yield ssh_win
        browser_unix = _CopyCommandPanel(
            "Linux / macOS - browser",
            p.browser_cmd_unix(),
            "copy-browser-unix",
            "Copy Linux/macOS browser command",
            panel_id="panel-browser-unix",
            command_id="cmd-browser-unix",
        )
        browser_unix.display = self._provider_ready
        yield browser_unix
        browser_win = _CopyCommandPanel(
            "Windows - browser",
            p.browser_cmd_windows(),
            "copy-browser-win",
            "Copy Windows browser command",
            panel_id="panel-browser-win",
            command_id="cmd-browser-win",
        )
        browser_win.display = self._provider_ready
        yield browser_win

        status = Static("", id="proxy-status")
        status.display = self._provider_ready
        yield status

    def set_access(
        self,
        proxy: ProxyInfo,
        ssh_command: str,
        *,
        provider_ready: bool,
        provider_name: str,
    ) -> None:
        self._proxy = proxy
        self._ssh_command = ssh_command
        self._provider_ready = provider_ready
        self._provider_name = provider_name
        self._update_command_panels()
        self._sync_ready_state()

    def _update_command_panels(self) -> None:
        p = self._proxy
        commands = {
            "panel-ssh-creds": _ssh_credentials(p.password),
            "panel-ssh-direct": self._ssh_command,
            "panel-ssh-unix": p.ssh_cmd_unix(),
            "panel-ssh-win": p.ssh_cmd_windows(),
            "panel-browser-unix": p.browser_cmd_unix(),
            "panel-browser-win": p.browser_cmd_windows(),
        }
        for panel_id, command in commands.items():
            try:
                self.query_one(f"#{panel_id}", _CopyCommandPanel).set_command(command)
            except Exception:
                pass

    def _sync_ready_state(self) -> None:
        for selector in (
            "#proxy-controls",
            "#btn-launch-browser",
            "#tunnel-commands-label",
            "#panel-ssh-unix",
            "#panel-ssh-win",
            "#panel-browser-unix",
            "#panel-browser-win",
            "#proxy-status",
        ):
            try:
                self.query_one(selector).display = self._provider_ready
            except Exception:
                pass
        try:
            self.query_one("#proxy-hint", Label).update(self._access_hint())
            self.query_one("#manual-commands-label", Label).update(self._manual_label())
        except Exception:
            pass

    def _access_hint(self) -> str:
        if self._provider_ready:
            return (
                f"{self._provider_name} is ready. "
                "Launch a proxied browser or copy the manual commands."
            )
        return (
            "SSH is ready. Use direct SSH to troubleshoot the OOB management server "
            f"while {self._provider_name} starts."
        )

    def _manual_label(self) -> str:
        return "Manual commands" if self._provider_ready else "SSH details"

    def on_button_pressed(self, event: Button.Pressed) -> None:
        btn = event.button.id or ""
        if btn == "btn-launch-browser":
            self._launch_browser()
            event.stop()

    def on_input_changed(self, event: Input.Changed) -> None:
        if event.input.id != "socks-port":
            return
        value = event.value.strip()
        if not value:
            event.stop()
            return
        try:
            socks_port = int(value)
        except ValueError:
            self._set_proxy_status("[yellow]Enter a numeric SOCKS port.[/yellow]")
            event.stop()
            return
        if not 1 <= socks_port <= 65535:
            self._set_proxy_status("[yellow]Enter a SOCKS port between 1 and 65535.[/yellow]")
            event.stop()
            return

        self._proxy.socks_port = socks_port
        self._update_command_panels()
        self.post_message(_SocksPortChanged(socks_port))
        event.stop()

    def _set_proxy_status(self, markup: str) -> None:
        try:
            self.query_one("#proxy-status", Static).update(markup)
        except Exception:
            pass

    @work(thread=True)
    def _launch_browser(self) -> None:
        self.app.call_from_thread(
            self.query_one("#proxy-status", Static).update,
            "[yellow]Starting SOCKS tunnel...[/yellow]",
        )
        proc = self._proxy.start_tunnel()
        if proc is None:
            self.app.call_from_thread(
                self.query_one("#proxy-status", Static).update,
                "[red]Could not start tunnel — run the SSH command manually.[/red]",
            )
            return
        self._tunnel_proc = proc
        ok = self._proxy.launch_browser()
        if ok:
            self.app.call_from_thread(
                self.query_one("#proxy-status", Static).update,
                f"[green]Tunnel running (PID {proc.pid}). Browser launched.[/green]",
            )
        else:
            self.app.call_from_thread(
                self.query_one("#proxy-status", Static).update,
                f"[yellow]Tunnel running (PID {proc.pid}). "
                "No browser found — use the commands above.[/yellow]",
            )

    def on_unmount(self) -> None:
        if self._tunnel_proc and self._tunnel_proc.poll() is None:
            self._tunnel_proc.terminate()


# ── Pod status widget ─────────────────────────────────────────────────────────


class _PodStatusWidget(Vertical):
    """Polls lightweight deployment health and provisioning status over SSH."""

    def __init__(self, provider_status: AirProviderStatus, **kwargs: object) -> None:
        super().__init__(**kwargs)
        self._provider_status = provider_status
        self._host = ""
        self._port = 0
        self._manager: object = None  # AirSimulationManager, set at start_polling
        self._stop = threading.Event()
        self._status_worker_running = False
        self._last_pod_summary = ""
        self._last_prov: tuple[int, int, tuple[str, ...]] | None = None
        self._notified_provider_ready = False

    def compose(self) -> ComposeResult:
        yield Label("Progress", classes="section-title")
        yield Label("─" * 30, classes="section-divider")
        yield Static("Switches Provisioned: —", id="prov-count")
        yield Static("", id="prov-detail")
        yield Label("─" * 30, classes="section-divider")
        yield Static("Pods: waiting for cluster", id="pod-summary")
        yield Static("", id="pod-detail")

    def start_polling(self, host: str, port: int, manager: object) -> None:
        self._host = host
        self._port = port
        self._manager = manager
        self._stop.clear()
        self._notified_provider_ready = False
        self.query_one("#prov-count", Static).update(self._provider_status.provisioning_wait_text)
        if not self._status_worker_running:
            self._status_worker_running = True
            self._run_status_polling()

    def stop_polling(self) -> None:
        self._stop.set()

    @work(thread=True, group="air_sim_status", exit_on_error=False)
    def _run_status_polling(self) -> None:
        worker = get_current_worker()
        try:
            next_pod_poll = 0.0
            next_provisioning_poll = 0.0
            while not worker.is_cancelled and not self._stop.is_set():
                if self._manager is None:
                    self._stop.wait(_WORKER_IDLE_INTERVAL)
                    continue

                now = time.monotonic()
                if now >= next_pod_poll:
                    self._refresh_pods()
                    next_pod_poll = time.monotonic() + _POD_POLL_INTERVAL
                if now >= next_provisioning_poll:
                    self._refresh_provisioning()
                    next_provisioning_poll = time.monotonic() + _PROVISIONING_POLL_INTERVAL

                self._stop.wait(_WORKER_IDLE_INTERVAL)
        finally:
            self._status_worker_running = False

    def _refresh_pods(self) -> None:
        if not isinstance(self._manager, AirSimulationManager):
            raise TypeError("expected AirSimulationManager for self._manager")
        pods = self._manager.get_pod_status(self._host, self._port)
        self.app.call_from_thread(self._update_table, pods)

    def _refresh_provisioning(self) -> None:
        if not isinstance(self._manager, AirSimulationManager):
            raise TypeError("expected AirSimulationManager for self._manager")
        prov, total, remaining = self._manager.get_provisioning_status(self._host, self._port)
        self.app.call_from_thread(self._update_prov, prov, total, remaining)

    def _update_prov(self, prov: int, total: int, remaining: list[str]) -> None:
        next_state = (prov, total, tuple(remaining))
        if next_state == self._last_prov:
            return
        self._last_prov = next_state
        try:
            self.query_one("#prov-count", Static).update(
                f"Switches Provisioned: {prov}/{total}"
                if total
                else self._provider_status.provisioning_wait_text
            )
            detail = ""
            if remaining and total and prov < total:
                detail = "Pending: " + ", ".join(remaining)
            self.query_one("#prov-detail", Static).update(detail)
        except Exception:
            pass

    def _update_table(self, pods: list[dict[str, str]]) -> None:
        total = len(pods)
        if not total:
            summary = "Pods: waiting for cluster"
            detail = ""
        else:
            provider = next(
                (pod for pod in pods if self._provider_status.is_web_pod(pod)),
                None,
            )
            provider_name = self._provider_status.display_name
            if provider is None:
                summary = f"{provider_name}: waiting for pod"
            elif _is_ready_pod(provider):
                summary = f"{provider_name}: ready ({_pod_state_text(provider)})"
                if not self._notified_provider_ready and self._host and self._port:
                    self._notified_provider_ready = True
                    self.post_message(_ProviderReady(self._host, self._port))
            else:
                summary = f"{provider_name}: {_pod_state_text(provider)}"

            remaining = [pod for pod in pods if pod is not provider]
            remaining_not_ready = [pod for pod in remaining if not _is_ready_pod(pod)]
            dependent_not_ready = [
                pod
                for pod in remaining_not_ready
                if pod.get("name", "").startswith(self._provider_status.dependent_pod_prefixes)
            ]
            other_not_ready = [pod for pod in remaining_not_ready if pod not in dependent_not_ready]
            ready_remaining = len(remaining) - len(remaining_not_ready)
            detail = f"Other pods ready: {ready_remaining}/{len(remaining)}"

            attention = [*dependent_not_ready, *other_not_ready]
            if attention:
                detail += " | Remaining: " + ", ".join(
                    _pod_attention_text(pod) for pod in attention[:4]
                )
                if len(attention) > 4:
                    detail += f", +{len(attention) - 4} more"

        next_summary = f"{summary}\n{detail}"
        if next_summary == self._last_pod_summary:
            return
        self._last_pod_summary = next_summary
        self.query_one("#pod-summary", Static).update(summary)
        self.query_one("#pod-detail", Static).update(detail)


# ── Launch screen ─────────────────────────────────────────────────────────────


class LaunchScreen(Container):
    """Launch panel: summary, launch button, step list, and log stream."""

    PROVIDER_STATUS = DEFAULT_PROVIDER_STATUS

    def __init__(
        self,
        config: SimConfig,
        provider_status: AirProviderStatus | None = None,
        **kwargs: object,
    ) -> None:
        super().__init__(**kwargs)
        self._config = config
        self._provider_status = provider_status or self.PROVIDER_STATUS
        self._bringup_running = False
        self._host = ""
        self._port = 0
        self._simulation_id = ""
        self._simulation_url = ""
        self._ssh_cmd_text = ""
        self._monitor_stop = threading.Event()
        self._deploy_log_path: Path | None = None
        self._pending_log_lines: SimpleQueue[tuple[str, str]] = SimpleQueue()
        self._log_stop = threading.Event()
        self._log_worker_lock = threading.Lock()
        self._log_worker_running = False
        self._log_thread: threading.Thread | None = None
        self._service_polling = False
        self._seen_service_events: set[str] = set()
        self._socks_port = SOCKS_PORT
        self._proxy_access_target: tuple[str, int, bool, int, str] | None = None

    def compose(self) -> ComposeResult:
        yield Label("Launch", classes="section-title")
        yield Label("─" * 40, classes="section-divider")

        with Horizontal(id="launch-controls"):
            yield Button("Launch Simulation", id="btn-launch", variant="success")
        yield Static("", id="launch-status")

        yield Label("─" * 40, classes="section-divider")

        yield _AirLinkBar(id="air-link-bar", classes="ssh-info-bar")

        with Vertical(id="launch-dashboard"):
            with Horizontal(id="dashboard-top"):
                with VerticalScroll(id="step-panel"):
                    yield _StepListWidget(id="step-list")
                yield _PodStatusWidget(self._provider_status, id="pod-status-panel")
            yield _StreamTabsWidget(id="stream-viewer")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "btn-launch" and not self._bringup_running:
            self._start_bringup()
            event.stop()

    def _set_status(self, markup: str) -> None:
        self.query_one("#launch-status", Static).update(markup)

    def _status_text(self, state: str) -> str:
        parts = [state]
        if self._deploy_log_path:
            parts.append(f"log -> {self._deploy_log_path}")
        return "\n".join(parts)

    def _air_frontend_url(self) -> str:
        return (
            DEFAULT_AIR_INTERNAL_FRONTEND_URL
            if self._config.use_internal
            else DEFAULT_AIR_FRONTEND_URL
        ).rstrip("/")

    def set_simulation_id(self, simulation_id: str) -> None:
        if not simulation_id or simulation_id == self._simulation_id:
            return
        self._simulation_id = simulation_id
        self._simulation_url = f"{self._air_frontend_url()}/simulations/{simulation_id}"
        self.query_one("#air-link-bar", _AirLinkBar).set_url(self._simulation_url)
        self._set_status(self._status_text("[yellow]Running...[/yellow]"))

    def validate_provider_config(self) -> str:
        """Return a provider-owned validation error, or an empty string when ready."""
        if self._config.run_mock_topology_job and not self._config.mock_topology_path:
            return "Mock topology path required for bundled Nautobot population."
        return ""

    def _start_bringup(self) -> None:
        if self._config.use_mock_context_for_fabric:
            if not self._config.mock_blueprint:
                self._set_status(
                    "[bold red][!] Mock blueprint required — set it on the Topology screen.[/bold red]"
                )
                return
            if not self._config.deployment_name:
                self._set_status(
                    "[bold red][!] Deployment name required — set it on the Topology screen.[/bold red]"
                )
                return
        elif not self._config.topology_path:
            self._set_status(
                "[bold red][!] No topology file — set one on the Topology screen.[/bold red]"
            )
            return
        provider_error = self.validate_provider_config()
        if provider_error:
            self._set_status(f"[bold red][!] {provider_error}[/bold red]")
            return
        if not self._config.ngc_api_key:
            self._set_status(
                "[bold red][!] NGC API key required — set it on the Options screen.[/bold red]"
            )
            return

        self._deploy_log_path = _create_deploy_log_path()
        self._bringup_running = True
        self._monitor_stop.clear()
        self.query_one("#btn-launch", Button).disabled = True
        self._set_status(self._status_text("[yellow]Running...[/yellow]"))
        self._run_orchestrator()

    @work(thread=True, group="air_sim_orchestrator", exit_on_error=False)
    def _run_orchestrator(self) -> None:
        log_path = self._deploy_log_path
        with open(log_path if log_path else os.devnull, "w") as lf:
            cb = _TuiCallback(self, log_file=lf)
            orchestrator = self.create_orchestrator(cb)
            orchestrator.run()

    def create_orchestrator(self, callback: OrchestratorCallback) -> SimOrchestrator:
        """Construct the simulation orchestrator used by the launch worker."""
        return SimOrchestrator(self._config, callback)

    def create_simulation_manager(self) -> AirSimulationManager:
        """Construct the AIR and remote-host client used by dashboard workers."""
        return AirSimulationManager(
            ngc_api_key=self._config.ngc_api_key,
            use_internal=self._config.use_internal,
            org_id=self._config.org_id,
            ssh_password=self._config.oob_ssh_password,
        )

    @work(thread=True, group="air_sim_service_logs", exit_on_error=False)
    def _run_monitoring(self, host: str, port: int) -> None:
        worker = get_current_worker()
        try:
            manager = self.create_simulation_manager()
            while not worker.is_cancelled and not self._monitor_stop.is_set():
                snapshots = manager.get_service_log_snapshots(host, port)
                entries: list[tuple[str, str]] = []
                for stream, lines in snapshots.items():
                    for line in lines:
                        if stream == "dhcp":
                            clean = _clean_dhcp_line(line)
                            interesting = _is_interesting_dhcp_line(clean)
                        else:
                            clean = _clean_ztp_line(line)
                            interesting = _is_interesting_ztp_line(clean)
                        key = f"{stream}:{clean}"
                        if not interesting or key in self._seen_service_events:
                            continue
                        self._seen_service_events.add(key)
                        entries.append((clean, stream))
                for line, stream in entries[-_MAX_SERVICE_EVENTS_PER_POLL:]:
                    self.enqueue_log_line(line, stream)
                self._monitor_stop.wait(_SERVICE_POLL_INTERVAL)
        finally:
            self._service_polling = False

    def _start_monitoring(self, host: str, port: int) -> None:
        if self._service_polling:
            return
        self._service_polling = True
        self._run_monitoring(host, port)

    def on_worker_state_changed(self, event: Worker.StateChanged) -> None:
        if event.worker.name == "_run_orchestrator" and event.state == WorkerState.ERROR:
            self._bringup_running = False
            self.query_one("#btn-launch", Button).disabled = False
            self._set_status(
                self._status_text(
                    "[bold red][!] Worker crashed - check the Textual log for details.[/bold red]"
                )
            )

    def on__step_updated(self, event: _StepUpdated) -> None:
        self.query_one("#step-list", _StepListWidget).update_step(event.step_id, event.status)

    def on__log_line(self, event: _LogLine) -> None:
        self.enqueue_log_line(event.line, event.stream)

    def enqueue_log_line(self, line: str, stream: str = "deploy") -> None:
        """Queue a log line from any thread for the log worker to batch."""
        self._pending_log_lines.put((line, stream))
        self._start_log_worker()

    def on__log_batch(self, event: _LogBatch) -> None:
        try:
            viewer = self.query_one("#stream-viewer", _StreamTabsWidget)
        except Exception:
            event.stop()
            return
        viewer.append_lines(event.entries)
        event.stop()

    def _start_log_worker(self) -> None:
        with self._log_worker_lock:
            if self._log_worker_running:
                return
            self._log_stop.clear()
            self._log_worker_running = True
            self._log_thread = threading.Thread(
                target=self._run_log_worker,
                name="nvcm-air-log-flush",
                daemon=True,
            )
            self._log_thread.start()

    def _run_log_worker(self) -> None:
        try:
            while not self._log_stop.is_set():
                batch = self._collect_log_batch()
                if batch:
                    self.post_message(_LogBatch(batch))
                else:
                    break
        finally:
            should_restart = False
            with self._log_worker_lock:
                self._log_worker_running = False
                should_restart = not self._log_stop.is_set() and not self._pending_log_lines.empty()
            if should_restart:
                self._start_log_worker()

    def _collect_log_batch(self) -> list[tuple[str, str]]:
        batch: list[tuple[str, str]] = []
        try:
            batch.append(self._pending_log_lines.get(timeout=_LOG_FLUSH_INTERVAL))
        except Empty:
            return batch

        self._log_stop.wait(_LOG_FLUSH_INTERVAL)
        while len(batch) < _MAX_LOG_DRAIN_PER_FLUSH:
            try:
                line, stream = self._pending_log_lines.get_nowait()
            except Empty:
                break
            batch.append((line, stream))
        return batch

    def on__ssh_ready(self, event: _SshReady) -> None:
        self._host = event.host
        self._port = event.port
        self._ssh_cmd_text = _ssh_command(
            event.host,
            event.port,
            self._config.oob_ssh_password,
        )
        self._show_proxy_panel(event.host, event.port)
        manager = self.create_simulation_manager()
        self.query_one("#pod-status-panel", _PodStatusWidget).start_polling(
            event.host, event.port, manager
        )

    def on__deploy_started(self, event: _DeployStarted) -> None:
        self._host = event.host
        self._port = event.port

    def on__provider_ready(self, event: _ProviderReady) -> None:
        self._host = event.host
        self._port = event.port
        self._show_proxy_panel(event.host, event.port, provider_ready=True)

    def on__bringup_complete(self, event: _BringupComplete) -> None:
        self._bringup_running = False
        self.query_one("#btn-launch", Button).disabled = False
        if event.success:
            self._host = event.host
            self._port = event.port
            self.enqueue_log_line(
                "Bringup complete - monitoring DHCP and ZTP events.",
                "deploy",
            )
            self._set_status(self._status_text("[bold green][*] Bringup complete![/bold green]"))
            self.app.notify("Simulation bringup complete!", severity="information")
            if event.host:
                self._show_proxy_panel(event.host, event.port, provider_ready=True)
                self._start_monitoring(event.host, event.port)
        else:
            self.enqueue_log_line(
                "Bringup failed - check the deploy log for details.",
                "deploy",
            )
            self._set_status(
                self._status_text("[bold red][!] Bringup failed - check the deploy log[/bold red]")
            )
            self.app.notify("Bringup failed. See log for details.", severity="error")

    def on__simulation_created(self, event: _SimulationCreated) -> None:
        self.set_simulation_id(event.simulation_id)

    def on__socks_port_changed(self, event: _SocksPortChanged) -> None:
        self._socks_port = event.socks_port
        event.stop()

    def _show_proxy_panel(self, host: str, port: int, *, provider_ready: bool = False) -> None:
        access_target = (
            host,
            port,
            provider_ready,
            self._socks_port,
            self._config.oob_ssh_password,
        )
        if self._proxy_access_target == access_target:
            return
        self._ssh_cmd_text = self._ssh_cmd_text or _ssh_command(
            host,
            port,
            self._config.oob_ssh_password,
        )
        proxy = ProxyInfo(
            host=host,
            port=port,
            password=self._config.oob_ssh_password,
            socks_port=self._socks_port,
            target_url=self._provider_status.access_url,
        )
        widget = _ProxyAccessWidget(
            proxy,
            self._ssh_cmd_text,
            provider_ready=provider_ready,
            provider_name=self._provider_status.display_name,
            id="proxy-access",
        )
        self.query_one("#stream-viewer", _StreamTabsWidget).set_access_widget(widget)
        self._proxy_access_target = access_target

    def on_unmount(self) -> None:
        self._log_stop.set()
        self._monitor_stop.set()
        try:
            self.query_one("#pod-status-panel", _PodStatusWidget).stop_polling()
        except Exception:
            pass

    def get_status(self, config: SimConfig) -> str:
        if self._bringup_running:
            return "[>]"
        if self._host:
            return "[*]"
        return "[ ]"
