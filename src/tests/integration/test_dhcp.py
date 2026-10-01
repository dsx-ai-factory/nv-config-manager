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
"""Integration tests for DHCP API.

These tests verify that the DHCP API correctly serves KEA DHCP configuration
and that the configuration contains expected subnets and reservations.
"""

import ipaddress
import json
import subprocess
import time
from copy import deepcopy
from itertools import islice
from typing import Any, cast

import pytest
import requests

from tests.integration.dcim_adapter import DCIMIntegrationAdapter

# Mark all tests in this module as integration tests
pytestmark = pytest.mark.integration

# Maximum time to wait for DHCP data to be populated after job execution
DHCP_DATA_WAIT_TIMEOUT = 300  # 5 minutes
DHCP_POLL_INTERVAL = 10  # seconds
DHCP_SCALE_LEASE_COUNT = 201
DHCP_SCALE_CONFIG_RECORD_COUNT = 201
DHCP_SCALE_PAGE_SIZE = 100

_KEA_BATCH_SCRIPT = """
import json
import sys
import urllib.request

commands = json.load(sys.stdin)
responses = []
for command in commands:
    try:
        request = urllib.request.Request(
            "http://127.0.0.1:8000/",
            data=json.dumps(command).encode(),
            headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(request, timeout=10) as response:
            responses.append(json.load(response))
    except Exception as exc:
        responses.append([{"result": -1, "text": repr(exc)}])
json.dump(responses, sys.stdout)
"""


def _get_dhcp_pod(namespace: str) -> str:
    """Return the live DHCP pod used by the ephemeral Kind integration cluster."""
    deployment = subprocess.run(
        [
            "kubectl",
            "get",
            "deployments",
            "-n",
            namespace,
            "-l",
            "app.kubernetes.io/component=network-dhcp",
            "-o",
            "jsonpath={.items[0].spec.selector.matchLabels.app}",
            "--request-timeout=15s",
        ],
        capture_output=True,
        check=False,
        text=True,
        timeout=30,
    )
    if deployment.returncode != 0 or not deployment.stdout.strip():
        pytest.fail(
            "Could not find the DHCP deployment for lease scale setup: "
            f"{deployment.stderr.strip() or 'empty kubectl response'}"
        )

    result = subprocess.run(
        [
            "kubectl",
            "get",
            "pods",
            "-n",
            namespace,
            "-l",
            f"app={deployment.stdout.strip()}",
            "-o",
            "jsonpath={.items[0].metadata.name}",
            "--request-timeout=15s",
        ],
        capture_output=True,
        check=False,
        text=True,
        timeout=30,
    )
    if result.returncode != 0 or not result.stdout.strip():
        pytest.fail(
            "Could not find the DHCP pod for lease scale setup: "
            f"{result.stderr.strip() or 'empty kubectl response'}"
        )
    return result.stdout.strip()


def _run_kea_commands(
    namespace: str,
    pod: str,
    commands: list[dict[str, Any]],
) -> list[list[dict[str, Any]]]:
    """Run a batch of supported lease commands through Kea's control agent."""
    result = subprocess.run(
        [
            "kubectl",
            "exec",
            "-i",
            "-n",
            namespace,
            pod,
            "-c",
            "api",
            "--",
            "python",
            "-c",
            _KEA_BATCH_SCRIPT,
        ],
        capture_output=True,
        check=False,
        input=json.dumps(commands),
        text=True,
        timeout=120,
    )
    if result.returncode != 0:
        pytest.fail(f"Kea lease setup command failed: {result.stderr.strip()}")
    try:
        responses: list[list[dict[str, Any]]] = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        pytest.fail(f"Kea lease setup returned invalid JSON: {result.stdout!r}")
        raise AssertionError from exc
    return responses


def _get_kea_dhcp4_config(namespace: str, pod: str) -> dict[str, Any]:
    """Fetch the unsanitized running DHCPv4 config for ephemeral restoration."""
    responses = _run_kea_commands(
        namespace,
        pod,
        [{"command": "config-get", "service": ["dhcp4"]}],
    )
    if len(responses) != 1 or len(responses[0]) != 1:
        pytest.fail("Kea config-get returned an unexpected response envelope")

    response = responses[0][0]
    if response.get("result") != 0:
        pytest.fail(f"Kea config-get failed: {response.get('text', 'unknown error')}")
    arguments = response.get("arguments")
    dhcp4 = arguments.get("Dhcp4") if isinstance(arguments, dict) else None
    if not isinstance(dhcp4, dict):
        pytest.fail("Kea config-get response is missing Dhcp4")
    return dhcp4


