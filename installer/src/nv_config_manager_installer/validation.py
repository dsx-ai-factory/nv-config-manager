# SPDX-FileCopyrightText: Copyright (c) 2024-2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Shared readiness validation for generated output and deployments."""

from __future__ import annotations

from pydantic import ValidationError

from nv_config_manager_installer.schema import NVConfigManagerInstallConfig


class ConfigReadinessError(ValueError):
    """Configuration is structurally valid but not ready for output or deployment."""

    def __init__(self, errors: list[str]) -> None:
        self.errors = errors
        super().__init__(
            "Configuration is not ready:\n" + "\n".join(f"- {error}" for error in errors)
        )


def _format_validation_error(error: dict[str, object]) -> str:
    location = ".".join(str(part) for part in error.get("loc", ()))
    message = str(error["msg"])
    if message.startswith("Value error, "):
        message = message.removeprefix("Value error, ")
    return f"{location}: {message}" if location else message


def _revalidate_config(
    config: NVConfigManagerInstallConfig,
) -> tuple[NVConfigManagerInstallConfig | None, list[str]]:
    try:
        model_type = type(config)
        return model_type.model_validate(config.model_dump(mode="python")), []
    except ValidationError as exc:
        return None, [
            _format_validation_error(error)
            for error in exc.errors(include_url=False, include_input=False)
        ]


def collect_validation_errors(config: NVConfigManagerInstallConfig) -> list[str]:
    """Return structural and deployment-readiness errors without mutating ``config``."""
    validated, errors = _revalidate_config(config)
    if validated is None:
        return errors

    if not validated.cluster.hostname:
        errors.append("cluster.hostname is required")
    if not validated.sites:
        errors.append("At least one site is required")
    if validated.sso.enabled and not validated.sso.issuer_url:
        errors.append("sso.issuer_url is required when SSO is enabled")

    nats = validated.external_services.nats
    if nats.enabled and "replace-me.invalid" in nats.server:
        errors.append("External NATS server must not contain replace-me.invalid")
    nats_placeholders = {
        "queue": nats.queue,
        "Config Manager durable": nats.streams.config_manager.consumer.name,
        "archive durable": nats.streams.config_manager.archive_consumer.name,
        "archive delivery subject": nats.streams.config_manager.archive_consumer.delivery_subject,
        "Nautobot durable": nats.streams.nautobot.consumer.name,
    }
    for field_name, value in nats_placeholders.items():
        if value.startswith("replace-me"):
            errors.append(f"NATS {field_name} must not begin with replace-me")

    if not validated.services.nautobot and validated.content.requires_local_nautobot:
        errors.append(
            "Custom jobs and post-deploy jobs require a local Nautobot deployment "
            "(services.nautobot must be true, or remove content.jobs and "
            "content.run_after_deploy)"
        )
    return errors


def validate_config_readiness(
    config: NVConfigManagerInstallConfig,
) -> NVConfigManagerInstallConfig:
    """Return a fully revalidated copy or raise actionable readiness errors."""
    validated, errors = _revalidate_config(config)
    if validated is not None:
        errors = collect_validation_errors(validated)
    if errors:
        raise ConfigReadinessError(errors)
    assert validated is not None
    return validated
