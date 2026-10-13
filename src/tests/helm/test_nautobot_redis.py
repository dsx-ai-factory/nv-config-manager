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
"""Helm rendering tests for the bundled Nautobot's Redis cache database.

``nautobot-server post_upgrade`` clears the Nautobot cache with ``FLUSHDB``, so
the cache database must be configurable and must never be a database that
nv-config-manager uses. These tests skip cleanly when ``helm`` is unavailable
or chart dependencies have not been vendored.
"""

from __future__ import annotations

import io
import shutil
import subprocess
from pathlib import Path
from typing import Any

import pytest
from ruamel.yaml import YAML

_CHART_DIR = Path(__file__).resolve().parents[3] / "deploy" / "helm"
_NAUTOBOT_ENV_CONFIGMAP = "test-nv-config-manager-nautobot-env"


def _helm_template(
    *set_args: str, set_string: str | None = None
) -> subprocess.CompletedProcess[str]:
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
    if set_string is not None:
        cmd += ["--set-string", set_string]
    return subprocess.run(cmd, capture_output=True, text=True, check=False)


def _nautobot_env(*set_args: str) -> dict[str, Any]:
    result = _helm_template(*set_args)
    if result.returncode != 0:
        pytest.fail(f"helm template failed:\n{result.stderr.strip()}")
    for doc in YAML(typ="safe").load_all(io.StringIO(result.stdout)):
        if (
            doc
            and doc.get("kind") == "ConfigMap"
            and doc["metadata"]["name"] == _NAUTOBOT_ENV_CONFIGMAP
        ):
            return doc["data"]
    pytest.fail(f"ConfigMap {_NAUTOBOT_ENV_CONFIGMAP} not rendered")


def test_cache_db_defaults_to_1() -> None:
    assert _nautobot_env()["NAUTOBOT_CACHE_REDIS_DB"] == "1"


def test_cache_db_is_configurable() -> None:
    env = _nautobot_env("externalServices.redis.nautobotCacheDb=7")
    assert env["NAUTOBOT_CACHE_REDIS_DB"] == "7"


@pytest.mark.parametrize("key", ["db", "lockDb"])
def test_cache_db_must_differ_from_manager_dbs(key: str) -> None:
    result = _helm_template(
        f"externalServices.redis.{key}=5", "externalServices.redis.nautobotCacheDb=5"
    )
    assert result.returncode != 0
    assert "nautobotCacheDb (5) must differ" in result.stderr


def test_cache_db_must_not_be_celery_broker_db() -> None:
    result = _helm_template(
        "externalServices.redis.db=3",
        "externalServices.redis.lockDb=3",
        "externalServices.redis.nautobotCacheDb=0",
    )
    assert result.returncode != 0
    assert "Celery broker uses Redis database 0" in result.stderr


@pytest.mark.parametrize(
    ("set_args", "set_string"),
    [
        (("externalServices.redis.nautobotCacheDb=null",), None),
        ((), "externalServices.redis.nautobotCacheDb="),
        ((), "externalServices.redis.nautobotCacheDb=two"),
        ((), "externalServices.redis.nautobotCacheDb=010"),
        (("externalServices.redis.nautobotCacheDb=-1",), None),
    ],
)
def test_cache_db_must_be_a_non_negative_integer(
    set_args: tuple[str, ...], set_string: str | None
) -> None:
    result = _helm_template(*set_args, set_string=set_string)
    assert result.returncode != 0
    assert "nautobotCacheDb must be a non-negative decimal integer" in result.stderr


def test_external_nautobot_skips_cache_db_check() -> None:
    result = _helm_template(
        "externalServices.redis.db=1",
        "externalServices.nautobot.local=false",
        "externalServices.nautobot.server=https://nautobot.example",
    )
    assert result.returncode == 0, result.stderr
