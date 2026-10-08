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
"""Provider-neutral CLI commands for local template iteration."""

# pylint: disable=too-many-arguments
from __future__ import annotations

import asyncio
import json
import os
import sys
import tomllib
from collections.abc import Mapping
from typing import Any

import click
from nv_config_manager_dcim import (
    RenderData,
    RenderDataRequest,
    RenderDataRequirement,
    create_dcim_client,
)

from nv_config_manager_templates.render import Renderer


def _read_json_mapping(path: str, description: str) -> Mapping[str, Any]:
    """Read a JSON mapping, rejecting values unsuitable for template data."""
    with open(path, encoding="utf-8") as file:
        data = json.load(file)
    if not isinstance(data, Mapping):
        raise click.ClickException(f"{description} in {path} must be a JSON object")
    return data


def _read_cached_render_data(path: str) -> RenderData:
    """Read one portable provider-neutral render-data cache envelope."""
    try:
        return RenderData.from_cache(_read_json_mapping(path, "render-data cache"))
    except ValueError as exc:
        raise click.ClickException(f"Invalid render-data cache {path}: {exc}") from exc


def _read_provider_config(path: str) -> tuple[str, Mapping[str, Any]]:
    """Read template-cli's service-level provider TOML configuration."""
    try:
        with open(path, "rb") as file:
            document = tomllib.load(file)
    except (OSError, tomllib.TOMLDecodeError) as exc:
        raise click.ClickException(f"Unable to read provider config {path}: {exc}") from exc
    provider = document.get("provider")
    if not isinstance(provider, Mapping):
        raise click.ClickException("Provider config requires a [provider] table")
    name = provider.get("name")
    settings = provider.get("settings", {})
    if not isinstance(name, str) or not name.strip():
        raise click.ClickException("Provider config requires provider.name")
    if not isinstance(settings, Mapping):
        raise click.ClickException("Provider config [provider.settings] must be a table")
    return name.strip(), settings


async def _query_render_data(
    provider_name: str,
    provider_settings: Mapping[str, Any],
    device_id: str | None,
    device_name: str | None,
    plugin_data_requirements: Mapping[str, RenderDataRequirement],
) -> RenderData:
    """Load provider-neutral render data directly through the DCIM SDK."""
    client = create_dcim_client(provider_name, provider_settings)
    try:
        if device_id is None:
            if device_name is None:
                raise click.ClickException("Must provide either --device-id or --device-name")
            device_id = (await client.get_device_selection_by_name(device_name)).id
        return await client.get_render_data(
            RenderDataRequest(
                device_id=device_id,
                plugin_data_requirements=plugin_data_requirements,
            )
        )
    finally:
        await client.close()


def _load_render_data(
    provider_config: str,
    device_id: str | None,
    device_name: str | None,
    plugin_data_requirements: Mapping[str, RenderDataRequirement],
) -> RenderData:
    """Synchronously load live render data using an explicit TOML provider config."""
    provider_name, provider_settings = _read_provider_config(provider_config)
    try:
        return asyncio.run(
            _query_render_data(
                provider_name,
                provider_settings,
                device_id,
                device_name,
                plugin_data_requirements,
            )
        )
    except click.ClickException:
        raise
    except Exception as exc:  # noqa: BLE001 - provider implementations are external
        raise click.ClickException(f"Unable to query DCIM provider {provider_name}: {exc}") from exc


def _exception_handler(
    exception_type: type[BaseException],
    exception: BaseException,
    _traceback: object,
) -> None:
    """Print concise CLI failures unless the caller requested debug output."""
    click.echo(f"{exception_type.__name__}: {exception} Use --debug for more detail.", err=True)


def _configure_local_render(vault: bool) -> None:
    """Disable Vault lookups for local rendering unless explicitly requested."""
    if vault:
        return
    os.environ["NV_CONFIG_MANAGER_SKIP_VAULT"] = "1"
    os.environ["NV_CONFIG_MANAGER_DEV_SALT"] = "H0QFj2rx"
    os.environ["NV_CONFIG_MANAGER_DEV_SALT_T7"] = "0"


def _load_render_data_from_options(
    *,
    provider_config: str | None,
    device_id: str | None,
    device_name: str | None,
    cached_render_data: str | None,
    plugin_data_requirements: Mapping[str, RenderDataRequirement],
) -> RenderData:
    """Load a portable cache or provider data."""
    if cached_render_data:
        return _read_cached_render_data(cached_render_data)

    if provider_config is None:
        raise click.ClickException("--provider-config is required for a live query")
    return _load_render_data(provider_config, device_id, device_name, plugin_data_requirements)


