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


# The Template Preview chart is private, so the checked-in Chart.yaml does not depend on it: trusted
# CI jobs, or a developer, add it with .gitlab/ci/scripts/build_chart_dependencies.sh. The tests that
# render its Deployments are marked, and conftest.py deselects them until it has been added.
needs_subchart = pytest.mark.needs_subchart


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


def test_the_database_is_not_created_unless_asked_for() -> None:
    assert _cluster(_render()) is None


def test_enabling_it_creates_a_cloudnativepg_cluster_for_template_preview() -> None:
    cluster = _cluster(_render("cnpg.templatePreview.enabled=true"))

    assert cluster is not None
    initdb = cluster["spec"]["bootstrap"]["initdb"]
    assert (initdb["database"], initdb["owner"]) == ("template_preview", "template_preview")
    assert cluster["spec"]["instances"] == 3  # highly available unless a local overlay lowers it


def test_by_default_the_credentials_secret_must_already_exist_like_the_other_clusters() -> None:
    cluster = _cluster(_render("cnpg.templatePreview.enabled=true"))

    assert cluster["spec"]["bootstrap"]["initdb"]["secret"] == {
        "name": "cluster-template-preview-app"
    }


def test_generated_credentials_leave_the_secret_to_the_operator() -> None:
    cluster = _cluster(
        _render(
            "cnpg.templatePreview.enabled=true", "cnpg.templatePreview.generateCredentials=true"
        )
    )

    assert "secret" not in cluster["spec"]["bootstrap"]["initdb"]


def test_the_other_clusters_still_require_their_precreated_secrets() -> None:
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


GATE_ON = (
    "templatePreview.enabled=true",
    "templatePreview.unsafeDynamicTemplatePlugins.enabled=true",
    "templatePreview.unsafeDynamicTemplatePlugins.acknowledgeArbitraryCodeExecution=true",
    "renderService.unsafeDynamicTemplatePlugins.enabled=true",
    "renderService.unsafeDynamicTemplatePlugins.acknowledgeArbitraryCodeExecution=true",
)


def _backend_env(docs: list[dict], container: str = "backend") -> dict[str, dict]:
    deployment = next(
        d
        for d in docs
        if d["kind"] == "Deployment" and d["metadata"]["name"] == "template-preview-backend"
    )
    spec = deployment["spec"]["template"]["spec"]
    containers = spec["containers"] + (spec.get("initContainers") or [])
    return {e["name"]: e for c in containers if c["name"] == container for e in c["env"]}


@needs_subchart
def test_template_preview_connects_with_a_username_and_password_from_the_secret() -> None:
    env = _backend_env(_render(*GATE_ON))

    assert env["KIWI_RENDER_DATABASE_HOST"]["value"] == "cluster-template-preview-rw"
    assert env["KIWI_RENDER_DATABASE_NAME"]["value"] == "template_preview"
    for variable, key in (("USER", "username"), ("PASSWORD", "password")):
        ref = env[f"KIWI_RENDER_DATABASE_{variable}"]["valueFrom"]["secretKeyRef"]
        assert ref == {"name": "cluster-template-preview-app", "key": key}
    assert "KIWI_RENDER_CONTROL_PLANE_DATABASE_URL" not in env  # no SQLite URL, no `uri` key needed


@needs_subchart
def test_the_migration_step_uses_the_same_connection() -> None:
    env = _backend_env(_render(*GATE_ON), container="migrate-control-plane-db")

    assert {
        "KIWI_RENDER_DATABASE_HOST",
        "KIWI_RENDER_DATABASE_USER",
        "KIWI_RENDER_DATABASE_PASSWORD",
    } <= set(env)


@needs_subchart
def test_archives_are_kept_in_the_database_and_no_artifacts_volume_is_created() -> None:
    docs = _render(*GATE_ON)

    assert _backend_env(docs)["KIWI_RENDER_ARTIFACT_STORAGE"]["value"] == "database"
    claims = {d["metadata"]["name"] for d in docs if d["kind"] == "PersistentVolumeClaim"}
    assert "template-preview-artifacts" not in claims


@needs_subchart
def test_a_volume_for_archives_can_still_be_asked_for() -> None:
    docs = _render(*GATE_ON, "templatePreview.controlPlane.artifacts.storage=volume")

    assert "KIWI_RENDER_ARTIFACT_STORAGE" not in _backend_env(docs)
    assert "template-preview-artifacts" in {
        d["metadata"]["name"] for d in docs if d["kind"] == "PersistentVolumeClaim"
    }


def _render_error(*sets: str) -> str:
    command = ["helm", "template", "test", ".", "--values", "values-ci.yaml"]
    for value in sets:
        command += ["--set", value]
    done = subprocess.run(command, cwd=HELM_DIR, capture_output=True, text=True, check=False)
    assert done.returncode != 0, "expected the chart to refuse to render"
    return done.stderr


@needs_subchart
def test_the_install_is_refused_if_the_plugin_control_plane_would_silently_use_sqlite() -> None:
    error = _render_error(*GATE_ON, "templatePreview.controlPlane.database.existingSecret=")

    assert "controlPlane.database.existingSecret is empty" in error
    assert "SQLite" in error


@needs_subchart
def test_sqlite_is_allowed_only_when_asked_for_explicitly() -> None:
    docs = _render(
        *GATE_ON,
        "templatePreview.controlPlane.database.existingSecret=",
        "templatePreview.controlPlane.database.allowSqlite=true",
    )

    env = _backend_env(docs)
    assert "KIWI_RENDER_DATABASE_HOST" not in env
    assert env["KIWI_RENDER_CONTROL_PLANE_DATABASE_URL"]["value"].startswith("sqlite:")


def test_with_the_plugin_control_plane_off_no_database_is_required() -> None:
    assert _render("templatePreview.enabled=true")  # renders without a database Secret configured


def test_production_vault_provides_the_same_secret_through_external_secrets() -> None:
    docs = _render("secrets.method=eso", "cnpg.templatePreview.enabled=true")

    external = next(
        d
        for d in docs
        if d["kind"] == "ExternalSecret"
        and d["metadata"]["name"] == "cnpg-template-preview-credentials"
    )
    assert external["spec"]["target"]["name"] == "cluster-template-preview-app"
    remote = {item["secretKey"]: item["remoteRef"]["property"] for item in external["spec"]["data"]}
    assert remote == {"username": "template_preview_user", "password": "template_preview_password"}
    assert [
        d
        for d in docs
        if d["kind"] == "Cluster" and d["metadata"]["name"] == "cluster-template-preview"
    ]


def test_no_external_secret_is_created_unless_the_database_is_enabled() -> None:
    docs = _render("secrets.method=eso")

    assert not [
        d
        for d in docs
        if d["kind"] == "ExternalSecret" and "template-preview" in d["metadata"]["name"]
    ]
