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
"""Provider-owned behavior used by the shared DSX Air orchestration."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from nv_config_manager_installer.air_sim.sim_config import SimConfig
    from nv_config_manager_installer.air_sim.sim_manager import AirSimulationManager


@dataclass(frozen=True)
class AirProviderStatus:
    """Provider-owned labels and readiness rules used by the shared AIR dashboard."""

    display_name: str
    web_pod_prefix: str
    access_url: str
    access_display_name: str | None = None
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


@dataclass(frozen=True)
class AirInstallerPackages:
    """Python packages layered onto the installer run on the AIR box.

    Each requirement is passed to ``uv run --with`` and each directory to
    ``--find-links``, so a provider that is not part of the installer package is
    discoverable by ``nv-config-manager-installer deploy`` on the box. Paths are
    remote paths the provider uploaded during ``pre_deploy``.
    """

    requirements: tuple[str, ...] = ()
    find_links: tuple[str, ...] = ()


class AirProviderHooks:
    """Lifecycle hooks a DCIM provider supplies to the AIR orchestrator.

    Every hook has a no-op default so a provider overrides only the stages
    it owns.
    """

    def validate_config(self, cfg: SimConfig) -> str:
        """Return a launch-blocking validation error, or an empty string."""
        return ""

    def install_settings(self, cfg: SimConfig) -> dict[str, Any]:
        """Return the ``dcim.settings`` block for the generated installer config."""
        return {}

    def install_secrets(self, cfg: SimConfig) -> dict[str, dict[str, str]]:
        """Return ``secrets.k8s`` group values for the generated installer config.

        Lets values a provider already used in ``pre_deploy`` (an API token, a
        NATS password) reach the box's deploy instead of being generated again.
        """
        return {}

    def prepare_images(self, cfg: SimConfig, log: Callable[[str], None]) -> None:
        """Build or pull provider-owned images before the simulation is created."""

    def gateway_hostnames(self, cfg: SimConfig) -> tuple[str, ...]:
        """Return provider-owned UI hostnames routed through the simulation gateway."""
        return ()

    def pre_deploy(self, manager: AirSimulationManager, host: str, port: int) -> None:
        """Install provider-owned services before NVIDIA Config Manager deploys."""

    def installer_packages(self, cfg: SimConfig) -> AirInstallerPackages:
        """Return packages the box's installer run needs to load this provider.

        They must exist on the box by the time the deploy command runs, which is
        after ``pre_deploy``. Built-in providers need none.
        """
        return AirInstallerPackages()

    def post_deploy(self, manager: AirSimulationManager, host: str, port: int) -> None:
        """Run provider setup after common services are ready."""

    def wait_for_configs(
        self,
        manager: AirSimulationManager,
        host: str,
        port: int,
        expected_total: int,
    ) -> None:
        """Wait for the provider to persist rendered configurations."""

    def provisioning_status(
        self,
        manager: AirSimulationManager,
        host: str,
        port: int,
    ) -> tuple[int, int, list[str]]:
        """Return ``(provisioned, total, remaining[:5])`` switch counts."""
        return 0, 0, []
