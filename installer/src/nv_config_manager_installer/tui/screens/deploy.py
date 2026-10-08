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
"""Deploy dashboard screen with step progress, pod status, and log viewer."""

from __future__ import annotations

import platform
import shutil
import subprocess
import threading
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from kubernetes import client as k8s_client
from kubernetes import config as k8s_config
from kubernetes.config.config_exception import ConfigException
from textual import work
from textual.app import ComposeResult
from textual.containers import Container, Horizontal, Vertical
from textual.message import Message
from textual.widgets import (
    Button,
    DataTable,
    Input,
    Label,
    Log,
    Select,
    Static,
)

from nv_config_manager_installer.deployer import (
    DeployCallback,
    Deployer,
    DeployOptions,
    DeployStep,
    StepStatus,
)
from nv_config_manager_installer.k8s import kubectl_current_context
from nv_config_manager_installer.schema import (
    GatewayType,
    ImageSource,
    NVConfigManagerInstallConfig,
    SecretsMethod,
)
from nv_config_manager_installer.tui.widgets import LabeledSwitch

_W_POD_LOG_SEL = "#pod-log-selectors"
_W_LOG_OUTPUT = "#log-output"
_W_POD_SELECT = "#pod-select"
_W_CONTAINER_SELECT = "#container-select"
_W_DEPLOY_OPTIONS = "#deploy-options"


def _is_real_selection(value: object) -> bool:
    """Return True only when a Select widget holds a user-chosen value."""
    return isinstance(value, str)


_STATUS_ICONS = {
    StepStatus.PENDING: "[ ]",
    StepStatus.RUNNING: "[>]",
    StepStatus.SUCCESS: "[*]",
    StepStatus.FAILED: "[!]",
    StepStatus.SKIPPED: "[-]",
}


class DeployStepUpdated(Message):
    """Posted by the deployer callback when a step changes status."""

    def __init__(self, step: DeployStep) -> None:
        super().__init__()
        self.step = step


class DeployLogMessage(Message):
    """Posted by the deployer callback for log output."""

    def __init__(self, message: str) -> None:
        super().__init__()
        self.log_message = message


class DeployCompleted(Message):
    """Posted when the deployment finishes."""

    def __init__(self, *, success: bool, endpoints: list[str]) -> None:
        super().__init__()
        self.success = success
        self.endpoints = endpoints


class _TuiCallback(DeployCallback):
    """Bridges deployer events into Textual messages posted to the deploy screen."""

    def __init__(self, screen: DeployScreen) -> None:
        self._screen = screen

    def on_step_update(self, step: DeployStep) -> None:
        self._screen.post_message(DeployStepUpdated(step))

    def on_log(self, message: str) -> None:
        self._screen.post_message(DeployLogMessage(message))

    def on_complete(self, success: bool, endpoints: list[str]) -> None:
        self._screen.post_message(DeployCompleted(success=success, endpoints=endpoints))


class StepListWidget(Vertical):
    """Left panel: list of deployment steps with status indicators."""

    def __init__(self, steps: list[DeployStep], **kwargs: object) -> None:
        super().__init__(**kwargs)
        self._steps = steps
        self._labels: dict[str, Static] = {}

    def compose(self) -> ComposeResult:
        yield Label("Deployment Steps", classes="section-title")
        yield Label("─" * 26, classes="section-divider")
        for step in self._steps:
            label = Static(f"{_STATUS_ICONS[step.status]}  {step.label}", id=f"step-{step.id}")
            self._labels[step.id] = label
            yield label

    def refresh_steps(self, steps: list[DeployStep]) -> None:
        """Update the step list to reflect a new set of deployer steps.

        Reuses existing Static widgets where IDs match and adds/removes
        as needed, avoiding async remove_children + mount_all which causes
        DuplicateIds errors in Textual.
        """
        self._steps = steps
        old_ids = set(self._labels.keys())
        new_ids = {s.id for s in steps}

        for sid in old_ids - new_ids:
            widget = self._labels.pop(sid)
            widget.remove()

        for step in steps:
            text = f"{_STATUS_ICONS[step.status]}  {step.label}"
            if step.id in self._labels:
                self._labels[step.id].update(text)
            else:
                label = Static(text, id=f"step-{step.id}")
                self._labels[step.id] = label
                self.mount(label)

    def update_step(self, step: DeployStep) -> None:
        label = self._labels.get(step.id)
        if label:
            label.update(f"{_STATUS_ICONS[step.status]}  {step.label}")


