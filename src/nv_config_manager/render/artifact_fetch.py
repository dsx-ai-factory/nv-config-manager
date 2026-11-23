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
"""Fetch and digest-verify a plugin artifact from Template Preview.

See SRD FR-244, SADD §3.1 principle 3 / §9.6: Template Preview hosts
/internal/artifacts/{digest}; NVCM render pods are the client. Run as an
init container (console script `nv-config-manager-render-fetch-plugin-
artifact`) at pod startup -- it reads the currently-active digest from the
same activation-state ConfigMap the activation endpoint writes to, so a
pod started by a rolling restart installs whatever was just activated,
without the activation endpoint itself needing to know anything about
plugin installation.

No dependency on Template Preview's control-plane service layer -- this
only needs the artifact-storage endpoint, which serves any digest already
`put()` into ArtifactStore regardless of how it got there.
"""

from __future__ import annotations

import hashlib
import os
import re
import shutil
import sys
import tarfile
import zipfile
from io import BytesIO
from pathlib import Path

import requests

from nv_config_manager.common.log import LogCategory, get_logger
from nv_config_manager.render.activation_state import ActivationStateStore, artifacts_to_install

logger = get_logger(__name__, category=LogCategory.RENDER_API)


class ArtifactFetchError(Exception):
    """Raised when a plugin artifact can't be fetched, verified, or extracted."""


def fetch_and_verify_artifact(
    url: str,
    digest: str,
    archive_checksum: str,
    dest_dir: str,
    token: str | None = None,
    timeout_seconds: float = 60,
) -> None:
    """Download the artifact at `url` and extract it into `dest_dir`, after
    verifying the downloaded bytes hash to exactly `archive_checksum`.

    `digest` (a canonicalized-tree content digest) names the plugin and is
    used only for logging here -- it is not a hash of any byte sequence
    that crosses the wire, so it can't be what a downloaded archive's raw
    bytes are checked against. `archive_checksum` is that hash; verify
    against it, not `digest`.

    `url` comes directly from the activation request's per-plugin
    ArtifactLocation (Template Preview computes it, e.g.
    "https://template-preview/internal/artifacts/{digest}") -- not
    reconstructed here from a separately-configured base URL, since
    Template Preview already knows its own address and there's no reason
    for NVCM to guess at the same URL shape independently.

    Raises ArtifactFetchError on any failure (network, checksum mismatch,
    unsupported archive format, unsafe archive contents) -- callers should
    treat this as fatal for pod startup, the same way a failed static
    plugin install would be today.
    """
    headers = {"Authorization": f"Bearer {token}"} if token else {}

    try:
        resp = requests.get(url, headers=headers, timeout=timeout_seconds)
    except requests.RequestException as exc:
        raise ArtifactFetchError(f"Failed to fetch artifact {digest!r} from {url}: {exc}") from exc

    if resp.status_code != 200:
        raise ArtifactFetchError(
            f"Artifact fetch for {digest!r} returned HTTP {resp.status_code}: {url}"
        )

    content = resp.content
    actual_checksum = f"sha256:{hashlib.sha256(content).hexdigest()}"
    if actual_checksum != archive_checksum:
        raise ArtifactFetchError(
            f"Checksum mismatch for {url}: expected {archive_checksum!r}, got {actual_checksum!r}"
        )

    _extract_archive(content, dest_dir)
    logger.info("Fetched and extracted plugin artifact %s into %s", digest, dest_dir)


def _extract_archive(content: bytes, dest_dir: str) -> None:
    """Extract a zip or tar archive's regular files into dest_dir.

    Minimal safety only (reject absolute paths and '..' traversal) -- the
    content has already been digest-verified against a value the
    activation endpoint recorded, so this isn't defending against a
    malicious artifact so much as a corrupted or malformed one. Full
    archive-safety validation happens on the Template Preview side at
    registration time (SRD FR-201/FR-210), not here.
    """
    buf = BytesIO(content)
    dest = Path(dest_dir)
    dest.mkdir(parents=True, exist_ok=True)

    if zipfile.is_zipfile(buf):
        buf.seek(0)
        with zipfile.ZipFile(buf) as zf:
            for info in zf.infolist():
                if info.is_dir():
                    continue
                _write_member(dest, info.filename, zf.read(info))
        return

    buf.seek(0)
    try:
        with tarfile.open(fileobj=buf) as tf:
            for member in tf.getmembers():
                if not member.isfile():
                    continue
                extracted = tf.extractfile(member)
                if extracted is None:
                    continue
                _write_member(dest, member.name, extracted.read())
        return
    except tarfile.TarError as exc:
        raise ArtifactFetchError(f"Artifact is neither a valid zip nor tar archive: {exc}") from exc


