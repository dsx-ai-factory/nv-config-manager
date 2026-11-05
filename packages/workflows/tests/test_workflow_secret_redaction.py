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
    redact_diff_fields,
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


def test_redact_iosxr_secrets_redacts_one_line_username_secrets() -> None:
    text = "username admin secret 10 $6$salt$hashvalue\n+username ops password 7 0822455D0A16\n"

    result = redact_iosxr_secrets(text)

    assert "hashvalue" not in result
    assert "0822455D0A16" not in result
    assert "username admin secret 10 <redacted>\n" in result
    assert "+username ops password 7 <redacted>\n" in result


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


def test_iosxr_encrypted_secrets_and_md5_keys_are_redacted() -> None:
    text = (
        "username ops\n"
        " password encrypted 0A1B2C3D4E5F\n"
        "username admin secret encrypted 6F5E4D3C2B1A\n"
        "ntp authentication-key 1 md5 encrypted 1A2B3C4D5E6F\n"
        " message-digest-key 10 md5 encrypted 9F8E7D6C5B4A\n"
        " message-digest-key 11 md5 7 0822455D0A16\n"
        "   key-string password encrypted 7C6B5A493827\n"
        "description encrypted link to pe1\n"
        "hostname r1\n"
    )

    result = redact_iosxr_secrets(text)

    for leaked in (
        "0A1B2C3D4E5F",
        "6F5E4D3C2B1A",
        "1A2B3C4D5E6F",
        "9F8E7D6C5B4A",
        "0822455D0A16",
        "7C6B5A493827",
    ):
        assert leaked not in result
    assert " password encrypted <redacted>\n" in result
    assert "username admin secret encrypted <redacted>\n" in result
    assert "ntp authentication-key 1 md5 encrypted <redacted>\n" in result
    assert " message-digest-key 10 md5 encrypted <redacted>\n" in result
    assert " message-digest-key 11 md5 7 <redacted>\n" in result
    assert "   key-string password encrypted <redacted>\n" in result
    assert "description encrypted link to pe1\n" in result
    assert "hostname r1\n" in result


def test_iosxr_secrets_on_diff_marker_lines_are_redacted() -> None:
    diff = (
        "-   secret 10 $6$old$aaaa\n"
        "+   secret 10 $6$new$bbbb\n"
        "#   secret 10 $6$old$cccc\n"
        "#   secret 10 $6$new$dddd\n"
        "#  ntp authentication-key 1 md5 encrypted 1A2B3C4D5E6F\n"
        "#  snmp-server community topsecretstring RO\n"
    )

    result = redact_iosxr_secrets(diff)

    for leaked in ("aaaa", "bbbb", "cccc", "dddd", "1A2B3C4D5E6F", "topsecretstring"):
        assert leaked not in result
    assert result.count("<redacted>") == 6


def test_iosxr_typed_secret_with_enc_type_redacts_the_value() -> None:
    text = "username ops secret 0 enc-type 10 testpassword\n"

    result = redact_iosxr_secrets(text)

    assert "testpassword" not in result
    assert result == "username ops secret 0 enc-type 10 <redacted>\n"


def test_iosxr_clear_and_untyped_md5_keys_are_redacted() -> None:
    text = (
        " message-digest-key 1 md5 clear plainkey1\n"
        " message-digest-key 2 md5 plainkey2\n"
        "ntp authentication-key 3 md5 7 0822455D0A16\n"
        "hostname r1\n"
    )

    result = redact_iosxr_secrets(text)

    assert "plainkey1" not in result
    assert "plainkey2" not in result
    assert " message-digest-key 1 md5 clear <redacted>\n" in result
    assert " message-digest-key 2 md5 <redacted>\n" in result
    assert "ntp authentication-key 3 md5 7 <redacted>\n" in result
    assert "hostname r1\n" in result


def test_iosxr_md5_redaction_leaves_other_lines_that_mention_md5_alone() -> None:
    text = (
        "description check md5 checksum\n"
        "ntp authentication-key 1 md5 encrypted 1A2B3C4D5E6F\n"
        " message-digest-key 2 md5 7 0822455D0A16\n"
    )

    result = redact_iosxr_secrets(text)

    assert "description check md5 checksum\n" in result
    assert "1A2B3C4D5E6F" not in result
    assert "0822455D0A16" not in result


def test_redact_diff_fields_redacts_diffs_at_any_depth_and_leaves_the_original_alone() -> None:
    secret_line = "+   secret 10 $6$new$bbbb"
    data = {
        "diff": secret_line,
        "approved_diff": secret_line,
        "diff_group": {"diff_content": secret_line, "devices": [{"diff": secret_line, "n": 1}]},
        "intended_config": "username ops\n secret 10 $6$new$bbbb\nhostname r1\n",
        "running_config": " secret 10 $6$old$aaaa",
        "display": "kept as is",
        "other": [secret_line],
        "count": 3,
    }

    result = redact_diff_fields(data)

    assert result["diff"] == "+   secret 10 <redacted>"
    assert result["approved_diff"] == "+   secret 10 <redacted>"
    assert result["diff_group"]["diff_content"] == "+   secret 10 <redacted>"
    assert result["diff_group"]["devices"][0] == {"diff": "+   secret 10 <redacted>", "n": 1}
    assert result["intended_config"] == "username ops\n secret 10 <redacted>\nhostname r1\n"
    assert result["running_config"] == " secret 10 <redacted>"
    assert result["display"] == "kept as is"
    assert result["other"] == [secret_line]  # not a diff field
    assert result["count"] == 3
    assert data["diff"] == secret_line  # the stored value is unchanged