class PodStatusWidget(Vertical):
    """Right panel: live pod status table using the kubernetes Python client."""

    def __init__(self, namespace: str, **kwargs: object) -> None:
        super().__init__(**kwargs)
        self._namespace = namespace
        self._k8s_v1: k8s_client.CoreV1Api | None = None

    def compose(self) -> ComposeResult:
        yield Label("Pod Status", classes="section-title")
        yield Label("─" * 30, classes="section-divider")
        yield DataTable(id="pod-table")

    def on_mount(self) -> None:
        table = self.query_one("#pod-table", DataTable)
        table.add_columns("NAME", "READY", "STATUS", "RESTARTS", "AGE")
        try:
            # Pin to the same context kubectl reports — without this, the Python
            # kubernetes client's "last-wins" KUBECONFIG merge can bind this
            # widget to a totally different cluster than the deploy is running
            # against, leaving Pod Status silently empty.
            k8s_config.load_kube_config(context=kubectl_current_context())
            self._k8s_v1 = k8s_client.CoreV1Api()
        except ConfigException:
            self._k8s_v1 = None
        self.set_interval(3.0, self._refresh_pods)

    @staticmethod
    def _format_age(ts: object) -> str:
        """Format a creation timestamp as a kubectl-style relative age."""
        if ts is None:
            return ""
        now = datetime.now(UTC)
        dt = ts if isinstance(ts, datetime) else datetime.fromisoformat(str(ts))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=UTC)
        delta = int((now - dt).total_seconds())
        if delta < 0:
            return "0s"
        if delta < 60:
            return f"{delta}s"
        if delta < 3600:
            return f"{delta // 60}m"
        if delta < 86400:
            h, m = divmod(delta // 60, 60)
            return f"{h}h{m}m" if m else f"{h}h"
        d, h = divmod(delta // 3600, 24)
        return f"{d}d{h}h" if h else f"{d}d"

    @staticmethod
    def _pod_row(pod: object) -> tuple[str, str, str, str, str]:
        """Extract a table row from a kubernetes pod object."""
        name = pod.metadata.name or ""  # type: ignore[union-attr]
        if len(name) > 35:
            name = name[:32] + "..."
        phase = pod.status.phase if pod.status else "Unknown"  # type: ignore[union-attr]
        total = ready_count = restarts = 0
        for cs in pod.status.container_statuses or []:  # type: ignore[union-attr]
            total += 1
            if cs.ready:
                ready_count += 1
            restarts += cs.restart_count or 0
        for cs in pod.status.init_container_statuses or []:  # type: ignore[union-attr]
            restarts += cs.restart_count or 0
        ts = pod.metadata.creation_timestamp  # type: ignore[union-attr]
        return name, f"{ready_count}/{total}", phase, str(restarts), PodStatusWidget._format_age(ts)

    def _refresh_pods(self) -> None:
        v1 = self._k8s_v1
        if v1 is None:
            return
        try:
            pods = v1.list_namespaced_pod(self._namespace)
            table = self.query_one("#pod-table", DataTable)
            table.clear()
            for pod in pods.items:
                table.add_row(*self._pod_row(pod))
        except Exception:
            pass


class LogViewerWidget(Vertical):
    """Bottom panel: deploy log + interactive pod log browser.

    Two modes selected by tab buttons:
    - **Deploy Log**: real-time output from the deployment pipeline.
    - **Pod Logs**: pick any pod and container (including init containers) and
      stream its logs live via the kubernetes Python client.
    """

    _TAIL_LINES = 200
    _NOISE_PATTERNS = ("/healthcheck", "/healthz", "/readyz", "/livez", "/metrics")

    def __init__(self, namespace: str, **kwargs: object) -> None:
        super().__init__(**kwargs)
        self._namespace = namespace
        self._k8s_v1: k8s_client.CoreV1Api | None = None
        self._active_stream: subprocess.Popen[str] | None = None
        self._stream_thread: threading.Thread | None = None
        self._stop_stream = threading.Event()
        self._deploy_log_buffer: list[str] = []
        self._pod_log_buffer: list[str] = []
        self._active_tab = "deploy"
        self._pod_cache: list[tuple[str, list[str], list[str]]] = []
        self._last_pod_names: list[str] = []
        self._refresh_timer: object | None = None
        self._refreshing = False
        self._filter_noise = True

    def compose(self) -> ComposeResult:
        yield Label("Log Viewer", classes="section-title")
        with Horizontal(id="log-tabs"):
            yield Button("Deploy Log", id="log-tab-deploy", variant="primary")
            yield Button("Pod Logs", id="log-tab-pods", variant="default")
            yield Button("Copy", id="log-copy", variant="default")

        with Horizontal(id="pod-log-selectors"):
            with Vertical(classes="compact-field"):
                yield Label("Pod", classes="field-label-compact")
                yield Select[str]([], id="pod-select", prompt="Select a pod...")
            with Vertical(classes="compact-field"):
                yield Label("Container", classes="field-label-compact")
                yield Select[str]([], id="container-select", prompt="Select container...")
            yield LabeledSwitch("Filter health/metrics", id="log-filter-noise", value=True)

        yield Log(id="log-output", auto_scroll=True)

    def on_mount(self) -> None:
        self.query_one(_W_POD_LOG_SEL).display = False
        try:
            # Same context-pinning rationale as PodStatus.on_mount — avoid
            # silently streaming logs from the wrong cluster.
            k8s_config.load_kube_config(context=kubectl_current_context())
            self._k8s_v1 = k8s_client.CoreV1Api()
        except ConfigException:
            self._k8s_v1 = None

    def _flush_buffer(self, buf: list[str]) -> None:
        """Replace the log widget contents with *buf* in a single write."""
        log = self.query_one(_W_LOG_OUTPUT, Log)
        log.clear()
        if buf:
            log.write("\n".join(buf) + "\n")

    def _switch_to_deploy_tab(self) -> None:
        self.query_one(_W_POD_LOG_SEL).display = False
        self._stop_active_stream()
        if self._refresh_timer is not None:
            self._refresh_timer.stop()  # type: ignore[union-attr]
            self._refresh_timer = None
        self._flush_buffer(self._deploy_log_buffer)

    def _switch_to_pods_tab(self) -> None:
        self.query_one(_W_POD_LOG_SEL).display = True
        self._flush_buffer(self._pod_log_buffer)
        self._refresh_pod_list()
        self._refresh_timer = self.set_interval(5.0, self._refresh_pod_list)

    def on_button_pressed(self, event: Button.Pressed) -> None:
        bid = event.button.id or ""
        if bid == "log-copy":
            self._copy_log()
            event.button.blur()
            return
        if not bid.startswith("log-tab-"):
            return

        for btn in self.query("#log-tabs Button"):
            if isinstance(btn, Button) and btn.id != "log-copy":
                btn.variant = "primary" if btn.id == bid else "default"

        tab = bid.removeprefix("log-tab-")
        self._active_tab = tab

        if tab == "deploy":
            self._switch_to_deploy_tab()
        elif tab == "pods":
            self._switch_to_pods_tab()

        event.button.blur()

    def on_labeled_switch_changed(self, event: LabeledSwitch.Changed) -> None:
        if event.labeled_switch.id == "log-filter-noise":
            self._filter_noise = event.value

    def on_select_changed(self, event: Select.Changed) -> None:
        if self._refreshing:
            return
        if event.select.id == "pod-select":
            self._on_pod_selected()
        elif event.select.id == "container-select":
            self._on_container_selected()

    def _refresh_pod_list(self) -> None:
        """Fetch the current pod list and update the pod selector only when it changes."""
        v1 = self._k8s_v1
        if v1 is None:
            return
        try:
            pods = v1.list_namespaced_pod(self._namespace)
        except Exception:
            return

        cache: list[tuple[str, list[str], list[str]]] = []
        for pod in pods.items:
            name = pod.metadata.name or ""
            init_containers = [c.name for c in (pod.spec.init_containers or [])]
            containers = [c.name for c in (pod.spec.containers or [])]
            cache.append((name, init_containers, containers))
        cache.sort(key=lambda t: t[0])

        new_names = [t[0] for t in cache]
        if new_names == self._last_pod_names:
            return
        self._last_pod_names = new_names
        self._pod_cache = cache

        pod_select = self.query_one(_W_POD_SELECT, Select)
        current = pod_select.value

        self._refreshing = True
        try:
            options = [(name, name) for name, _, _ in cache]
            pod_select.set_options(options)
            if _is_real_selection(current) and any(n == current for n, _, _ in cache):
                pod_select.value = current
            elif _is_real_selection(current):
                self._stop_active_stream()
                self.query_one(_W_CONTAINER_SELECT, Select).set_options([])
                msg = f"Pod '{current}' is no longer available."
                self._pod_log_buffer.append(msg)
                if self._active_tab == "pods":
                    self.query_one(_W_LOG_OUTPUT, Log).write_line(msg)
        finally:
            self._refreshing = False

    def _on_pod_selected(self) -> None:
        """When a pod is selected, populate the container dropdown."""
        pod_select = self.query_one(_W_POD_SELECT, Select)
        pod_name = pod_select.value
        if not _is_real_selection(pod_name):
            return

        for name, inits, containers in self._pod_cache:
            if name == pod_name:
                options: list[tuple[str, str]] = []
                for c in inits:
                    options.append((f"[init] {c}", f"init:{c}"))
                for c in containers:
                    options.append((c, c))

                container_select = self.query_one(_W_CONTAINER_SELECT, Select)
                container_select.set_options(options)
                if len(options) == 1:
                    container_select.value = options[0][1]
                    self._start_pod_log_stream(str(pod_name), options[0][1])
                return

    def _on_container_selected(self) -> None:
        """Start streaming logs for the selected pod + container."""
        pod_select = self.query_one(_W_POD_SELECT, Select)
        container_select = self.query_one(_W_CONTAINER_SELECT, Select)
        pod_name = pod_select.value
        container_val = container_select.value

        if not _is_real_selection(pod_name) or not _is_real_selection(container_val):
            return

        self._start_pod_log_stream(str(pod_name), str(container_val))

    def _pod_log_line(self, line: str) -> None:
        """Write a line to the pod log buffer and, if viewing pods, the Log widget."""
        self._pod_log_buffer.append(line)
        if self._active_tab == "pods":
            self.query_one(_W_LOG_OUTPUT, Log).write_line(line)

    @staticmethod
    def _format_stream_error(exc: Exception, pod_name: str, container_name: str) -> str:
        """Return a human-readable message for a pod log stream failure."""
        msg = str(exc)
        if "400" in msg or "Bad Request" in msg:
            return f"Container '{container_name}' is not available (may have completed)."
        if "404" in msg or "not found" in msg.lower():
            return f"Pod '{pod_name}' no longer exists."
        return f"Log stream ended: {msg.splitlines()[0]}"

    def _start_pod_log_stream(self, pod_name: str, container_value: str) -> None:
        """Stream logs from a specific pod and container using the k8s client."""
        self._stop_active_stream()
        self._pod_log_buffer.clear()
        log_output = self.query_one(_W_LOG_OUTPUT, Log)
        log_output.clear()

        is_init = container_value.startswith("init:")
        container_name = container_value.removeprefix("init:")

        v1 = self._k8s_v1
        if v1 is None:
            self._pod_log_line("Kubernetes client not available")
            return

        label = "init-container" if is_init else "container"
        self._pod_log_line(f"Streaming logs: {pod_name} / {label}: {container_name}")
        self._pod_log_line("─" * 60)

        self._stop_stream.clear()

        def _write(text: str) -> None:
            self._pod_log_buffer.append(text)
            if self._active_tab == "pods":
                self.app.call_from_thread(log_output.write_line, text)

        target = self._make_stream_reader(v1, pod_name, container_name, _write)
        self._stream_thread = threading.Thread(target=target, daemon=True)
        self._stream_thread.start()

    def _make_stream_reader(
        self, v1: Any, pod_name: str, container_name: str, write: Callable[[str], None]
    ) -> Callable[[], None]:
        """Build a log-streaming closure for a background thread."""

        def _reader() -> None:
            try:
                resp = v1.read_namespaced_pod_log(
                    name=pod_name,
                    namespace=self._namespace,
                    container=container_name,
                    follow=True,
                    tail_lines=self._TAIL_LINES,
                    _preload_content=False,
                )
                self._consume_log_stream(resp, container_name, write)
            except Exception as exc:
                if not self._stop_stream.is_set():
                    write(self._format_stream_error(exc, pod_name, container_name))

        return _reader

    def _consume_log_stream(
        self, resp: Any, container_name: str, write: Callable[[str], None]
    ) -> None:
        """Read lines from a k8s log stream, filtering noise."""
        for raw_line in resp:
            if self._stop_stream.is_set():
                break
            line = raw_line.decode("utf-8", errors="replace").rstrip()
            if self._filter_noise and any(p in line for p in self._NOISE_PATTERNS):
                continue
            write(line)
        resp.close()
        if not self._stop_stream.is_set():
            write("─" * 60)
            write(f"Stream ended — container '{container_name}' is no longer running.")

    def _stop_active_stream(self) -> None:
        self._stop_stream.set()
        if self._active_stream:
            try:
                self._active_stream.terminate()
            except Exception:
                pass
            self._active_stream = None

    def _copy_log(self) -> None:
        """Copy the current log view to system clipboard via pbcopy/xclip."""
        buf = self._deploy_log_buffer if self._active_tab == "deploy" else self._pod_log_buffer
        text = "\n".join(buf)

        copied = False
        if platform.system() == "Darwin" and shutil.which("pbcopy"):
            try:
                subprocess.run(["pbcopy"], input=text, text=True, check=True)
                copied = True
            except Exception:
                pass
        elif shutil.which("xclip"):
            try:
                subprocess.run(
                    ["xclip", "-selection", "clipboard"], input=text, text=True, check=True
                )
                copied = True
            except Exception:
                pass

        if copied:
            self.app.notify("Log copied to clipboard")
        else:
            self.app.notify("No clipboard tool found (pbcopy/xclip)", severity="warning")

    def clear_deploy_log(self) -> None:
        """Clear the deploy log buffer and the visible widget (used on retry)."""
        self._deploy_log_buffer.clear()
        if self._active_tab == "deploy":
            self.query_one(_W_LOG_OUTPUT, Log).clear()

    def append_deploy_log(self, message: str) -> None:
        """Append a line to the deploy log buffer, and to the widget if showing."""
        self._deploy_log_buffer.append(message)
        if self._active_tab == "deploy":
            self.query_one(_W_LOG_OUTPUT, Log).write_line(message)


class DeployScreen(Container):
    """Full deployment dashboard: steps + pod status + log viewer."""

    def __init__(self, config: NVConfigManagerInstallConfig, **kwargs: object) -> None:
        super().__init__(**kwargs)
        self._config = config
        self._deployer: Deployer | None = None
        self._deploy_running = False

    def compose(self) -> ComposeResult:
        yield Label("Deploy", classes="section-title")
        yield Label("─" * 40, classes="section-divider")

        with Container(id="deploy-options"):
            with Horizontal(classes="compact-field-row"):
                yield LabeledSwitch("Build images", id="opt-build-images", value=True)
                yield LabeledSwitch("Load to Kind", id="opt-load-kind", value=True)
                yield LabeledSwitch("Install Envoy Gateway", id="opt-envoy-gw")
                yield LabeledSwitch("Install cert-manager", id="opt-cert-mgr")
                yield LabeledSwitch("Install CNPG operator", id="opt-cnpg")

            with Horizontal(classes="compact-field-row"):
                with Vertical(classes="compact-field"):
                    yield Label("Kind cluster name", classes="field-label-compact")
                    yield Input(value="nv-config-manager", id="opt-kind-cluster")
                with Vertical(classes="compact-field"):
                    yield Label("Helm timeout", classes="field-label-compact")
                    yield Input(value="15m", id="opt-helm-timeout")
                with Vertical(classes="compact-field"):
                    yield Label("Vault token file (ESO)", classes="field-label-compact")
                    yield Input(placeholder="/path/to/token", id="opt-openbao-token-file")

            with Horizontal(classes="compact-field-row"):
                yield LabeledSwitch("Recreate existing secrets", id="opt-recreate-secrets")
                yield LabeledSwitch("Run integration tests", id="opt-run-tests")
                yield LabeledSwitch("Populate Vault secrets", id="opt-populate-vault", value=True)

        yield Button("Start Deployment", id="start-deploy", variant="success", classes="add-button")

        with Container(id="deploy-dashboard"):
            with Horizontal(id="deploy-top-panels"):
                yield StepListWidget(self._get_initial_steps(), id="step-list-panel")
                yield PodStatusWidget(self._config.cluster.namespace, id="pod-status-panel")
            yield LogViewerWidget(self._config.cluster.namespace, id="log-viewer-panel")

    def _get_initial_steps(self) -> list[DeployStep]:
        """Return the selected deployer's steps for the initial render."""
        return self.deployer_class().create_steps()

    def deployer_class(self) -> type[Deployer]:
        """Return the deployment engine used by this screen."""
        return Deployer

    def create_deployer(
        self,
        config: NVConfigManagerInstallConfig,
        options: DeployOptions,
        callback: DeployCallback,
    ) -> Deployer:
        """Construct the deployment engine; provider screens may override this factory."""
        return self.deployer_class()(config, options, callback)

    def on_mount(self) -> None:
        self._sync_image_defaults()
        self._sync_gateway_options()
        self._sync_test_toggle()
        self._sync_secret_options()

    def _sync_image_defaults(self) -> None:
        """Auto-toggle build/load switches based on config.images.source."""
        is_local = self._config.images.source == ImageSource.LOCAL
        self.query_one("#opt-build-images", LabeledSwitch).value = is_local
        self.query_one("#opt-load-kind", LabeledSwitch).value = is_local

    def _sync_gateway_options(self) -> None:
        """Disable Envoy-only installation controls when kgateway is selected."""
        envoy_switch = self.query_one("#opt-envoy-gw", LabeledSwitch)
        is_kgateway = self._config.infrastructure.gateway == GatewayType.KGATEWAY
        if is_kgateway:
            envoy_switch.value = False
        envoy_switch.disabled = is_kgateway
        envoy_switch.tooltip = (
            "Install kgateway and its Gateway API CRDs before deploying Config Manager"
            if is_kgateway
            else ""
        )

    def _sync_test_toggle(self) -> None:
        """Disable integration tests when SSO is enabled (OIDC requires browser auth)."""
        test_switch = self.query_one("#opt-run-tests", LabeledSwitch)
        if self._config.sso.enabled:
            test_switch.value = False
            test_switch.disabled = True
            test_switch.tooltip = "Integration tests are unavailable when SSO is enabled (OIDC requires browser authentication)"
        else:
            test_switch.disabled = False
            test_switch.tooltip = ""

    def _sync_secret_options(self) -> None:
        """Enable only the deploy controls supported by the secrets backend."""
        is_eso = self._config.secrets.method == SecretsMethod.ESO

        recreate_switch = self.query_one("#opt-recreate-secrets", LabeledSwitch)
        recreate_switch.disabled = is_eso
        recreate_switch.tooltip = (
            "Only applies to Kubernetes secrets. ESO-managed secrets are reconciled from Vault."
            if is_eso
            else ""
        )
        if is_eso:
            recreate_switch.value = False

        populate_switch = self.query_one("#opt-populate-vault", LabeledSwitch)
        was_disabled = populate_switch.disabled
        populate_switch.disabled = not is_eso
        populate_switch.tooltip = "" if is_eso else "Only applies when ESO is selected."
        if not is_eso:
            populate_switch.value = False
        elif was_disabled:
            populate_switch.value = True

        token_input = self.query_one("#opt-openbao-token-file", Input)
        token_input.disabled = not is_eso or not populate_switch.value
        if not is_eso:
            token_input.tooltip = "Only applies when ESO is selected."
        elif not populate_switch.value:
            token_input.tooltip = "Not needed when Vault population is disabled."
        else:
            token_input.tooltip = ""

    def on_labeled_switch_changed(self, event: LabeledSwitch.Changed) -> None:
        if event.labeled_switch.id == "opt-populate-vault":
            self._sync_secret_options()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "start-deploy" and not self._deploy_running:
            opts = self.query_one(_W_DEPLOY_OPTIONS)
            if not opts.display:
                opts.display = True
                event.button.label = "Start Deployment"
                event.button.variant = "success"
                return
            self._start_deploy()

    def _collect_deploy_options(self) -> DeployOptions:
        token_input = self.query_one("#opt-openbao-token-file", Input)
        token_file_value = "" if token_input.disabled else token_input.value.strip()
        vault_token_file = Path(token_file_value).expanduser() if token_file_value else None
        return DeployOptions(
            chart_dir="deploy/helm",
            build_images=self.query_one("#opt-build-images", LabeledSwitch).value,
            load_kind=self.query_one("#opt-load-kind", LabeledSwitch).value,
            kind_cluster=self.query_one("#opt-kind-cluster", Input).value,
            install_envoy_gateway=self.query_one("#opt-envoy-gw", LabeledSwitch).value,
            install_cert_manager=self.query_one("#opt-cert-mgr", LabeledSwitch).value,
            install_cnpg_operator=self.query_one("#opt-cnpg", LabeledSwitch).value,
            helm_timeout=self.query_one("#opt-helm-timeout", Input).value,
            recreate_secrets=self.query_one("#opt-recreate-secrets", LabeledSwitch).value,
            run_tests=self.query_one("#opt-run-tests", LabeledSwitch).value,
            vault_token_file=vault_token_file,
            populate_vault=self.query_one("#opt-populate-vault", LabeledSwitch).value,
        )

    def _sync_image_source_from_options(self, options: DeployOptions) -> None:
        """Use locally built or loaded images for the deployment."""
        if options.build_images or options.load_kind:
            self._config.images.source = ImageSource.LOCAL

    def _start_deploy(self) -> None:
        # Import here to avoid the app -> DeployScreen -> app circular dependency.
        from nv_config_manager_installer.tui.app import NVConfigManagerInstallerApp

        app = self.app
        if isinstance(app, NVConfigManagerInstallerApp):
            app.collect_config()
            self._config = app.config
            self._sync_gateway_options()
            self._sync_secret_options()

        self._deploy_running = True
        btn = self.query_one("#start-deploy", Button)
        btn.disabled = True
        btn.label = "Deploying..."
        self.query_one(_W_DEPLOY_OPTIONS).display = False

        log_viewer = self.query_one("#log-viewer-panel", LogViewerWidget)
        log_viewer.clear_deploy_log()

        options = self._collect_deploy_options()
        self._sync_image_source_from_options(options)
        callback = _TuiCallback(self)

        try:
            self._deployer = self.create_deployer(self._config, options, callback)
        except Exception as exc:
            self._deployer = None
            self._deploy_running = False
            btn.disabled = False
            btn.label = "Deploy"
            self.query_one(_W_DEPLOY_OPTIONS).display = True
            callback.on_log(f"Failed to initialize deployer: {exc}")
            self.app.notify(f"Deploy failed to start: {exc}", severity="error")
            return

        step_list = self.query_one("#step-list-panel", StepListWidget)
        step_list.refresh_steps(self._deployer.steps)

        self._run_deploy()

    @work(thread=True)
    def _run_deploy(self) -> None:
        if self._deployer:
            self._deployer.run()

    def on_deploy_step_updated(self, event: DeployStepUpdated) -> None:
        step_list = self.query_one("#step-list-panel", StepListWidget)
        step_list.update_step(event.step)

    def on_deploy_log_message(self, event: DeployLogMessage) -> None:
        log_viewer = self.query_one("#log-viewer-panel", LogViewerWidget)
        log_viewer.append_deploy_log(event.log_message)

    def on_deploy_completed(self, event: DeployCompleted) -> None:
        self._deploy_running = False
        btn = self.query_one("#start-deploy", Button)
        btn.disabled = False

        if event.success:
            btn.label = "Deployment Complete"
            btn.variant = "success"
            self.app.notify("Deployment completed successfully!")
        else:
            btn.label = "Retry Deployment"
            btn.variant = "error"
            self.app.notify("Deployment failed. Check logs for details.", severity="error")

    def sync_from_config(self, config: NVConfigManagerInstallConfig) -> None:
        """Refresh deploy controls after another TUI section updates the config."""
        self._config = config
        self._sync_image_defaults()
        self._sync_gateway_options()
        self._sync_test_toggle()
        self._sync_secret_options()

    def get_status(self, config: NVConfigManagerInstallConfig) -> str:
        if self._deploy_running:
            return "[>]"
        return "[ ]"
