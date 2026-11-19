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
"""Helm rendering tests for exact NATS stream limits across initializers and services."""

from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path
from typing import Any

import pytest
from ruamel.yaml import YAML

_CHART_DIR = Path(__file__).resolve().parents[2] / "deploy" / "helm"
_MAX_BYTES_SETTING = "nautobotNats.natsReady.streamMaxBytes"
_BINARY_UNITS = {"": 1, "K": 1024, "M": 1024**2, "G": 1024**3, "T": 1024**4}


def _render(
    limit: int | None, *, use_cli: bool = True, secret_method: str = "eso"
) -> subprocess.CompletedProcess[str]:
    if shutil.which("helm") is None:
        pytest.skip("helm binary not available")
    if not (_CHART_DIR / "charts").is_dir():
        pytest.skip("helm chart dependencies not vendored (run `helm dependency build`)")

    command = [
        "helm",
        "template",
        "test",
        str(_CHART_DIR),
        "--values",
        str(_CHART_DIR / "values-ci.yaml"),
        "--set",
        f"nautobotNats.natsReady.useNatsCli={str(use_cli).lower()}",
        "--set",
        f"secrets.method={secret_method}",
    ]
    if limit is not None:
        command += ["--set", f"{_MAX_BYTES_SETTING}={limit}"]
    return subprocess.run(command, capture_output=True, text=True, check=False)


def _initializer(rendered: str) -> dict[str, Any]:
    for document in YAML(typ="safe").load_all(rendered):
        if (
            document
            and document.get("kind") == "Deployment"
            and document["metadata"]["name"] == "test-nv-config-manager-nats-box"
        ):
            return document["spec"]["template"]["spec"]["initContainers"][0]
    pytest.fail("NATS initializer Deployment not rendered")


def _assert_limit(rendered: str, expected_bytes: int) -> None:
    script = _initializer(rendered)["args"][0]
    match = re.search(r'^\s*MAX_BYTES="(-?\d+)([KMGT]?)"$', script, re.MULTILINE)
    assert match is not None, script
    coefficient, suffix = int(match[1]), match[2]
    assert -1 <= coefficient <= 2**31 - 1
    assert coefficient * _BINARY_UNITS[suffix] == expected_bytes
    if expected_bytes <= 2**31 - 1:
        assert suffix == ""
    assert script.count('--max-bytes="$MAX_BYTES"') == 4

    raw_limits = re.findall(r"^\s*local_stream_max_bytes = (-?\d+)\s*$", rendered, re.MULTILINE)
    assert raw_limits
    assert {int(limit) for limit in raw_limits} == {expected_bytes}


@pytest.mark.parametrize(
    "limit",
    [None, 123456789, 50 * 1024**2, 256 * 1024**2, 2**31 - 1, 2**31, 5 * 1024**4, -1],
    ids=[
        "default-4-GiB",
        "raw-bytes",
        "50-MiB",
        "256-MiB",
        "int32-max",
        "2-GiB",
        "5-TiB",
        "unlimited",
    ],
)
def test_cli_stream_limits_preserve_exact_bytes(limit: int | None) -> None:
    result = _render(limit)
    assert result.returncode == 0, result.stderr
    _assert_limit(result.stdout, 4 * 1024**3 if limit is None else limit)


@pytest.mark.parametrize("secret_method", ["kubernetes", "vault-agent"])
def test_cli_stream_limit_matches_service_config(secret_method: str) -> None:
    limit = 4 * 1024**3
    result = _render(limit, secret_method=secret_method)
    assert result.returncode == 0, result.stderr
    _assert_limit(result.stdout, limit)


@pytest.mark.parametrize(
    ("limit", "suggestion"),
    [
        (2**31 + 1, 2**31 + 1024),
        ((2**31 - 1) * 1024 + 1, 2**41),
        (5 * 1024**4 + 1, 5 * 1024**4 + 1024**2),
        (2**63 - 1, 2**63 - 1024**4),
    ],
    ids=["KiB-alignment", "unit-boundary", "MiB-alignment", "int64-max"],
)
def test_misaligned_limits_suggest_a_renderable_value(limit: int, suggestion: int) -> None:
    result = _render(limit)
    assert result.returncode != 0
    assert f"{_MAX_BYTES_SETTING}={limit} cannot be expressed exactly" in result.stderr
    assert f"try {_MAX_BYTES_SETTING}={suggestion}" in result.stderr

    suggested = _render(suggestion)
    assert suggested.returncode == 0, suggested.stderr
    _assert_limit(suggested.stdout, suggestion)


def test_native_initializer_keeps_its_fixed_stream_limit() -> None:
    result = _render(2**31 + 1, use_cli=False)
    assert result.returncode == 0, result.stderr
    assert _initializer(result.stdout)["command"] == ["/nats-ready"]
    raw_limits = re.findall(
        r"^\s*local_stream_max_bytes = (-?\d+)\s*$", result.stdout, re.MULTILINE
    )
    assert raw_limits
    assert {int(limit) for limit in raw_limits} == {20 * 1024**3}
