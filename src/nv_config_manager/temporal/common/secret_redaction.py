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
"""Text sanitizers for device secrets surfaced outside a trusted device session.

Config Store is typically locked down to network administrators, while
workflow output (backups, diffs) is viewed by more members for
troubleshooting. Anything read back from a device for that broader audience
must be redacted first.
"""

import re

# Junos encodes any schema-marked secret leaf (login passwords, BGP/OSPF/RIP/IS-IS
# authentication keys, IKE pre-shared keys, RADIUS/TACACS+/SNMP secrets, etc.) into a
# quoted "$<format>$<data>" value at commit time, regardless of which statement holds
# it. $9$ is a published, reversible cipher, and $1$/$5$/$6$/$8$ are hashes or
# master-password encryption within reach of dictionary attacks or key recovery, so
# none of them are safe outside a trusted device session. Matching on the value shape
# instead of a statement-name allowlist catches every secret leaf Junos defines today
# and any it adds later.
_JUNOS_SECRET_VALUE_RE = re.compile(r'"\$([15689])\$[^"]*"')


def redact_junos_secrets(config_text: str) -> str:
    """Replace Junos ``$``-format secret values with a placeholder."""
    return _JUNOS_SECRET_VALUE_RE.sub(r'"$\1$<redacted>"', config_text)