def _fetch_lease_pages(
    dhcp_api_url: str,
    dhcp_client: requests.Session,
    page_size: int,
) -> list[list[dict[str, Any]]]:
    """Fetch every normalized lease page and reject a repeated API cursor."""
    pages: list[list[dict[str, Any]]] = []
    seen_cursors: set[str] = set()
    cursor: str | None = None

    for _ in range(100):
        params: dict[str, str | int] = {"limit": page_size}
        if cursor is not None:
            params["cursor"] = cursor
        response = dhcp_client.get(
            f"{dhcp_api_url}/lease",
            params=params,
            timeout=30,
        )
        response.raise_for_status()
        payload = response.json()
        pages.append(payload["leases"])
        cursor = payload["next_cursor"]
        if cursor is None:
            return pages
        if cursor in seen_cursors:
            pytest.fail(f"DHCP lease pagination repeated cursor {cursor!r}")
        seen_cursors.add(cursor)

    pytest.fail("DHCP lease pagination did not finish within 100 pages")


def _fetch_exact_collection_pages(
    dhcp_api_url: str,
    dhcp_client: requests.Session,
    resource: str,
    response_field: str,
    page_size: int,
) -> tuple[list[list[dict[str, Any]]], int]:
    """Fetch a complete exact-total collection and reject unstable cursors."""
    pages: list[list[dict[str, Any]]] = []
    seen_cursors: set[str] = set()
    cursor: str | None = None
    total_count: int | None = None

    for _ in range(100):
        params: dict[str, str | int] = {"limit": page_size}
        if cursor is not None:
            params["cursor"] = cursor
        response = dhcp_client.get(
            f"{dhcp_api_url}/{resource}",
            params=params,
            timeout=30,
        )
        response.raise_for_status()
        payload = response.json()
        page_total = int(payload["total_count"])
        if total_count is None:
            total_count = page_total
        elif page_total != total_count:
            pytest.fail(
                f"DHCP {resource} total changed during pagination: {total_count} != {page_total}"
            )
        pages.append(payload[response_field])
        cursor = payload["next_cursor"]
        if cursor is None:
            assert total_count is not None
            return pages, total_count
        if cursor in seen_cursors:
            pytest.fail(f"DHCP {resource} pagination repeated cursor {cursor!r}")
        seen_cursors.add(cursor)

    pytest.fail(f"DHCP {resource} pagination did not finish within 100 pages")


def _set_kea_dhcp4_config(
    namespace: str,
    pod: str,
    dhcp4: dict[str, Any],
) -> None:
    """Apply an ephemeral DHCPv4 configuration through Kea's control agent."""
    responses = _run_kea_commands(
        namespace,
        pod,
        [
            {
                "command": "config-set",
                "service": ["dhcp4"],
                "arguments": {"Dhcp4": dhcp4},
            }
        ],
    )
    if len(responses) != 1 or len(responses[0]) != 1 or responses[0][0].get("result") != 0:
        pytest.fail(f"Failed to apply Kea scale configuration: {responses}")


def _scaled_dhcp4_config(
    dhcp4: dict[str, Any],
    count: int,
) -> tuple[dict[str, Any], str]:
    """Append a non-overlapping synthetic subnet with scale pools and reservations."""
    configured_networks = [
        ipaddress.ip_network(subnet["subnet"])
        for subnet in dhcp4.get("subnet4", [])
        if subnet.get("subnet")
    ]
    candidate_networks = (
        ipaddress.ip_network("198.18.0.0/16"),
        ipaddress.ip_network("198.19.0.0/16"),
        ipaddress.ip_network("10.255.0.0/16"),
        ipaddress.ip_network("172.31.0.0/16"),
    )
    scale_network = next(
        (
            candidate
            for candidate in candidate_networks
            if all(not candidate.overlaps(configured) for configured in configured_networks)
        ),
        None,
    )
    if scale_network is None:
        pytest.fail("No non-overlapping IPv4 subnet is available for DHCP scale setup")

    addresses = [str(address) for address in islice(scale_network.hosts(), count)]
    if len(addresses) != count:
        pytest.fail(f"DHCP scale subnet has only {len(addresses)} usable addresses")

    subnet_ids = [
        int(subnet["id"]) for subnet in dhcp4.get("subnet4", []) if subnet.get("id") is not None
    ]
    scale_subnet = {
        "id": max(subnet_ids, default=0) + 1,
        "subnet": str(scale_network),
        "pools": [{"pool": f"{address}/32"} for address in addresses],
        "reservations": [
            {
                "hostname": f"nvcm-scale-reservation-{index:03d}",
                "hw-address": f"02:fc:00:00:{index // 256:02x}:{index % 256:02x}",
                "ip-address": address,
            }
            for index, address in enumerate(addresses)
        ],
    }
    scaled_config = deepcopy(dhcp4)
    scaled_config.setdefault("subnet4", []).append(scale_subnet)
    return scaled_config, str(scale_network)


def _reservation_addresses(dhcp4: dict[str, Any]) -> set[str]:
    """Collect configured reservation addresses that scale setup must not use."""
    reservations = list(dhcp4.get("reservations", []))
    for subnet in dhcp4.get("subnet4", []):
        reservations.extend(subnet.get("reservations", []))

    addresses: set[str] = set()
    for reservation in reservations:
        address = reservation.get("ip-address")
        if address:
            addresses.add(address)
        multiple_addresses = reservation.get("ip-addresses", [])
        if isinstance(multiple_addresses, str):
            multiple_addresses = [multiple_addresses]
        addresses.update(multiple_addresses)
    return addresses


