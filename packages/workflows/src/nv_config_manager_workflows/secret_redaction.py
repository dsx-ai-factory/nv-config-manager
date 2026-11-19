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
    r"[ \t]+(?P<type>\d+|encrypted|clear)(?P<enc>(?:[ \t]+enc-type[ \t]+\d+)?)[ \t]+\S+",
    re.MULTILINE,
)
# MD5 keys of NTP and routing protocols: ``ntp authentication-key 1 md5 encrypted <hash>``,
# ``message-digest-key 1 md5 7 <hash>``, ``md5 clear <key>`` or a bare ``md5 <key>``. Only
# these key commands match, so a description or other text that mentions md5 is left alone.
_IOSXR_MD5_RE = re.compile(
    rf"^(?P<lead>{_IOSXR_LEAD}(?:ntp[ \t]+)?(?:authentication-key|message-digest-key)[ \t]+\S+[ \t]+md5)"
    r"[ \t]+(?P<type>(?:\d+|encrypted|clear)[ \t]+)?\S+",
    re.MULTILINE,
)
# Newer releases write the type attached to the keyword: ``key-string password7 <hash>``,
# ``key-string password6 <hash>``, or a clear ``key-string clear <text>``.
_IOSXR_ATTACHED_TYPE_RE = re.compile(
    rf"^(?P<lead>{_IOSXR_LEAD})(?P<kw>key-string[ \t]+(?:password\d+|clear))[ \t]+\S+",
    re.MULTILINE,
)
# ``snmp-server community [clear|encrypted|<n>] <name> [RO|RW] ...``: the community name is the
# secret and sits after the optional type. A first word counts as the type only when a name
# follows it, so a community that is itself called ``clear`` is still redacted.
_IOSXR_COMMUNITY_RE = re.compile(
    rf"^(?P<lead>{_IOSXR_LEAD})(?P<kw>snmp-server community)[ \t]+"
    r"(?P<type>(?:clear|encrypted|\d+)[ \t]+(?!(?i:ro|rw|view|sdrowner|systemowner|ipv4|ipv6)\b))?\S+",
    re.MULTILINE,
)
# SNMPv3 users carry two secrets on one line: ``snmp-server user <name> <group> v3 auth md5 clear
# <password> priv aes 128 clear <password>``. The type (clear, encrypted or a number) stays.
_IOSXR_SNMP_USER_RE = re.compile(rf"^{_IOSXR_LEAD}snmp-server[ \t]+user[ \t][^\n]*$", re.MULTILINE)
_IOSXR_SNMP_AUTH_RE = re.compile(r"(?P<head>\bauth[ \t]+\S+[ \t]+(?:clear|encrypted|\d+)[ \t]+)\S+")
_IOSXR_SNMP_PRIV_RE = re.compile(
    r"(?P<head>\bpriv[ \t]+(?:aes[ \t]+\d+|\S+)[ \t]+(?:clear|encrypted|\d+)[ \t]+)\S+"
)


