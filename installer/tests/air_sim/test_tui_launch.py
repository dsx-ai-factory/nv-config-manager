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
"""Textual tests for the DSX Air simulation launch screen."""

from __future__ import annotations

from dataclasses import dataclass
from types import SimpleNamespace

import pytest
from textual.app import ComposeResult
from textual.widgets import Input, Static

import nv_config_manager_installer.air_sim.sim_manager as sim_manager_module
from nv_config_manager_installer.air_sim.orchestrator import (
    OrchestratorCallback,
    SimOrchestrator,
)
from nv_config_manager_installer.air_sim.sim_config import SimConfig
from nv_config_manager_installer.air_sim.sim_manager import AirSimulationManager
from nv_config_manager_installer.tui.air_sim.app import NVCMAirSimApp
from nv_config_manager_installer.tui.air_sim.screens.launch import (
    _MAX_DEPLOY_LOG_LINES,
    AirProviderStatus,
    LaunchScreen,
    _clean_dhcp_line,
    _create_deploy_log_path,
    _DeployStarted,
    _is_interesting_dhcp_line,
    _PodStatusWidget,
    _StreamTabsWidget,
    _TuiCallback,
)
from nv_config_manager_installer.tui.air_sim.screens.topology import (
    PopulationPanel,
    TopologyScreen,
)
from nv_config_manager_installer.tui.widgets import LabeledSwitch

PUBLIC_AIR_WORKER = "eb515e50.workers.ngc.air.nvidia.com"
TEST_OOB_SSH_PASSWORD = "testOobPassword123"


class ClipboardAirSimApp(NVCMAirSimApp):
    """Test app that records clipboard writes."""

    copied_text: str | None = None

    def copy_to_clipboard(self, text: str) -> None:
        self.copied_text = text


class CallbackRecorder:
    """Small callback target used to verify callback filtering behavior."""

    def __init__(self) -> None:
        self.entries: list[tuple[str, str]] = []
        self.messages: list[object] = []

    def post_message(self, message: object) -> None:
        self.messages.append(message)

    def enqueue_log_line(self, line: str, stream: str = "deploy") -> None:
        self.entries.append((line, stream))

    def on_step(self, step_id: str, status: object, message: str = "") -> None:
        pass

    def on_log(self, line: str) -> None:
        pass

    def on_ssh_ready(self, host: str, port: int) -> None:
        pass

    def on_deploy_started(self, host: str, port: int) -> None:
        pass

    def on_complete(self, success: bool, host: str = "", port: int = 0) -> None:
        pass


@pytest.mark.asyncio
async def test_direct_ssh_copy_button_lives_in_access_panel() -> None:
    app = ClipboardAirSimApp(
        config=SimConfig(
            ngc_api_key="nvapi-test",
            oob_ssh_password=TEST_OOB_SSH_PASSWORD,
        )
    )
    command = f"sshpass -p {TEST_OOB_SSH_PASSWORD} ssh -p 17117 nvcm@example.air"

    async with app.run_test(size=(180, 100)) as pilot:
        app.switch_section("launch")
        await pilot.pause(0.1)

        launch = app.query_one("#screen-launch", LaunchScreen)
        launch._ssh_cmd_text = command
        launch._show_proxy_panel(PUBLIC_AIR_WORKER, 17117)
        launch.query_one("#stream-viewer", _StreamTabsWidget).select_stream("access")
        await pilot.pause(0.1)

        assert not app.query("#ssh-info-bar")
        assert TEST_OOB_SSH_PASSWORD in str(app.query_one("#cmd-ssh-creds", Static).render())
        assert app.query("#panel-ssh-direct")
        assert app.query_one("#btn-launch-browser").display is False
        assert app.query_one("#panel-ssh-unix").display is False

        await pilot.click("#copy-ssh-direct")
        await pilot.pause(0.1)
        assert app.copied_text == command

        app.copied_text = None
        launch.query_one("#access-pane").scroll_to_widget(launch.query_one("#panel-ssh-direct"))
        await pilot.pause(0.1)
        await pilot.click("#cmd-ssh-direct")
        await pilot.pause(0.1)
        assert app.copied_text == command


