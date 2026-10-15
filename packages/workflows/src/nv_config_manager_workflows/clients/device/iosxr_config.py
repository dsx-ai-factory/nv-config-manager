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
"""Cisco IOS XR configuration text handling: normalize, parse, diff, remediate, guard.

Everything here is pure text processing so it can be tested without a router.
The connection class feeds it the output of ``show running-config`` and the
rendered intended configuration.

The diff is client-side. XR has no read-only way to load a candidate and compare
it with the running configuration, so both texts are canonicalized
(:func:`normalize_config`), parsed into trees (:func:`parse_config`) and compared
(:func:`render_diff`). The commands that would make the router match the intended
configuration come from :func:`build_commands`.
"""

from __future__ import annotations

import ipaddress
import re
from dataclasses import dataclass, field

# ---------------------------------------------------------------------------
# Normalization. This is the one place that decides which lines of a
# ``show running-config`` are noise rather than configuration.
# ---------------------------------------------------------------------------

# "Mon Oct 11 10:00:00.123 UTC": the clock line XR prints before show output.
_CLOCK_LINE_RE = re.compile(
    r"^[A-Z][a-z]{2}\s+[A-Z][a-z]{2}\s+\d{1,2}\s+\d{1,2}:\d{2}:\d{2}(\.\d+)?\s+\S+$"
)
_NOISE_LINE_RES = (
    _CLOCK_LINE_RE,
    re.compile(r"^Building configuration\.\.\.$", re.IGNORECASE),
    re.compile(r"^Current configuration\b.*$", re.IGNORECASE),
    re.compile(r"^end$"),
)

# Blocks that end with an explicit terminator instead of by indentation. The
# body is order- and whitespace-sensitive only as a whole, so it is one unit.
_SET_HEADER_RE = re.compile(
    r"^(prefix-set|community-set|as-path-set|rd-set|large-community-set|"
    r"extcommunity-set\s+\S+)\s+\S+"
)
_POLICY_HEADER_RE = re.compile(r"^route-policy\s+\S+")
_GROUP_HEADER_RE = re.compile(r"^group\s+\S+$")
_BANNER_RE = re.compile(r"^banner\s+\S+\s+(\S)(.*)$")

# Leaf commands whose value is replaced by simply entering the new value; they
# need no ``no <old value>`` first. Verified against the XR CLI on the lab
# routers before the first push (see the live runbook).
SINGLETON_KEYWORDS = frozenset(
    {
        "description",
        "mtu",
        "bandwidth",
        "secret",
        "password",
        "hostname",
        "router-id",
        "maximum-paths",
    }
)

# A config this small cannot be a real router configuration; refuse to apply it.
MIN_INTENDED_TOP_LEVEL_NODES = 3


class IosXrConfigError(ValueError):
    """The configuration text cannot be parsed as IOS XR configuration."""


_IPV4_SLASH_RE = re.compile(
    r"^(?P<lead>\s*ipv4 address\s+)(?P<ip>\d+\.\d+\.\d+\.\d+)/(?P<len>\d+)(?P<rest>.*)$"
)


def _canonical_address_line(line: str) -> str:
    """Write ``ipv4 address A.B.C.D/N`` as ``ipv4 address A.B.C.D <netmask>``.

    Releases differ in which form ``show running-config`` prints; one form on both
    sides keeps an unchanged address out of the diff.
    """
    match = _IPV4_SLASH_RE.match(line)
    if not match:
        return line
    try:
        mask = ipaddress.ip_network(f"0.0.0.0/{match.group('len')}").netmask
    except ValueError:
        return line
    return f"{match.group('lead')}{match.group('ip')} {mask}{match.group('rest')}"


def normalize_config(text: str) -> str:
    """Return ``text`` without the lines that are not configuration.

    Removed: the leading clock line, ``Building configuration...``, the
    ``!! IOS XR Configuration`` / ``!! Last configuration change`` comments and
    every other line that starts with ``!`` (block separators and comments),
    blank lines, the trailing ``end``, carriage returns and trailing blanks.
    ``ipv4 address A.B.C.D/N`` becomes the netmask form.
    Indentation is kept because it carries the hierarchy.
    """
    kept: list[str] = []
    for raw in text.replace("\r\n", "\n").replace("\r", "\n").split("\n"):
        line = raw.rstrip()
        stripped = line.strip()
        if not stripped or stripped.startswith("!"):
            continue
        if any(pattern.match(stripped) for pattern in _NOISE_LINE_RES):
            continue
        kept.append(_canonical_address_line(line))
    return "\n".join(kept) + ("\n" if kept else "")


