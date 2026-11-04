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
"""Generate SVG screenshots of every TUI section for documentation.

Usage:
    uv run python scripts/screenshot_tui.py
    uv run python scripts/screenshot_tui.py --output-dir ../docs/assets/images/installer

    # To also capture the tag-discovery flow (requires a live registry):
    NV_CONFIG_MANAGER_SCREENSHOT_REGISTRY_KEY=<api-key> uv run python scripts/screenshot_tui.py
    NV_CONFIG_MANAGER_SCREENSHOT_REGISTRY=nvcr.io/nvidian/cfa   # default
    NV_CONFIG_MANAGER_SCREENSHOT_REGISTRY_USER='$oauthtoken'      # default

Section shots (01–15):
  NN-section-slug.svg   one per nav section (01-cluster … 15-deploy)

Extra shots (16+):
  16-ingest-data-file-picker.svg
  17-template-plugins-node-browser.svg
  18-workflows-dropdown.svg
  19-values-preview-generated.svg
  20-container-images-tags.svg       (only when NV_CONFIG_MANAGER_SCREENSHOT_REGISTRY_KEY is set)
  21-container-images-tag-select.svg (only when NV_CONFIG_MANAGER_SCREENSHOT_REGISTRY_KEY is set)
"""

from __future__ import annotations

import argparse
import asyncio
import os
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from textual.widgets import Input, Label

from nv_config_manager_installer.schema import (
    LBProvider,
    LoadBalancerConfig,
    NetworkSecretEntry,
    NVConfigManagerInstallConfig,
    SecretsMethod,
    SiteConfig,
    ZTPOSImage,
    ZTPStorageType,
)
from nv_config_manager_installer.tui.app import SECTION_LABELS, NVConfigManagerInstallerApp

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------
COLS = 180
ROWS = 70
REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_OUT_DIR = REPO_ROOT / "docs" / "assets" / "images" / "installer"

_REGISTRY_KEY = os.getenv("NV_CONFIG_MANAGER_SCREENSHOT_REGISTRY_KEY", "")
_REGISTRY = os.getenv("NV_CONFIG_MANAGER_SCREENSHOT_REGISTRY", "nvcr.io/nvidian/cfa")
_REGISTRY_USER = os.getenv("NV_CONFIG_MANAGER_SCREENSHOT_REGISTRY_USER", "$oauthtoken")

# Extras always captured beyond the per-section shots:
# file picker, node browser, dropdown, values preview
ALWAYS_CAPTURED_EXTRAS = 4