@pytest.mark.asyncio
async def test_access_panel_copy_button_and_panel_body_copy_command() -> None:
    app = ClipboardAirSimApp(
        config=SimConfig(
            ngc_api_key="nvapi-test",
            oob_ssh_password=TEST_OOB_SSH_PASSWORD,
        )
    )

    async with app.run_test(size=(180, 100)) as pilot:
        app.switch_section("launch")
        await pilot.pause(0.1)

        launch = app.query_one("#screen-launch", LaunchScreen)
        launch._ssh_cmd_text = f"sshpass -p {TEST_OOB_SSH_PASSWORD} ssh -p 17117 nvcm@example.air"
        launch._show_proxy_panel(PUBLIC_AIR_WORKER, 17117, provider_ready=True)
        launch.query_one("#stream-viewer", _StreamTabsWidget).select_stream("access")
        await pilot.pause(0.1)

        launch.query_one("#access-pane").scroll_to_widget(launch.query_one("#panel-ssh-unix"))
        await pilot.pause(0.1)

        await pilot.click("#copy-ssh-unix")
        await pilot.pause(0.1)
        assert app.copied_text is not None
        assert f"sshpass -p {TEST_OOB_SSH_PASSWORD}" in app.copied_text
        assert PUBLIC_AIR_WORKER in app.copied_text

        app.copied_text = None
        launch.query_one("#access-pane").scroll_to_widget(launch.query_one("#panel-ssh-unix"))
        await pilot.pause(0.1)
        await pilot.click("#cmd-ssh-unix")
        await pilot.pause(0.1)
        assert app.copied_text is not None
        assert f"sshpass -p {TEST_OOB_SSH_PASSWORD}" in app.copied_text
        assert PUBLIC_AIR_WORKER in app.copied_text


@pytest.mark.asyncio
async def test_access_panel_upgrades_when_nautobot_is_ready() -> None:
    app = ClipboardAirSimApp(
        config=SimConfig(
            ngc_api_key="nvapi-test",
            oob_ssh_password=TEST_OOB_SSH_PASSWORD,
        )
    )

    async with app.run_test(size=(180, 100)) as pilot:
        app.switch_section("launch")
        await pilot.pause(0.1)

        launch = app.query_one("#screen-launch", LaunchScreen)
        launch._ssh_cmd_text = f"sshpass -p {TEST_OOB_SSH_PASSWORD} ssh -p 17117 nvcm@example.air"
        launch._show_proxy_panel(PUBLIC_AIR_WORKER, 17117)
        launch.query_one("#stream-viewer", _StreamTabsWidget).select_stream("access")
        await pilot.pause(0.1)

        assert app.query_one("#btn-launch-browser").display is False

        launch._show_proxy_panel(PUBLIC_AIR_WORKER, 17117, provider_ready=True)
        await pilot.pause(0.1)

        assert app.query_one("#btn-launch-browser").display is True
        assert app.query_one("#panel-ssh-unix").display is True


@pytest.mark.asyncio
async def test_access_panel_socks_port_updates_proxy_commands() -> None:
    app = ClipboardAirSimApp(
        config=SimConfig(
            ngc_api_key="nvapi-test",
            oob_ssh_password=TEST_OOB_SSH_PASSWORD,
        )
    )

    async with app.run_test(size=(180, 100)) as pilot:
        app.switch_section("launch")
        await pilot.pause(0.1)

        launch = app.query_one("#screen-launch", LaunchScreen)
        launch._ssh_cmd_text = f"sshpass -p {TEST_OOB_SSH_PASSWORD} ssh -p 17117 nvcm@example.air"
        launch._show_proxy_panel(PUBLIC_AIR_WORKER, 17117, provider_ready=True)
        launch.query_one("#stream-viewer", _StreamTabsWidget).select_stream("access")
        await pilot.pause(0.1)

        app.query_one("#socks-port", Input).value = "18080"
        await pilot.pause(0.1)

        ssh_command = str(app.query_one("#cmd-ssh-unix", Static).render())
        browser_command = str(app.query_one("#cmd-browser-win", Static).render())

        assert "-D 18080" in ssh_command
        assert "localhost:18080" in browser_command


