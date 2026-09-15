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
"""BGP filter tests."""

import pytest

from nv_config_manager_templates.filters import FilterException
from nv_config_manager_templates.filters.bgp import asplain


def test_asplain_converts_asdot() -> None:
    """ASDOT values are converted to ASPLAIN."""
    assert asplain("1.1") == 65537


def test_asplain_returns_int_and_allows_max_asn() -> None:
    """Plain ASN input returns an int and includes the 32-bit maximum."""
    assert asplain("65000") == 65000
    assert isinstance(asplain("65000"), int)
    assert asplain("4294967295") == 4294967295


def test_asplain_invalid() -> None:
    """Invalid ASNs raise filter exceptions."""
    with pytest.raises(FilterException):
        asplain("abc.def")

    with pytest.raises(FilterException):
        asplain("4294967295.1")

    with pytest.raises(FilterException):
        asplain("0")
