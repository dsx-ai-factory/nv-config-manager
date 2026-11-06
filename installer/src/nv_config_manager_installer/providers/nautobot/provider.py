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
"""Installer provider for the bundled Nautobot DCIM stack."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any, ClassVar

from nv_config_manager_installer.accounts import scan_bootstrap_secrets
from nv_config_manager_installer.deployer import DeployStep
from nv_config_manager_installer.providers.api import (
    AirContribution,
    DCIMInstallerProvider,
    DeployStepsContribution,
    ImageContribution,
    InstallerProviderMetadata,
    ProviderDeployContext,
    SecretsContribution,
    SecretValue,
    TUIContribution,
)
from nv_config_manager_installer.providers.nautobot import air, deploy, helm_values, secrets
from nv_config_manager_installer.providers.nautobot.settings import (
    PROVIDER_NAME,
    TOKEN_SECRET_NAME,
    NautobotSettings,
    nautobot_settings,
)
from nv_config_manager_installer.providers.nautobot.tui import IngestDataScreen
from nv_config_manager_installer.schema import NVConfigManagerInstallConfig
from nv_config_manager_installer.secrets import (
    _generate_nats_config_password,
    _validate_nats_config_password,
)

_IMAGE_NAME = "nv-config-manager-nautobot"


class NautobotInstallerProvider(DCIMInstallerProvider):
    """Deploy and configure the bundled Nautobot through the common installer."""

    metadata: ClassVar[InstallerProviderMetadata] = InstallerProviderMetadata(
        name=PROVIDER_NAME,
        display_name="Nautobot",
        token_secret_name=TOKEN_SECRET_NAME,
        server_placeholder="https://nautobot.example.com",
    )
    settings_model: ClassVar[type[NautobotSettings]] = NautobotSettings
    secrets: ClassVar[SecretsContribution] = secrets.SECRETS
    images: ClassVar[ImageContribution] = ImageContribution(
        local_builds=((_IMAGE_NAME, "build/nautobot.Dockerfile", "components/nautobot", None),),
        local_only_builds=frozenset({_IMAGE_NAME}),
        image_keys=(("nautobot", _IMAGE_NAME),),
        override_keys=(
            ("nautobot", f"nvcr.io/nvidian/cfa/{_IMAGE_NAME}"),
            ("nautobotNginx", "docker.io/nginxinc/nginx-unprivileged"),
        ),
        split_image_defaults={
            "nautobotNginx": (
                ("nautobot", "nginx", "image"),
                "docker.io/nginxinc/nginx-unprivileged",
                "1.27",
            ),
        },
    )
    tui: ClassVar[TUIContribution] = TUIContribution(
        sections=(("ingest_data", "Ingest Data", IngestDataScreen),),
    )
    air: ClassVar[AirContribution | None] = AirContribution(
        launch_status=air.LAUNCH_STATUS,
        hooks=air.NautobotAirHooks(),
        population_panel=air.NautobotPopulationPanel,
    )

    # -- configuration -------------------------------------------------------

    def validate_config(self, config: NVConfigManagerInstallConfig) -> list[str]:
        if not config.dcim.local and nautobot_settings(config).requires_local:
            return [
                "Custom jobs and post-deploy jobs require a local Nautobot deployment "
                "(dcim.local must be true). Remove dcim.settings.jobs and "
                "dcim.settings.run_after_deploy or switch to local Nautobot."
            ]
        return []

    # -- secrets -------------------------------------------------------------

    def generate_secret_state(
        self,
        config: NVConfigManagerInstallConfig,
        state: dict[str, str],
        value_of: SecretValue,
    ) -> None:
        secrets.generate_secret_state(config, state, value_of)

    def token_secret_data(self, state: Mapping[str, str]) -> dict[str, str]:
        return secrets.token_secret_data(state)

    def openbao_groups(
        self,
        config: NVConfigManagerInstallConfig,
        value_of: SecretValue,
    ) -> dict[str, dict[str, str]]:
        return secrets.openbao_groups(
            config,
            value_of,
            nats_password=_generate_nats_config_password,
            validate_nats_password=_validate_nats_config_password,
        )

    def align_openbao_tokens(
        self,
        read_group: Callable[[str], dict[str, str]],
        desired_groups: dict[str, dict[str, str]],
    ) -> None:
        secrets.align_openbao_tokens(read_group, desired_groups)

    # -- helm ----------------------------------------------------------------

    def helm_values(
        self,
        config: NVConfigManagerInstallConfig,
        secrets_state: Mapping[str, str],
        *,
        is_local: bool,
        local_tags: Mapping[str, str] | None,
    ) -> dict[str, Any]:
        return helm_values.build_values(config, nautobot_settings(config))

    # -- deploy --------------------------------------------------------------

    def deploy_steps(self) -> DeployStepsContribution:
        return deploy.STEPS

    def detect_existing_state(self, ctx: ProviderDeployContext) -> None:
        deploy.detect_existing_state(ctx)

    def create_secrets(self, ctx: ProviderDeployContext, step: DeployStep) -> None:
        deploy.create_secrets(ctx, step)

    def setup_content(self, ctx: ProviderDeployContext) -> None:
        deploy.setup_jobs_pvc(ctx)

    def post_gateway(self, ctx: ProviderDeployContext) -> None:
        deploy.restart_nautobot(ctx)

    def post_restart(self, ctx: ProviderDeployContext) -> None:
        deploy.run_post_deploy_jobs(ctx)

    def refresh_caches_needed(self, config: NVConfigManagerInstallConfig) -> bool:
        return bool(nautobot_settings(config).run_after_deploy)

    def integration_test_env(self, ctx: ProviderDeployContext) -> dict[str, str]:
        return deploy.integration_test_env(ctx)

    def endpoints(
        self, config: NVConfigManagerInstallConfig, hostname: str, proto: str
    ) -> list[str]:
        return deploy.endpoints(config.dcim.local, config.dcim.server, hostname, proto)

    def bootstrap_secret_hints(self, project_root: Path | None) -> list[tuple[str, str, str]]:
        return scan_bootstrap_secrets(project_root)
