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
    redact_junos_secrets,
    redact_secrets_in_data,
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


@pytest.mark.parametrize(
    ("line", "secret", "kept"),
    [
        (
            "nv set system aaa user admin hashed-password $6$salt$hashvalue",
            "$6$salt$hashvalue",
            "nv set system aaa user admin hashed-password <redacted>",
        ),
        (
            "nv unset system aaa user admin password Plaintext1!",
            "Plaintext1!",
            "nv unset system aaa user admin password <redacted>",
        ),
        (
            "nv set vrf default router bgp neighbor 10.0.0.1 password BgpSecret",
            "BgpSecret",
            "neighbor 10.0.0.1 password <redacted>",
        ),
        (
            "+username admin privilege 15 role network-admin secret sha512 $6$abc$def",
            "$6$abc$def",
            "secret sha512 <redacted>",
        ),
        ("+   neighbor 10.0.0.1 password 7 AbCdEf==", "AbCdEf==", "password 7 <redacted>"),
        ("+tacacs-server host 10.1.1.1 key 7 0A1B2C3D", "0A1B2C3D", "key 7 <redacted>"),
        ("+snmp-server community s3cr3t ro", "s3cr3t", "community <redacted> ro"),
        (
            "username admin password 7 $6$onyxsalt$onyxhash",
            "$6$onyxsalt$onyxhash",
            "username admin password 7 <redacted>",
        ),
        (
            '+    encrypted-password "$6$jsalt$jhash"; ## SECRET-DATA',
            "jhash",
            "encrypted-password <redacted>; ## SECRET-DATA",
        ),
    ],
)
def test_redact_device_secrets_covers_every_platform_format(
    line: str,
    secret: str,
    kept: str,
) -> None:
    result = redact_device_secrets(line)

    assert secret not in result
    assert kept in result


def test_redact_device_secrets_preserves_non_secret_text() -> None:
    text = (
        "nv set interface swp1 link mtu 9216\n"
        "Password Rotation Configuration Diff (Auto-Approved password-only changes)"
    )

    assert redact_device_secrets(text) == text


def test_redact_secrets_in_data_walks_nested_workflow_details() -> None:
    details = {
        "id": "wf-1",
        "status": "Completed",
        "stages": [
            {
                "output": {
                    "diff": "nv set system aaa user admin hashed-password $6$s$h",
                    "approved": True,
                }
            }
        ],
        "search_attributes": {"Site": ["jhb01"]},
        "close_time": None,
    }

    result = redact_secrets_in_data(details)

    assert result["stages"][0]["output"]["diff"] == (
        "nv set system aaa user admin hashed-password <redacted>"
    )
    assert result["stages"][0]["output"]["approved"] is True
    assert result["search_attributes"] == {"Site": ["jhb01"]}
    assert result["close_time"] is None
    assert result["id"] == "wf-1"
