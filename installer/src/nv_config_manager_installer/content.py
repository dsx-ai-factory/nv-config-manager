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
"""Content management for template plugins and provider-owned custom jobs.

Handles bundling job directories/tarballs and template plugin directories/tarballs
into consolidated staging areas that the installer can push to PVCs. Supports
incremental updates (add new jobs/templates without full redeploy).
"""

from __future__ import annotations

import shutil
import tarfile
import tempfile
from pathlib import Path

from nv_config_manager_installer.schema import NVConfigManagerInstallConfig

# Directories to exclude when copying job/template content
_EXCLUDE_DIRS = {".venv", "__pycache__", ".git", "node_modules", ".mypy_cache", ".ruff_cache"}


def _copy_content(src: Path, dest: Path) -> None:
    """Copy a directory tree, excluding common noise directories."""
    if not src.is_dir():
        return
    for item in src.iterdir():
        if item.name in _EXCLUDE_DIRS:
            continue
        target = dest / item.name
        if item.is_dir():
            shutil.copytree(
                item, target, dirs_exist_ok=True, ignore=shutil.ignore_patterns(*_EXCLUDE_DIRS)
            )
        else:
            shutil.copy2(item, target)


def _extract_tarball(tarball: Path, dest: Path) -> None:
    """Extract a regular-file-only .tar.gz into the destination directory."""
    with tarfile.open(tarball, "r:gz") as tf:
        members = tf.getmembers()
        for member in members:
            member_path = Path(member.name)
            if member_path.is_absolute() or ".." in member_path.parts:
                raise ValueError(f"Archive contains an unsafe path: {member.name}")
            if not (member.isdir() or member.isfile()):
                raise ValueError(f"Archive contains unsupported entry: {member.name}")
        tf.extractall(path=dest, members=members, filter="data")


def _job_paths(config: NVConfigManagerInstallConfig) -> list[str]:
    """Return custom job paths declared under ``dcim.settings.jobs``."""
    entries = config.dcim.settings.get("jobs") or []
    return [str(entry.get("path", "")) for entry in entries if isinstance(entry, dict)]


def stage_jobs(config: NVConfigManagerInstallConfig, staging_dir: Path) -> Path:
    """Stage all custom job content into a consolidated directory.

    Processes each entry in ``dcim.settings.jobs``:
    - Directories are copied (excluding noise)
    - .tar.gz files are extracted

    Args:
        config: The install config.
        staging_dir: Parent directory for staging (a temp dir is fine).

    Returns:
        Path to the staged jobs directory.
    """
    jobs_dir = staging_dir / "custom-jobs"
    jobs_dir.mkdir(parents=True, exist_ok=True)

    for job_path in _job_paths(config):
        src = Path(job_path)
        if not src.exists():
            continue
        if src.is_dir():
            _copy_content(src, jobs_dir / src.name)
        elif src.suffix == ".gz" and src.stem.endswith(".tar"):
            _extract_tarball(src, jobs_dir)

    return jobs_dir


def stage_template_plugins(config: NVConfigManagerInstallConfig, staging_dir: Path) -> Path:
    """Stage all template plugin content into a consolidated directory.

    Args:
        config: The install config.
        staging_dir: Parent directory for staging.

    Returns:
        Path to the staged templates directory.
    """
    tpl_dir = staging_dir / "template-plugins"
    tpl_dir.mkdir(parents=True, exist_ok=True)

    for tpl_entry in config.content.template_plugins:
        src = Path(tpl_entry.path)
        if not src.exists():
            continue
        if src.is_dir():
            _copy_content(src, tpl_dir / src.name)
        elif src.suffix == ".gz" and src.stem.endswith(".tar"):
            _extract_tarball(src, tpl_dir)

    return tpl_dir


def create_content_tarball(content_dir: Path, output_path: Path) -> Path:
    """Create a .tar.gz from a staged content directory.

    Args:
        content_dir: The staged directory to archive.
        output_path: Where to write the tarball.

    Returns:
        Path to the created tarball.
    """
    with tarfile.open(output_path, "w:gz") as tf:
        for item in content_dir.iterdir():
            tf.add(item, arcname=item.name)
    return output_path


def stage_all_content(config: NVConfigManagerInstallConfig, output_dir: Path) -> dict[str, Path]:
    """Stage and bundle all content (jobs + templates) for deployment.

    Args:
        config: The install config.
        output_dir: Directory to write tarballs to.

    Returns:
        Dict with keys 'jobs_tarball' and 'templates_tarball' pointing to
        the created tarballs (only present if content exists).
    """
    output_dir.mkdir(parents=True, exist_ok=True)
    result: dict[str, Path] = {}

    with tempfile.TemporaryDirectory() as tmpdir:
        staging = Path(tmpdir)

        if _job_paths(config):
            jobs_dir = stage_jobs(config, staging)
            if any(jobs_dir.iterdir()):
                tarball = output_dir / "custom-jobs.tar.gz"
                create_content_tarball(jobs_dir, tarball)
                result["jobs_tarball"] = tarball

        if config.content.template_plugins:
            tpl_dir = stage_template_plugins(config, staging)
            if any(tpl_dir.iterdir()):
                tarball = output_dir / "template-plugins.tar.gz"
                create_content_tarball(tpl_dir, tarball)
                result["templates_tarball"] = tarball

    return result
