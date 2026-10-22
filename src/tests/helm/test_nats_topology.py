# SPDX-FileCopyrightText: Copyright (c) 2024-2026 NVIDIA CORPORATION & AFFILIATES.
# SPDX-License-Identifier: Apache-2.0
"""Helm rendering tests for installer-owned NATS topology."""

from __future__ import annotations

import io
import shutil
import subprocess
from pathlib import Path
from typing import Any

import pytest
from ruamel.yaml import YAML

_CHART_DIR = Path(__file__).resolve().parents[3] / "deploy" / "helm"


def _render(tmp_path: Path, values: str) -> tuple[str, list[dict[str, Any]]]:
    if shutil.which("helm") is None:
        pytest.skip("helm binary not available")
    if not (_CHART_DIR / "charts").is_dir():
        pytest.skip("helm chart dependencies not vendored (run `helm dependency build`)")
    custom_values = tmp_path / "nats-values.yaml"
    custom_values.write_text(values)
    result = subprocess.run(
        [
            "helm",
            "template",
            "test",
            str(_CHART_DIR),
            "--values",
            str(_CHART_DIR / "values-ci.yaml"),
            "--values",
            str(custom_values),
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        pytest.fail(f"helm template failed:\n{result.stderr.strip()}")
    yaml = YAML(typ="safe")
    yaml.allow_duplicate_keys = True
    documents = [
        document
        for document in yaml.load_all(io.StringIO(result.stdout))
        if isinstance(document, dict)
    ]
    return result.stdout, documents


def _bundled_values() -> str:
    return """
externalServices:
  nats:
    local: true
    authMethod: password
    queue: custom-queue
    streams:
      configManager:
        name: custom-config-manager
        apiPrefix: $JS.API
        subjects:
          - custom.render
          - custom.device
          - custom.workflow.result
        renderChangeSubject: custom.render
        deviceChangeSubject: custom.device
        archiveSubject: custom.workflow.result
        consumer:
          name: custom-device-consumer
        archiveConsumer:
          name: custom-archive-consumer
          deliverySubject: custom.archive.delivery
      nautobot:
        name: custom-nautobot
        apiPrefix: $JS.API
        subjects:
          - custom.nautobot
        subject: custom.nautobot
        consumer:
          name: custom-nautobot-consumer
nautobotNats:
  enabled: true
  natsReady:
    enabled: true
    useNatsCli: true
"""


def test_bundled_stream_create_and_edit_reconcile_configured_subjects(
    tmp_path: Path,
) -> None:
    rendered, _ = _render(tmp_path, _bundled_values())

    assert 'CONFIG_MANAGER_SUBJECTS="custom.render,custom.device,custom.workflow.result"' in (
        rendered
    )
    assert 'DCIM_SUBJECTS="custom.nautobot"' in rendered
    assert rendered.count('--subjects="$CONFIG_MANAGER_SUBJECTS"') == 2
    assert rendered.count('--subjects="$DCIM_SUBJECTS"') == 2


def test_custom_consumers_and_archive_delivery_reach_rendered_ini(tmp_path: Path) -> None:
    rendered, _ = _render(tmp_path, _bundled_values())

    assert "config_manager_consumer_name = custom-device-consumer" in rendered
    assert "archive_consumer_name = custom-archive-consumer" in rendered
    assert "archive_deliver_subject = custom.archive.delivery" in rendered
    assert "nautobot_consumer_name = custom-nautobot-consumer" in rendered


def test_external_mode_does_not_render_bundled_nats_resources(tmp_path: Path) -> None:
    _, documents = _render(
        tmp_path,
        """
externalServices:
  nats:
    local: false
    server: tls://nats.example.com:4222
    authMethod: JWT
nautobotNats:
  enabled: false
""",
    )

    assert not [
        document
        for document in documents
        if document.get("metadata", {}).get("labels", {}).get("app.kubernetes.io/component")
        == "nats"
    ]


def test_installer_contract_renders_consistent_bundled_ownership_switches(
    tmp_path: Path,
) -> None:
    """Raw inconsistent Helm values are unsupported; installer output sets both switches."""
    _, documents = _render(tmp_path, _bundled_values())

    assert any(
        document.get("metadata", {}).get("labels", {}).get("app.kubernetes.io/component") == "nats"
        for document in documents
    )