# ---------------------------------------------------------------------------
# Tree.
# ---------------------------------------------------------------------------


@dataclass
class Node:
    """One configuration line, its children, and (for atomic blocks) its body."""

    line: str
    children: list[Node] = field(default_factory=list)
    body: tuple[str, ...] = ()

    @property
    def atomic(self) -> bool:
        """True for blocks compared and replaced as a whole."""
        return bool(self.body)


def _collapse(line: str) -> str:
    return " ".join(line.split())


def _canonical_prefix_set(body: list[str]) -> list[str]:
    """Entries of a prefix-set in the router's order, without the separating commas.

    IOS XR prints the entries sorted as text (``10.54.10.0/23`` before ``10.54.8.0/25``) whatever order they were
    entered in, and set membership has no order, so both sides are compared in that one order.
    """
    entries = sorted(entry.rstrip(",").strip() for entry in body if entry.strip())
    return [f"{entry}," for entry in entries[:-1]] + entries[-1:]


def parse_config(text: str) -> list[Node]:
    """Parse normalized configuration text into a tree by indentation."""
    lines = normalize_config(text).splitlines()
    root = Node("")
    stack: list[tuple[int, Node]] = [(-1, root)]
    index = 0
    while index < len(lines):
        raw = lines[index]
        indent = len(raw) - len(raw.lstrip())
        content = _collapse(raw)
        index += 1
        while stack[-1][0] >= indent:
            stack.pop()
        parent = stack[-1][1]
        node = Node(content)
        # Definitions are top-level only. A nested ``route-policy NAME in`` is a binding, not a block.
        at_top_level = parent is root
        if at_top_level and (
            _POLICY_HEADER_RE.match(content)
            or _SET_HEADER_RE.match(content)
            or _GROUP_HEADER_RE.match(content)
        ):
            if content.startswith("route-policy"):
                terminator = "end-policy"
            elif content.startswith("group"):
                terminator = "end-group"  # a configuration group, as used by ``apply-group``
            else:
                terminator = "end-set"
            body: list[str] = []
            while index < len(lines) and _collapse(lines[index]) != terminator:
                body.append(_collapse(lines[index]))
                index += 1
            if index >= len(lines):
                raise IosXrConfigError(f"Block '{content}' has no '{terminator}'")
            index += 1
            if content.startswith("prefix-set "):
                body = _canonical_prefix_set(body)
            node.body = (*body, terminator)
        else:
            banner = _BANNER_RE.match(content)
            if banner and banner.group(1) not in banner.group(2):
                delimiter = banner.group(1)
                body = []
                while index < len(lines):
                    inner = lines[index].rstrip()
                    index += 1
                    body.append(inner.strip())
                    if delimiter in inner:
                        break
                else:
                    raise IosXrConfigError(f"Banner '{content}' is not terminated")
                node.body = tuple(body)
        existing = next((c for c in parent.children if c.line == node.line), None)
        if existing is not None and not node.atomic:
            node = existing
        else:
            parent.children.append(node)
        stack.append((indent, node))
    return root.children


def _find(nodes: list[Node], line: str) -> Node | None:
    return next((node for node in nodes if node.line == line), None)


def flatten(nodes: list[Node], prefix: tuple[str, ...] = ()) -> list[tuple[str, ...]]:
    """All lines of a tree as paths from the top level, for assertions and guards."""
    paths: list[tuple[str, ...]] = []
    for node in nodes:
        path = (*prefix, node.line)
        paths.append(path)
        paths.extend((*path, line) for line in node.body)
        paths.extend(flatten(node.children, path))
    return paths


# ---------------------------------------------------------------------------
# Diff for the approver.
# ---------------------------------------------------------------------------


def _render_added(node: Node, depth: int, out: list[str]) -> None:
    pad = "  " * depth
    out.append(f"+ {pad}{node.line}")
    out.extend(f"+ {pad}  {line}" for line in node.body)
    for child in node.children:
        _render_added(child, depth + 1, out)


def _render_removed(node: Node, depth: int, out: list[str]) -> None:
    pad = "  " * depth
    out.append(f"- {pad}{node.line}")
    out.extend(f"- {pad}  {line}" for line in node.body)
    for child in node.children:
        _render_removed(child, depth + 1, out)