@pytest.mark.asyncio
async def test_access_panel_preserves_custom_socks_port_after_refresh() -> None:
    app = ClipboardAirSimApp(
        config=SimConfig(
            ngc_api_key="nvapi-test",
            oob_ssh_password=TEST_OOB_SSH_PASSWORD,
        )
    )

    async with app.run_test(size=(180, 100)) as pilot:
        app.switch_section("launch")
        await pilot.pause(0.1)

        launch = app.query_one("#screen-launch", LaunchScreen)
        launch._ssh_cmd_text = f"sshpass -p {TEST_OOB_SSH_PASSWORD} ssh -p 17117 nvcm@example.air"
        launch._show_proxy_panel(PUBLIC_AIR_WORKER, 17117, provider_ready=True)
        launch.query_one("#stream-viewer", _StreamTabsWidget).select_stream("access")
        await pilot.pause(0.1)

        app.query_one("#socks-port", Input).value = "18080"
        await pilot.pause(0.1)

        launch._show_proxy_panel(PUBLIC_AIR_WORKER, 17117, provider_ready=True)
        await pilot.pause(0.1)

        assert app.query_one("#socks-port", Input).value == "18080"
        assert "-D 18080" in str(app.query_one("#cmd-ssh-unix", Static).render())


@pytest.mark.asyncio
async def test_stream_tabs_buffer_unfocused_progress_events() -> None:
    app = ClipboardAirSimApp(config=SimConfig(ngc_api_key="nvapi-test"))

    async with app.run_test(size=(180, 70)) as pilot:
        app.switch_section("launch")
        await pilot.pause(0.1)

        launch = app.query_one("#screen-launch", LaunchScreen)
        viewer = launch.query_one("#stream-viewer", _StreamTabsWidget)
        viewer.append_lines(
            [
                ("Simulation: demo-id", "deploy"),
                ("DHCP4_LEASE_ALLOC [hwtype=1 44:38:39:00:00:02], cid=[no info]", "dhcp"),
                ('10.120.1.10:12345 - "GET /v1/device/abc/boot-script HTTP/1.1" 200', "ztp"),
            ]
        )
        await pilot.pause(0.1)

        assert viewer.active_lines == ["Simulation: demo-id"]

        viewer.select_stream("dhcp")
        await pilot.pause(0.1)
        assert viewer.active_lines == [
            "DHCP4_LEASE_ALLOC [hwtype=1 44:38:39:00:00:02], cid=[no info]"
        ]

        viewer.select_stream("ztp")
        await pilot.pause(0.1)
        assert viewer.active_lines == [
            '10.120.1.10:12345 - "GET /v1/device/abc/boot-script HTTP/1.1" 200'
        ]


@pytest.mark.asyncio
async def test_stream_tabs_follow_and_end_controls() -> None:
    app = ClipboardAirSimApp(config=SimConfig(ngc_api_key="nvapi-test"))

    async with app.run_test(size=(180, 70)) as pilot:
        app.switch_section("launch")
        await pilot.pause(0.1)

        launch = app.query_one("#screen-launch", LaunchScreen)
        viewer = launch.query_one("#stream-viewer", _StreamTabsWidget)

        await pilot.click("#btn-stream-follow")
        await pilot.pause(0.1)
        assert viewer._follow_streams["deploy"] is False
        assert str(viewer.query_one("#btn-stream-follow").label) == "Follow: Off"

        viewer.append_lines([(f"deploy line {i}", "deploy") for i in range(5)])
        await pilot.pause(0.1)
        assert viewer.active_lines[-1] == "deploy line 4"

        await pilot.click("#btn-stream-end")
        await pilot.pause(0.1)
        assert viewer._follow_streams["deploy"] is True
        assert str(viewer.query_one("#btn-stream-follow").label) == "Follow: On"


