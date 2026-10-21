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
"""Unit tests for IOS XR config normalization, parsing, diff, commands and the lockout guard."""

import pytest

from nv_config_manager_workflows.clients.device import iosxr_config as xr

from .iosxr_fixtures import HOST, INTENDED, RUNNING, USERNAME


def _guard(intended: str, running: str = RUNNING) -> list[str]:
    return xr.check_access_preserved(
        xr.parse_config(running), xr.parse_config(intended), username=USERNAME, host=HOST
    )


def _diff(running: str, intended: str) -> str:
    return xr.render_diff(xr.parse_config(running), xr.parse_config(intended))


def _commands(running: str, intended: str) -> list[str]:
    return xr.build_commands(xr.parse_config(running), xr.parse_config(intended))


def _without(text: str, *lines: str) -> str:
    result = text
    for line in lines:
        assert line in result, line
        result = result.replace(line + "\n", "", 1)
    return result


# --- normalization ---------------------------------------------------------


def test_normalize_strips_noise_but_keeps_hierarchy_and_secrets() -> None:
    result = xr.normalize_config(RUNNING)

    assert "Building configuration" not in result
    assert "Last configuration change" not in result
    assert "Mon Oct 11" not in result
    assert not any(line.startswith("!") for line in result.splitlines())
    assert not result.splitlines()[-1] == "end"
    assert " secret 10 $6$H0QFj2rx$placeholderplaceholder" in result
    assert "\n group root-lr\n" in result


def test_normalize_writes_slash_addresses_in_netmask_form() -> None:
    result = xr.normalize_config(
        "interface Loopback0\n ipv4 address 10.0.0.7/32\n ipv4 address 10.0.0.8/24 secondary\n"
    )

    assert " ipv4 address 10.0.0.7 255.255.255.255\n" in result
    assert " ipv4 address 10.0.0.8 255.255.255.0 secondary\n" in result
    assert _diff(RUNNING, INTENDED.replace("10.0.0.7 255.255.255.255", "10.0.0.7/32")) == ""


def test_normalize_handles_crlf_and_blank_lines() -> None:
    assert xr.normalize_config("hostname a\r\n\r\n!\r\nend\r\n") == "hostname a\n"


# --- parser ----------------------------------------------------------------


def test_parse_builds_nested_tree() -> None:
    tree = xr.parse_config(RUNNING)
    isis = next(node for node in tree if node.line == "router isis CORE")

    assert [c.line for c in isis.children] == [
        "net 49.0001.0100.0000.0007.00",
        "address-family ipv4 unicast",
        "interface HundredGigE0/0/0/0",
    ]
    assert isis.children[2].children[1].children[0].line == "metric 100"


def test_parse_keeps_sets_and_policies_as_atomic_blocks() -> None:
    tree = xr.parse_config(RUNNING)
    prefix_set = next(node for node in tree if node.line == "prefix-set PL-LOOPBACKS")

    assert prefix_set.atomic
    assert prefix_set.body == ("10.0.0.0/8 le 32,", "192.168.0.0/16", "end-set")


GROUP = (
    "group DC-INT\n"
    " interface 'HundredGigE.*'\n"
    "  mtu 9100\n"
    " !\n"
    "end-group\n"
    "username admin\n"
    " group root-lr\n"
    "!\n"
)


def test_a_configuration_group_is_an_atomic_block_and_a_task_group_line_is_not() -> None:
    tree = xr.parse_config(GROUP)

    assert [n.line for n in tree] == ["group DC-INT", "username admin"]
    assert tree[0].atomic
    assert tree[0].body == ("interface 'HundredGigE.*'", "mtu 9100", "end-group")
    assert [c.line for c in tree[1].children] == ["group root-lr"]
    assert not tree[1].atomic


def test_an_unterminated_configuration_group_is_rejected() -> None:
    with pytest.raises(xr.IosXrConfigError, match="end-group"):
        xr.parse_config("group DC-INT\n interface 'HundredGigE.*'\n")


def test_commands_resend_a_changed_configuration_group_as_a_whole() -> None:
    changed = GROUP.replace("mtu 9100", "mtu 9000")

    commands = xr.build_commands(xr.parse_config(GROUP), xr.parse_config(changed))

    assert commands == ["group DC-INT", "interface 'HundredGigE.*'", "mtu 9000", "end-group"]


