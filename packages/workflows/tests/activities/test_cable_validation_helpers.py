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
"""Tests for cable-validation formatting and export helpers."""

import base64
import io

import pandas as pd
import pytest

from nv_config_manager_workflows.activities.cable_validation.helpers import (
    _build_host_summary,
    _classify_issue,
    _format_results_markdown,
    _format_single_interface_row,
    _generate_csv_link,
    _generate_xlsx_link,
)
from nv_config_manager_workflows.activities.cable_validation.models import (
    CSV_EXCLUDE_COLUMNS,
    HOST_SUMMARY_COLUMNS,
    MARKDOWN_EXCLUDE_COLUMNS,
    CableValidationResultData,
    CableValidationRow,
    InvalidCable,
    _escape_formula,
)
from nv_config_manager_workflows.clients.device.models import InterfaceNeighborData


def _row(
    start_device: str,
    issue: str,
    start_rack: str | None = None,
) -> CableValidationRow:
    """Build a minimal cable-validation row for summary and export tests."""
    return CableValidationRow.model_validate(
        {"start_device": start_device, "start_rack": start_rack, "issue": issue}
    )


def _decode_workbook(link: str) -> dict[str, pd.DataFrame]:
    """Decode an Excel data link into its worksheets."""
    assert link.startswith("[Download Excel](data:")
    encoded = link.split("base64,", 1)[1].rstrip(")")
    return pd.read_excel(io.BytesIO(base64.b64decode(encoded)), sheet_name=None)


def _decode_csv(link: str) -> pd.DataFrame:
    """Decode a CSV data link into a dataframe."""
    assert link.startswith("[Export to CSV](data:")
    encoded = link.split("base64,", 1)[1].rstrip(")")
    csv_text = base64.b64decode(encoded).decode("utf-8")
    return pd.read_csv(io.StringIO(csv_text), dtype=str, keep_default_na=False)


class TestLaneMetadata:
    """Keep intended labels and observed descriptions visible in every report format."""

    @pytest.mark.parametrize("issue_kind", ["lldp", "mac", "down", "missing", "unexpected"])
    def test_issue_rows_preserve_interface_metadata(self, issue_kind: str) -> None:
        """Carry metadata through every cable issue category without replacing port IDs."""
        intended = InterfaceNeighborData(
            name="eth1",
            device_name="su02-hyperreach-host",
            label="C1-2-L1",
            macs=["00-00-00-00-00-01"] if issue_kind == "mac" else [],
        )
        actual = InterfaceNeighborData(
            name=None if issue_kind == "missing" else "A56p0",
            device_name="su02-hyperreach-host",
            port_description="su02-hyperreach-host A56p0",
            link_up=issue_kind != "down",
            macs=["00-00-00-00-00-02"] if issue_kind == "mac" else [],
        )
        row = _format_single_interface_row(
            "su01-cin-leaf-r01",
            None,
            "swp2",
            InvalidCable(intended=None if issue_kind == "unexpected" else intended, actual=actual),
        )

        assert row is not None
        assert row.intended_interface_label == (None if issue_kind == "unexpected" else "C1-2-L1")
        assert row.lldp_port_description == "su02-hyperreach-host A56p0"
        if issue_kind == "lldp":
            assert row.intended_end_port == "eth1"
            assert row.actual_end_port == "A56p0"
            assert row.issue == (
                "Port naming differs. Check the Cerebro lane map and host LLDP configuration."
            )

    @pytest.mark.parametrize("export", ["csv", "xlsx"])
    @pytest.mark.parametrize(
        ("label", "description", "label_display", "description_display"),
        [
            (
                "C1-2-L1",
                "su02-hyperreach-host A56p0",
                "C1-2-L1",
                "su02-hyperreach-host A56p0",
            ),
            ("C1|2\r\nL1", "host\\|tray\nWRONG-LANE", r"C1\|2 L1", r"host\\\|tray WRONG-LANE"),
        ],
    )
    def test_metadata_is_readable_in_markdown_and_preserved_in_downloads(
        self,
        export: str,
        label: str,
        description: str,
        label_display: str,
        description_display: str,
    ) -> None:
        """Escape table delimiters for display and preserve raw metadata in CSV and Excel."""
        row = CableValidationRow.model_validate(
            {
                "start_device": "su01-cin-leaf-r01",
                "start_port": "swp2",
                "intended_end_port": "eth1",
                "intended_interface_label": label,
                "actual_end_port": "A56p0",
                "lldp_port_description": description,
                "issue": "Incorrect cabling, actual should match intended. Based on LLDP data",
            }
        )

        markdown = _format_results_markdown(
            [row], CSV_EXCLUDE_COLUMNS, MARKDOWN_EXCLUDE_COLUMNS, export=export
        )
        link, table = markdown.split("\n", 1)
        detail = _decode_csv(link) if export == "csv" else _decode_workbook(link)["Cable Issues"]

        assert "Intended Interface Label" in table
        assert "LLDP Port Description" in table
        assert label_display in table
        assert description_display in table
        assert len(table.splitlines()) == 3
        assert detail.iloc[0]["Intended Interface Label"] == label
        assert detail.iloc[0]["LLDP Port Description"] == description
        assert detail.iloc[0]["Intended End Port"] == "eth1"
        assert detail.iloc[0]["Actual End Port"] == "A56p0"
        assert row.intended_interface_label == label
        assert row.lldp_port_description == description

    def test_missing_metadata_has_a_placeholder(self) -> None:
        """Older and non-LLDP findings remain displayable without metadata."""
        row = _row("switch", "Link is down.")

        assert row.to_markdown()["Intended Interface Label"] == "--"
        assert row.to_markdown()["LLDP Port Description"] == "--"