def test_tui_callback_streams_unfiltered_deploy_log_lines() -> None:
    recorder = CallbackRecorder()
    callback = _TuiCallback(recorder)  # type: ignore[arg-type]

    callback.on_log("ordinary docker build output with no activity keyword")

    assert recorder.entries == [("ordinary docker build output with no activity keyword", "deploy")]


def test_create_deploy_log_path_is_owner_only() -> None:
    log_path = _create_deploy_log_path()
    try:
        assert log_path.stat().st_mode & 0o777 == 0o600
    finally:
        log_path.unlink()


def test_dhcp_activity_helpers_include_refresh_and_config_events() -> None:
    refresh_line = '{"levelname": "INFO", "message": "KEA DHCP4 Configuration Refresh Complete."}'
    config_line = "2026-05-31 00:50:03 DHCPSRV_CFGMGR_NEW_SUBNET4 a new subnet has been added"

    clean_refresh = _clean_dhcp_line(refresh_line)
    clean_config = _clean_dhcp_line(config_line)

    assert clean_refresh == "KEA DHCP4 Configuration Refresh Complete."
    assert _is_interesting_dhcp_line(clean_refresh)
    assert clean_config.startswith("DHCPSRV_CFGMGR_NEW_SUBNET4")
    assert _is_interesting_dhcp_line(clean_config)


def test_service_log_snapshots_include_dhcp_refresh_logs(monkeypatch: pytest.MonkeyPatch) -> None:
    manager = AirSimulationManager.__new__(AirSimulationManager)
    commands: list[str] = []

    def fake_ssh_cmd(host: str, port: int) -> list[str]:
        assert host == PUBLIC_AIR_WORKER
        assert port == 17117
        return ["ssh", "nvcm@worker"]

    def fake_run(
        cmd: list[str],
        *,
        capture_output: bool,
        text: bool,
        timeout: int,
    ) -> SimpleNamespace:
        assert capture_output is True
        assert text is True
        assert timeout == 15
        remote_command = cmd[-1]
        commands.append(remote_command)
        if sim_manager_module.CONFIG_MANAGER_DHCP_REFRESH_DEPLOYMENT in remote_command:
            return SimpleNamespace(
                returncode=0,
                stdout='{"message": "KEA DHCP4 Configuration Refresh Complete."}\n',
            )
        if sim_manager_module.CONFIG_MANAGER_DHCP_DEPLOYMENT in remote_command:
            return SimpleNamespace(returncode=0, stdout="DHCP4_LEASE_ALLOC allocated lease\n")
        return SimpleNamespace(returncode=0, stdout="")

    monkeypatch.setattr(manager, "_ssh_cmd", fake_ssh_cmd)
    monkeypatch.setattr(sim_manager_module.subprocess, "run", fake_run)

    snapshots = manager.get_service_log_snapshots(PUBLIC_AIR_WORKER, 17117)

    assert any(
        sim_manager_module.CONFIG_MANAGER_DHCP_REFRESH_DEPLOYMENT in command for command in commands
    )
    assert snapshots["dhcp"] == [
        "DHCP4_LEASE_ALLOC allocated lease",
        '{"message": "KEA DHCP4 Configuration Refresh Complete."}',
    ]


