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
"""Markdown rendering and the compressed wire format the stages query returns."""

from typing import ClassVar

from pydantic import BaseModel

from nv_config_manager_workflows.stage import (
    HistoryEntry,
    Stage,
    StageMixin,
    StateEnum,
    compress_stages,
    decompress_stages,
    render_markdown_table,
    render_markdown_table_dict,
)


class Device(BaseModel):
    name: str
    site: str
    roles: list[str] = []


class CuratedDevice(BaseModel):
    name: str
    site: str
    secret: str

    markdown_fields: ClassVar[list[str]] = ["name", "site"]


def build_stage(name: str = "deploy", state: StateEnum = StateEnum.NOT_STARTED) -> Stage:
    """Build a stage with the history a real run through that state would leave.

    A terminal state needs its preceding IN_PROGRESS entry: ``execution_time``
    measures the span between the two and the state machine never produces one
    without the other.
    """
    history = [HistoryEntry(state=StateEnum.NOT_STARTED, time=0.0)]
    if state in (StateEnum.COMPLETE, StateEnum.FAILED):
        history.append(HistoryEntry(state=StateEnum.IN_PROGRESS, time=0.0))
    if state != StateEnum.NOT_STARTED:
        history.append(HistoryEntry(state=state, time=0.0))
    return Stage(
        name=name,
        description="Deploy the rendered configuration",
        requires_approval=False,
        state=state,
        depends_on=[],
        state_history=history,
        retryable=True,
        traceback=None,
    )


class TestMarkdownTable:
    def test_rows_render_with_a_header_and_one_line_each(self) -> None:
        table = render_markdown_table([Device(name="leaf01", site="SJC01")])

        assert "name" in table
        assert "leaf01" in table
        assert "SJC01" in table

    def test_a_single_model_renders_without_being_wrapped_in_a_list(self) -> None:
        assert render_markdown_table(Device(name="leaf01", site="SJC01")) == render_markdown_table(
            [Device(name="leaf01", site="SJC01")]
        )

    def test_no_rows_render_as_the_empty_string(self) -> None:
        assert render_markdown_table([]) == ""

    def test_string_lists_render_comma_separated(self) -> None:
        table = render_markdown_table(
            [Device(name="leaf01", site="SJC01", roles=["spine", "leaf"])]
        )

        assert "spine, leaf" in table

    def test_an_empty_string_list_renders_blank(self) -> None:
        table = render_markdown_table([Device(name="leaf01", site="SJC01", roles=[])])

        assert "[]" not in table

    def test_markdown_fields_restrict_the_columns(self) -> None:
        """A model can keep a field off the operator-facing table."""
        table = render_markdown_table(
            [CuratedDevice(name="leaf01", site="SJC01", secret="hunter2")]
        )

        assert "hunter2" not in table
        assert "leaf01" in table

    def test_excluded_fields_are_dropped(self) -> None:
        table = render_markdown_table([Device(name="leaf01", site="SJC01")], exclude={"site"})

        assert "SJC01" not in table

    def test_dicts_render_through_the_dict_variant(self) -> None:
        table = render_markdown_table_dict([{"name": "leaf01", "site": "SJC01"}])

        assert "leaf01" in table

    def test_no_dict_rows_render_as_the_empty_string(self) -> None:
        assert render_markdown_table_dict([]) == ""

    def test_the_mixin_exposes_both_renderers(self) -> None:
        """Workflows call these through self, so the mixin must keep forwarding."""
        rows = [Device(name="leaf01", site="SJC01")]

        assert StageMixin.markdown_table(rows) == render_markdown_table(rows)
        assert StageMixin.markdown_table_dict([{"name": "leaf01"}]) == render_markdown_table_dict(
            [{"name": "leaf01"}]
        )


class TestCompression:
    def test_stages_survive_a_compression_round_trip(self) -> None:
        stages = [build_stage("render", StateEnum.COMPLETE), build_stage("deploy")]

        restored = decompress_stages(compress_stages(stages))

        assert [stage.name for stage in restored] == ["render", "deploy"]
        assert [stage.state for stage in restored] == [StateEnum.COMPLETE, StateEnum.NOT_STARTED]

    def test_no_stages_survive_a_round_trip(self) -> None:
        assert decompress_stages(compress_stages([])) == []

    def test_compression_shrinks_a_realistic_stage_list(self) -> None:
        """The query exists because uncompressed stages exceed Temporal's payload limits."""
        stages = [build_stage(f"stage-{index}") for index in range(50)]

        compressed = compress_stages(stages)

        assert len(compressed) < len(str([stage.model_dump(mode="json") for stage in stages]))

    def test_stage_history_and_traceback_survive(self) -> None:
        stage = build_stage("render", StateEnum.FAILED)
        stage.traceback = "Traceback (most recent call last): ..."

        restored = decompress_stages(compress_stages([stage]))[0]

        assert restored.traceback == stage.traceback
        assert [entry.state for entry in restored.state_history] == [
            StateEnum.NOT_STARTED,
            StateEnum.IN_PROGRESS,
            StateEnum.FAILED,
        ]

    def test_the_mixin_exposes_both_codecs(self) -> None:
        """workflow_v1 decodes the query result through StageMixin.decompress_stages."""
        stages = [build_stage()]

        assert StageMixin.decompress_stages(StageMixin.compress_stages(stages)) == stages