NEW_STANZAS = (
    "key chain KC\n macsec\n  key 0A1B\n   key-string password7 0822455D0A16\n"
    "   cryptographic-algorithm aes-256-cmac\n   lifetime 00:00:00 march 12 2026 infinite\n"
    "  !\n !\n!\n"
    "ipv4 access-list SNMP-1\n 10 permit ipv4 10.0.0.0/8 any\n!\n"
    "snmp-server community abc RO IPv4 SNMP-1\n"
    "interface HundredGigE0/0/0/5.100 l2transport\n encapsulation dot1q 100\n!\n"
    "interface tunnel-te1001\n ipv4 unnumbered Loopback0\n destination 10.0.0.2\n path-option 10 dynamic\n!\n"
    "controller Optics0/0/0/4\n breakout 4x100\n!\n"
    "l2vpn\n xconnect group VPWS\n  p2p A\n   interface HundredGigE0/0/0/5.100\n"
    "   neighbor evpn evi 100 target 2 source 1\n  !\n !\n!\n"
)


def test_the_data_driven_stanzas_parse_and_converge() -> None:
    tree = xr.parse_config(NEW_STANZAS)

    assert [n.line for n in tree][:3] == [
        "key chain KC",
        "ipv4 access-list SNMP-1",
        "snmp-server community abc RO IPv4 SNMP-1",
    ]
    assert xr.render_diff(tree, xr.parse_config(NEW_STANZAS)) == ""


def test_a_new_community_and_a_changed_key_string_produce_commands() -> None:
    changed = NEW_STANZAS.replace("0822455D0A16", "1304010B0818").replace(
        "community abc RO", "community abc RO SystemOwner"
    )

    commands = xr.build_commands(xr.parse_config(NEW_STANZAS), xr.parse_config(changed))

    assert "no snmp-server community abc RO IPv4 SNMP-1" in commands
    assert "snmp-server community abc RO SystemOwner IPv4 SNMP-1" in commands or any(
        c.startswith("snmp-server community abc RO SystemOwner") for c in commands
    )
    assert "key-string password7 1304010B0818" in commands


def test_nested_route_policy_binding_is_a_leaf_not_a_block() -> None:
    text = (
        "router bgp 65000\n"
        " neighbor-group G\n"
        "  address-family ipv4 unicast\n"
        "   route-policy P-IN in\n"
        "   route-policy P-OUT out\n"
        "  !\n"
        " !\n"
        " neighbor 10.0.0.1\n"
        "  use neighbor-group G\n"
        "!\n"
    )

    bgp = xr.parse_config(text)[0]

    assert [c.line for c in bgp.children] == ["neighbor-group G", "neighbor 10.0.0.1"]
    af = bgp.children[0].children[0]
    assert [c.line for c in af.children] == ["route-policy P-IN in", "route-policy P-OUT out"]
    assert not any(c.atomic for c in af.children)


def test_parse_rejects_unterminated_block() -> None:
    with pytest.raises(xr.IosXrConfigError, match="end-set"):
        xr.parse_config("prefix-set A\n  10.0.0.0/8\n")


def test_parse_keeps_multiline_banner_as_one_unit() -> None:
    tree = xr.parse_config("banner motd #\n Authorized use only\n#\nhostname a\n")

    assert tree[0].atomic
    assert tree[1].line == "hostname a"


# --- diff ------------------------------------------------------------------


def test_converged_router_has_empty_diff_despite_layout_and_comments() -> None:
    assert _diff(RUNNING, INTENDED) == ""


def test_diff_shows_changed_leaf_with_its_parents() -> None:
    changed = INTENDED.replace("metric 100", "metric 200")

    assert _diff(RUNNING, changed) == (
        "  router isis CORE\n"
        "    interface HundredGigE0/0/0/0\n"
        "      address-family ipv4 unicast\n"
        "-       metric 100\n"
        "+       metric 200\n"
    )


def test_diff_shows_added_and_removed_blocks() -> None:
    intended = (
        _without(INTENDED, "interface Loopback0")
        + "interface Loopback9\n  ipv4 address 10.9.9.9 255.255.255.255\n"
    )
    diff = _diff(RUNNING, intended)

    assert "- interface Loopback0" in diff
    assert "+ interface Loopback9" in diff
    assert "+   ipv4 address 10.9.9.9 255.255.255.255" in diff


def test_diff_replaces_changed_set_as_a_whole() -> None:
    intended = INTENDED.replace("192.168.0.0/16", "172.16.0.0/12")
    diff = _diff(RUNNING, intended)

    assert "-   192.168.0.0/16" in diff
    assert "+   172.16.0.0/12" in diff


def test_diff_is_deterministic() -> None:
    changed = INTENDED.replace("metric 100", "metric 200")

    assert _diff(RUNNING, changed) == _diff(RUNNING, changed)


# --- commands --------------------------------------------------------------