def _scale_lease_candidates(
    dhcp4: dict[str, Any],
    unavailable_addresses: set[str],
    count: int,
) -> list[tuple[int, str]]:
    """Choose unused addresses from configured subnets for ephemeral test leases."""
    candidates: list[tuple[int, str]] = []
    unavailable = unavailable_addresses | _reservation_addresses(dhcp4)
    subnet_networks = sorted(
        (
            (subnet, ipaddress.ip_network(subnet["subnet"]))
            for subnet in dhcp4.get("subnet4", [])
            if subnet.get("id") is not None and subnet.get("subnet")
        ),
        key=lambda item: item[1].num_addresses,
        reverse=True,
    )

    for subnet, network in subnet_networks:
        for address in network.hosts():
            address_text = str(address)
            if address_text in unavailable:
                continue
            candidates.append((int(subnet["id"]), address_text))
            unavailable.add(address_text)
            if len(candidates) == count:
                return candidates

    pytest.fail(
        f"DHCP integration config has only {len(candidates)} unused addresses; {count} required"
    )


class TestDHCPAPI:
    """Tests for the DHCP API endpoints."""

    def _wait_for_dhcp_data(
        self,
        dhcp_api_url: str,
        dhcp_client: requests.Session,
        timeout: int = DHCP_DATA_WAIT_TIMEOUT,
    ) -> dict[str, Any] | list[dict[str, Any]]:
        """Wait for DHCP to have subnets or reservations populated.

        After job execution, the DHCP service needs to run its first refresh
        to populate subnets and reservations from the selected DCIM.

        Returns the DHCP config once data is available.
        """
        print(f"\n⏳ Waiting up to {timeout}s for DHCP data to be populated...")
        start_time = time.time()

        while time.time() - start_time < timeout:
            try:
                response = dhcp_client.get(f"{dhcp_api_url}/config", timeout=30)
                if response.status_code == 200:
                    config = cast(dict[str, Any] | list[dict[str, Any]], response.json())
                    dhcp4 = self._extract_dhcp4_config(config)
                    if dhcp4:
                        subnets = dhcp4.get("subnet4", [])
                        reservations = dhcp4.get("reservations", [])
                        if subnets or reservations:
                            elapsed = int(time.time() - start_time)
                            print(
                                f"✅ DHCP data available after {elapsed}s: "
                                f"{len(subnets)} subnets, {len(reservations)} reservations"
                            )
                            return config
                        print(
                            f"  ... DHCP config empty, waiting ({int(time.time() - start_time)}s elapsed)"
                        )
            except requests.RequestException as e:
                print(f"  ... DHCP request failed: {e}")

            time.sleep(DHCP_POLL_INTERVAL)

        pytest.fail(
            f"DHCP data not populated after {timeout}s. "
            "The DHCP refresh may not have run after job execution."
        )

    @pytest.mark.timeout(30)
    def test_dhcp_healthcheck(
        self,
        dhcp_api_url: str,
        dhcp_client: requests.Session,
        base_hostname: str,
        sso_enabled: bool,
    ) -> None:
        """Test that the DHCP healthcheck endpoint is accessible.

        Without SSO the healthcheck is hit unauthenticated to verify it
        requires no auth.  With SSO the gateway enforces JWT validation
        before the request reaches the app, so we reuse the authenticated
        session (the app-level healthcheck still exercises the same code
        path; the gateway health-check bypass is validated separately via
        the ``healthChecks.paths`` Helm value).
        """
        print("\n=== Testing DHCP healthcheck ===")

        if sso_enabled:
            # Gateway enforces JWT on svc-* routes; use the authenticated client.
            response = dhcp_client.get(
                f"{dhcp_api_url}/healthcheck",
                timeout=10,
            )
        else:
            # No SSO — verify the endpoint works without any auth.
            session = requests.Session()
            session.headers.update({"Host": f"dhcp.{base_hostname}"})
            session.verify = False
            response = session.get(
                f"{dhcp_api_url}/healthcheck",
                timeout=10,
            )

        print(f"Healthcheck response: {response.status_code}")

        if response.status_code != 200:
            pytest.fail(
                f"DHCP healthcheck failed with status {response.status_code}: {response.text}"
            )

        print("✅ DHCP healthcheck passed")

    @pytest.mark.timeout(60)
    def test_dhcp_config_accessible(
        self,
        dhcp_api_url: str,
        dhcp_client: requests.Session,
    ) -> None:
        """Test that the DHCP config endpoint returns a valid configuration.

        This verifies that:
        1. The endpoint is accessible with auth
        2. The config is valid JSON
        3. The config has expected KEA structure
        """
        print("\n=== Testing DHCP config endpoint ===")

        response = dhcp_client.get(
            f"{dhcp_api_url}/config",
            timeout=30,
        )

        print(f"Config response status: {response.status_code}")

        if response.status_code == 403:
            pytest.fail(
                "DHCP config returned 403 Forbidden. "
                "Check that X-AUTH-REQUEST-EMAIL header is being passed correctly."
            )

        response.raise_for_status()

        config = response.json()
        print(f"Config type: {type(config)}")

        # Validate structure - KEA returns a list with the config
        if isinstance(config, list):
            # KEA response format: [{"result": 0, "arguments": {...}}]
            if len(config) > 0 and "arguments" in config[0]:
                dhcp_config = config[0]["arguments"]
                print(f"KEA config keys: {list(dhcp_config.keys())}")
            else:
                dhcp_config = config
        else:
            dhcp_config = config

        # Check for Dhcp4 key (IPv4 DHCP config)
        if "Dhcp4" in dhcp_config:
            dhcp4 = dhcp_config["Dhcp4"]
            print(f"Dhcp4 config keys: {list(dhcp4.keys())}")

            if "subnet4" in dhcp4:
                subnet_count = len(dhcp4["subnet4"])
                print(f"Number of IPv4 subnets: {subnet_count}")

            if "reservations" in dhcp4:
                reservation_count = len(dhcp4["reservations"])
                print(f"Number of global reservations: {reservation_count}")

        print("✅ DHCP config endpoint accessible and returns valid structure")

    @pytest.mark.timeout(360)  # 6 minutes to allow for DHCP refresh wait
    def test_dhcp_config_has_subnets(
        self,
        dhcp_api_url: str,
        dhcp_client: requests.Session,
    ) -> None:
        """Test that the DHCP configuration contains subnets.

        In a properly configured deployment with mock topology,
        there should be at least one DHCP subnet configured.

        This test waits for the DHCP refresh to populate data after job execution.
        """
        print("\n=== Verifying DHCP subnets ===")

        # Wait for DHCP data to be populated (first refresh after job execution)
        config = self._wait_for_dhcp_data(dhcp_api_url, dhcp_client)

        # Extract Dhcp4 config
        dhcp4 = self._extract_dhcp4_config(config)

        if dhcp4 is None:
            pytest.skip("No Dhcp4 configuration found - DHCP may not be configured")

        subnets = dhcp4.get("subnet4", [])
        subnet_count = len(subnets)

        print(f"Found {subnet_count} IPv4 subnets:")
        for subnet in subnets[:5]:
            subnet_str = subnet.get("subnet", "unknown")
            subnet_id = subnet.get("id", "?")
            pools = subnet.get("pools", [])
            print(f"  - ID {subnet_id}: {subnet_str} ({len(pools)} pools)")

        if subnet_count > 5:
            print(f"  ... and {subnet_count - 5} more")

        # We don't fail if no subnets - the mock topology might not have DHCP configured
        if subnet_count == 0:
            print("⚠️ No subnets configured - this may be expected for some deployments")
        else:
            print(f"✅ Found {subnet_count} DHCP subnets")

    @pytest.mark.timeout(60)
    def test_dhcp_config_has_reservations(
        self,
        dhcp_api_url: str,
        dhcp_client: requests.Session,
    ) -> None:
        """Test that the DHCP configuration contains host reservations.

        In a properly configured deployment with ZTP-enabled devices,
        there should be host reservations for those devices.
        """
        print("\n=== Verifying DHCP reservations ===")

        response = dhcp_client.get(
            f"{dhcp_api_url}/config",
            timeout=30,
        )
        response.raise_for_status()

        config = response.json()
        dhcp4 = self._extract_dhcp4_config(config)

        if dhcp4 is None:
            pytest.skip("No Dhcp4 configuration found - DHCP may not be configured")

        # Check global reservations
        global_reservations = dhcp4.get("reservations", [])

        # Also check per-subnet reservations
        subnets = dhcp4.get("subnet4", [])
        subnet_reservations = sum(len(subnet.get("reservations", [])) for subnet in subnets)

        total_reservations = len(global_reservations) + subnet_reservations

        print(f"Global reservations: {len(global_reservations)}")
        print(f"Per-subnet reservations: {subnet_reservations}")
        print(f"Total reservations: {total_reservations}")

        if global_reservations:
            print("\nSample global reservations:")
            for res in global_reservations[:3]:
                hw = res.get("hw-address", "unknown")
                hostname = res.get("hostname", "unknown")
                ip = res.get("ip-address", "unknown")
                print(f"  - {hostname}: {hw} → {ip}")

        if total_reservations == 0:
            print("⚠️ No reservations configured - this may be expected for some deployments")
        else:
            print(f"✅ Found {total_reservations} DHCP reservations")

    @pytest.mark.ci_only
    @pytest.mark.timeout(240)
    def test_lease_pagination_and_search_at_scale(
        self,
        dhcp_api_url: str,
        dhcp_client: requests.Session,
        config_manager_namespace: str,
    ) -> None:
        """Traverse real Kea pages and search for a lease beyond the first page."""
        print("\n=== Verifying DHCP lease pagination at scale ===")

        config = self._wait_for_dhcp_data(dhcp_api_url, dhcp_client)
        dhcp4 = self._extract_dhcp4_config(config)
        if dhcp4 is None:
            pytest.fail("No Dhcp4 configuration found")

        baseline_pages = _fetch_lease_pages(dhcp_api_url, dhcp_client, page_size=500)
        unavailable_addresses = {lease["ip_address"] for page in baseline_pages for lease in page}
        candidates = _scale_lease_candidates(
            dhcp4,
            unavailable_addresses,
            DHCP_SCALE_LEASE_COUNT,
        )
        lease_records = [
            {
                "subnet-id": subnet_id,
                "ip-address": address,
                "hw-address": f"02:fd:00:00:{index // 256:02x}:{index % 256:02x}",
                "hostname": f"nvcm-scale-{index:03d}",
                "valid-lft": 3600,
            }
            for index, (subnet_id, address) in enumerate(candidates)
        ]
        add_commands = [
            {
                "command": "lease4-add",
                "service": ["dhcp4"],
                "arguments": lease,
            }
            for lease in lease_records
        ]
        dhcp_pod = _get_dhcp_pod(config_manager_namespace)
        cleanup_addresses = [str(lease["ip-address"]) for lease in lease_records]
        added_addresses: list[str] = []

        try:
            add_responses = _run_kea_commands(
                config_manager_namespace,
                dhcp_pod,
                add_commands,
            )
            added_addresses = [
                str(lease["ip-address"])
                for lease, response in zip(lease_records, add_responses, strict=True)
                if len(response) == 1 and response[0].get("result") == 0
            ]
            add_failures = [
                response
                for response in add_responses
                if len(response) != 1 or response[0].get("result") != 0
            ]
            assert not add_failures, f"Failed to seed Kea scale leases: {add_failures[:3]}"
            assert len(added_addresses) == DHCP_SCALE_LEASE_COUNT

            pages = _fetch_lease_pages(
                dhcp_api_url,
                dhcp_client,
                page_size=DHCP_SCALE_PAGE_SIZE,
            )
            all_leases = [lease for page in pages for lease in page]
            all_addresses = [lease["ip_address"] for lease in all_leases]
            seeded_addresses = set(added_addresses)

            assert len(pages) >= 3
            assert len(all_addresses) == len(set(all_addresses)), "Lease pages overlap"
            assert seeded_addresses <= set(all_addresses), "Lease pagination skipped seeded rows"

            target_lease = next(
                (
                    lease
                    for page in pages[1:]
                    for lease in page
                    if lease["ip_address"] in seeded_addresses
                ),
                None,
            )
            assert target_lease is not None, "No seeded lease landed beyond the first API page"

            search_response = dhcp_client.get(
                f"{dhcp_api_url}/lease",
                params={"limit": DHCP_SCALE_PAGE_SIZE, "search": target_lease["hostname"]},
                timeout=30,
            )
            search_response.raise_for_status()
            search_payload = search_response.json()
            assert [lease["ip_address"] for lease in search_payload["leases"]] == [
                target_lease["ip_address"]
            ]
            assert target_lease["subnet"] is not None
            subnet_search_response = dhcp_client.get(
                f"{dhcp_api_url}/lease",
                params={
                    "limit": DHCP_SCALE_PAGE_SIZE,
                    "search": target_lease["hostname"],
                    "subnet": target_lease["subnet"],
                },
                timeout=30,
            )
            subnet_search_response.raise_for_status()
            assert [lease["ip_address"] for lease in subnet_search_response.json()["leases"]] == [
                target_lease["ip_address"]
            ]
            print(
                f"✅ Found {DHCP_SCALE_LEASE_COUNT} seeded leases across {len(pages)} pages "
                "and searched beyond page one with and without a subnet filter"
            )
        finally:
            if cleanup_addresses:
                delete_responses = _run_kea_commands(
                    config_manager_namespace,
                    dhcp_pod,
                    [
                        {
                            "command": "lease4-del",
                            "service": ["dhcp4"],
                            "arguments": {"ip-address": address},
                        }
                        for address in cleanup_addresses
                    ],
                )
                delete_failures = [
                    response
                    for response in delete_responses
                    if len(response) != 1 or response[0].get("result") not in (0, 3)
                ]
                assert not delete_failures, (
                    f"Failed to clean up Kea scale leases: {delete_failures[:3]}"
                )

    @pytest.mark.ci_only
    @pytest.mark.timeout(240)
    def test_reservation_and_pool_pagination_and_search_at_scale(
        self,
        dhcp_api_url: str,
        dhcp_client: requests.Session,
        config_manager_namespace: str,
    ) -> None:
        """Traverse multi-page reservation and pool collections with exact totals."""
        print("\n=== Verifying DHCP reservation and pool pagination at scale ===")

        self._wait_for_dhcp_data(dhcp_api_url, dhcp_client)
        dhcp_pod = _get_dhcp_pod(config_manager_namespace)
        dhcp4 = _get_kea_dhcp4_config(config_manager_namespace, dhcp_pod)

        original_dhcp4 = deepcopy(dhcp4)
        scaled_dhcp4, scale_subnet = _scaled_dhcp4_config(
            dhcp4,
            DHCP_SCALE_CONFIG_RECORD_COUNT,
        )
        scale_config = scaled_dhcp4["subnet4"][-1]
        seeded_reservation_names = {
            reservation["hostname"] for reservation in scale_config["reservations"]
        }
        seeded_pools = {pool["pool"] for pool in scale_config["pools"]}

        try:
            _set_kea_dhcp4_config(config_manager_namespace, dhcp_pod, scaled_dhcp4)

            reservation_pages, reservation_total = _fetch_exact_collection_pages(
                dhcp_api_url,
                dhcp_client,
                "reservation",
                "reservations",
                DHCP_SCALE_PAGE_SIZE,
            )
            reservations = [record for page in reservation_pages for record in page]
            assert len(reservation_pages) >= 3
            assert len(reservations) == reservation_total
            reservation_identities = {
                (
                    reservation["ip_address"],
                    reservation["hostname"],
                    reservation["identifier_type"],
                    reservation["identifier"],
                    reservation["subnet"],
                )
                for reservation in reservations
            }
            assert len(reservation_identities) == len(reservations), "Reservation pages overlap"
            assert seeded_reservation_names <= {
                reservation["hostname"] for reservation in reservations
            }
            target_reservation = next(
                (
                    reservation
                    for page in reservation_pages[1:]
                    for reservation in page
                    if reservation["hostname"] in seeded_reservation_names
                ),
                None,
            )
            assert target_reservation is not None, (
                "No seeded reservation landed beyond the first API page"
            )
            reservation_search = dhcp_client.get(
                f"{dhcp_api_url}/reservation",
                params={
                    "limit": DHCP_SCALE_PAGE_SIZE,
                    "search": target_reservation["hostname"],
                },
                timeout=30,
            )
            reservation_search.raise_for_status()
            reservation_search_payload = reservation_search.json()
            assert reservation_search_payload["total_count"] == 1
            assert [
                reservation["hostname"]
                for reservation in reservation_search_payload["reservations"]
            ] == [target_reservation["hostname"]]
            reservation_get = dhcp_client.get(
                f"{dhcp_api_url}/reservation/{target_reservation['ip_address']}",
                timeout=30,
            )
            reservation_get.raise_for_status()
            assert reservation_get.json()["hostname"] == target_reservation["hostname"]
            subnet_reservations = dhcp_client.get(
                f"{dhcp_api_url}/reservation",
                params={"limit": 500, "subnet": scale_subnet},
                timeout=30,
            )
            subnet_reservations.raise_for_status()
            subnet_reservation_payload = subnet_reservations.json()
            assert subnet_reservation_payload["total_count"] == len(seeded_reservation_names)
            assert {
                reservation["hostname"]
                for reservation in subnet_reservation_payload["reservations"]
            } == seeded_reservation_names

            pool_pages, pool_total = _fetch_exact_collection_pages(
                dhcp_api_url,
                dhcp_client,
                "pool",
                "pools",
                DHCP_SCALE_PAGE_SIZE,
            )
            pools = [record for page in pool_pages for record in page]
            assert len(pool_pages) >= 3
            assert len(pools) == pool_total
            assert seeded_pools <= {pool["pool"] for pool in pools}
            assert len({(pool["subnet"], pool["pool"]) for pool in pools}) == len(pools), (
                "Pool pages overlap"
            )
            target_pool = next(
                (pool for page in pool_pages[1:] for pool in page if pool["pool"] in seeded_pools),
                None,
            )
            assert target_pool is not None, "No seeded pool landed beyond the first API page"
            pool_search = dhcp_client.get(
                f"{dhcp_api_url}/pool",
                params={"limit": DHCP_SCALE_PAGE_SIZE, "search": target_pool["pool"]},
                timeout=30,
            )
            pool_search.raise_for_status()
            pool_search_payload = pool_search.json()
            assert pool_search_payload["total_count"] == 1
            assert [pool["pool"] for pool in pool_search_payload["pools"]] == [target_pool["pool"]]
            subnet_pools = dhcp_client.get(
                f"{dhcp_api_url}/pool",
                params={"limit": 500, "subnet": scale_subnet},
                timeout=30,
            )
            subnet_pools.raise_for_status()
            subnet_pool_payload = subnet_pools.json()
            assert subnet_pool_payload["total_count"] == len(seeded_pools)
            assert {pool["pool"] for pool in subnet_pool_payload["pools"]} == seeded_pools
            print(
                f"✅ Found {DHCP_SCALE_CONFIG_RECORD_COUNT} synthetic reservations and "
                f"pools in {scale_subnet} across {len(reservation_pages)} and "
                f"{len(pool_pages)} pages"
            )
        finally:
            _set_kea_dhcp4_config(config_manager_namespace, dhcp_pod, original_dhcp4)

    # SMN roles use DHCP pools on /31 subnets, not reservations
    SMN_ROLES = {"SMN-Core", "SMN-Spine", "SMN-Leaf", "SMN-Aggleaf", "SMN-ZTPLeaf"}

    @pytest.mark.timeout(120)
    def test_ztp_devices_have_dhcp_reservations(
        self,
        dhcp_api_url: str,
        dhcp_client: requests.Session,
        dcim_adapter: DCIMIntegrationAdapter,
    ) -> None:
        """Test that every explicitly reserved ZTP device has a DHCP reservation.

        This test verifies that:
        1. Devices with ztp_enabled=true and an IP tagged dhcp-reserve in the DCIM
        2. Have a corresponding DHCP reservation in the KEA config
        3. The reservation matches by MAC address (hw-address) or serial (client-id)
        """
        print("\n=== Verifying ZTP devices have DHCP reservations ===")

        devices = dcim_adapter.list_devices(ztp_enabled=True, include_interfaces=True)
        print(f"Found {len(devices)} ZTP-enabled devices in the DCIM")

        if not devices:
            pytest.skip("No ZTP-enabled devices found in the DCIM")

        reserved_device_ids = {device["id"] for device in devices if device["dhcp_reserved"]}

        # Get DHCP config
        dhcp_response = dhcp_client.get(
            f"{dhcp_api_url}/config",
            timeout=30,
        )
        dhcp_response.raise_for_status()

        dhcp_config = dhcp_response.json()
        dhcp4 = self._extract_dhcp4_config(dhcp_config)

        if dhcp4 is None:
            pytest.fail("No Dhcp4 configuration found")

        # Collect all DHCP reservations (global + per-subnet)
        all_reservations: list[dict[str, Any]] = list(dhcp4.get("reservations", []))
        for subnet in dhcp4.get("subnet4", []):
            all_reservations.extend(subnet.get("reservations", []))

        # Build sets of identifiers that have reservations
        # MAC addresses (hw-address) - normalized to lowercase
        reserved_macs = {
            res.get("hw-address", "").lower() for res in all_reservations if res.get("hw-address")
        }
        # Client IDs (typically based on serial number) - store raw for matching
        reserved_client_ids_raw = [
            res.get("client-id", "") for res in all_reservations if res.get("client-id")
        ]

        print(f"Found {len(all_reservations)} total DHCP reservations")
        print(f"  - hw-address reservations: {len(reserved_macs)}")
        print(f"  - client-id reservations: {len(reserved_client_ids_raw)}")
        if reserved_client_ids_raw:
            print(f"  - sample client-ids: {reserved_client_ids_raw[:3]}")

        # Check each ZTP-enabled device
        missing_reservations: list[str] = []
        matched_devices: list[str] = []
        no_identifier: list[str] = []

        pool_backed_devices: list[str] = []

        for device in devices:
            device_name = device.get("name", "unknown")
            device_serial = device.get("serial", "")
            device_role = device.get("role", "")
            interfaces = device.get("interfaces", [])

            # Devices without a dhcp-reserve address intentionally bootstrap from pools.
            if device_role in self.SMN_ROLES or device.get("id") not in reserved_device_ids:
                pool_backed_devices.append(device_name)
                continue

            # Find management interface MAC (prefer mgmt_only, fallback to any)
            device_mac = None
            for iface in interfaces:
                mac = iface.get("mac_address")
                if mac:
                    # Prefer management interface
                    if iface.get("mgmt_only"):
                        device_mac = mac.lower()
                        break
                    elif device_mac is None:
                        device_mac = mac.lower()

            # Check if device has either MAC or serial for reservation matching
            if not device_mac and not device_serial:
                no_identifier.append(device_name)
                continue

            # Check if this device has a reservation (by MAC or by serial/client-id)
            has_reservation = False
            if device_mac and device_mac in reserved_macs:
                has_reservation = True
            elif device_serial:
                # Check various client-id formats:
                # 1. Quoted: 'SERIAL'
                # 2. Hex encoded (no colons): continuous hex string
                # 3. Hex encoded (with colons): colon-separated hex
                serial_quoted = f"'{device_serial}'"
                serial_hex_no_colons = "".join(f"{ord(c):02X}" for c in device_serial)
                serial_hex_colons = ":".join(f"{ord(c):02x}" for c in device_serial)
                for client_id in reserved_client_ids_raw:
                    if client_id.upper() in (
                        serial_quoted.upper(),
                        serial_hex_no_colons,
                        serial_hex_colons.upper(),
                        device_serial.upper(),
                    ):
                        has_reservation = True
                        break

            if has_reservation:
                matched_devices.append(device_name)
            else:
                identifier = f"MAC: {device_mac}" if device_mac else f"serial: {device_serial}"
                missing_reservations.append(f"{device_name} ({identifier})")

        print(f"\nDevices using pools, not reservations: {len(pool_backed_devices)}")
        print(f"Devices with DHCP reservations: {len(matched_devices)}")
        print(f"Devices missing DHCP reservations: {len(missing_reservations)}")
        if no_identifier:
            print(f"Devices with no MAC or serial: {len(no_identifier)}")

        if missing_reservations:
            print("\nDevices missing reservations:")
            for device in missing_reservations[:10]:
                print(f"  - {device}")
            if len(missing_reservations) > 10:
                print(f"  ... and {len(missing_reservations) - 10} more")

            pytest.fail(
                f"{len(missing_reservations)} ZTP-enabled devices are missing "
                f"DHCP reservations: {missing_reservations[:5]}"
            )

        print(f"✅ All {len(matched_devices)} reserved ZTP devices have DHCP reservations")
        if pool_backed_devices:
            print(
                f"✅ {len(pool_backed_devices)} ZTP devices intentionally use DHCP pools "
                "(not reservations)"
            )

    @pytest.mark.timeout(120)
    def test_smn_devices_have_dhcp_pools(
        self,
        dhcp_api_url: str,
        dhcp_client: requests.Session,
        dcim_adapter: DCIMIntegrationAdapter,
    ) -> None:
        """Test that SMN devices have DHCP subnets with pools and correct options.

        SMN devices use /31 subnets with pools of size 1 on their uplink interfaces,
        rather than reservations based on MAC/serial.
        """
        print("\n=== Verifying SMN devices have DHCP pools ===")

        # Filter to SMN devices and collect their uplink /31 subnets
        smn_uplink_subnets: dict[str, str] = {}  # subnet -> device name
        devices = dcim_adapter.list_devices(ztp_enabled=True, include_interfaces=True)

        for device in devices:
            device_name = device.get("name", "")
            device_role = device.get("role", "")

            if device_role not in self.SMN_ROLES:
                continue

            for iface in device.get("interfaces", []):
                if iface.get("role") == "Uplink":
                    for ip_addr in iface.get("ip_addresses", []):
                        addr = ip_addr.get("address", "")
                        if "/31" in addr:
                            # Extract the /31 subnet from the IP
                            ip_iface = ipaddress.ip_interface(addr)
                            subnet = str(ip_iface.network)
                            smn_uplink_subnets[subnet] = device_name

        print(f"Found {len(smn_uplink_subnets)} SMN uplink /31 subnets")

        if not smn_uplink_subnets:
            pytest.skip("No SMN uplink subnets found")

        # Get DHCP config
        dhcp_response = dhcp_client.get(
            f"{dhcp_api_url}/config",
            timeout=30,
        )
        dhcp_response.raise_for_status()

        dhcp_config = dhcp_response.json()
        dhcp4 = self._extract_dhcp4_config(dhcp_config)

        if dhcp4 is None:
            pytest.fail("No Dhcp4 configuration found")

        # Build a map of subnet -> config
        dhcp_subnets: dict[str, dict[str, Any]] = {}
        for subnet_config in dhcp4.get("subnet4", []):
            dhcp_subnets[subnet_config.get("subnet", "")] = subnet_config

        print(json.dumps(dhcp_subnets["10.240.164.64/31"], indent=4))

        # Check each SMN uplink subnet
        missing_subnets: list[str] = []
        subnets_without_pools: list[str] = []
        subnets_with_pools: list[str] = []

        for subnet, device_name in smn_uplink_subnets.items():
            if subnet not in dhcp_subnets:
                missing_subnets.append(f"{subnet} ({device_name})")
                continue

            subnet_cfg = dhcp_subnets[subnet]
            pools = subnet_cfg.get("pools", [])

            if not pools:
                subnets_without_pools.append(f"{subnet} ({device_name})")
            else:
                subnets_with_pools.append(subnet)

        print(f"SMN subnets with pools: {len(subnets_with_pools)}")
        print(f"SMN subnets missing from DHCP: {len(missing_subnets)}")
        print(f"SMN subnets without pools: {len(subnets_without_pools)}")

        if missing_subnets:
            print("\nMissing subnets:")
            for s in missing_subnets[:5]:
                print(f"  - {s}")

        if subnets_without_pools:
            print("\nSubnets without pools:")
            for s in subnets_without_pools[:5]:
                print(f"  - {s}")

        if missing_subnets or subnets_without_pools:
            pytest.fail(
                f"SMN DHCP issues: {len(missing_subnets)} missing subnets, "
                f"{len(subnets_without_pools)} subnets without pools"
            )

        print(f"✅ All {len(subnets_with_pools)} SMN uplink subnets have DHCP pools")

    def _extract_dhcp4_config(self, config: Any) -> dict[str, Any] | None:
        """Extract the Dhcp4 configuration from the KEA response."""
        # KEA returns: [{"result": 0, "arguments": {"Dhcp4": {...}}}]
        if isinstance(config, list) and len(config) > 0:
            first = config[0]
            if "arguments" in first and "Dhcp4" in first["arguments"]:
                return cast(dict[str, Any], first["arguments"]["Dhcp4"])
            if "Dhcp4" in first:
                return cast(dict[str, Any], first["Dhcp4"])

        # Direct config format
        if isinstance(config, dict):
            if "Dhcp4" in config:
                return cast(dict[str, Any], config["Dhcp4"])
            if "arguments" in config and "Dhcp4" in config["arguments"]:
                return cast(dict[str, Any], config["arguments"]["Dhcp4"])

        return None
