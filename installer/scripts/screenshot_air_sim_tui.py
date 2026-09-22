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
"""Generate SVG screenshots of the DSX Air simulation TUI for documentation.

Usage:
    uv run python scripts/screenshot_air_sim_tui.py
    uv run python scripts/screenshot_air_sim_tui.py --output-dir ../docs/assets/images/air-sim

The launch screenshots use deterministic mock data. They do not contact DSX Air,
Kubernetes, or the SSH target.
"""

from __future__ import annotations

import argparse
import asyncio
import io
import os
import re
import time
from collections.abc import Callable
from html import unescape
from pathlib import Path

from rich.console import Console
from textual.widgets import Button, Static

from nv_config_manager_installer.air_sim.constants import (
    NVCM_BOX_PASSWORD,
    NVCM_BOX_USER,
)
from nv_config_manager_installer.air_sim.orchestrator import STEPS, StepStatus
from nv_config_manager_installer.air_sim.prebuilt_configs import load_prebuilt_config
from nv_config_manager_installer.air_sim.sim_config import SimConfig
from nv_config_manager_installer.tui.air_sim.app import SECTION_LABELS, NVCMAirSimApp
from nv_config_manager_installer.tui.air_sim.screens.launch import (
    LaunchScreen,
    _clean_dhcp_line,
    _clean_ztp_line,
    _PodStatusWidget,
    _StepListWidget,
    _StreamTabsWidget,
)

DEFAULT_COLS = 180
DEFAULT_ROWS = 70
REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_OUT_DIR = REPO_ROOT / "docs" / "assets" / "images" / "air-sim"

MOCK_HOST = "eb515e50.workers.ngc.air.nvidia.com"
MOCK_PORT = 17117
MOCK_SIM_ID = "7dfde74b-ce46-4a29-97dc-58294ee39390"
MOCK_DEPLOY_LOG = Path("/tmp/nvcm-deploy-20260530-000000.log")
TRUFFLEHOG_IGNORE_COMMENT = "<!-- trufflehog:ignore - public DSX Air demo VM password -->"
_SVG_CHAR_HEIGHT = 20.0
_SVG_LINE_HEIGHT = 24.4
_SVG_LINE_WIDTH = 1.6
_SVG_LINE_GLYPHS = frozenset("─━│┌┐└┘├┤┬┴┼╸╺▁▃▔")
_LOCAL_TERMINAL_SVG_FORMAT = """<svg class="nvcm-tui-screenshot" viewBox="0 0 {terminal_width} {terminal_height}" xmlns="http://www.w3.org/2000/svg">
    <!-- Generated with Rich https://www.textualize.io -->
    <style>
    .nvcm-tui-screenshot {{
        background: #121212;
    }}

    .{unique_id}-matrix {{
        font-family: "Ubuntu Sans Mono", "Ubuntu Mono", "DejaVu Sans Mono", "Liberation Mono", monospace;
        font-size: {char_height}px;
        line-height: {line_height}px;
        font-variant-east-asian: full-width;
        text-rendering: geometricPrecision;
    }}

    {styles}
    </style>

    <defs>
    <clipPath id="{unique_id}-clip-terminal">
      <rect x="0" y="0" width="{terminal_width}" height="{terminal_height}" />
    </clipPath>
    {lines}
    </defs>

    <rect fill="#121212" x="0" y="0" width="{terminal_width}" height="{terminal_height}" />
    <g clip-path="url(#{unique_id}-clip-terminal)">
    {backgrounds}
    <g class="{unique_id}-matrix">
    {matrix}
    </g>
    </g>
</svg>
"""