@click.group()
def cli() -> None:
    """Template CLI operations using the configured DCIM provider."""


@cli.command()
@click.option(
    "--provider-config",
    help="TOML file containing [provider] name and [provider.settings]",
)
@click.option("--device-id", help="provider device identifier")
@click.option(
    "--device-name",
    "--hostname",
    "device_name",
    help="provider device display name; --hostname remains an alias",
)
@click.option("--cached-render-data", help="portable RenderData cache to use")
@click.option("--debug", is_flag=True, default=False, help="display tracebacks in error output")
def list_entrypoints(
    provider_config: str | None,
    device_id: str | None,
    device_name: str | None,
    cached_render_data: str | None,
    debug: bool,
) -> None:
    """List entrypoint templates for one device."""
    if not debug:
        sys.excepthook = _exception_handler
    renderer = Renderer()
    if cached_render_data:
        device_data = _read_cached_render_data(cached_render_data).device
    else:
        if provider_config is None:
            raise click.ClickException("--provider-config is required for a live query")
        device_data = _load_render_data(
            provider_config,
            device_id,
            device_name,
            renderer.plugin_data_requirements,
        ).device

    click.echo("Templates:")
    for template in renderer.list_entrypoints(device_data):
        click.echo(f"\t{template}")


@cli.command()
@click.option(
    "--provider-config",
    help="TOML file containing [provider] name and [provider.settings]",
)
@click.option("--device-id", help="provider device identifier")
@click.option(
    "--device-name",
    "--hostname",
    "device_name",
    help="provider device display name; --hostname remains an alias",
)
@click.option("--cached-render-data", help="portable RenderData cache to use")
@click.option("--entrypoint", help="entrypoint template to render")
@click.option("--template", help="full template path to render")
@click.option("--output-file", help="optional output file for rendered output")
@click.option("--vault", is_flag=True, default=False, help="perform Vault lookups during render")
@click.option("--debug", is_flag=True, default=False, help="display tracebacks in error output")
def render(
    provider_config: str | None,
    device_id: str | None,
    device_name: str | None,
    cached_render_data: str | None,
    entrypoint: str | None,
    template: str | None,
    output_file: str | None,
    vault: bool,
    debug: bool,
) -> None:
    """Render one template using provider-neutral render data."""
    if not debug:
        sys.excepthook = _exception_handler
    _configure_local_render(vault)
    if not (template or entrypoint):
        raise click.ClickException("Must supply either an entrypoint or full template path")

    renderer = Renderer()
    render_data = _load_render_data_from_options(
        provider_config=provider_config,
        device_id=device_id,
        device_name=device_name,
        cached_render_data=cached_render_data,
        plugin_data_requirements=renderer.plugin_data_requirements,
    )
    render_data = renderer.load_data(render_data)

    if template is None:
        try:
            template = next(
                candidate
                for candidate in renderer.list_entrypoints(render_data.device)
                if candidate.endswith(f"/{entrypoint}")
            )
        except StopIteration as exc:
            raise click.ClickException(
                f"No entrypoint template found with name {entrypoint}"
            ) from exc

    output = renderer.render(template, render_data)
    if output_file:
        with open(output_file, "w", encoding="utf-8") as file:
            file.write(output)
    else:
        click.echo(output)


@cli.command()
@click.option(
    "--provider-config",
    required=True,
    help="TOML file containing [provider] name and [provider.settings]",
)
@click.option("--device-id", help="provider device identifier")
@click.option(
    "--device-name",
    "--hostname",
    "device_name",
    help="provider device display name; --hostname remains an alias",
)
@click.option("--output-render-data-file", help="portable RenderData cache output file")
@click.option("--debug", is_flag=True, default=False, help="display tracebacks in error output")
def cache_query(
    provider_config: str,
    device_id: str | None,
    device_name: str | None,
    output_render_data_file: str | None,
    debug: bool,
) -> None:
    """Cache render data returned by the selected DCIM provider."""
    if not debug:
        sys.excepthook = _exception_handler
    if output_render_data_file is None:
        raise click.ClickException("--output-render-data-file is required")

    click.echo("Querying the configured DCIM provider for render data.")
    renderer = Renderer()
    render_data = _load_render_data(
        provider_config,
        device_id,
        device_name,
        renderer.plugin_data_requirements,
    )
    with open(output_render_data_file, "w", encoding="utf-8") as file:
        json.dump(render_data.to_cache(), file, indent=4)
    click.echo(f"Portable render data has been cached to {output_render_data_file}.")


def main() -> None:
    """CLI entrypoint."""
    cli()


if __name__ == "__main__":
    main()
