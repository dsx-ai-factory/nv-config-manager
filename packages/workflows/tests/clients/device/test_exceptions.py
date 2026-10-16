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

import json

import pytest

from nv_config_manager_workflows.clients.device.exceptions import (
    ConfigApplyFailureException,
    ConfigSyntaxException,
)


@pytest.mark.parametrize(
    "transition",
    [{}, {"state": "ignore_fail"}, {"issue": {}}, {"other": 1}],
)
def test_format_nvue_apply_error_without_issues_includes_raw_transition(
    transition: dict[str, object],
) -> None:
    result = ConfigApplyFailureException.format_nvue_apply_error(transition)

    assert "Configuration apply failed" in result
    assert json.dumps(transition) in result


def test_format_nvue_apply_error_formats_issues_and_defaults() -> None:
    transition = {
        "progress": "Failure during apply. Ignore?",
        "issue": {
            "00000": {
                "code": "systemctl",
                "message": "Unable to reload-or-restart services (frr)",
                "severity": "error",
            }
        },
    }

    result = ConfigApplyFailureException.format_nvue_apply_error(transition)
    defaulted = ConfigApplyFailureException.format_nvue_apply_error(
        {"issue": {"00000": {"message": "Only message"}}}
    )

    assert "Failure during apply. Ignore?" in result
    assert "[ERROR] systemctl: Unable to reload-or-restart services (frr)" in result
    assert "Configuration apply failed" in defaulted
    assert "[UNKNOWN] unknown: Only message" in defaulted


def test_format_nvue_config_syntax_error() -> None:
    """Test formatting of NVUE API syntax error JSON."""
    error_json = {
        "detail": "Error: Unevaluated properties are not "
        "allowed ('ROUTE-SERVER-CLIENTS' was "
        "unexpected: expected ['@clear', "
        "'address-family', "
        "'autonomous-system', 'confederation', "
        "'dynamic-neighbor', 'enable', 'in', "
        "'neighbor', 'out', 'path-selection', "
        "'peer-group', 'rd', 'route-export', "
        "'route-import', 'route-reflection', "
        "'router-id', 'soft', 'timers'])",
        "status": 400,
        "title": "Bad Request",
        "type": "about:blank",
        "validation": {
            "selected_errors": [
                {
                    "error": "Unevaluated "
                    "properties "
                    "are "
                    "not "
                    "allowed "
                    "('ROUTE-SERVER-CLIENTS' "
                    "was "
                    "unexpected: "
                    "expected "
                    "['@clear', "
                    "'address-family', "
                    "'autonomous-system', "
                    "'confederation', "
                    "'dynamic-neighbor', "
                    "'enable', "
                    "'in', "
                    "'neighbor', "
                    "'out', "
                    "'path-selection', "
                    "'peer-group', "
                    "'rd', "
                    "'route-export', "
                    "'route-import', "
                    "'route-reflection', "
                    "'router-id', "
                    "'soft', "
                    "'timers'])",
                    "instanceLocation": "#/vrf/default/router/bgp",
                    "keywordLocation": "#/allOf/0/properties/vrf/allOf/0/additionalProperties/allOf/0/properties/router/allOf/0/properties/bgp/x-unevaluatedProperties",
                }
            ]
        },
    }
    expected_output = (
        "Error at '#/vrf/default/router/bgp': "
        "Unevaluated properties are not allowed ('ROUTE-SERVER-CLIENTS' was "
        "unexpected: expected ['@clear', "
        "'address-family', "
        "'autonomous-system', "
        "'confederation', "
        "'dynamic-neighbor', "
        "'enable', 'in', 'neighbor', 'out', 'path-selection', "
        "'peer-group', 'rd', 'route-export', 'route-import', "
        "'route-reflection', 'router-id', 'soft', 'timers'])"
    )
    assert ConfigSyntaxException.format_nvue_error(error_json) == expected_output
