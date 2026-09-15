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

from nv_config_manager.temporal.client.device import ConfigSyntaxException


def test_format_nvue_config_syntax_error():
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
