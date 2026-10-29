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


# IOS XR keeps secrets as ``<keyword> <type> <value>`` (``secret 10 $6$...``,
# ``key-string password 7 ...``, ``key 7 ...``, ``password encrypted ...``) and as the SNMP
# community name itself. The keyword, type and structure stay readable; only the value goes.
# A line can carry a diff marker: ``+`` and ``-`` for added and removed lines, ``#`` for the
# old and new line of one that changed in place.
_IOSXR_LEAD = r"[ \t]*[+#-]?[ \t]*"
_IOSXR_TYPED_SECRET_RE = re.compile(
    rf"^(?P<lead>{_IOSXR_LEAD}(?:username[ \t]+\S+[ \t]+)?)"
    r"(?P<kw>secret|password|key-string(?:[ \t]+password)?|key|shared-secret(?:[ \t]+key)?)"
    r"[ \t]+(?P<type>\d+|encrypted)[ \t]+\S+",
    re.MULTILINE,
)
# MD5 keys of NTP and routing protocols: ``ntp authentication-key 1 md5 encrypted <hash>``,
# ``message-digest-key 1 md5 7 <hash>``.
_IOSXR_MD5_RE = re.compile(
    rf"^(?P<lead>{_IOSXR_LEAD}\S[^\n]*?[ \t]md5)[ \t]+(?P<type>\d+|encrypted)[ \t]+\S+",
    re.MULTILINE,
)
# Newer releases write the type attached to the keyword: ``key-string password7 <hash>``,
# ``key-string password6 <hash>``, or a clear ``key-string clear <text>``.
_IOSXR_ATTACHED_TYPE_RE = re.compile(
    rf"^(?P<lead>{_IOSXR_LEAD})(?P<kw>key-string[ \t]+(?:password\d+|clear))[ \t]+\S+",
    re.MULTILINE,
)
_IOSXR_COMMUNITY_RE = re.compile(
    rf"^(?P<lead>{_IOSXR_LEAD})(?P<kw>snmp-server community)[ \t]+\S+", re.MULTILINE
)


def redact_iosxr_secrets(config_text: str) -> str:
    """Replace the values of IOS XR secret lines with a placeholder."""
    redacted = _IOSXR_TYPED_SECRET_RE.sub(r"\g<lead>\g<kw> \g<type> <redacted>", config_text)
    redacted = _IOSXR_MD5_RE.sub(r"\g<lead> \g<type> <redacted>", redacted)
    redacted = _IOSXR_ATTACHED_TYPE_RE.sub(r"\g<lead>\g<kw> <redacted>", redacted)
    return _IOSXR_COMMUNITY_RE.sub(r"\g<lead>\g<kw> <redacted>", redacted)


def redact_device_secrets(config_text: str) -> str:
    """Redact the secrets of every supported platform from config or diff text."""
    return redact_iosxr_secrets(redact_junos_secrets(config_text))