@pytest.mark.asyncio
async def test_pod_summary_prioritizes_nautobot_progress() -> None:
    app = ClipboardAirSimApp(config=SimConfig(ngc_api_key="nvapi-test"))

    async with app.run_test(size=(180, 70)) as pilot:
        app.switch_section("launch")
        await pilot.pause(0.1)

        panel = app.query_one("#pod-status-panel", _PodStatusWidget)
        panel._update_table(
            [
                {
                    "name": "nv-config-manager-dhcp-6f86b9f5b7-lm2xq",
                    "ready": "4/4",
                    "status": "Running",
                    "restarts": "0",
                    "age": "4m",
                },
                {
                    "name": "nv-config-manager-nautobot-7c6c5b566-2kqq2",
                    "ready": "1/2",
                    "status": "Running",
                    "restarts": "0",
                    "age": "3m",
                },
                {
                    "name": "nv-config-manager-render-api-5858dcb947-n257z",
                    "ready": "0/1",
                    "status": "Init:0/1",
                    "restarts": "0",
                    "age": "1m",
                },
                {
                    "name": "cluster-nautobot-1",
                    "ready": "1/1",
                    "status": "Running",
                    "restarts": "0",
                    "age": "5m",
                },
            ]
        )

        summary_text = str(panel.query_one("#pod-summary").render())
        detail_text = str(panel.query_one("#pod-detail").render())

        assert summary_text == "Nautobot: 1/2 Running"
        assert "Other pods ready: 2/3" in detail_text
        assert "nv-config-manager-render-api" in detail_text


@pytest.mark.asyncio
async def test_switch_provisioning_waiting_state_names_nautobot_dependency() -> None:
    app = ClipboardAirSimApp(config=SimConfig(ngc_api_key="nvapi-test"))

    async with app.run_test(size=(180, 70)) as pilot:
        app.switch_section("launch")
        await pilot.pause(0.1)

        panel = app.query_one("#pod-status-panel", _PodStatusWidget)
        panel._update_prov(0, 0, [])

        assert (
            str(panel.query_one("#prov-count").render())
            == "Switches Provisioned: waiting for Nautobot"
        )


@pytest.mark.asyncio
async def test_derived_air_tui_selects_provider_launch_behavior() -> None:
    provider_status = AirProviderStatus(
        display_name="NetBox",
        web_pod_prefix="nv-config-manager-netbox",
        access_url="https://netbox.nvcm.air",
        dependent_pod_prefixes=("nv-config-manager-render-",),
    )

    class ProviderOrchestrator(SimOrchestrator):
        pass

    class ProviderLaunchScreen(LaunchScreen):
        PROVIDER_STATUS = provider_status

        def create_orchestrator(self, callback: OrchestratorCallback) -> SimOrchestrator:
            return ProviderOrchestrator(self._config, callback)

    class ProviderAirApp(ClipboardAirSimApp):
        SCREEN_CLASSES = {
            **NVCMAirSimApp.SCREEN_CLASSES,
            "launch": ProviderLaunchScreen,
        }

    app = ProviderAirApp(
        config=SimConfig(
            ngc_api_key="nvapi-test",
            oob_ssh_password=TEST_OOB_SSH_PASSWORD,
        )
    )
    async with app.run_test(size=(180, 100)) as pilot:
        app.switch_section("launch")
        await pilot.pause(0.1)

        launch = app.query_one("#screen-launch", ProviderLaunchScreen)
        assert isinstance(launch.create_orchestrator(CallbackRecorder()), ProviderOrchestrator)

        panel = launch.query_one("#pod-status-panel", _PodStatusWidget)
        panel._update_table(
            [
                {
                    "name": "nv-config-manager-netbox-7c6c5b566-2kqq2",
                    "ready": "1/1",
                    "status": "Running",
                }
            ]
        )
        assert "NetBox: ready" in str(panel.query_one("#pod-summary").render())

        launch._show_proxy_panel(PUBLIC_AIR_WORKER, 17117, provider_ready=True)
        await pilot.pause(0.1)
        assert "netbox.nvcm.air" in str(app.query_one("#cmd-browser-unix", Static).render())
        assert "NetBox is ready" in str(app.query_one("#proxy-hint").render())


