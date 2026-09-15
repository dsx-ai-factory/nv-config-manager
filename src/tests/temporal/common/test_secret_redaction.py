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
"""Tests for device secret redaction helpers."""

import pytest

from nv_config_manager.temporal.common.secret_redaction import redact_junos_secrets


@pytest.mark.parametrize(
    ("statement", "value", "redacted_tag"),
    [
        ("encrypted-password", "$6$abcDE12$ghijklmnopqrstuvwxyz0123456789ABCDEFGH", "6"),
        ("authentication-key", "$9$AbCdEfGhIjKlMnOpQrSt", "9"),
        ("pre-shared-key ascii-text", "$9$XyZ123AbCdEfGhIjK", "9"),
        ("community", "$9$MnOpQrStUvWxYz012345", "9"),
        ("master-password", "$8$AbCdEfGhIjKlMnOp", "8"),
    ],
)
def test_redact_junos_secrets_redacts_any_statement_by_value_shape(statement, value, redacted_tag):
    """Any quoted Junos $-format secret value is redacted, regardless of statement."""
    config = f'system {{\n    {statement} "{value}"; ## SECRET-DATA\n}}'
    result = redact_junos_secrets(config)
    assert value not in result
    assert f'{statement} "${redacted_tag}$<redacted>"; ## SECRET-DATA' in result


def test_redact_junos_secrets_handles_multiple_values_in_one_blob():
    """Every secret value in a multi-statement config is redacted independently."""
    config = (
        "system {\n"
        "    root-authentication {\n"
        '        encrypted-password "$6$hash1$abcdefgh"; ## SECRET-DATA\n'
        "    }\n"
        "}\n"
        "protocols {\n"
        "    bgp {\n"
        "        group EXT {\n"
        "            neighbor 10.0.0.1 {\n"
        '                authentication-key "$9$key1value"; ## SECRET-DATA\n'
        "            }\n"
        "        }\n"
        "    }\n"
        "}\n"
    )
    result = redact_junos_secrets(config)
    assert '"$6$<redacted>"' in result
    assert '"$9$<redacted>"' in result
    assert "hash1" not in result
    assert "key1value" not in result
    assert "root-authentication" in result
    assert "neighbor 10.0.0.1" in result


def test_redact_junos_secrets_preserves_non_secret_text():
    """Redaction only touches $-format secret values, not the rest of the config."""
    config = "system {\n    host-name RTR1;\n}\n"
    assert redact_junos_secrets(config) == config


def test_redact_junos_secrets_is_noop_on_empty_string():
    """An empty config is returned unchanged."""
    assert redact_junos_secrets("") == ""