def _diff_level(running: list[Node], intended: list[Node], depth: int) -> list[str]:
    out: list[str] = []
    for node in running:
        match = _find(intended, node.line)
        if match is None:
            _render_removed(node, depth, out)
            continue
        if node.atomic or match.atomic:
            if node.body != match.body:
                pad = "  " * depth
                out.append(f"  {pad}{node.line}")
                out.extend(f"- {pad}  {line}" for line in node.body)
                out.extend(f"+ {pad}  {line}" for line in match.body)
            continue
        inner = _diff_level(node.children, match.children, depth + 1)
        if inner:
            out.append(f"  {'  ' * depth}{node.line}")
            out.extend(inner)
    for node in intended:
        if _find(running, node.line) is None:
            _render_added(node, depth, out)
    return out


def render_diff(running: list[Node], intended: list[Node]) -> str:
    """Return the diff that turns ``running`` into ``intended`` ('' when converged).

    Lines start with ``-`` (only on the router), ``+`` (only in the intended
    configuration) or two spaces (the parent of a change). The text is
    deterministic, so the diff approved by a person can be compared with the
    diff recomputed at apply time.
    """
    lines = _diff_level(running, intended, 0)
    return "\n".join(lines) + ("\n" if lines else "")


# ---------------------------------------------------------------------------
# Commands.
# ---------------------------------------------------------------------------


def _first_token(line: str) -> str:
    return line.split(" ", 1)[0]


def _added_commands(node: Node) -> list[str]:
    if node.atomic:
        return [node.line, *node.body]
    if not node.children:
        return [node.line]
    commands = [node.line]
    for child in node.children:
        commands.extend(_added_commands(child))
    commands.append("exit")
    return commands


def _command_level(running: list[Node], intended: list[Node]) -> list[str]:
    added_leaves = [
        n for n in intended if not n.children and not n.atomic and _find(running, n.line) is None
    ]
    replaced = {
        _first_token(n.line) for n in added_leaves if _first_token(n.line) in SINGLETON_KEYWORDS
    }
    commands: list[str] = []
    for node in running:
        match = _find(intended, node.line)
        if match is None:
            leaf = not node.children and not node.atomic
            if leaf and _first_token(node.line) in replaced:
                continue
            commands.append(f"no {node.line}")
            continue
        if node.atomic or match.atomic:
            if node.body != match.body:
                commands.extend(_added_commands(match))
            continue
        inner = _command_level(node.children, match.children)
        if inner:
            commands.append(node.line)
            commands.extend(inner)
            commands.append("exit")
    for node in intended:
        if _find(running, node.line) is None:
            commands.extend(_added_commands(node))
    return commands


def build_commands(running: list[Node], intended: list[Node]) -> list[str]:
    """Return the config-mode commands that make ``running`` equal ``intended``.

    Removals are ``no <line>`` (a removed block is removed by its header only),
    additions follow, and every entered sub-mode is left with ``exit``. The
    commands are applied in one atomic commit, so their order does not open a
    window where the router has half a configuration.
    """
    return _command_level(running, intended)


# ---------------------------------------------------------------------------
# Lockout guard.
# ---------------------------------------------------------------------------

_MGMT_INTERFACE_RE = re.compile(r"^interface\s+MgmtEth\S*$", re.IGNORECASE)
_IPV4_ADDRESS_RE = re.compile(r"^ipv4 address\s+(\S+)")
_PROTECTED_TOP_RE = re.compile(r"^(line\s+\S+|vty-pool\s+\S+.*)$")


def _interface_addresses(node: Node) -> list[str]:
    addresses = []
    for child in node.children:
        match = _IPV4_ADDRESS_RE.match(child.line)
        if match:
            addresses.append(match.group(1).split("/")[0])
    return addresses


def _same_host(left: str, right: str) -> bool:
    try:
        return ipaddress.ip_address(left) == ipaddress.ip_address(right)
    except ValueError:
        return left == right


def _protected_interfaces(running: list[Node], host: str) -> list[Node]:
    result = []
    for node in running:
        if not node.line.startswith("interface "):
            continue
        owns_host = any(_same_host(addr, host) for addr in _interface_addresses(node))
        if owns_host or _MGMT_INTERFACE_RE.match(node.line):
            result.append(node)
    return result