# Secrets that follow a command prefix and not just ``username <name>``. Each pattern names the
# command (``head``), an optional type word (``type``, kept with its trailing space) and the
# value, which is the only part that goes.
_IOSXR_TYPE = r"(?:\d+|encrypted|clear)"
_IOSXR_PREFIXED_SECRET_RES = (
    # ``tacacs-server key 7 <hash>``, ``radius-server host <ip> auth-port 1812 key 7 <hash>``,
    # ``server-private <ip> port 49 key <text>``
    re.compile(
        rf"^(?P<lead>{_IOSXR_LEAD})"
        r"(?P<head>(?:(?:tacacs|radius)-server|server-private)\b[^\n]*?[ \t]key)"
        rf"[ \t]+(?P<type>{_IOSXR_TYPE}[ \t]+)?\S+",
        re.MULTILINE,
    ),
    # ``snmp-server host <ip> traps version 2c [encrypted] <community> udp-port 162``; version 3
    # names a user, not a community, so it does not match
    re.compile(
        rf"^(?P<lead>{_IOSXR_LEAD})"
        r"(?P<head>snmp-server[ \t]+host[ \t]+\S+(?:[ \t]+(?:traps|informs))?"
        r"(?:[ \t]+version[ \t]+(?:1|2c))?(?:[ \t]+(?:encrypted|clear))?)"
        r"[ \t]+(?!(?:version|udp-port|vrf|traps|informs|encrypted|clear)\b)(?P<type>)\S+",
        re.MULTILINE,
    ),
    # IS-IS ``hello-password hmac-md5 encrypted <hash>``, ``lsp-password text clear <text>``
    re.compile(
        rf"^(?P<lead>{_IOSXR_LEAD})"
        rf"(?P<head>(?:hello|lsp)-password[ \t]+(?:hmac-md5|text)(?:[ \t]+{_IOSXR_TYPE})?)"
        r"[ \t]+(?!(?:level|send-only|snmp|keychain)\b)(?P<type>)\S+",
        re.MULTILINE,
    ),
    # LDP ``neighbor <ip> password encrypted <hash>``
    re.compile(
        rf"^(?P<lead>{_IOSXR_LEAD})(?P<head>neighbor[ \t]+\S+[ \t]+password)"
        rf"[ \t]+(?P<type>{_IOSXR_TYPE}[ \t]+)?\S+",
        re.MULTILINE,
    ),
    # OSPF ``authentication-key encrypted <key>``: the router prints the type, and requiring it
    # keeps Junos ``authentication-key "<value>"`` lines out of this pattern (the md5 forms are
    # handled above)
    re.compile(
        rf"^(?P<lead>{_IOSXR_LEAD})(?P<head>authentication-key)"
        rf"[ \t]+(?!(?:\S+[ \t]+)?md5\b)(?P<type>{_IOSXR_TYPE}[ \t]+)\S+",
        re.MULTILINE,
    ),
)


def _redact_snmp_user(match: re.Match[str]) -> str:
    line = _IOSXR_SNMP_AUTH_RE.sub(r"\g<head><redacted>", match.group(0))
    return _IOSXR_SNMP_PRIV_RE.sub(r"\g<head><redacted>", line)


def redact_iosxr_secrets(config_text: str) -> str:
    """Replace the values of IOS XR secret lines with a placeholder."""
    redacted = _IOSXR_TYPED_SECRET_RE.sub(r"\g<lead>\g<kw> \g<type>\g<enc> <redacted>", config_text)
    redacted = _IOSXR_MD5_RE.sub(r"\g<lead> \g<type><redacted>", redacted)
    redacted = _IOSXR_ATTACHED_TYPE_RE.sub(r"\g<lead>\g<kw> <redacted>", redacted)
    for pattern in _IOSXR_PREFIXED_SECRET_RES:
        redacted = pattern.sub(r"\g<lead>\g<head> \g<type><redacted>", redacted)
    redacted = _IOSXR_SNMP_USER_RE.sub(_redact_snmp_user, redacted)
    return _IOSXR_COMMUNITY_RE.sub(r"\g<lead>\g<kw> \g<type><redacted>", redacted)


def redact_device_secrets(config_text: str) -> str:
    """Redact the secrets of every supported platform from config or diff text."""
    return redact_iosxr_secrets(redact_junos_secrets(config_text))


# Fields of workflow inputs and stage data that carry a device configuration or a diff of it.
_CONFIG_FIELDS = frozenset(
    {
        "diff",
        "diff_content",
        "approved_diff",
        "invalid_diff",
        "intended_config",
        "running_config",
        "configuration",
    }
)


def redact_diff_fields(value: Any) -> Any:
    """Return a copy of ``value`` with the secrets in its configuration and diff fields redacted.

    For what an API returns about a workflow: the stored stage data and workflow input keep the
    raw text, which the apply step compares exactly, while the copy sent out does not carry
    credentials. Only string values of those fields (at any depth) change.
    """
    if isinstance(value, dict):
        return {
            key: redact_device_secrets(item)
            if key in _CONFIG_FIELDS and isinstance(item, str)
            else redact_diff_fields(item)
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [redact_diff_fields(item) for item in value]
    return value