class TestLldpGuidance:
    """Only classify supported discrepancies; keep guidance short and conditional."""

    NAMING = "Port naming differs. Check the Cerebro lane map and host LLDP configuration."
    DEVICE = "Device identity differs. Verify the LLDP hostname and cable destination."
    C_NOTATION = "C-notation labels differ. Verify the lane map, inventory, and cable placement."
    GENERIC = "Incorrect cabling, actual should match intended. Based on LLDP data"

    @pytest.mark.parametrize(
        ("intended_changes", "actual_changes", "expected_issue"),
        [
            pytest.param({}, {}, NAMING, id="port-name-convention"),
            pytest.param({}, {"port_description": "host eth_r0_p0"}, NAMING, id="legacy-name"),
            pytest.param(
                {}, {"port_description": "  HOST   a56p0  "}, NAMING, id="case-whitespace"
            ),
            pytest.param({}, {"port_description": "host C1-5-L1"}, C_NOTATION, id="c-label"),
            pytest.param({}, {"port_description": "host c1-3-l1"}, C_NOTATION, id="c-label-case"),
            pytest.param(
                {},
                {"device_name": "other-host", "port_description": "other-host C1-2-L1"},
                DEVICE,
                id="different-device",
            ),
            pytest.param(
                {},
                {"device_name": "other-host", "port_description": None},
                DEVICE,
                id="device-without-description",
            ),
            pytest.param(
                {},
                {"device_name": None, "port_description": "other-host C1-2-L1"},
                DEVICE,
                id="description-device-only",
            ),
            pytest.param(
                {"device_name": "line-card", "chassis_name": "host"},
                {},
                NAMING,
                id="chassis-alias",
            ),
            pytest.param(
                {"device_name": "line-card", "chassis_name": "host"},
                {"device_name": "LINE-CARD"},
                NAMING,
                id="known-identities",
            ),
            pytest.param({}, {"device_name": None}, NAMING, id="description-identity"),
            pytest.param(
                {},
                {"device_name": "00:11:22:33:44:55"},
                NAMING,
                id="mac-chassis-id",
            ),
            pytest.param(
                {},
                {"port_description": "other-host C1-2-L1"},
                GENERIC,
                id="conflicting-description-host",
            ),
            pytest.param(
                {},
                {"device_name": "other-host"},
                GENERIC,
                id="conflicting-system-host",
            ),
            pytest.param(
                {},
                {"device_name": None, "port_description": None},
                GENERIC,
                id="missing-identity",
            ),
            pytest.param(
                {},
                {"device_name": "00:11:22:33:44:55", "port_description": None},
                GENERIC,
                id="only-mac-identity",
            ),
            pytest.param({"device_name": None}, {}, GENERIC, id="missing-intended-identity"),
            pytest.param(
                {"device_name": None, "chassis_name": "host"},
                {},
                NAMING,
                id="only-chassis-alias",
            ),
            pytest.param({"label": None}, {}, GENERIC, id="no-label"),
            pytest.param({"label": ""}, {}, GENERIC, id="empty-label"),
            pytest.param(
                {"label": None, "name": "C1-2-L1"},
                {},
                NAMING,
                id="c-interface-name",
            ),
            pytest.param(
                {},
                {"port_description": "host c1-2-l1"},
                GENERIC,
                id="matching-label",
            ),
            pytest.param({}, {"port_description": None}, NAMING, id="observed-port-id"),
            pytest.param(
                {},
                {"port_description": None, "name": "00:11:22:33:44:66"},
                GENERIC,
                id="mac-port-id-without-description",
            ),
            pytest.param(
                {},
                {"port_description": "host 00:11:22:33:44:66"},
                GENERIC,
                id="mac-port-in-description",
            ),
            pytest.param({}, {"port_description": "host"}, GENERIC, id="one-token"),
            pytest.param(
                {},
                {"port_description": "host C1-2-L1 extra"},
                GENERIC,
                id="extra-token",
            ),
            pytest.param(
                {},
                {"port_description": "host C1-5-L1-extra"},
                NAMING,
                id="c-like-name",
            ),
            pytest.param({"label": "C1|2\nL1"}, {}, GENERIC, id="non-c-label"),
            pytest.param(
                {},
                {"port_description": "00:11:22:33:44:55 C1-5-L1"},
                GENERIC,
                id="description-mac-identity",
            ),
        ],
    )
    def test_issue_uses_supported_evidence(
        self,
        intended_changes: dict[str, str | None],
        actual_changes: dict[str, str | None],
        expected_issue: str,
    ) -> None:
        intended = InterfaceNeighborData.model_validate(
            {"device_name": "host", "name": "eth1", "label": "C1-2-L1", **intended_changes}
        )
        actual = InterfaceNeighborData.model_validate(
            {
                "device_name": "host",
                "name": "A56p0",
                "port_description": "host A56p0",
                "link_up": True,
                **actual_changes,
            }
        )
        row = _format_single_interface_row(
            "leaf", None, "swp2", InvalidCable(intended=intended, actual=actual)
        )
        assert row is not None
        assert row.issue == expected_issue
        assert row.intended_interface_label == intended.label
        assert row.lldp_port_description == actual.port_description
        assert row.intended_end_port == intended.name
        assert row.actual_end_port == actual.name
        assert _classify_issue(row.issue) == "miscabled"

    @pytest.mark.parametrize("issue", [NAMING, DEVICE, C_NOTATION])
    @pytest.mark.parametrize("export", ["csv", "xlsx"])
    def test_guidance_and_summary_survive_exports(self, issue: str, export: str) -> None:
        row = _row("leaf", issue)
        markdown = _format_results_markdown(
            [row], CSV_EXCLUDE_COLUMNS, MARKDOWN_EXCLUDE_COLUMNS, export=export
        )
        link, table = markdown.split("\n", 1)
        assert issue in table
        detail = _decode_csv(link) if export == "csv" else _decode_workbook(link)["Cable Issues"]
        assert detail.iloc[0]["Issue"] == issue
        summary = _build_host_summary([row])
        assert summary[0]["Miscabled"] == 1
        assert summary[0]["Total Issues"] == 1
        assert len(issue) < 90


