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
"""Replacing runtime trust clears inherited bundles and individual anchors."""

import runpy
from pathlib import Path

import certifi
import pytest

TOOL = Path(__file__).resolve().parents[2] / "build/refresh-rootfs-ca.py"


def source_store(source: Path) -> None:
    bundle = source / "etc/ssl/certs/ca-certificates.crt"
    bundle.parent.mkdir(parents=True)
    bundle.write_bytes(Path(certifi.where()).read_bytes())
    (source / "usr/share/ca-certificates").mkdir(parents=True)


def test_replaces_all_linux_stores_without_changing_the_binary(tmp_path: Path) -> None:
    tool = runpy.run_path(str(TOOL))
    root, source = tmp_path / "runtime", tmp_path / "source"
    source_store(source)
    for relative in tool["TRUST_PATHS"]:
        path = root / relative
        if relative.endswith(("/certs", "/ca-certificates")):
            path /= "stale.crt"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("inherited untrusted certificate")
    binary = root / "bin/oauth2-proxy"
    binary.parent.mkdir()
    binary.write_bytes(b"original upstream binary")

    tool["refresh"](root, source)

    assert binary.read_bytes() == b"original upstream binary"
    assert not list(root.rglob("stale.crt"))
    assert (root / "etc/ssl/certs/ca-certificates.crt").read_bytes() == Path(
        certifi.where()
    ).read_bytes()
    for relative in tool["TRUST_PATHS"]:
        if relative not in tool["FRESH_DIRECTORIES"]:
            assert not (root / relative).exists()


def test_rejects_bad_replacement_trust(tmp_path: Path) -> None:
    tool = runpy.run_path(str(TOOL))
    root, source = tmp_path / "runtime", tmp_path / "source"
    source_store(source)
    (source / "etc/ssl/certs/ca-certificates.crt").write_text("not a CA bundle")
    with pytest.raises(SystemExit, match="Invalid CA bundle"):
        tool["refresh"](root, source)


def test_does_not_follow_runtime_parents_outside_export(tmp_path: Path) -> None:
    tool = runpy.run_path(str(TOOL))
    root, source = tmp_path / "runtime", tmp_path / "source"
    source_store(source)
    root.mkdir()
    (root / "etc").symlink_to("../../outside")
    with pytest.raises(SystemExit, match="escapes image export"):
        tool["refresh"](root, source)


def test_refuses_to_replace_the_source_store(tmp_path: Path) -> None:
    tool = runpy.run_path(str(TOOL))
    with pytest.raises(ValueError, match="separate"):
        tool["refresh"](tmp_path, tmp_path)