def check_access_preserved(
    running: list[Node], intended: list[Node], *, username: str, host: str
) -> list[str]:
    """Return the reasons applying ``intended`` could lock this client out ([] if none).

    A full apply removes whatever the intended configuration omits, so this
    compares what keeps the session working (the account in use, the interface
    that carries the management address, SSH and NETCONF, AAA, VTY transport and the
    management default route) between the router and the intended
    configuration. Values may change; the items may not disappear.
    """
    problems: list[str] = []

    if len(intended) < MIN_INTENDED_TOP_LEVEL_NODES:
        problems.append(
            f"the intended configuration has only {len(intended)} top-level stanza(s); "
            "it looks empty or truncated"
        )

    user_node = _find(running, f"username {username}")
    if user_node is not None:
        wanted = _find(intended, f"username {username}")
        if wanted is None:
            problems.append(
                f"the intended configuration drops the account in use ('username {username}')"
            )
        elif not any(child.line.startswith("group ") for child in wanted.children):
            problems.append(
                f"the intended 'username {username}' has no 'group' line, so the account would lose its privileges"
            )

    protected_vrfs: set[str] = set()
    for node in _protected_interfaces(running, host):
        wanted = _find(intended, node.line)
        if wanted is None:
            problems.append(
                f"the intended configuration drops '{node.line}', which carries management reachability"
            )
            continue
        running_addresses = _interface_addresses(node)
        wanted_addresses = _interface_addresses(wanted)
        if running_addresses and not wanted_addresses:
            problems.append(f"'{node.line}' loses its IPv4 address in the intended configuration")
        if any(_same_host(addr, host) for addr in running_addresses) and not any(
            _same_host(addr, host) for addr in wanted_addresses
        ):
            problems.append(f"'{node.line}' no longer carries the management address {host}")
        for child in node.children:
            if child.line.startswith("vrf "):
                protected_vrfs.add(child.line.split(" ", 1)[1])
                if _find(wanted.children, child.line) is None:
                    problems.append(
                        f"'{node.line}' loses '{child.line}' in the intended configuration"
                    )
        if (
            _find(wanted.children, "shutdown") is not None
            and _find(node.children, "shutdown") is None
        ):
            problems.append(f"the intended configuration shuts down '{node.line}'")

    for node in running:
        if node.line.startswith("ssh server") and _find(intended, node.line) is None:
            problems.append(f"the intended configuration drops '{node.line}'")
        if node.line.startswith("netconf-yang"):
            # NETCONF is the transport NVCM itself uses: the agent and its ``ssh``
            # transport must survive a full apply or the next login fails.
            wanted = _find(intended, node.line)
            if wanted is None:
                problems.append(f"the intended configuration drops '{node.line}' (NETCONF)")
            else:
                for child in node.children:
                    if _find(wanted.children, child.line) is None:
                        problems.append(
                            f"the intended configuration drops '{node.line} / {child.line}' (NETCONF)"
                        )
        if node.line.startswith("aaa authentication") and _find(intended, node.line) is None:
            problems.append(f"the intended configuration drops '{node.line}'")
        if node.line.startswith("aaa "):
            wanted = _find(intended, node.line)
            for child in node.children:
                if child.line.startswith("authentication") and (
                    wanted is None or _find(wanted.children, child.line) is None
                ):
                    problems.append(f"the intended configuration drops 'aaa ... {child.line}'")
        if _PROTECTED_TOP_RE.match(node.line):
            wanted = _find(intended, node.line)
            for child in node.children:
                if child.line.startswith("transport") and (
                    wanted is None or _find(wanted.children, child.line) is None
                ):
                    problems.append(
                        f"the intended configuration drops '{node.line} / {child.line}'"
                    )

    static = _find(running, "router static")
    if static is not None:
        wanted_static = _find(intended, "router static")
        problems.extend(_missing_default_routes(static, wanted_static, protected_vrfs))

    return problems


def _default_route_paths(static: Node, vrfs: set[str]) -> list[tuple[str, ...]]:
    paths = []
    for path in flatten(static.children):
        if not path[-1].startswith(("0.0.0.0/0 ", "::/0 ")):
            continue
        in_vrf = next((p.split(" ", 1)[1] for p in path if p.startswith("vrf ")), None)
        if (in_vrf is None and not vrfs) or (in_vrf is not None and in_vrf in vrfs):
            paths.append(path)
    return paths


def _missing_default_routes(static: Node, wanted: Node | None, vrfs: set[str]) -> list[str]:
    present = set(flatten(wanted.children)) if wanted is not None else set()
    return [
        f"the intended configuration drops the management default route ({' / '.join(path)})"
        for path in _default_route_paths(static, vrfs)
        if path not in present
    ]