class TestClassifyIssue:
    """Issue-string bucketing for the host summary."""

    def test_link_down_is_missing(self) -> None:
        assert _classify_issue("Link is down.") == "missing"

    def test_link_up_no_neighbor_is_missing(self) -> None:
        assert _classify_issue("Link is up but no neighbor found") == "missing"

    def test_incorrect_cabling_is_miscabled(self) -> None:
        issue = "Incorrect cabling, actual should match intended. Based on LLDP data"
        assert _classify_issue(issue) == "miscabled"

    def test_unexpected_connection(self) -> None:
        assert _classify_issue("Unexpected connection found") == "unexpected"

    def test_none_is_other(self) -> None:
        assert _classify_issue(None) == "other"

    def test_unknown_is_other(self) -> None:
        assert _classify_issue("some new issue") == "other"


class TestBuildHostSummary:
    """Per-host aggregation and triage ordering."""

    def test_counts_categories_per_host(self) -> None:
        rows = [
            _row("hostA", "Link is down.", "rack1:u1"),
            _row("hostA", "Link is up but no neighbor found", "rack1:u1"),
            _row("hostA", "Unexpected connection found", "rack1:u1"),
            _row(
                "hostB",
                "Incorrect cabling, actual should match intended. Based on LLDP data",
                "rack2:u3",
            ),
        ]

        by_host = {row["Host"]: row for row in _build_host_summary(rows)}

        assert by_host["hostA"]["Missing Cables"] == 2
        assert by_host["hostA"]["Unexpected Connections"] == 1
        assert by_host["hostA"]["Miscabled"] == 0
        assert by_host["hostA"]["Total Issues"] == 3
        assert by_host["hostA"]["Rack"] == "rack1:u1"
        assert by_host["hostB"]["Miscabled"] == 1
        assert by_host["hostB"]["Missing Cables"] == 0

    def test_sorted_by_missing_then_total(self) -> None:
        rows = [
            _row("low", "Link is down."),
            _row("high", "Link is down."),
            _row("high", "Link is up but no neighbor found"),
            _row("mid_total", "Link is down."),
            _row("mid_total", "Unexpected connection found"),
        ]

        hosts = [row["Host"] for row in _build_host_summary(rows)]

        assert hosts == ["high", "mid_total", "low"]

    def test_rack_falls_back_to_first_populated_value(self) -> None:
        rows = [
            _row("hostA", "Link is down.", None),
            _row("hostA", "Link is down.", "rack9:u9"),
        ]

        summary = _build_host_summary(rows)

        assert summary[0]["Rack"] == "rack9:u9"

    def test_includes_queried_switches_with_zero_issues(self) -> None:
        devices = {
            "switchA": CableValidationResultData(interfaces={}, device=None),
            "switchB": CableValidationResultData(interfaces={}, device=None),
        }
        rows = [_row("switchA", "Link is down.", "rack1:u1")]

        summary = _build_host_summary(rows, devices)
        by_host = {row["Host"]: row for row in summary}

        assert by_host["switchB"]["Missing Cables"] == 0
        assert by_host["switchB"]["Total Issues"] == 0
        assert by_host["switchA"]["Missing Cables"] == 1
        assert summary[0]["Host"] == "switchA"
        assert summary[-1]["Host"] == "switchB"

    def test_empty_results(self) -> None:
        assert _build_host_summary([]) == []