def test_commands_for_changed_singleton_do_not_negate_the_old_value() -> None:
    intended = INTENDED.replace("Underlay Loopback", "Core Loopback")

    assert _commands(RUNNING, intended) == [
        "interface Loopback0",
        "description Core Loopback",
        "exit",
    ]


def test_commands_remove_with_no_and_enter_parents() -> None:
    intended = _without(INTENDED, "      metric 100")
    # the address-family block is now empty on the router side, so it stays in place
    commands = _commands(RUNNING, intended)

    assert commands == [
        "router isis CORE",
        "interface HundredGigE0/0/0/0",
        "address-family ipv4 unicast",
        "no metric 100",
        "exit",
        "exit",
        "exit",
    ]


def test_commands_remove_whole_block_by_header_only() -> None:
    intended = _without(
        INTENDED,
        "interface Loopback0",
        "  description Underlay Loopback",
        "  ipv4 address 10.0.0.7 255.255.255.255",
    )

    assert _commands(RUNNING, intended) == ["no interface Loopback0"]


def test_commands_add_nested_block_with_exits() -> None:
    intended = INTENDED + "router bgp 65000\n  neighbor 10.1.1.1\n    remote-as 65001\n"

    assert _commands(RUNNING, intended) == [
        "router bgp 65000",
        "neighbor 10.1.1.1",
        "remote-as 65001",
        "exit",
        "exit",
    ]


def test_commands_resend_a_changed_atomic_block() -> None:
    intended = INTENDED.replace("192.168.0.0/16", "172.16.0.0/12")

    assert _commands(RUNNING, intended) == [
        "prefix-set PL-LOOPBACKS",
        "10.0.0.0/8 le 32,",
        "172.16.0.0/12",
        "end-set",
    ]


def test_no_commands_when_converged() -> None:
    assert _commands(RUNNING, INTENDED) == []


# --- lockout guard: one case per protected item ------------------------------


def test_guard_passes_for_the_converged_config_and_for_a_normal_change() -> None:
    assert _guard(INTENDED) == []
    assert _guard(INTENDED.replace("metric 100", "metric 200")) == []


def test_guard_blocks_dropping_the_account_in_use() -> None:
    intended = _without(
        INTENDED,
        "username admin",
        "  group root-lr",
        "  group cisco-support",
        "  secret 10 $6$H0QFj2rx$placeholderplaceholder",
    )

    assert any("account in use" in problem for problem in _guard(intended))


def test_guard_blocks_account_without_group() -> None:
    intended = _without(INTENDED, "  group root-lr", "  group cisco-support")

    assert any("no 'group' line" in problem for problem in _guard(intended))


def test_guard_allows_changing_the_account_secret() -> None:
    intended = INTENDED.replace("placeholderplaceholder", "anotherplaceholder")

    assert _guard(intended) == []


def test_guard_blocks_dropping_the_management_interface() -> None:
    intended = _without(
        INTENDED,
        "interface MgmtEth0/RP0/CPU0/0",
        "  vrf MGMT",
        "  ipv4 address 192.0.2.7 255.255.255.0",
    )

    assert any("MgmtEth0/RP0/CPU0/0" in problem for problem in _guard(intended))


def test_guard_blocks_changing_the_management_address() -> None:
    intended = INTENDED.replace("192.0.2.7 255.255.255.0", "192.0.2.99 255.255.255.0")

    assert any(
        "no longer carries the management address" in problem for problem in _guard(intended)
    )


def test_guard_blocks_dropping_the_management_vrf() -> None:
    intended = _without(INTENDED, "  vrf MGMT")

    assert any("loses 'vrf MGMT'" in problem for problem in _guard(intended))


def test_guard_blocks_shutting_down_the_management_interface() -> None:
    intended = INTENDED.replace(
        "interface MgmtEth0/RP0/CPU0/0\n", "interface MgmtEth0/RP0/CPU0/0\n  shutdown\n"
    )

    assert any("shuts down" in problem for problem in _guard(intended))


def test_guard_protects_the_interface_that_owns_the_host_address_even_if_not_mgmteth() -> None:
    running = RUNNING.replace("192.0.2.7 255.255.255.0", "192.0.2.8 255.255.255.0") + (
        "interface TenGigE0/0/0/5\n ipv4 address 192.0.2.7 255.255.255.0\n!\n"
    )
    intended = INTENDED.replace("192.0.2.7 255.255.255.0", "192.0.2.8 255.255.255.0")

    assert any("TenGigE0/0/0/5" in problem for problem in _guard(intended, running))


def test_guard_blocks_dropping_ssh_server_lines() -> None:
    assert any(
        "ssh server vrf MGMT" in p for p in _guard(_without(INTENDED, "ssh server vrf MGMT"))
    )
    assert any("ssh server v2" in p for p in _guard(_without(INTENDED, "ssh server v2")))