def _slug(label: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", label.lower()).strip("-")


def _save(output_dir: Path, name: str, svg: str) -> None:
    (output_dir / name).write_text(_allowlist_demo_password(_solidify_box_glyphs(svg)))
    print(f"  {name}")


def _allowlist_demo_password(svg: str) -> str:
    """Mark generated SVG lines containing the public demo password for scanners."""
    lines: list[str] = []
    for line in svg.splitlines(keepends=True):
        if NVCM_BOX_PASSWORD not in line or "trufflehog:ignore" in line:
            lines.append(line)
            continue

        ending = ""
        body = line
        if line.endswith("\r\n"):
            ending = "\r\n"
            body = line[:-2]
        elif line.endswith("\n"):
            ending = "\n"
            body = line[:-1]
        lines.append(f"{body}{TRUFFLEHOG_IGNORE_COMMENT}{ending}")
    return "".join(lines)


def _solidify_box_glyphs(svg: str) -> str:
    """Replace browser-font-sensitive terminal box glyphs with crisp SVG shapes."""

    def _replace(match: re.Match[str]) -> str:
        attrs = _parse_svg_attrs(match.group("attrs"))
        raw_text = unescape(match.group("text"))
        if not raw_text or any(char not in _SVG_LINE_GLYPHS for char in raw_text):
            return match.group(0)
        if not {"class", "x", "y", "textLength"}.issubset(attrs):
            return match.group(0)

        x = float(attrs["x"])
        baseline = float(attrs["y"])
        width = float(attrs["textLength"])
        cell_width = width / len(raw_text)
        class_name = attrs["class"]
        clip_path = attrs.get("clip-path")
        cell_top = baseline - _SVG_CHAR_HEIGHT
        cell_mid = cell_top + (_SVG_LINE_HEIGHT / 2)
        parts: list[str] = []

        for index, char in enumerate(raw_text):
            cell_x = x + (index * cell_width)
            cell_mid_x = cell_x + (cell_width / 2)
            parts.extend(
                _box_glyph_rects(
                    char,
                    cell_x=cell_x,
                    cell_mid_x=cell_mid_x,
                    cell_top=cell_top,
                    cell_mid=cell_mid,
                    cell_width=cell_width,
                    class_name=class_name,
                    clip_path=clip_path,
                )
            )
        return "".join(parts)

    return re.sub(
        r"<text (?P<attrs>[^>]*)>(?P<text>.*?)</text>",
        _replace,
        svg,
        flags=re.DOTALL,
    )


def _parse_svg_attrs(attrs: str) -> dict[str, str]:
    return dict(re.findall(r'([\w-]+)="([^"]*)"', attrs))


def _box_glyph_rects(
    char: str,
    *,
    cell_x: float,
    cell_mid_x: float,
    cell_top: float,
    cell_mid: float,
    cell_width: float,
    class_name: str,
    clip_path: str | None,
) -> list[str]:
    clip_attr = f' clip-path="{clip_path}"' if clip_path else ""

    def rect(x: float, y: float, width: float, height: float) -> str:
        return (
            f'<rect class="{class_name}" x="{_fmt(x)}" y="{_fmt(y)}" '
            f'width="{_fmt(width)}" height="{_fmt(height)}"'
            f'{clip_attr} shape-rendering="crispEdges"/>'
        )

    stroke = _SVG_LINE_WIDTH
    half_stroke = stroke / 2
    shapes: list[str] = []

    if char in {"─", "━", "╸", "╺"}:
        shapes.append(rect(cell_x, cell_mid - half_stroke, cell_width, stroke))
    elif char == "│":
        shapes.append(rect(cell_mid_x - half_stroke, cell_top, stroke, _SVG_LINE_HEIGHT))
    elif char == "┌":
        shapes.append(rect(cell_mid_x - half_stroke, cell_mid, stroke, _SVG_LINE_HEIGHT / 2))
        shapes.append(rect(cell_mid_x, cell_mid - half_stroke, cell_width / 2, stroke))
    elif char == "┐":
        shapes.append(rect(cell_mid_x - half_stroke, cell_mid, stroke, _SVG_LINE_HEIGHT / 2))
        shapes.append(rect(cell_x, cell_mid - half_stroke, cell_width / 2, stroke))
    elif char == "└":
        shapes.append(rect(cell_mid_x - half_stroke, cell_top, stroke, _SVG_LINE_HEIGHT / 2))
        shapes.append(rect(cell_mid_x, cell_mid - half_stroke, cell_width / 2, stroke))
    elif char == "┘":
        shapes.append(rect(cell_mid_x - half_stroke, cell_top, stroke, _SVG_LINE_HEIGHT / 2))
        shapes.append(rect(cell_x, cell_mid - half_stroke, cell_width / 2, stroke))
    elif char == "├":
        shapes.append(rect(cell_mid_x - half_stroke, cell_top, stroke, _SVG_LINE_HEIGHT))
        shapes.append(rect(cell_mid_x, cell_mid - half_stroke, cell_width / 2, stroke))
    elif char == "┤":
        shapes.append(rect(cell_mid_x - half_stroke, cell_top, stroke, _SVG_LINE_HEIGHT))
        shapes.append(rect(cell_x, cell_mid - half_stroke, cell_width / 2, stroke))
    elif char == "┬":
        shapes.append(rect(cell_x, cell_mid - half_stroke, cell_width, stroke))
        shapes.append(rect(cell_mid_x - half_stroke, cell_mid, stroke, _SVG_LINE_HEIGHT / 2))
    elif char == "┴":
        shapes.append(rect(cell_x, cell_mid - half_stroke, cell_width, stroke))
        shapes.append(rect(cell_mid_x - half_stroke, cell_top, stroke, _SVG_LINE_HEIGHT / 2))
    elif char == "┼":
        shapes.append(rect(cell_x, cell_mid - half_stroke, cell_width, stroke))
        shapes.append(rect(cell_mid_x - half_stroke, cell_top, stroke, _SVG_LINE_HEIGHT))
    elif char == "▔":
        shapes.append(rect(cell_x, cell_top + 2.0, cell_width, stroke))
    elif char == "▁":
        shapes.append(rect(cell_x, cell_top + _SVG_LINE_HEIGHT - 3.0, cell_width, stroke))
    elif char == "▃":
        shapes.append(
            rect(cell_x, cell_top + (_SVG_LINE_HEIGHT * 0.68), cell_width, _SVG_LINE_HEIGHT * 0.24)
        )
    return shapes


def _fmt(value: float) -> str:
    return f"{value:.2f}".rstrip("0").rstrip(".")


def _shot(app: NVCMAirSimApp, title: str) -> str:
    no_color = os.environ.pop("NO_COLOR", None)
    try:
        return _export_app_viewport_svg(app, title=f"NVCM DSX Air Sim Wizard - {title}")
    finally:
        if no_color is not None:
            os.environ["NO_COLOR"] = no_color


def _export_app_viewport_svg(app: NVCMAirSimApp, *, title: str) -> str:
    """Export the TUI viewport without Rich's default macOS-style terminal chrome."""
    assert app._driver is not None, "App must be running"
    width, height = app.size
    console = Console(
        width=width,
        height=height,
        file=io.StringIO(),
        force_terminal=True,
        color_system="truecolor",
        record=True,
        legacy_windows=False,
        safe_box=False,
    )
    screen_render = app.screen._compositor.render_update(
        full=True,
        screen_stack=app.app._background_screens,
        simplify=False,
    )
    console.print(screen_render)
    return console.export_svg(
        title=title,
        code_format=_LOCAL_TERMINAL_SVG_FORMAT,
        font_aspect_ratio=0.61,
    )


async def _stabilize(pilot: object, pauses: int = 2, delay: float = 0.1) -> None:
    """Pause multiple times so async mounts and screen updates can settle."""
    for _ in range(pauses):
        await pilot.pause(delay)  # type: ignore[attr-defined]


def _example_config() -> SimConfig:
    """Return the public DSX Air trial demo config with screenshot-only auth filled in."""
    cfg = load_prebuilt_config("air-trial")
    cfg.ngc_api_key = "nvapi-demo-key-for-screenshots"
    cfg.oob_ssh_password = NVCM_BOX_PASSWORD
    return cfg


def _ssh_cmd(host: str = MOCK_HOST, port: int = MOCK_PORT) -> str:
    return (
        f"sshpass -p {NVCM_BOX_PASSWORD} ssh"
        " -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null"
        " -o PreferredAuthentications=password"
        f" -p {port} {NVCM_BOX_USER}@{host}"
    )


def _launch_screen(app: NVCMAirSimApp) -> LaunchScreen:
    return app.query_one("#screen-launch", LaunchScreen)


def _set_step_states(
    launch: LaunchScreen,
    *,
    running_step: str | None = None,
    failed_step: str | None = None,
) -> None:
    step_list = launch.query_one("#step-list", _StepListWidget)
    step_list._start_times.clear()
    step_list._durations.clear()
    step_list._running_step = None
    now = time.monotonic()
    for idx, (step_id, _label) in enumerate(STEPS):
        if failed_step and step_id == failed_step:
            step_list.update_step(step_id, StepStatus.FAILED)
            step_list._durations[step_id] = 42.0
            continue
        if running_step and step_id == running_step:
            step_list.update_step(step_id, StepStatus.RUNNING)
            step_list._start_times[step_id] = now - 392
            continue
        if failed_step:
            failed_idx = next(i for i, (sid, _lbl) in enumerate(STEPS) if sid == failed_step)
            status = StepStatus.SUCCESS if idx < failed_idx else StepStatus.PENDING
        elif running_step:
            running_idx = next(i for i, (sid, _lbl) in enumerate(STEPS) if sid == running_step)
            status = StepStatus.SUCCESS if idx < running_idx else StepStatus.PENDING
        else:
            status = StepStatus.SUCCESS
        step_list.update_step(step_id, status)
        if status == StepStatus.SUCCESS:
            step_list._durations[step_id] = _MOCK_STEP_DURATIONS.get(step_id, 8.0)
            step_list._refresh(step_id)


_MOCK_STEP_DURATIONS: dict[str, float] = {
    "parse-topology": 4.0,
    "create-sim": 13.0,
    "attach-cloud-init": 7.0,
    "start-sim": 286.0,
    "create-ssh": 18.0,
    "wait-setup": 241.0,
    "upload-files": 6.0,
    "run-deploy": 1188.0,
    "post-deploy": 51.0,
}


_MOCK_PODS: list[dict[str, str]] = [
    {
        "name": "cluster-dhcp-1",
        "ready": "1/1",
        "status": "Running",
        "restarts": "0",
        "age": "53m",
    },
    {
        "name": "cluster-nautobot-1",
        "ready": "1/1",
        "status": "Running",
        "restarts": "0",
        "age": "53m",
    },
    {
        "name": "nv-config-manager-dhcp-c49966454-wvlts",
        "ready": "4/4",
        "status": "Running",
        "restarts": "0",
        "age": "54m",
    },
    {
        "name": "nv-config-manager-dhcp-refresh-5cc75b56fd-cbl2v",
        "ready": "1/1",
        "status": "Running",
        "restarts": "0",
        "age": "40m",
    },
    {
        "name": "nv-config-manager-nautobot-7c6c5b566-2kqq2",
        "ready": "2/2",
        "status": "Running",
        "restarts": "0",
        "age": "54m",
    },
    {
        "name": "nv-config-manager-nautobot-celery-559d9986b8-fvkzn",
        "ready": "1/1",
        "status": "Running",
        "restarts": "0",
        "age": "54m",
    },
    {
        "name": "nv-config-manager-nautobot-celery-beat-5d68f4c455-bznbk",
        "ready": "1/1",
        "status": "Running",
        "restarts": "0",
        "age": "54m",
    },
    {
        "name": "nv-config-manager-render-api-5858dcb947-n257z",
        "ready": "1/1",
        "status": "Running",
        "restarts": "0",
        "age": "9m43s",
    },
    {
        "name": "nv-config-manager-render-consumer-device-58599695-2q862",
        "ready": "1/1",
        "status": "Running",
        "restarts": "0",
        "age": "9m43s",
    },
    {
        "name": "nv-config-manager-render-consumer-device-58599695-4dn2k",
        "ready": "1/1",
        "status": "Running",
        "restarts": "0",
        "age": "9m43s",
    },
    {
        "name": "nv-config-manager-render-consumer-device-58599695-f5l4d",
        "ready": "1/1",
        "status": "Running",
        "restarts": "0",
        "age": "9m34s",
    },
    {
        "name": "nv-config-manager-render-consumer-device-58599695-j26nw",
        "ready": "1/1",
        "status": "Running",
        "restarts": "0",
        "age": "9m34s",
    },
    {
        "name": "nv-config-manager-render-consumer-nautobot-59c6d5bf8b-gcdlv",
        "ready": "1/1",
        "status": "Running",
        "restarts": "0",
        "age": "9m43s",
    },
    {
        "name": "nv-config-manager-ztp-69cbf8cd46-tm52t",
        "ready": "3/3",
        "status": "Running",
        "restarts": "0",
        "age": "54m",
    },
]


_DEPLOY_LOG_LINES = [
    "23:52:44  Prefixes tagged 'lb-allowed': 172.18.0.0/16, 10.0.0.0/8",
    "23:52:44  Prefixes tagged 'relay-return': 10.120.0.0/16",
    "23:52:44  Found 6 Cumulus Linux devices",
    "23:52:44  Found 1 server nodes",
    "23:52:44  Found 1 exit interface(s) for SSH access",
    (
        "23:52:44  Overriding existing server 'oob-mgmt-server' with nvcm-box image "
        "(generic/ubuntu2404, 16 CPU, 32768MB RAM, 100GB storage)"
    ),
    "23:52:44  Built topology with 7 nodes and 8 links",
    "23:52:45  Created simulation: 9e1f8be2-43a0-4797-9e14-91e5b170b656",
    (f"23:53:31  Created SSH service for oob-mgmt-server:eth0 -> {MOCK_HOST}:17117"),
    f"SSH ready: nvcm@{MOCK_HOST}:17117",
    (
        "23:56:03  Uploading /tmp/nv-config-manager-install-sujopgqf.yaml -> "
        f"{MOCK_HOST}:/home/nvcm/nv-config-manager-install.yaml ..."
    ),
    "23:56:03  Upload complete: /home/nvcm/nv-config-manager-install.yaml",
    "Uploaded nv-config-manager-install.yaml",
    "Running deploy command:",
    (
        "  sudo NO_COLOR=1 KUBECONFIG=/home/nvcm/.kube/config uv run --directory "
        "/home/nvcm/nv-config-manager --project /home/nvcm/nv-config-manager/installer "
        "nv-config-manager-installer deploy /home/nvcm/nv-config-manager-install.yaml "
        "--chart-dir /home/nvcm/nv-config-manager/deploy/helm --kind-cluster nvcm "
        "--install-envoy-gateway --install-cert-manager --install-cnpg-operator "
        "--image-source local --build-images --load-kind"
    ),
    "23:56:03  Running installer (this may take 15-30 min)...",
    "23:56:07  [oob-mgmt-server] [>]  Check prerequisites",
    "23:56:07  [oob-mgmt-server] [*]  Check prerequisites",
    "23:56:08  [oob-mgmt-server] [>]  Build local images",
    "23:56:08  [oob-mgmt-server]   Building nv-config-manager-nautobot:local...",
    "00:01:45  [oob-mgmt-server] [*]  Build local images",
    "00:01:45  [oob-mgmt-server] [>]  Load images to Kind",
    "00:02:18  [oob-mgmt-server] [*]  Load images to Kind",
    "00:02:18  [oob-mgmt-server] [>]  Install CRDs / operators",
    "00:03:13  [oob-mgmt-server] [*]  Install CRDs / operators",
    "00:03:13  [oob-mgmt-server] [>]  Create namespace",
    "00:03:13  [oob-mgmt-server]   Created: nv-config-manager (context=kind-nvcm)",
    "00:03:13  [oob-mgmt-server] [*]  Create namespace",
    "00:03:24  [oob-mgmt-server] [>]  Helm install / upgrade",
    "00:13:27  [oob-mgmt-server] [*]  Helm install / upgrade",
    "00:13:27  [oob-mgmt-server]   Network ZTP:      https://ztp.nvcm.air",
    "00:13:27  [oob-mgmt-server]   Network DHCP:     https://dhcp.nvcm.air",
    "00:13:27  [oob-mgmt-server] Deployment completed successfully!",
    (
        "00:16:56  Resetting 6 Cumulus node(s) to force fresh ZTP/DHCP cycle: "
        "tan-leaf-05, tan-leaf-02, oob-mleaf-01, tan-leaf-01, tan-leaf-04, tan-leaf-03"
    ),
]


_RAW_DHCP_LOG_LINES = [
    (
        "2026-05-30 00:57:54.424 INFO  [kea-dhcp4.packets/14.139514333066944] "
        "DHCP4_PACKET_RECEIVED [hwtype=1 44:38:39:00:00:08], cid=[no info], "
        "tid=0xbec5f077: DHCPREQUEST (type 3) received from 172.18.0.1 to "
        "10.244.0.20 on interface eth0"
    ),
    (
        "2026-05-30 00:57:54.424 INFO  [kea-dhcp4.leases/14.139514333066944] "
        "DHCP4_INIT_REBOOT [hwtype=1 44:38:39:00:00:08], cid=[no info], "
        "tid=0xbec5f077: client is in INIT-REBOOT state and requests address 10.120.0.1"
    ),
    (
        "2026-05-30 00:57:54.425 INFO  [kea-dhcp4.leases/14.139514333066944] "
        "DHCP4_LEASE_ALLOC [hwtype=1 44:38:39:00:00:08], cid=[no info], "
        "tid=0xbec5f077: lease 10.120.0.1 has been allocated for 7200 seconds"
    ),
    (
        "2026-05-30 01:01:02.800 INFO  [kea-dhcp4.leases/14.139514408601280] "
        "DHCP4_LEASE_OFFER [hwtype=1 44:38:39:00:00:04], cid=[no info], "
        "tid=0xaf0466f: lease 10.120.1.12 will be offered"
    ),
    (
        "2026-05-30 01:01:02.802 INFO  [kea-dhcp4.leases/14.139514400208576] "
        "DHCP4_LEASE_ALLOC [hwtype=1 44:38:39:00:00:04], cid=[no info], "
        "tid=0xaf0466f: lease 10.120.1.12 has been allocated for 7200 seconds"
    ),
    (
        "2026-05-30 01:01:04.907 INFO  [kea-dhcp4.leases/14.139514307888832] "
        "DHCP4_LEASE_ALLOC [hwtype=1 44:38:39:00:00:07], cid=[no info], "
        "tid=0x4a0ae633: lease 10.120.1.15 has been allocated for 7200 seconds"
    ),
    (
        "2026-05-30 01:01:21.715 INFO  [kea-dhcp4.leases/14.139514333066944] "
        "DHCP4_LEASE_ALLOC [hwtype=1 44:38:39:00:00:03], cid=[no info], "
        "tid=0x4bb0432d: lease 10.120.1.11 has been allocated for 7200 seconds"
    ),
    (
        "2026-05-30 01:01:23.362 INFO  [kea-dhcp4.leases/14.139514383423168] "
        "DHCP4_LEASE_ALLOC [hwtype=1 44:38:39:00:00:06], cid=[no info], "
        "tid=0x1204853d: lease 10.120.1.14 has been allocated for 7200 seconds"
    ),
    (
        "2026-05-30 01:01:04.907 INFO  [kea-dhcp4.packets/14.139514307888832] "
        "DHCP4_PACKET_SEND [hwtype=1 44:38:39:00:00:07], cid=[no info], "
        "tid=0x4a0ae633: trying to send packet DHCPACK (type 5) from "
        "10.244.0.20:67 to 10.120.1.1:67 on interface eth0"
    ),
]


_RAW_ZTP_LOG_LINES = [
    (
        r'{"message": "10.120.0.1:40294 - \"GET /v1/device/'
        r'8f5a1532-e155-4119-937e-86e8aa8f4007/boot-script HTTP/1.1\" 200", '
        r'"levelname": "INFO", "name": "uvicorn.access", "asctime": '
        r'"2026-05-30 01:00:35,976", "module": "httptools_impl", "lineno": '
        r'484, "level": "info", "service": "ztp"}'
    ),
    (
        r'{"message": "10.120.0.1:40304 - \"POST /v1/device/'
        r'8f5a1532-e155-4119-937e-86e8aa8f4007/validate_serial HTTP/1.1\" 200", '
        r'"levelname": "INFO", "name": "uvicorn.access", "asctime": '
        r'"2026-05-30 01:00:39,764", "module": "httptools_impl", "lineno": '
        r'484, "level": "info", "service": "ztp"}'
    ),
    (
        r'{"message": "10.120.0.1:40320 - \"GET /v1/device/'
        r"8f5a1532-e155-4119-937e-86e8aa8f4007/config/startup.yaml HTTP/1.1\" "
        r'200", "levelname": "INFO", "name": "uvicorn.access", "asctime": '
        r'"2026-05-30 01:00:40,162", "module": "httptools_impl", "lineno": '
        r'484, "level": "info", "service": "ztp"}'
    ),
    (
        r'{"message": "10.120.0.1:53424 - \"POST /v1/device/'
        r'8f5a1532-e155-4119-937e-86e8aa8f4007/provisioned HTTP/1.1\" 200", '
        r'"levelname": "INFO", "name": "uvicorn.access", "asctime": '
        r'"2026-05-30 01:00:52,692", "module": "httptools_impl", "lineno": '
        r'484, "level": "info", "service": "ztp"}'
    ),
    (
        r'{"message": "10.120.1.13:56094 - \"GET /v1/device/'
        r'38065dde-1abe-41ef-865b-60fbb6405d06/boot-script HTTP/1.1\" 200", '
        r'"levelname": "INFO", "name": "uvicorn.access", "asctime": '
        r'"2026-05-30 01:01:02,408", "module": "httptools_impl", "lineno": '
        r'484, "level": "info", "service": "ztp"}'
    ),
    (
        r'{"message": "10.120.1.13:46012 - \"POST /v1/device/'
        r'38065dde-1abe-41ef-865b-60fbb6405d06/validate_serial HTTP/1.1\" 200", '
        r'"levelname": "INFO", "name": "uvicorn.access", "asctime": '
        r'"2026-05-30 01:01:06,051", "module": "httptools_impl", "lineno": '
        r'484, "level": "info", "service": "ztp"}'
    ),
    (
        r'{"message": "10.120.1.13:46020 - \"GET /v1/device/'
        r"38065dde-1abe-41ef-865b-60fbb6405d06/config/startup.yaml HTTP/1.1\" "
        r'200", "levelname": "INFO", "name": "uvicorn.access", "asctime": '
        r'"2026-05-30 01:01:06,164", "module": "httptools_impl", "lineno": '
        r'484, "level": "info", "service": "ztp"}'
    ),
    (
        r'{"message": "10.120.1.13:46026 - \"POST /v1/device/'
        r'38065dde-1abe-41ef-865b-60fbb6405d06/provisioned HTTP/1.1\" 200", '
        r'"levelname": "INFO", "name": "uvicorn.access", "asctime": '
        r'"2026-05-30 01:01:14,605", "module": "httptools_impl", "lineno": '
        r'484, "level": "info", "service": "ztp"}'
    ),
    (
        r'{"message": "10.120.1.12:59136 - \"GET /v1/device/'
        r'48331931-a577-4ad7-ac03-dc22461a9d0c/boot-script HTTP/1.1\" 200", '
        r'"levelname": "INFO", "name": "uvicorn.access", "asctime": '
        r'"2026-05-30 01:02:01,311", "module": "httptools_impl", "lineno": '
        r'484, "level": "info", "service": "ztp"}'
    ),
    (
        r'{"message": "10.120.1.15:35374 - \"GET /v1/device/'
        r'9cae2d62-00a3-457e-b8c1-6bc9a18d8e0a/boot-script HTTP/1.1\" 200", '
        r'"levelname": "INFO", "name": "uvicorn.access", "asctime": '
        r'"2026-05-30 01:02:01,608", "module": "httptools_impl", "lineno": '
        r'484, "level": "info", "service": "ztp"}'
    ),
    (
        r'{"message": "10.120.1.11:46504 - \"POST /v1/device/'
        r'857985c8-d3c8-41f2-90d8-f966e3306113/provisioned HTTP/1.1\" 200", '
        r'"levelname": "INFO", "name": "uvicorn.access", "asctime": '
        r'"2026-05-30 01:02:14,424", "module": "httptools_impl", "lineno": '
        r'484, "level": "info", "service": "ztp"}'
    ),
    (
        r'{"message": "10.120.1.14:41842 - \"POST /v1/device/'
        r'e4484bba-527d-4b60-8f2c-74bfdfd7516e/provisioned HTTP/1.1\" 200", '
        r'"levelname": "INFO", "name": "uvicorn.access", "asctime": '
        r'"2026-05-30 01:02:14,745", "module": "httptools_impl", "lineno": '
        r'484, "level": "info", "service": "ztp"}'
    ),
]

_DHCP_LOG_LINES = [_clean_dhcp_line(line) for line in _RAW_DHCP_LOG_LINES]
_ZTP_LOG_LINES = [_clean_ztp_line(line) for line in _RAW_ZTP_LOG_LINES]


def _set_launch_identity(launch: LaunchScreen) -> None:
    launch._deploy_log_path = MOCK_DEPLOY_LOG
    launch.set_simulation_id(MOCK_SIM_ID)


def _populate_logs(launch: LaunchScreen, *, focus: str = "deploy") -> None:
    viewer = launch.query_one("#stream-viewer", _StreamTabsWidget)
    for buffer in viewer._buffers.values():
        buffer.clear()
    for seen in viewer._seen_service_lines.values():
        seen.clear()

    deploy_lines = _DEPLOY_LOG_LINES
    dhcp_lines = _DHCP_LOG_LINES
    ztp_lines = _ZTP_LOG_LINES
    if focus == "dhcp":
        entries = [(line, "deploy") for line in deploy_lines[-6:]]
        entries.extend((line, "dhcp") for line in dhcp_lines)
        entries.extend((line, "ztp") for line in ztp_lines[:2])
    elif focus == "ztp":
        entries = [(line, "deploy") for line in deploy_lines[-5:]]
        entries.extend((line, "dhcp") for line in dhcp_lines[-3:])
        entries.extend((line, "ztp") for line in ztp_lines)
    else:
        entries = [(line, "deploy") for line in deploy_lines]
        entries.extend((line, "dhcp") for line in dhcp_lines[:3])
        entries.extend((line, "ztp") for line in ztp_lines[:2])
    viewer.append_lines(entries)
    viewer.select_stream(focus)


def _populate_ssh_and_pods(
    launch: LaunchScreen,
    *,
    provisioned: str = "4/6",
    pending: str = "Pending: tan-leaf-04, tan-leaf-05",
    show_access: bool = True,
    provider_ready: bool = True,
) -> None:
    """Populate deterministic SSH, provider-readiness, and pod-status screenshot data."""
    launch._host = MOCK_HOST
    launch._port = MOCK_PORT
    launch._ssh_cmd_text = _ssh_cmd()
    if show_access:
        launch._show_proxy_panel(MOCK_HOST, MOCK_PORT, provider_ready=provider_ready)
    pod_panel = launch.query_one("#pod-status-panel", _PodStatusWidget)
    pod_panel._host = MOCK_HOST
    pod_panel._port = MOCK_PORT
    pod_panel._update_table(_MOCK_PODS)
    pod_panel.query_one("#prov-count", Static).update(f"Switches Provisioned: {provisioned}")
    pod_panel.query_one("#prov-detail", Static).update(pending)


def _populate_ready_launch(launch: LaunchScreen) -> None:
    launch.query_one("#btn-launch", Button).disabled = False
    launch.query_one("#launch-status", Static).update(
        "[green]Ready to create DSX Air simulation from mock topology air_trial.[/green]"
    )


def _populate_running_launch(launch: LaunchScreen) -> None:
    launch._bringup_running = True
    launch.query_one("#btn-launch", Button).disabled = True
    _set_launch_identity(launch)
    launch.query_one("#launch-status", Static).update(
        launch._status_text("[yellow]Running...[/yellow]")
    )
    _set_step_states(launch, running_step="run-deploy")
    _populate_ssh_and_pods(
        launch,
        provisioned="0/6",
        pending="Waiting for first ZTP callback",
        provider_ready=False,
    )
    _populate_logs(launch)


def _populate_pods_launch(launch: LaunchScreen) -> None:
    _set_launch_identity(launch)
    launch.query_one("#launch-status", Static).update(
        launch._status_text(
            "[yellow]Deployment running - monitoring Kubernetes pods over SSH.[/yellow]"
        )
    )
    _set_step_states(launch, running_step="post-deploy")
    _populate_ssh_and_pods(launch, provisioned="4/6", pending="Pending: tan-leaf-04, tan-leaf-05")
    _populate_logs(launch)


def _populate_dhcp_log_launch(launch: LaunchScreen) -> None:
    _populate_pods_launch(launch)
    _populate_logs(launch, focus="dhcp")


def _populate_ztp_log_launch(launch: LaunchScreen) -> None:
    _set_launch_identity(launch)
    launch.query_one("#launch-status", Static).update(
        launch._status_text(
            "[yellow]Deployment running - watching ZTP callbacks over SSH.[/yellow]"
        )
    )
    _set_step_states(launch, running_step="post-deploy")
    _populate_ssh_and_pods(launch, provisioned="6/6", pending="All devices reported provisioned")
    _populate_logs(launch, focus="ztp")


def _populate_complete_launch(launch: LaunchScreen) -> None:
    launch._bringup_running = False
    launch.query_one("#btn-launch", Button).disabled = False
    _set_launch_identity(launch)
    launch.query_one("#launch-status", Static).update(
        launch._status_text("[bold green][*] Bringup complete![/bold green]")
    )
    _set_step_states(launch)
    _populate_ssh_and_pods(launch, provisioned="6/6", pending="")
    launch.query_one("#prov-detail", Static).update("")
    _populate_logs(launch)
    launch.query_one("#stream-viewer", _StreamTabsWidget).select_stream("access")


async def _capture_launch(
    output_dir: Path,
    name: str,
    title: str,
    populate: Callable[[LaunchScreen], None],
    *,
    size: tuple[int, int],
) -> None:
    app = NVCMAirSimApp(config=_example_config())
    async with app.run_test(size=size) as pilot:
        app.switch_section("launch")
        await _stabilize(pilot)
        populate(_launch_screen(app))
        await _stabilize(pilot)
        _save(output_dir, name, _shot(app, title))


async def _capture_all(output_dir: Path, *, size: tuple[int, int]) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    for stale_svg in output_dir.glob("*.svg"):
        stale_svg.unlink()

    cfg = _example_config()
    app = NVCMAirSimApp(config=cfg)

    async with app.run_test(size=size) as pilot:
        for idx, (section_id, label) in enumerate(SECTION_LABELS, start=1):
            app.switch_section(section_id)
            await _stabilize(pilot)
            if section_id == "launch":
                _populate_ready_launch(_launch_screen(app))
                await _stabilize(pilot)
                filename = f"{idx:02d}-{_slug(label)}-ready.svg"
            else:
                filename = f"{idx:02d}-{_slug(label)}.svg"
            _save(output_dir, filename, _shot(app, label))

    launch_shots: list[tuple[str, str, Callable[[LaunchScreen], None]]] = [
        ("launch-running", "Launch / Running", _populate_running_launch),
        ("launch-pod-status", "Launch / Pod Status", _populate_pods_launch),
        ("launch-dhcp-log", "Launch / DHCP Log", _populate_dhcp_log_launch),
        ("launch-ztp-log", "Launch / ZTP Log", _populate_ztp_log_launch),
        ("launch-access", "Launch / Access", _populate_complete_launch),
    ]
    for offset, (slug, title, populate) in enumerate(launch_shots, start=1):
        n = len(SECTION_LABELS) + offset
        await _capture_launch(
            output_dir,
            f"{n:02d}-{slug}.svg",
            title,
            populate,
            size=size,
        )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=DEFAULT_OUT_DIR,
        help=f"Directory for generated SVGs (default: {DEFAULT_OUT_DIR})",
    )
    parser.add_argument(
        "--cols",
        type=int,
        default=DEFAULT_COLS,
        help=f"Terminal columns for wrapping (default: {DEFAULT_COLS})",
    )
    parser.add_argument(
        "--rows",
        type=int,
        default=DEFAULT_ROWS,
        help=f"Terminal rows for captured viewport height (default: {DEFAULT_ROWS})",
    )
    args = parser.parse_args()

    total = len(SECTION_LABELS) + 5
    print(f"Capturing {total} DSX Air sim screenshots at {args.cols}x{args.rows}...")
    no_color = os.environ.pop("NO_COLOR", None)
    try:
        asyncio.run(_capture_all(args.output_dir, size=(args.cols, args.rows)))
    finally:
        if no_color is not None:
            os.environ["NO_COLOR"] = no_color
    print(f"\n{total} screenshots saved to {args.output_dir}/")


if __name__ == "__main__":
    main()