class TestGenerateXlsxLink:
    """The two-tab Excel download."""

    def test_has_detail_and_summary_sheets(self) -> None:
        rows = [
            _row("hostA", "Link is down.", "rack1:u1"),
            _row("hostA", "Link is up but no neighbor found", "rack1:u1"),
            _row("hostB", "Unexpected connection found", "rack2:u2"),
        ]

        sheets = _decode_workbook(_generate_xlsx_link(rows))

        assert set(sheets) == {"Cable Issues", "Host Summary"}
        assert len(sheets["Cable Issues"]) == 3
        summary = sheets["Host Summary"]
        assert list(summary.columns) == list(HOST_SUMMARY_COLUMNS)
        assert summary.iloc[0]["Host"] == "hostA"
        assert summary.iloc[0]["Missing Cables"] == 2

    def test_empty_results_still_valid_workbook(self) -> None:
        sheets = _decode_workbook(_generate_xlsx_link([]))

        assert set(sheets) == {"Cable Issues", "Host Summary"}
        assert len(sheets["Host Summary"]) == 0

    def test_summary_lists_all_queried_switches(self) -> None:
        devices = {
            "switchA": CableValidationResultData(interfaces={}, device=None),
            "switchB": CableValidationResultData(interfaces={}, device=None),
        }
        rows = [_row("switchA", "Link is down.", "rack1:u1")]

        sheets = _decode_workbook(_generate_xlsx_link(rows, devices))
        summary = sheets["Host Summary"]

        assert set(summary["Host"]) == {"switchA", "switchB"}
        assert summary.iloc[0]["Host"] == "switchA"


