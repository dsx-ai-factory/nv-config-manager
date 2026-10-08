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
"""BGP related filters."""

import re

from nv_config_manager_templates.filters import FilterException


def asplain(value: str) -> int:
    """Convert an asdot ASN to asplain."""
    # Check for asplain ASN, no conversion needed
    try:
        asn = int(value)
        if 0 < asn <= 4294967295:
            return asn
        raise FilterException("Invalid BGP ASN supplied.")
    except ValueError:
        pass

    # If 4-byte, convert
    match = re.match(r"^(\d+)(\.|:)(\d+)$", value)
    if not match:
        raise FilterException("Invalid ASDOT BGP ASN supplied.")
    left = int(match.group(1))
    right = int(match.group(3))

    conv = left * 65536 + right
    if conv < 1 or conv > 4294967295:
        raise FilterException("Invalid ASDOT BGP ASN supplied.")
    return conv
