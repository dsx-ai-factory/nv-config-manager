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
    _generate_csv_link,
    _generate_xlsx_link,
)
from nv_config_manager_workflows.activities.cable_validation.models import (
    CSV_EXCLUDE_COLUMNS,
    HOST_SUMMARY_COLUMNS,
    MARKDOWN_EXCLUDE_COLUMNS,
    CableValidationResultData,
    CableValidationRow,
    _escape_formula,
)


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