class TestFormatResultsMarkdownExport:
    """Export-link selection in the shared markdown formatter."""

    def test_xlsx_export_uses_excel_link(self) -> None:
        rows = [_row("hostA", "Link is down.", "rack1:u1")]

        markdown = _format_results_markdown(
            rows,
            csv_exclude_columns=CSV_EXCLUDE_COLUMNS,
            markdown_exclude_columns=MARKDOWN_EXCLUDE_COLUMNS,
            export="xlsx",
        )

        assert "[Download Excel](data:application/vnd.openxmlformats" in markdown
        assert "[Export to CSV]" not in markdown

    def test_csv_export_is_default(self) -> None:
        rows = [_row("hostA", "Link is down.", "rack1:u1")]

        markdown = _format_results_markdown(
            rows,
            csv_exclude_columns=CSV_EXCLUDE_COLUMNS,
            markdown_exclude_columns=MARKDOWN_EXCLUDE_COLUMNS,
        )

        assert "[Export to CSV](data:text/csv" in markdown

    def test_too_many_results_names_excel_for_xlsx_export(self) -> None:
        rows = [_row(f"host{i}", "Link is down.", "rack1:u1") for i in range(3)]

        markdown = _format_results_markdown(
            rows,
            csv_exclude_columns=CSV_EXCLUDE_COLUMNS,
            markdown_exclude_columns=MARKDOWN_EXCLUDE_COLUMNS,
            max_display_results=1,
            export="xlsx",
        )

        assert "please export to Excel to view." in markdown
        assert "export to CSV to view" not in markdown

    def test_too_many_results_names_csv_for_csv_export(self) -> None:
        rows = [_row(f"host{i}", "Link is down.", "rack1:u1") for i in range(3)]

        markdown = _format_results_markdown(
            rows,
            csv_exclude_columns=CSV_EXCLUDE_COLUMNS,
            markdown_exclude_columns=MARKDOWN_EXCLUDE_COLUMNS,
            max_display_results=1,
        )

        assert "please export to CSV to view." in markdown


class TestEscapeFormula:
    """Formula-injection escaping for spreadsheet exports."""

    @pytest.mark.parametrize("trigger", ["=", "+", "-", "@", "\t", "\r"])
    def test_leading_trigger_is_prefixed_with_quote(self, trigger: str) -> None:
        payload = f"{trigger}SUM(A1)"

        assert _escape_formula(payload) == f"'{payload}"

    def test_benign_string_is_unchanged(self) -> None:
        assert _escape_formula("hostA") == "hostA"
        assert _escape_formula("rack1:u1") == "rack1:u1"

    def test_trigger_not_leading_is_unchanged(self) -> None:
        assert _escape_formula("host=A") == "host=A"

    def test_empty_string_is_unchanged(self) -> None:
        assert _escape_formula("") == ""

    def test_non_string_passes_through(self) -> None:
        assert _escape_formula(None) is None
        assert _escape_formula(42) == 42


class TestExportsEscapeFormulas:
    """Both the CSV and Excel exports must neutralize formula-like cell values."""

    def test_to_csv_dict_escapes_leading_formula_char(self) -> None:
        row = _row("=cmd|'/c calc'!A1", "Link is down.", "rack1:u1")

        assert row.to_csv_dict()["Start Device"] == "'=cmd|'/c calc'!A1"

    def test_xlsx_cell_is_not_interpreted_as_formula(self) -> None:
        rows = [_row("=1+2", "Link is down.", "rack1:u1")]

        sheets = _decode_workbook(_generate_xlsx_link(rows))
        detail = sheets["Cable Issues"]

        assert detail.iloc[0]["Start Device"] == "'=1+2"

    def test_csv_cell_is_escaped(self) -> None:
        rows = [_row("@evil", "Link is down.", "rack1:u1")]

        detail = _decode_csv(_generate_csv_link(rows))

        assert detail.iloc[0]["Start Device"] == "'@evil"
