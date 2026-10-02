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
"""Helm rendering tests for Template Preview's optional PostgreSQL control-plane database."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest
import yaml

HELM_DIR = Path(__file__).resolve().parents[3] / "deploy" / "helm"

pytestmark = pytest.mark.skipif(shutil.which("helm") is None, reason="helm is not installed")


def _render(*sets: str) -> list[dict]:
    command = ["helm", "template", "test", ".", "--values", "values-ci.yaml"]
    for value in sets:
        command += ["--set", value]
    output = subprocess.run(
        command, cwd=HELM_DIR, capture_output=True, text=True, check=True
    ).stdout
    return [doc for doc in yaml.safe_load_all(output) if doc]


def _cluster(docs: list[dict]) -> dict | None:
    for doc in docs:
        if doc.get("kind") == "Cluster" and doc["metadata"]["name"] == "cluster-template-preview":
            return doc
    return None


def test_the_database_is_not_created_unless_asked_for():
    assert _cluster(_render()) is None


def test_enabling_it_creates_a_cloudnativepg_cluster_for_template_preview():
    cluster = _cluster(_render("cnpg.templatePreview.enabled=true"))

    assert cluster is not None
    initdb = cluster["spec"]["bootstrap"]["initdb"]
    assert (initdb["database"], initdb["owner"]) == ("template_preview", "template_preview")
    assert cluster["spec"]["instances"] == 3  # highly available unless a local overlay lowers it


def test_by_default_the_credentials_secret_must_already_exist_like_the_other_clusters():
    cluster = _cluster(_render("cnpg.templatePreview.enabled=true"))

    assert cluster["spec"]["bootstrap"]["initdb"]["secret"] == {
        "name": "cluster-template-preview-app"
    }


def test_generated_credentials_leave_the_secret_to_the_operator():
    cluster = _cluster(
        _render(
            "cnpg.templatePreview.enabled=true", "cnpg.templatePreview.generateCredentials=true"
        )
    )

    assert "secret" not in cluster["spec"]["bootstrap"]["initdb"]


def test_the_other_clusters_still_require_their_precreated_secrets():
    docs = _render(
        "cnpg.templatePreview.enabled=true", "cnpg.templatePreview.generateCredentials=true"
    )
    config_store = next(
        d
        for d in docs
        if d.get("kind") == "Cluster" and d["metadata"]["name"] == "cluster-config-store"
    )

    assert config_store["spec"]["bootstrap"]["initdb"]["secret"] == {
        "name": "cluster-config-store-app"
    }