def _slug(label: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", label.lower()).strip("-")


def _save(output_dir: Path, name: str, svg: str) -> None:
    (output_dir / name).write_text(svg)
    print(f"  {name}")


def _shot(app: NVConfigManagerInstallerApp, title: str) -> str:
    return app.export_screenshot(title=f"NVIDIA Config Manager Install Wizard — {title}")


async def _stabilize(pilot: object, pauses: int = 2, delay: float = 0.1) -> None:
    """Pause multiple times so async mounts and workers can settle."""
    for _ in range(pauses):
        await pilot.pause(delay)  # type: ignore[attr-defined]


# ---------------------------------------------------------------------------
# Example config
# ---------------------------------------------------------------------------
def _example_config() -> NVConfigManagerInstallConfig:
    """Return a config with enough data filled in that every panel looks useful."""
    cfg = NVConfigManagerInstallConfig()

    cfg.cluster.hostname = "config-manager.example.com"
    cfg.cluster.environment = "prod"
    cfg.cluster.namespace = "nv-config-manager"
    cfg.cluster.custom_labels = {
        "production": "true",
        "include_in_slo": "true",
    }

    cfg.secrets.method = SecretsMethod.KUBERNETES
    cfg.network_secrets = [
        NetworkSecretEntry(
            name="Hash Salt",
            secret_key="hash_salt",
            description="Password hashing salt",
            required=True,
        ),
        NetworkSecretEntry(
            name="BGP Password",
            secret_key="bgp_password",
            description="BGP peering authentication",
            required=True,
        ),
        NetworkSecretEntry(
            name="Device Admin Password",
            secret_key="root_password",
            description="Admin/root password for managed devices",
            required=True,
        ),
    ]

    cfg.sites = [SiteConfig(name="dc01"), SiteConfig(name="dc02")]

    cfg.services.render = True
    cfg.services.ztp = True
    cfg.services.dhcp = True
    cfg.services.temporal = True
    cfg.services.config_store = True
    cfg.services.nautobot = True

    cfg.infrastructure.tls = True
    cfg.infrastructure.load_balancer = LoadBalancerConfig(
        provider=LBProvider.METALLB,
        ztp_lb_ip="10.0.1.10",
        dhcp_lb_ip="10.0.1.11",
        ztp_dns_name="ztp.datacenter.example.com",
        dhcp_dns_name="dhcp.datacenter.example.com",
    )
    cfg.infrastructure.ztp_storage.type = ZTPStorageType.FILE
    cfg.infrastructure.ztp_storage.os_images = [
        ZTPOSImage(
            platform="cumulus-linux", version="5.14.0", path="/mnt/images/cumulus-5.14.0.bin"
        ),
        ZTPOSImage(platform="arista-eos", version="4.32.0F", path="/mnt/images/eos-4.32.0F.swi"),
    ]

    # SSO — keycloak, fields populated so the panel is informative.
    cfg.sso.enabled = True
    cfg.sso.provider = "keycloak"
    cfg.sso.issuer_url = "https://keycloak.datacenter.example.com/realms/nv-config-manager"
    cfg.sso.client_id = "nv-config-manager"

    # SPIFFE — SPIRE, fields populated so the panel is informative.
    cfg.spiffe.enabled = True
    cfg.spiffe.provider = "spire"
    cfg.spiffe.trust_domain = "datacenter.example.com"

    # Pre-fill registry info so the Container Images section looks populated.
    if _REGISTRY_KEY:
        cfg.images.registry = _REGISTRY
        cfg.images.pull_secret.username = _REGISTRY_USER
        cfg.images.pull_secret.password = _REGISTRY_KEY

    return cfg


# ---------------------------------------------------------------------------
# Main capture loop
# ---------------------------------------------------------------------------
async def _capture_all(output_dir: Path) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)

    cfg = _example_config()
    app = NVConfigManagerInstallerApp(config=cfg)

    async with app.run_test(size=(COLS, ROWS)) as pilot:
        # ── 01–15: one shot per nav section ────────────────────────────
        for idx, (section_id, label) in enumerate(SECTION_LABELS, start=1):
            app.switch_section(section_id)
            await _stabilize(pilot)
            _save(output_dir, f"{idx:02d}-{_slug(label)}.svg", _shot(app, label))

        n = len(SECTION_LABELS)

        # ── 16: Ingest Data — directory file-picker dialog ──────────────
        n += 1
        app.switch_section("ingest_data")
        await _stabilize(pilot)
        await pilot.click("#add-job-path")
        # Three pauses: worker schedules → push_screen → modal mounts + fs reads
        await _stabilize(pilot, pauses=3, delay=0.2)
        _save(
            output_dir,
            f"{n:02d}-ingest-data-file-picker.svg",
            _shot(app, "Ingest Data / File Picker"),
        )
        await pilot.press("escape")
        await pilot.pause(0.1)

        # ── 17: Template Plugins — node browser modal ───────────────────
        n += 1
        app.switch_section("render")
        await _stabilize(pilot)
        await pilot.click("#render-ns-browse")
        # Two longer pauses: worker → push_screen → modal → _load_nodes worker
        await _stabilize(pilot, pauses=2, delay=0.3)
        _save(
            output_dir,
            f"{n:02d}-template-plugins-node-browser.svg",
            _shot(app, "Template Plugins / Node Browser"),
        )
        await pilot.press("escape")
        await pilot.pause(0.1)

        # ── 18: Workflows — workflow override select dropdown open ───────
        n += 1
        app.switch_section("workflows")
        await _stabilize(pilot)
        await pilot.click("#rbac-workflow-select")
        await _stabilize(pilot, pauses=2, delay=0.15)
        _save(
            output_dir,
            f"{n:02d}-workflows-dropdown.svg",
            _shot(app, "Workflows / Override Select"),
        )
        await pilot.press("escape")
        await pilot.pause(0.1)

        # ── 19: Values Preview — with generated YAML output ────────────
        n += 1
        app.switch_section("values_preview")
        await _stabilize(pilot)
        await pilot.click("#values-generate")
        await pilot.pause(0.2)
        _save(
            output_dir,
            f"{n:02d}-values-preview-generated.svg",
            _shot(app, "Values Preview / Generated"),
        )

        # ── 20–21: Container Images — tag discovery (env-gated) ─────────
        if _REGISTRY_KEY:
            n += 1
            app.switch_section("images")
            await _stabilize(pilot)
            # Ensure widget values match env (config was pre-populated, but widgets
            # need to reflect it after sync_from_config hasn't been called yet).
            app.query_one("#img-registry", Input).value = _REGISTRY
            app.query_one("#img-username", Input).value = _REGISTRY_USER
            app.query_one("#img-password", Input).value = _REGISTRY_KEY
            await pilot.pause(0.1)
            await pilot.click("#img-fetch-tags")
            # Poll for the threaded fetch to finish (up to ~8 s).
            status_lbl = app.query_one("#img-fetch-status", Label)
            for _ in range(16):
                await pilot.pause(0.5)
                txt = str(status_lbl.content or "")
                if txt and txt not in ("", "Fetching tags..."):
                    break
            await pilot.pause(0.2)
            # Scroll the tag-select and status into view before shooting.
            app.query_one("#img-fetch-status", Label).scroll_visible()
            await pilot.pause(0.1)
            _save(
                output_dir,
                f"{n:02d}-container-images-tags.svg",
                _shot(app, "Container Images / Tag Discovery"),
            )

            # Also open the tag-select dropdown for a second shot.
            n += 1
            await pilot.click("#img-tag-select")
            await _stabilize(pilot, pauses=2, delay=0.2)
            _save(
                output_dir,
                f"{n:02d}-container-images-tag-select.svg",
                _shot(app, "Container Images / Tag Select"),
            )
            await pilot.press("escape")
            await pilot.pause(0.1)

    extra = n - len(SECTION_LABELS)
    reg_note = (
        ""
        if _REGISTRY_KEY
        else " (registry shots skipped — set NV_CONFIG_MANAGER_SCREENSHOT_REGISTRY_KEY to enable)"
    )
    print(
        f"\n{len(SECTION_LABELS)} section + {extra} extra screenshots saved to {output_dir}/{reg_note}"
    )


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Generate SVG screenshots of the installer TUI for the documentation site."
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=DEFAULT_OUT_DIR,
        help=f"Directory for generated screenshots (default: {DEFAULT_OUT_DIR})",
    )
    args = parser.parse_args()

    base = len(SECTION_LABELS) + ALWAYS_CAPTURED_EXTRAS
    registry_extra = 2 if _REGISTRY_KEY else 0
    print(f"Capturing {base + registry_extra} screenshots at {COLS}×{ROWS}…\n")
    try:
        asyncio.run(_capture_all(args.output_dir))
    except KeyboardInterrupt:
        print("\nInterrupted.")
        sys.exit(1)


if __name__ == "__main__":
    main()
