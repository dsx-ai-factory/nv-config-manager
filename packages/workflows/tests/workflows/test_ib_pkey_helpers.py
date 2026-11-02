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
"""Tests for the shared IB PKey workflow helpers."""

from typing import Any, cast

import pytest

from nv_config_manager_workflows.workflows._ib_pkey_helpers import (
    normalize_membership_type,
    validate_pkey_format,
)


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("0x0001", "0x0001"),
        ("0x0100", "0x0100"),
        ("0xffff", "0xffff"),
        ("0x1", "0x0001"),
        ("0x01", "0x0001"),
        ("0x001", "0x0001"),
        ("0X100", "0x0100"),
        ("0xFFFF", "0xffff"),
        ("  0x100  ", "0x0100"),
        ("\t0x8001\n", "0x8001"),
    ],
)
def test_validate_pkey_format_canonicalizes(raw: Any, expected: Any) -> None:
    """Accept any 0x + 1-4 hex digit form (with surrounding whitespace) and return 0x + 4 lowercase hex."""
    assert validate_pkey_format(raw) == expected


@pytest.mark.parametrize(
    "bad",
    [
        "",
        "   ",
        "5",
        "0x",
        "0xZZZZ",
        "0x12345",
        "x100",
        "100",
        "0x 100",
    ],
)
def test_validate_pkey_format_rejects_invalid(bad: Any) -> None:
    """Reject anything that isn't 0x + 1-4 hex digits."""
    with pytest.raises(ValueError, match="pkey must be hex"):
        validate_pkey_format(bad)


def test_validate_pkey_format_rejects_none() -> None:
    """None is treated as an empty pkey and rejected."""
    with pytest.raises(ValueError, match="pkey must be hex"):
        validate_pkey_format(cast(Any, None))


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("full", "full"),
        ("limited", "limited"),
        ("FULL", "full"),
        ("Limited", "limited"),
        ("  full  ", "full"),
        ("\tlimited\n", "limited"),
    ],
)
def test_normalize_membership_type_honors_supplied(raw: Any, expected: Any) -> None:
    """A supplied full/limited value (any case/whitespace) is honored."""
    assert normalize_membership_type(raw) == expected


@pytest.mark.parametrize("blank", ["", "   ", "\t\n", None])
def test_normalize_membership_type_defaults_blank_to_full(blank: Any) -> None:
    """Blank or missing membership defaults to the PKey default 'full'."""
    assert normalize_membership_type(blank) == "full"


@pytest.mark.parametrize("bad", ["partial", "none", "fll", "0", "limitedd"])
def test_normalize_membership_type_rejects_invalid(bad: Any) -> None:
    """Anything other than full/limited is rejected with a clear message."""
    with pytest.raises(ValueError, match="membership_type must be 'full' or 'limited'"):
        normalize_membership_type(bad)


@pytest.mark.parametrize("bad", [1, 0, True, False, 1.5, ["full"], {"full"}])
def test_normalize_membership_type_rejects_non_string(bad: Any) -> None:
    """Non-string input raises ValueError, not AttributeError (clean 422)."""
    with pytest.raises(ValueError, match="membership_type must be 'full' or 'limited'"):
        normalize_membership_type(bad)
