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
"""Installer-side DCIM provider contract.

A provider package registers one entry point in the
``nv_config_manager.installer.dcim`` group whose name equals
``InstallerProviderMetadata.name`` and the ``dcim.provider`` value in the
installer configuration. The installer composes the provider's static
contributions (settings model, secret groups, images, deploy steps, screens)
into its own pipeline and calls the runtime hooks at fixed points.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any, ClassVar, Protocol

from pydantic import BaseModel, ConfigDict

from nv_config_manager_installer.air_sim.provider import AirProviderHooks, AirProviderStatus

if TYPE_CHECKING:
    from nv_config_manager_installer.deployer import (
        DeployCallback,
        DeployOptions,
        DeployStep,
        StepStatus,
    )
    from nv_config_manager_installer.k8s import K8sClient
    from nv_config_manager_installer.schema import NVConfigManagerInstallConfig

INSTALLER_PROVIDER_API_VERSION = "1.0"
INSTALLER_DCIM_ENTRY_POINT_GROUP = "nv_config_manager.installer.dcim"

SecretValue = Callable[[str, str], str]
ProviderStep = tuple[str, str]


class InstallerProviderMetadata(BaseModel):
    """Identity and defaults the installer reads before loading any settings."""

    model_config = ConfigDict(frozen=True)

    name: str
    display_name: str
    supported_api_versions: tuple[str, ...] = (INSTALLER_PROVIDER_API_VERSION,)
    supports_local: bool = True
    supports_external: bool = True
    token_secret_name: str = "dcim-token"
    event_stream: str = ""
    event_subject: str = ""
    server_placeholder: str = "https://dcim.example.com"


class ProviderSettings(BaseModel):
    """Default (empty) ``dcim.settings`` model; unknown keys are rejected."""

    model_config = ConfigDict(extra="forbid")


@dataclass(frozen=True)
class SecretGroupSpec:
    """One provider-owned secret group under ``secrets.k8s`` and ``secrets.vault.paths``."""

    name: str
    helm_key: str
    path_suffix: str
    label: str
    keys: Mapping[str, str]
    fields: tuple[tuple[str, str], ...] = ()
    enabled_default: bool = True
    optional: bool = False
    # Values are written only when the DCIM runs in the cluster (dcim.local).
    local_only: bool = False
    # Wizard placement: the core group this card follows ("" places it first).
    after: str = "network"
    # Placement and label for the Kubernetes-mode card; None/"" reuse after/label.
    k8s_after: str | None = None
    k8s_label: str = ""


@dataclass(frozen=True)
class SecretsContribution:
    """Secret groups, databases, and NATS Secret names a provider adds."""

    groups: tuple[SecretGroupSpec, ...] = ()
    databases: tuple[str, ...] = ()
    nats_secret_names: tuple[str, ...] = ()


@dataclass(frozen=True)
class ImageContribution:
    """Container images a provider adds to the installer image tables."""

    local_builds: tuple[tuple[str, str, str, str | None], ...] = ()
    local_only_builds: frozenset[str] = frozenset()
    image_keys: tuple[tuple[str, str], ...] = ()
    override_keys: tuple[tuple[str, str], ...] = ()
    split_image_defaults: Mapping[str, tuple[tuple[str, ...], str, str]] = field(
        default_factory=dict
    )


@dataclass(frozen=True)
class DeployStepsContribution:
    """Provider steps inserted before or after a core step id."""

    before: Mapping[str, tuple[ProviderStep, ...]] = field(default_factory=dict)
    after: Mapping[str, tuple[ProviderStep, ...]] = field(default_factory=dict)


@dataclass(frozen=True)
class TUIContribution:
    """Screens a provider adds to the install wizard."""

    sections: tuple[tuple[str, str, type[Any]], ...] = ()
    insert_after: str = "network_secrets"
    dcim_panel: type[Any] | None = None


@dataclass(frozen=True)
class AirContribution:
    """DSX Air behavior a provider supplies to the shared simulation wizard."""

    launch_status: AirProviderStatus
    hooks: AirProviderHooks = field(default_factory=AirProviderHooks)
    settings_model: type[BaseModel] = ProviderSettings
    population_panel: type[Any] | None = None


class ProviderDeployContext(Protocol):
    """The deployer surface available to provider deploy hooks."""

    config: NVConfigManagerInstallConfig
    options: DeployOptions
    callback: DeployCallback

    @property
    def k8s(self) -> K8sClient: ...

    @property
    def secrets_state(self) -> Mapping[str, str]: ...

    @property
    def is_rerun(self) -> bool: ...

    @property
    def nats_user(self) -> str: ...

    @property
    def nats_url(self) -> str: ...

    @property
    def provider_state(self) -> dict[str, Any]: ...

    def start_step(self, step_id: str) -> DeployStep: ...
    def finish_step(self, step: DeployStep, status: StepStatus | None = None) -> None: ...
    def skip_step(self, step_id: str, reason: str = "") -> None: ...
    def apply_secret(self, step: DeployStep, name: str, data: dict[str, str]) -> None: ...
    def check_content_diff(
        self, paths: list[Path], pvc_name: str, namespace: str, label: str
    ) -> bool: ...
    def content_hash(self, paths: list[Path]) -> str: ...


class DCIMInstallerProvider:
    """Base class for installer DCIM providers.

    Static contributions are class attributes. Runtime hooks default to no-ops
    so a provider implements only the stages it owns.
    """

    metadata: ClassVar[InstallerProviderMetadata]
    settings_model: ClassVar[type[BaseModel]] = ProviderSettings
    secrets: ClassVar[SecretsContribution] = SecretsContribution()
    images: ClassVar[ImageContribution] = ImageContribution()
    tui: ClassVar[TUIContribution] = TUIContribution()
    air: ClassVar[AirContribution | None] = None

    # -- configuration -------------------------------------------------------

    def settings(self, config: NVConfigManagerInstallConfig) -> BaseModel:
        """Return the validated ``dcim.settings`` block."""
        return self.settings_model.model_validate(config.dcim.settings)

    def validate_config(self, config: NVConfigManagerInstallConfig) -> list[str]:
        """Return provider-specific validation errors."""
        return []

    def prune_yaml(self, data: dict[str, Any]) -> None:
        """Remove inactive provider values from a serialized configuration."""

    # -- secrets -------------------------------------------------------------

    def generate_secret_state(
        self,
        config: NVConfigManagerInstallConfig,
        state: dict[str, str],
        value_of: SecretValue,
    ) -> None:
        """Seed provider secret values before the installer generates the rest."""

    def token_secret_data(self, state: Mapping[str, str]) -> dict[str, str]:
        """Return extra keys written into the DCIM token Secret."""
        return {}

    def openbao_groups(
        self,
        config: NVConfigManagerInstallConfig,
        value_of: SecretValue,
    ) -> dict[str, dict[str, str]]:
        """Return logical OpenBao values for provider-owned path groups."""
        return {}

    def align_openbao_tokens(
        self,
        read_group: Callable[[str], dict[str, str]],
        desired_groups: dict[str, dict[str, str]],
    ) -> None:
        """Reconcile desired provider values with what Vault already holds."""

    # -- helm ----------------------------------------------------------------

    def helm_values(
        self,
        config: NVConfigManagerInstallConfig,
        secrets_state: Mapping[str, str],
        *,
        is_local: bool,
        local_tags: Mapping[str, str] | None,
    ) -> dict[str, Any]:
        """Return Helm values merged over the installer-generated values."""
        return {}

    # -- deploy --------------------------------------------------------------

    def deploy_steps(self) -> DeployStepsContribution:
        """Return provider steps to splice into the core pipeline."""
        return DeployStepsContribution()

    def detect_existing_state(self, ctx: ProviderDeployContext) -> None:
        """Probe the cluster for provider content changes on re-runs."""

    def create_secrets(self, ctx: ProviderDeployContext, step: DeployStep) -> None:
        """Create provider-owned Kubernetes Secrets."""

    def setup_content(self, ctx: ProviderDeployContext) -> None:
        """Load provider content once Vault is populated, before the core content PVCs."""

    def pre_helm_install(self, ctx: ProviderDeployContext) -> None:
        """Run provider steps before the umbrella Helm release."""

    def post_helm_install(self, ctx: ProviderDeployContext) -> None:
        """Run provider steps after the umbrella Helm release."""

    def post_gateway(self, ctx: ProviderDeployContext) -> None:
        """Run provider steps once gateway access is configured."""

    def post_restart(self, ctx: ProviderDeployContext) -> None:
        """Run provider steps after service restarts, before caches refresh."""

    def refresh_caches_needed(self, config: NVConfigManagerInstallConfig) -> bool:
        """Return whether service caches must refresh after provider steps."""
        return False

    def integration_test_env(self, ctx: ProviderDeployContext) -> dict[str, str]:
        """Return environment variables for the in-cluster integration test run."""
        return {}

    def endpoints(
        self, config: NVConfigManagerInstallConfig, hostname: str, proto: str
    ) -> list[str]:
        """Return provider endpoint lines for the deployment summary."""
        return []

    def bootstrap_secret_hints(self, project_root: Path | None) -> list[tuple[str, str, str]]:
        """Return ``(display_name, secret_key, rotation)`` network secrets to pre-populate."""
        return []