def _write_member(dest_dir: Path, member_path: str, content: bytes) -> None:
    normalized = member_path.replace("\\", "/")
    if normalized.startswith("/") or any(part == ".." for part in normalized.split("/")):
        raise ArtifactFetchError(f"Refusing unsafe archive member path: {member_path!r}")
    target = dest_dir / normalized
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(content)


_PLUGIN_ID_PATTERN = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]*")
# Subdirectory of the install path holding dynamically activated plugins, one folder per plugin id. The
# renderer reads it through NVCM_DYNAMIC_PLUGINS_DIR (set by Helm); static plugins keep their own layout.
DYNAMIC_PLUGINS_SUBDIR = "_dynamic"


def _plugin_install_dir(dest_dir: str, plugin_id: object) -> str:
    """Where one plugin is extracted: ``<dest>/_dynamic/<plugin id>``.

    Each plugin gets its own folder so several plugins in one set cannot overwrite each other's
    ``plugin.yaml``. The id arrives in the activation request, so it is checked to be a single safe path
    component before it is used to build a path.
    """
    if (
        not isinstance(plugin_id, str)
        or not _PLUGIN_ID_PATTERN.fullmatch(plugin_id)
        or ".." in plugin_id
    ):
        raise ArtifactFetchError(f"Refusing unsafe plugin id {plugin_id!r}")
    return os.path.join(dest_dir, DYNAMIC_PLUGINS_SUBDIR, plugin_id)


def cli_main() -> None:
    """Console-script entrypoint for the init container. Reads the per-plugin
    artifact locations to install (the in-flight activation's, else the active
    set's; see ``artifacts_to_install``) from the activation-state ConfigMap
    and fetches each one; a no-op (exit 0) if no plugin set has
    ever been activated, so pod startup isn't blocked in the common case
    where this feature is enabled but unused.

    A plugin set can contain more than one plugin (SADD §9.6) -- there is
    no single blob to fetch for "the active digest" itself; that digest
    identifies the *combination*, not a fetchable artifact. Each plugin's
    own artifact is fetched and extracted individually, same as today's
    static mechanism combines multiple plugin images into one /opt/plugins.
    """
    configmap_name = os.environ["RENDER_ACTIVATION_CONFIGMAP"]
    namespace = os.environ["RENDER_ACTIVATION_NAMESPACE"]
    token = os.environ.get("TEMPLATE_PREVIEW_ARTIFACT_TOKEN") or None
    dest_dir = os.environ.get("RENDER_PLUGIN_INSTALL_PATH", "/opt/plugins")

    store = ActivationStateStore(configmap_name, namespace)
    state = store.get()
    artifacts = artifacts_to_install(state)

    # Leave only the selected set installed. If this init container ran before in the same pod (a retry
    # after a failed activation), its files are still on the volume and the loader would load them, even
    # though a FAILED activation leaves the previous set in place. Only _dynamic is cleared: static
    # plugins live elsewhere under dest_dir. Not ignoring errors: if it cannot be cleared, fail the
    # init container rather than start with files that are not part of the set.
    dynamic_root = os.path.join(dest_dir, DYNAMIC_PLUGINS_SUBDIR)
    if os.path.isdir(dynamic_root):
        shutil.rmtree(dynamic_root)

    if not artifacts:
        logger.info("No plugin set has been activated yet -- nothing to fetch.")
        return

    for artifact in artifacts:
        digest = artifact["digest"]
        try:
            fetch_and_verify_artifact(
                artifact["url"],
                digest,
                artifact["archive_checksum"],
                _plugin_install_dir(dest_dir, artifact.get("plugin_id")),
                token,
            )
        except ArtifactFetchError:
            logger.exception(
                "Failed to install plugin %s (digest %s)", artifact.get("plugin_id"), digest
            )
            sys.exit(1)


if __name__ == "__main__":
    cli_main()