@pytest.mark.asyncio
async def test_derived_air_tui_preserves_config_model_and_replaces_population_panel() -> None:
    @dataclass
    class ProviderSimConfig(SimConfig):
        provider_site: str = "netbox-demo"

    class ProviderPopulationPanel(PopulationPanel):
        def compose(self) -> ComposeResult:
            yield Input(value=self._config.provider_site, id="provider-site")

        def write_to_config(self, config: SimConfig) -> None:
            assert isinstance(config, ProviderSimConfig)
            config.provider_site = self.query_one("#provider-site", Input).value.strip()

        def sync_from_config(self, config: SimConfig) -> None:
            super().sync_from_config(config)
            assert isinstance(config, ProviderSimConfig)
            self.query_one("#provider-site", Input).value = config.provider_site

    class ProviderTopologyScreen(TopologyScreen):
        POPULATION_PANEL_CLASS = ProviderPopulationPanel

    class ProviderAirApp(ClipboardAirSimApp):
        CONFIG_MODEL = ProviderSimConfig
        SCREEN_CLASSES = {
            **NVCMAirSimApp.SCREEN_CLASSES,
            "topology": ProviderTopologyScreen,
        }

    app = ProviderAirApp()
    assert isinstance(app.config, ProviderSimConfig)
    assert isinstance(app.load_prebuilt_config("superpod"), ProviderSimConfig)

    async with app.run_test(size=(180, 100)):
        topology = app.query_one("#screen-topology", ProviderTopologyScreen)
        assert isinstance(topology.query_one("#population-panel"), ProviderPopulationPanel)
        assert not topology.query("#run-mock-topology-job")

        topology.query_one("#provider-site", Input).value = "netbox-lab"
        topology.write_to_config(app.config)
        assert app.config.provider_site == "netbox-lab"


def test_provider_launch_screen_can_replace_nautobot_validation() -> None:
    class ProviderLaunchScreen(LaunchScreen):
        def validate_provider_config(self) -> str:
            return ""

    config = SimConfig(run_mock_topology_job=True, mock_topology_path="")

    assert "Nautobot" in LaunchScreen(config).validate_provider_config()
    assert ProviderLaunchScreen(config).validate_provider_config() == ""


@pytest.mark.asyncio
async def test_stream_tabs_deploy_buffer_is_bounded() -> None:
    app = ClipboardAirSimApp(config=SimConfig(ngc_api_key="nvapi-test"))

    async with app.run_test(size=(180, 70)) as pilot:
        app.switch_section("launch")
        await pilot.pause(0.1)

        launch = app.query_one("#screen-launch", LaunchScreen)
        viewer = launch.query_one("#stream-viewer", _StreamTabsWidget)
        viewer.append_lines(
            [(f"deploy milestone {i:03d}", "deploy") for i in range(_MAX_DEPLOY_LOG_LINES + 20)]
        )
        await pilot.pause(0.1)

        assert len(viewer.active_lines) == _MAX_DEPLOY_LOG_LINES
        assert viewer.active_lines[0].endswith("deploy milestone 020")
        assert viewer.active_lines[-1].endswith(
            f"deploy milestone {_MAX_DEPLOY_LOG_LINES + 19:03d}"
        )


@pytest.mark.asyncio
async def test_log_flood_does_not_block_copy_or_save_key(tmp_path) -> None:
    app = ClipboardAirSimApp(
        config=SimConfig(
            ngc_api_key="nvapi-test",
            oob_ssh_password=TEST_OOB_SSH_PASSWORD,
        ),
        config_path=tmp_path / "air-sim.yaml",
    )

    async with app.run_test(size=(180, 70)) as pilot:
        app.switch_section("launch")
        await pilot.pause(0.1)

        launch = app.query_one("#screen-launch", LaunchScreen)
        ssh_command = f"sshpass -p {TEST_OOB_SSH_PASSWORD} ssh -p 17117 nvcm@{PUBLIC_AIR_WORKER}"
        launch._ssh_cmd_text = ssh_command
        launch._show_proxy_panel(PUBLIC_AIR_WORKER, 17117)
        launch.query_one("#stream-viewer", _StreamTabsWidget).select_stream("access")
        await pilot.pause(0.1)

        for i in range(2000):
            launch.enqueue_log_line(f"ztp line {i:04d}", "ztp")

        await pilot.click("#copy-ssh-direct")
        await pilot.press("f2")
        await pilot.pause(0.2)

        assert app.copied_text == ssh_command
        assert app.config_path.exists()


