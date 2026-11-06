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
"""Helm validation tests for CNPG backup configuration (GNIKIWI-339)."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

_CHART_DIR = Path(__file__).resolve().parents[3] / "deploy" / "helm"
_BUCKET = "cnpg.backup.s3.bucket=my-bucket"
_PATH = "cnpg.backup.s3.path=my/prefix"
_ENABLE_TEMPORAL = "cnpg.temporal.backup.enabled=true"
_ESO_PATH = "secrets.vault.paths.cnpgBackup.path=org/app/cnpg-backup"


def _helm_template(*set_args: str) -> subprocess.CompletedProcess[str]:
    if shutil.which("helm") is None:
        pytest.skip("helm binary not available")
    if not (_CHART_DIR / "charts").is_dir():
        pytest.skip("helm chart dependencies not vendored (run `helm dependency build`)")
    cmd = [
        "helm",
        "template",
        "test",
        str(_CHART_DIR),
        "--values",
        str(_CHART_DIR / "values-ci.yaml"),
    ]
    for pair in set_args:
        cmd += ["--set", pair]
    return subprocess.run(cmd, capture_output=True, text=True, check=False)


def test_backups_disabled_needs_no_s3_settings() -> None:
    assert _helm_template().returncode == 0


@pytest.mark.parametrize(
    ("set_args", "message"),
    [
        ((_ENABLE_TEMPORAL, _PATH), "cnpg.backup.s3.bucket is required"),
        ((_ENABLE_TEMPORAL, _BUCKET), "cnpg.backup.s3.path is required"),
        ((_ENABLE_TEMPORAL,), "cnpg.backup.s3.bucket is required"),
        (
            ("cnpg.nautobot.enabled=true", "cnpg.nautobot.backup.enabled=true", _BUCKET),
            "cnpg.backup.s3.path is required",
        ),
        (
            (_ENABLE_TEMPORAL, _BUCKET, _PATH, "cnpg.backup.destinationType=gcs"),
            "destinationType",
        ),
        (
            (_ENABLE_TEMPORAL, _BUCKET, _PATH, "cnpg.backup.destinationType="),
            "destinationType",
        ),
    ],
)
def test_incomplete_backup_config_fails(set_args: tuple[str, ...], message: str) -> None:
    result = _helm_template(*set_args)
    assert result.returncode != 0
    assert message in result.stderr


def test_complete_backup_config_renders_destination() -> None:
    result = _helm_template(_ENABLE_TEMPORAL, _BUCKET, _PATH)
    assert result.returncode == 0, result.stderr
    assert 's3://my-bucket/my/prefix/cluster-temporal"' in result.stdout


def test_backup_credentials_externalsecret_follows_per_cluster_flag() -> None:
    enabled = _helm_template(_ENABLE_TEMPORAL, _BUCKET, _PATH, _ESO_PATH)
    assert enabled.returncode == 0, enabled.stderr
    assert "cnpg-backup-credentials-eso" in enabled.stdout

    disabled = _helm_template(_ESO_PATH)
    assert disabled.returncode == 0, disabled.stderr
    assert "cnpg-backup-credentials-eso" not in disabled.stdout
