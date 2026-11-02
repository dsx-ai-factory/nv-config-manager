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
workflow output such as backups and diffs is viewed more broadly. Anything
read back from a device for that broader audience must be redacted first.
"""

import re
from typing import Any

# Junos encodes schema-marked secret leaves into a quoted
# ``$<format>$<data>`` value at commit time. Match the value shape instead of a
# statement-name allowlist so new secret leaves are covered automatically.
_JUNOS_SECRET_VALUE_RE = re.compile(r'"\$([15689])\$[^"]*"')

# Cumulus/NVOS (``nv set ... hashed-password <v>``), Arista (``secret sha512 <v>``,
# ``password 7 <v>``, ``key 7 <v>``), and Onyx (``password 7 <v>``) put the secret in
# the token after a keyword, optionally preceded by an encoding type. Matching is
# case-sensitive because device syntax is lowercase; prose such as "Password Rotation"
# in stage displays is left alone. Over-redacting a non-secret token is harmless.
_SECRET_KEYWORD_VALUE_RE = re.compile(
    r"(\b(?:hashed-password|encrypted-password|password|secret|community|"
    r"pre-shared-key|authentication-key|auth-key|key)\s+(?:(?:sha512|[0-9])\s+)?)"
    r'("[^"]*"|\S+)'
)


def redact_junos_secrets(config_text: str) -> str:
    """Replace Junos ``$``-format secret values with a placeholder."""
    return _JUNOS_SECRET_VALUE_RE.sub(r'"$\1$<redacted>"', config_text)


def redact_device_secrets(text: str) -> str:
    """Replace device secret values for every supported platform with a placeholder."""
    return redact_junos_secrets(_SECRET_KEYWORD_VALUE_RE.sub(r"\1<redacted>", text))


def redact_secrets_in_data(value: Any) -> Any:
    """Recursively apply :func:`redact_device_secrets` to every string in ``value``."""
    if isinstance(value, str):
        return redact_device_secrets(value)
    if isinstance(value, dict):
        return {key: redact_secrets_in_data(item) for key, item in value.items()}
    if isinstance(value, list):
        return [redact_secrets_in_data(item) for item in value]
    return value
