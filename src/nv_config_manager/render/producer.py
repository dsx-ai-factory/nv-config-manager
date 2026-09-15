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
"""Template updater for template version changes."""

import asyncio
import os

from nv_config_manager_templates.version import TemplateVersion

from nv_config_manager.common.log import LogCategory, get_logger
from nv_config_manager.dcim import dcim_client_session
from nv_config_manager.render.lock import create_lock
from nv_config_manager.render.render import execute_render

logger = get_logger(__name__, category=LogCategory.RENDER)
DEFAULT_TEMPLATE_UPDATE_CONCURRENCY = 8
DEFAULT_TEMPLATE_UPDATE_LOCK_TIMEOUT = 120


async def load_stale_renders(desired_version: TemplateVersion | str) -> list[str]:
    """Return device IDs where the provider records an old template version."""
    desired_template_version = TemplateVersion.parse(desired_version)
    async with dcim_client_session() as dcim_client:
        existing_renders = await dcim_client.get_render_template_versions()

    stale: list[str] = []
    for entry in existing_renders:
        if not entry.template_version:
            continue
        template_version = TemplateVersion.parse(entry.template_version)
        if desired_template_version > template_version:
            stale.append(entry.device_id)

    return stale


def template_update_concurrency() -> int:
    """Return the configured template updater render concurrency."""
    configured = os.getenv("NV_CONFIG_MANAGER_TEMPLATE_UPDATE_CONCURRENCY")
    if configured is None:
        return DEFAULT_TEMPLATE_UPDATE_CONCURRENCY
    try:
        return max(1, int(configured))
    except ValueError:
        logger.warning(
            "Invalid NV_CONFIG_MANAGER_TEMPLATE_UPDATE_CONCURRENCY=%s, using default %s",
            configured,
            DEFAULT_TEMPLATE_UPDATE_CONCURRENCY,
        )
        return DEFAULT_TEMPLATE_UPDATE_CONCURRENCY


def template_update_lock_timeout() -> int:
    """Return the configured per-device render lock wait timeout."""
    configured = os.getenv("NV_CONFIG_MANAGER_TEMPLATE_UPDATE_LOCK_TIMEOUT")
    if configured is None:
        return DEFAULT_TEMPLATE_UPDATE_LOCK_TIMEOUT
    try:
        return max(1, int(configured))
    except ValueError:
        logger.warning(
            "Invalid NV_CONFIG_MANAGER_TEMPLATE_UPDATE_LOCK_TIMEOUT=%s, using default %s",
            configured,
            DEFAULT_TEMPLATE_UPDATE_LOCK_TIMEOUT,
        )
        return DEFAULT_TEMPLATE_UPDATE_LOCK_TIMEOUT


async def _render_stale_device(
    device_uuid: str,
    desired_version: TemplateVersion,
    semaphore: asyncio.Semaphore,
    lock_timeout: int,
) -> None:
    """Render one stale device while holding its per-device render lock."""
    async with semaphore:
        lock = await create_lock(device_uuid, blocking=True, blocking_timeout=lock_timeout)
        acquired = await lock.acquire()
        if not acquired:
            raise RuntimeError(
                f"Could not acquire render lock for {device_uuid} within {lock_timeout}s."
            )

        try:
            await execute_render(
                device_uuid,
                f"Template version change: {desired_version}",
                "template-updater",
            )
        except Exception:
            logger.exception("Template update render failed for %s", device_uuid)
            raise
        finally:
            await lock.release()


async def update_stale_renders(
    desired_version: TemplateVersion | str | None = None,
    concurrency: int | None = None,
    lock_timeout: int | None = None,
) -> int:
    """Render all stale devices directly with bounded parallelism."""
    desired_template_version = (
        TemplateVersion.current()
        if desired_version is None
        else TemplateVersion.parse(desired_version)
    )
    stale_renders = await load_stale_renders(desired_template_version)
    if not stale_renders:
        logger.info("No change in template version from existing renders.")
        return 0

    render_concurrency = concurrency or template_update_concurrency()
    render_lock_timeout = lock_timeout or template_update_lock_timeout()
    logger.info(
        "Rendering %s outdated configurations with concurrency %s.",
        len(stale_renders),
        render_concurrency,
    )

    semaphore = asyncio.Semaphore(render_concurrency)
    results = await asyncio.gather(
        *(
            _render_stale_device(
                device_uuid,
                desired_template_version,
                semaphore,
                render_lock_timeout,
            )
            for device_uuid in stale_renders
        ),
        return_exceptions=True,
    )

    failures = [
        (device_uuid, result)
        for device_uuid, result in zip(stale_renders, results, strict=True)
        if isinstance(result, Exception)
    ]
    if failures:
        for device_uuid, exc in failures:
            logger.error("Template update render failed for %s: %s", device_uuid, exc)
        logger.warning(
            "Template update completed with %s failed renders out of %s stale devices.",
            len(failures),
            len(stale_renders),
        )

    successful_renders = len(stale_renders) - len(failures)
    logger.info("Template update rendered %s outdated configurations.", successful_renders)
    return successful_renders


async def main() -> None:
    """Render devices last rendered with an outdated template version."""
    await update_stale_renders()


def cli_main() -> None:
    """CLI entrypoint for template updater."""
    asyncio.run(main())


if __name__ == "__main__":
    cli_main()
