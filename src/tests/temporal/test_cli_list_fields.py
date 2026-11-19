# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
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
from __future__ import annotations

from typing import Any

import pytest
from click.testing import CliRunner
from pydantic import BaseModel

from nv_config_manager.temporal import cli as temporal_cli


class ListWorkflowInput(BaseModel):
    status: list[str] = ["Active", "Provisioned"]
    roles: list[str] = []


class ListWorkflow:
    """Workflow used to verify CLI serialization of list fields."""


def _invoke(monkeypatch: pytest.MonkeyPatch, args: list[str]) -> dict[str, Any]:
    """Run the generated command and return the parameters it would send."""
    captured_parameters: dict[str, Any] = {}

    monkeypatch.setattr(
        temporal_cli,
        "_build_auth",
        lambda *_args: (None, "https://workflow.example.com/v1/workflow"),
    )

    def capture_invoke(
        _self: temporal_cli.WorkflowClient,
        _workflow_info: temporal_cli.WorkflowInfo,
        parameters: dict[str, Any],
        **_kwargs: Any,
    ) -> dict[str, Any]:
        captured_parameters.update(parameters)
        return {}

    monkeypatch.setattr(temporal_cli.WorkflowClient, "invoke_workflow", capture_invoke)

    workflow_info = temporal_cli.WorkflowInfo(ListWorkflow, ListWorkflowInput, "/list")
    command = temporal_cli.create_workflow_command("list", workflow_info)

    result = CliRunner().invoke(command, ["--hostname", "config-manager.example.com", *args])

    assert result.exit_code == 0, result.output
    return captured_parameters


def test_workflow_command_sends_omitted_list_fields_as_their_defaults(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    assert _invoke(monkeypatch, []) == {"status": ["Active", "Provisioned"], "roles": []}


def test_workflow_command_parses_comma_separated_list_options(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    parameters = _invoke(monkeypatch, ["--status", "Planned, Staged", "--roles", ""])

    assert parameters == {"status": ["Planned", "Staged"], "roles": []}
