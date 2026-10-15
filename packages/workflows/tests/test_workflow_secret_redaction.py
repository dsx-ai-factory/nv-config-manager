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
"""Tests for workflow-owned device secret redaction helpers."""

import pytest

from nv_config_manager_workflows.secret_redaction import (
    redact_device_secrets,
    redact_iosxr_secrets,
    redact_junos_secrets,
)


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
def test_redacts_any_statement_by_value_shape(
    statement: str,
    value: str,
    redacted_tag: str,
) -> None:
    config = f'system {{\n    {statement} "{value}"; ## SECRET-DATA\n}}'

    result = redact_junos_secrets(config)

    assert value not in result
    assert f'{statement} "${redacted_tag}$<redacted>"; ## SECRET-DATA' in result


def test_redacts_multiple_values_in_one_blob() -> None:
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


def test_preserves_non_secret_text() -> None:
    config = "system {\n    host-name RTR1;\n}\n"

    assert redact_junos_secrets(config) == config


def test_empty_string_is_unchanged() -> None:
    assert redact_junos_secrets("") == ""


def test_redact_iosxr_secrets_hides_values_and_keeps_structure() -> None:
    config = (
        "username admin\n"
        " group root-lr\n"
        " secret 10 $6$salt$hashvalue\n"
        "snmp-server community topsecretstring RO\n"
        "key chain KC\n"
        " key 1\n"
        "  key-string password 7 0822455D0A16\n"
        "tacacs-server host 10.0.0.1\n"
        " key 7 094F471A1A0A\n"
        "hostname r1\n"
    )

    result = redact_iosxr_secrets(config)

    for leaked in ("$6$salt$hashvalue", "topsecretstring", "0822455D0A16", "094F471A1A0A"):
        assert leaked not in result
    assert " secret 10 <redacted>\n" in result
    assert "snmp-server community <redacted> RO\n" in result
    assert "  key-string password 7 <redacted>\n" in result
    assert " key 7 <redacted>\n" in result
    assert " key 1\n" in result
    assert "hostname r1\n" in result


def test_redact_iosxr_secrets_redacts_diff_lines_with_markers() -> None:
    diff = "-   secret 10 $6$old$aaaa\n+   secret 10 $6$new$bbbb\n"

    result = redact_iosxr_secrets(diff)

    assert "aaaa" not in result and "bbbb" not in result
    assert result.count("<redacted>") == 2


def test_redact_device_secrets_applies_both_platforms() -> None:
    text = 'encrypted-password "$6$abc$def";\nsecret 10 $6$x$y\n'

    result = redact_device_secrets(text)

    assert "$def" not in result and "$x$y" not in result


def test_iosxr_key_string_with_the_type_attached_to_the_keyword_is_redacted() -> None:
    text = (
        "   key-string password7 0822455D0A16\n"
        "   key-string password6 AbCdEf\n"
        "   key-string clear secretvalue\n"
        "   cryptographic-algorithm aes-256-cmac\n"
    )

    result = redact_iosxr_secrets(text)

    assert "   key-string password7 <redacted>\n" in result
    assert "   key-string password6 <redacted>\n" in result
    assert "   key-string clear <redacted>\n" in result
    assert "0822455D0A16" not in result and "secretvalue" not in result
    assert "cryptographic-algorithm aes-256-cmac" in result