def test_guard_blocks_dropping_the_netconf_agent() -> None:
    intended = INTENDED.replace("netconf-yang agent\n ssh\n", "")

    assert any("netconf-yang agent" in p and "NETCONF" in p for p in _guard(intended))


def test_guard_blocks_dropping_the_netconf_agent_ssh_transport() -> None:
    intended = INTENDED.replace("netconf-yang agent\n ssh\n", "netconf-yang agent\n")

    assert any("netconf-yang agent / ssh" in p for p in _guard(intended))


def test_guard_blocks_dropping_the_ssh_server_netconf_line() -> None:
    assert any(
        "ssh server netconf vrf MGMT" in p
        for p in _guard(_without(INTENDED, "ssh server netconf vrf MGMT"))
    )


def test_guard_blocks_dropping_aaa_authentication() -> None:
    intended = _without(INTENDED, "aaa authentication login default local")

    assert any("aaa authentication" in problem for problem in _guard(intended))


def test_guard_blocks_dropping_vty_transport() -> None:
    intended = _without(INTENDED, "  transport input ssh")

    assert any("transport input ssh" in problem for problem in _guard(intended))


def test_guard_blocks_dropping_the_management_default_route() -> None:
    intended = _without(INTENDED, "      0.0.0.0/0 192.0.2.1")

    assert any("management default route" in problem for problem in _guard(intended))


def test_guard_blocks_an_empty_or_truncated_intended_config() -> None:
    assert any("looks empty" in problem for problem in _guard(""))
    assert any("looks empty" in problem for problem in _guard("hostname a\n"))


def test_guard_does_not_apply_to_what_the_router_never_had() -> None:
    running = _without(RUNNING, "ssh server vrf MGMT")

    assert _guard(_without(INTENDED, "ssh server vrf MGMT"), running) == []


def test_prefix_set_entries_compare_in_the_routers_text_order_without_regard_to_input_order() -> (
    None
):
    router = "prefix-set P\n  10.54.10.0/23,\n  10.54.8.0/25\nend-set\n"
    rendered = "prefix-set P\n  10.54.8.0/25,\n  10.54.10.0/23\nend-set\n"

    assert _diff(router, rendered) == ""
    assert xr.parse_config(rendered)[0].body == ("10.54.10.0/23,", "10.54.8.0/25", "end-set")


def test_prefix_sets_with_different_members_still_differ() -> None:
    assert _diff("prefix-set P\n  10.0.0.0/8\nend-set\n", "prefix-set P\n  10.0.0.0/9\nend-set\n")


# --- text sent for a router-side replace and the router's diff -----------------------


def test_replacement_text_keeps_separators_and_drops_noise_lines() -> None:
    text = xr.replacement_text(RUNNING)

    assert "Building configuration" not in text
    assert "Mon Oct 11" not in text
    assert "!\n" in text
    assert "hostname xr-router-1\n" in text
    assert text.endswith("\nend\n")


def test_replacement_text_adds_one_closing_end() -> None:
    assert xr.replacement_text("hostname a\n") == "hostname a\nend\n"
    assert xr.replacement_text("hostname a\nend\n") == "hostname a\nend\n"
    assert xr.replacement_text("hostname a\r\n!\r\n") == "hostname a\n!\nend\n"


def test_router_diff_of_an_unchanged_configuration_is_empty() -> None:
    raw = "!! Building configuration...\n!! IOS XR Configuration 26.4.1.03I\nend\n"

    assert xr.parse_router_diff(raw) == ("", 0)


def test_router_diff_keeps_marked_lines_and_drops_headers_and_end() -> None:
    raw = (
        "!! Building configuration...\n"
        "!! IOS XR Configuration 26.4.1.03I\n"
        "+  interface Loopback99\n"
        "+   description probe\n"
        "   !\n"
        "-  hostname xr-router-1\n"
        "end\n"
    )

    diff, ignored = xr.parse_router_diff(raw)

    assert diff == "+  interface Loopback99\n+   description probe\n   !\n-  hostname xr-router-1\n"
    assert ignored == 0


def test_router_diff_counts_and_drops_hash_lines() -> None:
    raw = "!! IOS XR Configuration 26.4.1.03I\n#  prefix-set P\n#    10.0.0.0/8\n#  end-set\nend\n"

    assert xr.parse_router_diff(raw) == ("", 3)
    diff, ignored = xr.parse_router_diff(raw.replace("end\n", "+  hostname b\nend\n"))
    assert diff == "+  hostname b\n"
    assert ignored == 3