@pytest.mark.asyncio
async def test_deploy_started_does_not_start_service_log_polling() -> None:
    app = ClipboardAirSimApp(config=SimConfig(ngc_api_key="nvapi-test"))

    async with app.run_test(size=(180, 70)) as pilot:
        app.switch_section("launch")
        await pilot.pause(0.1)

        launch = app.query_one("#screen-launch", LaunchScreen)
        launch.on__deploy_started(_DeployStarted(PUBLIC_AIR_WORKER, 17117))

        assert launch._host == PUBLIC_AIR_WORKER
        assert launch._port == 17117
        assert launch._service_polling is False


@pytest.mark.asyncio
async def test_launch_status_keeps_deploy_log_and_air_bar_on_completion(tmp_path) -> None:
    app = ClipboardAirSimApp(config=SimConfig(ngc_api_key="nvapi-test", use_internal=True))

    async with app.run_test(size=(180, 70)) as pilot:
        app.switch_section("launch")
        await pilot.pause(0.1)

        launch = app.query_one("#screen-launch", LaunchScreen)
        launch._deploy_log_path = tmp_path / "nvcm-deploy.log"
        launch.set_simulation_id("7dfde74b-ce46-4a29-97dc-58294ee39390")
        launch._set_status(launch._status_text("[bold green][*] Bringup complete![/bold green]"))
        await pilot.pause(0.1)

        status_text = str(launch.query_one("#launch-status").render())
        air_url = (
            "https://ngc.air-inside.nvidia.com/simulations/7dfde74b-ce46-4a29-97dc-58294ee39390"
        )
        assert str(tmp_path / "nvcm-deploy.log") in status_text
        assert air_url not in status_text

        await pilot.click("#copy-air-link")
        await pilot.pause(0.1)
        assert app.copied_text == air_url


@pytest.mark.asyncio
async def test_launch_air_link_uses_public_dsx_endpoint() -> None:
    app = ClipboardAirSimApp(config=SimConfig(ngc_api_key="nvapi-test", use_internal=False))

    async with app.run_test(size=(180, 70)) as pilot:
        app.switch_section("launch")
        await pilot.pause(0.1)

        launch = app.query_one("#screen-launch", LaunchScreen)
        launch.set_simulation_id("7dfde74b-ce46-4a29-97dc-58294ee39390")
        await pilot.pause(0.1)

        air_url = "https://dsx-air.nvidia.com/simulations/7dfde74b-ce46-4a29-97dc-58294ee39390"
        await pilot.click("#copy-air-link")
        await pilot.pause(0.1)
        assert app.copied_text == air_url


@pytest.mark.asyncio
async def test_options_page_saves_visible_fields_without_resetting_hidden_flags(tmp_path) -> None:
    config = SimConfig(
        ngc_api_key="nvapi-test",
        auto_configure=False,
        deploy=False,
        no_aggressive_dhcp=True,
        no_reset_before_dhcp=True,
        size="medium",
    )
    app = ClipboardAirSimApp(config=config, config_path=tmp_path / "air-sim.yaml")

    async with app.run_test(size=(180, 70)) as pilot:
        app.switch_section("options")
        await pilot.pause(0.1)

        options = app.query_one("#screen-options")
        options.query_one("#use-public-air", LabeledSwitch).value = False
        options.query_one("#oob-ssh-password", Input).value = TEST_OOB_SSH_PASSWORD
        options.query_one("#config-manager-ref", Input).value = "feature/air-demo"
        await pilot.click("#size-large")
        await pilot.pause(0.1)

        app.collect_config()

    assert config.use_internal is True
    assert config.oob_ssh_password == TEST_OOB_SSH_PASSWORD
    assert config.config_manager_ref == "feature/air-demo"
    assert config.auto_configure is False
    assert config.deploy is False
    assert config.no_aggressive_dhcp is True
    assert config.no_reset_before_dhcp is True
    assert config.size == "large"
