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

# Junos encodes schema-marked secret leaves into a quoted
# ``$<format>$<data>`` value at commit time. Match the value shape instead of a
# statement-name allowlist so new secret leaves are covered automatically.
_JUNOS_SECRET_VALUE_RE = re.compile(r'"\$([15689])\$[^"]*"')


def redact_junos_secrets(config_text: str) -> str:
    """Replace Junos ``$``-format secret values with a placeholder."""
    return _JUNOS_SECRET_VALUE_RE.sub(r'"$\1$<redacted>"', config_text)
