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
"""Cumulus Linux / NVOS device connection (NVUE API)."""

from __future__ import annotations

import contextvars
import json
import logging
import re
import threading
import time
from collections.abc import Callable
from copy import deepcopy
from io import BytesIO, StringIO
from typing import Any, cast

import paramiko
import requests
from netmiko import ConnectHandler  # type: ignore[import-untyped]
from netmiko.exceptions import NetmikoAuthenticationException  # type: ignore[import-untyped]
from requests.adapters import HTTPAdapter, Retry
from ruamel.yaml import YAML
from ruamel.yaml.error import YAMLError
from temporalio.exceptions import ApplicationError

from nv_config_manager.common.log import LogCategory, get_logger
from nv_config_manager.temporal.client.device.base import (
    COMMIT_CONFIRM_ROLLBACK_SECONDS,
    NetworkConnection,
)
from nv_config_manager.temporal.client.device.exceptions import (
    ConfigApplyFailureException,
    ConfigSyntaxException,
    DiffChangedException,
    InvalidConfigException,
    NetworkDeviceException,
)
from nv_config_manager.temporal.client.device.models import (
    DeviceArpTable,
    DeviceMacEntry,
    DeviceMacTable,
    DeviceNeighborData,
    InterfaceNeighborData,
)

logger = get_logger(__name__, category=LogCategory.TEMPORAL_ACTIVITY)
logging.getLogger("paramiko").setLevel(logging.WARNING)


def _parse_cl_support_path(output: str) -> str | None:
    """Return the /var/support/ bundle path from cl-support output, or None."""
    match = re.search(r"/var/support/\S+", output)
    return match.group().rstrip(".") if match else None


class CumulusConnection(NetworkConnection):
    """Cumulus Device Connection."""

    _session: requests.Session | None

    def __init__(
        self,
        host: str,
        port: int = 8765,
        username: str | None = None,
        password: str | None = None,
        site: str | None = None,
    ) -> None:
        """Initialize a Cumulus Connection."""
        super().__init__(host, port, username, password, site)
        retry = Retry(total=5, backoff_factor=0.5, status_forcelist=[500, 502, 503, 504])
        self._session = requests.Session()
        self._session.mount("https://", adapter=HTTPAdapter(max_retries=retry))
        self._session.headers = {"Content-Type": "application/json"}
        self._session.verify = False
        self._base_url = f"https://{host}:{port}/nvue_v1/"
        self._authenticated = False

    def close(self) -> None:
        """Close the NVUE HTTP session."""
        session = getattr(self, "_session", None)
        if session is None:
            return
        try:
            session.close()
        except Exception:  # noqa: BLE001 - cleanup must not raise
            logger.debug("Error closing NVUE session to %s", self._host, exc_info=True)
        self._session = None

    def _make_request(
        self,
        method: str,
        url: str,
        **kwargs: Any,
    ) -> requests.Response:
        """Make an HTTP request with automatic password rotation on auth failure.

        Args:
            method: HTTP method (GET, POST, PATCH, DELETE)
            url: URL to request
            **kwargs: Additional arguments to pass to requests (params, json, data, timeout, etc.)

        Returns:
            requests.Response object

        Raises:
            NetworkDeviceException: If all password attempts fail or other errors occur
        """
        session = self._session
        if session is None:
            raise NetworkDeviceException(f"NVUE session to {self._host} is closed")

        def try_request_with_password(password: str) -> requests.Response:
            session.auth = (self._username, password)
            rsp = session.request(method, url, **kwargs)
            if rsp.status_code in (401, 403):
                raise NetworkDeviceException(f"Authentication failed: HTTP {rsp.status_code}")
            return rsp

        if self._authenticated:
            rsp = session.request(method, url, **kwargs)
            if rsp.status_code not in (401, 403):
                return rsp
            logger.info(
                "Cached password no longer valid for %s, retrying with available passwords",
                self._host,
            )
            self._authenticated = False

        # Try with password rotation
        rsp = self._try_passwords_with_callback(
            try_request_with_password,
            (NetworkDeviceException,),
        )
        self._authenticated = True
        return cast(requests.Response, rsp)

    def get(
        self,
        url: str,
        params: dict[str, Any] | None = None,
        timeout: int = 30,
        raise_on_failure: bool = True,
    ) -> requests.Response:
        """Get from the device."""
        try:
            rsp = self._make_request("GET", url, params=params, timeout=timeout)
        except requests.exceptions.Timeout as error:
            msg = f"Timed out getting from {url} with params: {params}"
            logger.exception(msg)
            raise NetworkDeviceException(msg) from error
        if rsp.status_code != 200 and raise_on_failure:
            msg = (
                f"Failed to get from {url} with params: {params} :"
                f" HTTP{rsp.status_code}: {rsp.text}"
            )
            logger.exception(msg)
            raise NetworkDeviceException(msg)
        return rsp

    def post(
        self, url: str, json: Any = None, data: Any = None, timeout: int = 30
    ) -> requests.Response:
        """Post to the device."""
        try:
            return self._make_request("POST", url, json=json, data=data, timeout=timeout)
        except requests.exceptions.Timeout as error:
            msg = f"Timed out posting to {url}"
            logger.exception(msg)
            raise NetworkDeviceException(msg) from error

    def patch(
        self, url: str, json: Any = None, data: Any = None, params: Any = None, timeout: int = 30
    ) -> requests.Response:
        """Patch to the device."""
        try:
            return self._make_request(
                "PATCH", url, json=json, data=data, params=params, timeout=timeout
            )
        except requests.exceptions.Timeout as error:
            msg = f"Timed out patching {url}"
            logger.exception(msg)
            raise NetworkDeviceException(msg) from error

    def delete(self, url: str, params: Any = None, timeout: int = 30) -> requests.Response:
        """Delete from the device."""
        try:
            return self._make_request("DELETE", url, params=params, timeout=timeout)
        except requests.exceptions.Timeout as error:
            msg = f"Timed out deleting {url}"
            logger.exception(msg)
            raise NetworkDeviceException(msg) from error

    def get_running_configuration(self) -> str:
        """Load the running configuration for a given device."""
        # https://docs.nvidia.com/networking-ethernet-software/cumulus-linux-56/System-Configuration/NVIDIA-User-Experience-NVUE/NVUE-API/#view-a-configuration
        rsp = self.get(self._base_url, params={"rev": "applied", "filled": "false"})
        json_config = rsp.json()
        # Convert to YAML to more closely match startup.yaml (still won't quite match)
        # ruamel.yaml is VERY opinionated about dumping to a string apparently
        # https://yaml.readthedocs.io/en/latest/example/#output-of-dump-as-a-string
        yaml_config_stream = StringIO()
        YAML().dump(json_config, yaml_config_stream)
        return yaml_config_stream.getvalue()

    def get_interfaces(self, state_only: bool = True) -> Any:
        """
        Get all interfaces.

        Recommend to stick with state_only due to connection issues observed
        with large bodies in the response. GET from an individual interfaces
        for more detailed data.
        """
        includes = [
            "/*/link/state",
            "/*/link/oper-status",
            "/*/type",
        ]
        return (
            self.get(f"{self._base_url}interface", params={"include": includes}).json()
            if state_only
            else self.get(f"{self._base_url}interface").json()
        )

    def get_ts_info(self, interface: str) -> Any:
        """Get the troubleshooting info for an interface."""
        return self.get(
            f"{self._base_url}interface/{interface}",
            params={"include": ["/link/troubleshooting-info"]},
        ).json()

    @staticmethod
    def flatten_config(config: str) -> list[str]:
        """Flatten the configuration."""
        config_obj = YAML().load(config)
        if not config_obj:
            return []
        set_config = config_obj[0].get("set")
        unset_config = config_obj[0].get("unset")
        commands = []
        if set_config:
            commands.extend([f"nv set {cmd}" for cmd in CumulusConnection._flatten(set_config, [])])
        if unset_config:
            commands.extend(
                [f"nv unset {cmd}" for cmd in CumulusConnection._flatten(unset_config, [])]
            )
        return commands

    def _load_candidate(self, new_configuration: str, partial: bool = False) -> str:
        # https://docs.nvidia.com/networking-ethernet-software/cumulus-linux-56/System-Configuration/NVIDIA-User-Experience-NVUE/NVUE-API/#replace-an-entire-configuration

        # Need to do some conversion as the startup.yaml begins with -set:
        # which is irrelevant to the API calls
        try:
            config_obj = YAML().load(new_configuration)
            config_obj = config_obj[0]["set"]
        except (KeyError, IndexError, YAMLError) as exc:
            raise ConfigSyntaxException("Invalid yaml loaded from the Config Store.") from exc

        try:
            # Create a revision
            rsp = self.post(f"{self._base_url}revision")
            rsp.raise_for_status()
            revision = list(rsp.json().keys())[0]

            if not partial:
                # Clear the root config for our revision
                rsp = self.delete(self._base_url, params={"rev": revision})
                rsp.raise_for_status()

            # Apply the full new configuration object
            rsp = self.patch(self._base_url, json=config_obj, params={"rev": revision})
            if rsp.status_code == 400:
                raise ConfigSyntaxException(ConfigSyntaxException.format_nvue_error(rsp.json()))
            rsp.raise_for_status()

            # return the revision ID for diff or commit
            return cast(str, revision)
        except requests.HTTPError as exc:
            raise NetworkDeviceException("Failed to load candidate configuration.") from exc

    def _get_revision_state(self, revision: str) -> tuple[str, dict[str, Any] | None]:
        rsp = self.get(f"{self._base_url}revision/{revision}", raise_on_failure=False)
        rsp.raise_for_status()
        state = rsp.json()["state"]
        transition = rsp.json().get("transition")
        return cast(str, state), transition

    def _get_diff(self, revision: str) -> str:
        # Need to diff in both directions
        removed_rsp = self.get(
            self._base_url,
            params={"rev": "applied", "diff": revision, "filled": "false"},
            raise_on_failure=False,
        )
        removed_rsp.raise_for_status()
        removed = removed_rsp.json()

        added_rsp = self.get(
            self._base_url,
            params={"rev": revision, "diff": "applied", "filled": "false"},
            raise_on_failure=False,
        )
        added_rsp.raise_for_status()
        added = added_rsp.json()

        cli_diff = []

        if removed:
            cli_diff.extend([f"nv unset {cmd}" for cmd in CumulusConnection._flatten(removed, [])])

        if added:
            cli_diff.extend([f"nv set {cmd}" for cmd in CumulusConnection._flatten(added, [])])

        return "\n".join(cli_diff)

    @staticmethod
    def _flatten(
        json_diff: dict[str, Any], commands: list[str], path: list[str] | None = None
    ) -> list[str]:
        path = path or []
        for key, val in json_diff.items():
            new_path = deepcopy(path)
            new_path.append(key)
            if isinstance(val, dict):
                if val:
                    CumulusConnection._flatten(val, commands, new_path)
                else:
                    # Handle empty dictionary case
                    commands.append(" ".join(new_path))
            elif val is None:
                # No need to include it at all
                # this happens if a config line was null
                # and now has a value or vice versa, it's enough
                # to show the associated opposite command (e.g. set/unset)
                pass
            else:
                # Base case
                new_path.append(str(val))
                commands.append(" ".join(new_path))

        return commands

    def _apply_config(self, revision: str, *, commit_confirm: bool = True) -> None:
        """Apply revision. If commit_confirm, use rollback-unless-confirmed; else apply directly."""
        if commit_confirm:
            data = {
                "state": "apply",
                "auto-prompt": {"confirm": "confirm_yes", "ays": "ays_yes"},
                "state-controls": {"confirm": COMMIT_CONFIRM_ROLLBACK_SECONDS},
            }
        else:
            data = {"state": "apply", "auto-prompt": {"ays": "ays_yes"}}
        rsp = self.patch(f"{self._base_url}revision/{revision}", json=data)
        rsp.raise_for_status()

    def _confirm_apply(self, revision: str) -> None:
        """Confirm the revision in ays state (cancel pending rollback). Verifies device is still reachable."""
        data = {
            "state": "apply",
            "auto-prompt": {"ays": "ays_yes"},
        }
        rsp = self.patch(f"{self._base_url}revision/{revision}", json=data)
        rsp.raise_for_status()

    def _save_applied_config(self) -> None:
        data = {"state": "save", "auto-prompt": {"ays": "ays_yes"}}
        rsp = self.patch(f"{self._base_url}revision/applied", json=data)
        rsp.raise_for_status()

    def perform_candidate_diff(self, new_configuration: str, partial: bool = False) -> str:
        """Load the candidate configuration and return the diff."""
        try:
            revision = self._load_candidate(new_configuration, partial)
            return self._get_diff(revision)
        except requests.HTTPError as exc:
            raise NetworkDeviceException("Failed to perform candidate diff.") from exc

    def _get_ignore_fail_error_message(
        self,
        revision: str,
        transition_data: dict[str, Any] | None,
    ) -> str:
        """Build manual-intervention error message for ignore_fail apply state."""
        if transition_data:
            error_details = ConfigApplyFailureException.format_nvue_apply_error(transition_data)
        else:
            error_details = "Config apply failed with ignore_fail state"
        return (
            f"{error_details}\n\n"
            f"MANUAL INTERVENTION REQUIRED:\n"
            f"You can try manually applying the configuration on "
            f"the switch:\n\n"
            f"  nv config apply {revision}\n\n"
            f"If successful, this workflow stage will "
            f"automatically retry and complete. If the manual "
            f"apply also fails, further investigation of the "
            f"configuration or device state is required."
        )

    def commit_candidate_config(
        self,
        new_configuration: str,
        approved_diff: str,
        partial: bool = False,
        *,
        commit_confirm: bool = True,
    ) -> None:
        """Load the candidate configuration and commit.

        When commit_confirm is True, applies with rollback-unless-confirmed then
        confirms. When False, applies directly (use for changes that cause brief
        interruption, e.g. IP change ahead of upstream VLAN change).
        """
        try:
            revision = self._load_candidate(new_configuration, partial)
            diff = self._get_diff(revision)

            if not diff:
                # A previous run succeeded to apply the config
                # but may have had issues validating the apply
                # consider it fully applied now and save if
                # it is not saved
                state = self._get_revision_state("applied")[0]
                if "saved" not in state:
                    self._save_applied_config()
                return

            if diff != approved_diff:
                raise DiffChangedException("Diff has changed since approval, aborting.")

            self._apply_config(revision, commit_confirm=commit_confirm)

            # Monitor until state is ays (awaiting confirm) or applied
            state = ""
            transition_data: dict[str, Any] | None = None
            while "ays" not in state and "applied" not in state:
                state, transition_data = self._get_revision_state(revision)
                if "invalid" in state:
                    msg = f"Invalid config: {json.dumps(transition_data)}"
                    raise InvalidConfigException(
                        msg,
                        non_retryable=True,
                    )
                if "ignore_fail" in state:
                    raise ConfigApplyFailureException(
                        self._get_ignore_fail_error_message(revision, transition_data),
                        non_retryable=True,
                    )
                time.sleep(1)

            if commit_confirm:
                # Confirm apply (cancel pending rollback); verifies device is still reachable.
                # All requests (_apply_config, _get_revision_state, _confirm_apply, _save) go
                # through _make_request, so 401 on any step (e.g. after password rotation) will
                # trigger password iteration via _try_passwords_with_callback.
                self._confirm_apply(revision)

            # If config auto-save is set in the device config,
            # state will be applied_and_saved
            # no need for an explicit save call
            if "saved" not in state:
                # Save the applied configuration to persist on reboot
                self._save_applied_config()
        except requests.HTTPError as exc:
            raise NetworkDeviceException("Failed to apply candidate configuration.") from exc

    def get_bridge_domains(self) -> Any:
        """Get all bridge domains."""
        return self.get(f"{self._base_url}bridge/domain").json()

    def get_mac_table(self) -> DeviceMacTable:
        """Get MAC addresses from all bridge domains."""
        result = DeviceMacTable()
        for domain in self.get_bridge_domains():
            macs = self.get(f"{self._base_url}bridge/domain/{domain}/mac-table").json()
            for _, entry in macs.items():
                new_entry = DeviceMacEntry.from_nvue(entry)
                if new_entry.interface == domain:
                    # Skip SVI MAC addresses which can have multiple entries
                    continue
                # Skip the local MAC address, which will be in the table
                # with no VLAN set as seen below
                # 1 7c:c2:55:90:03:e7  997   swp3
                # 2 b0:cf:0e:66:96:7e        swp3 permanent
                if not new_entry.vlan:
                    continue
                if new_entry.mac in result.by_mac:
                    logger.warning(
                        "Duplicate MAC address %s on device %s: using newest entry",
                        new_entry.mac,
                        self._host,
                    )
                    if new_entry.age > result.by_mac[new_entry.mac].age:
                        continue
                result.by_mac[new_entry.mac] = new_entry
                result.by_interface.setdefault(new_entry.interface, [])
                if new_entry.mac not in result.by_interface[new_entry.interface]:
                    result.by_interface[new_entry.interface].append(new_entry.mac)
        return result

    def get_arp_table(self) -> DeviceArpTable:
        """Get the device ARP table."""
        result = {}
        for interface, data in self.get_interfaces().items():
            if data.get("type") not in ["swp", "eth"]:
                # We only care about physical interfaces
                continue
            url = f"{self._base_url}interface/{interface}/ip/neighbor"
            rsp = self.get(url, raise_on_failure=False)
            if rsp.status_code == 200:
                result[interface] = rsp.json()
            elif rsp.status_code != 404:
                # 404 means the interface didn't have any neighbor info
                raise NetworkDeviceException(f"Error getting from url {url}: {rsp.text}")
        return DeviceArpTable.from_nvue(result)

    def get_lldp_data(self, interface_name: str) -> InterfaceNeighborData | None:
        url = f"{self._base_url}interface/{interface_name}/lldp"
        rsp = self.get(url, raise_on_failure=False, timeout=10)
        if rsp.status_code == 200:
            data = rsp.json()
            if data.get("neighbor"):
                if len(data["neighbor"]) != 1:
                    # Pull the entry with the youngest age
                    newest = min(data["neighbor"].values(), key=lambda x: int(x["age"]))
                    return InterfaceNeighborData.from_nvue(
                        {k: v for k, v in data["neighbor"].items() if v == newest}
                    )
                else:
                    return InterfaceNeighborData.from_nvue(data["neighbor"])
        elif rsp.status_code != 404:
            # 404 means no LLDP info for the interface
            raise NetworkDeviceException(f"Error getting from url {url}: {rsp.text}")
        return None

    def _get_all_lldp_data(self) -> dict[str, InterfaceNeighborData]:
        """Get all LLDP data for the device."""
        result = {}
        rsp = self.get(f"{self._base_url}interface", params={"include": "/*/lldp/neighbor"})
        if rsp.status_code == 404:
            return {}
        try:
            rsp.raise_for_status()
        except requests.HTTPError as exc:
            raise NetworkDeviceException(f"Error getting from url {rsp.url}: {rsp.text}") from exc

        for interface, data in rsp.json().items():
            if data.get("lldp", {}).get("neighbor"):
                neighbors = data["lldp"]["neighbor"]
                newest = min(neighbors.values(), key=lambda x: int(x["age"]))
                result[interface] = InterfaceNeighborData.from_nvue(
                    {k: v for k, v in neighbors.items() if v == newest}
                )
        return result

    def get_interface_connections(self) -> DeviceNeighborData:
        """Get all interface connections from LLDP."""
        neighbors = {}
        link_states = {}
        ts_info: dict[str, str] = {}
        lldp_data = self._get_all_lldp_data()
        for interface, link in self.get_interfaces().items():
            if link.get("type") not in ["swp", "eth"]:
                # We only care about physical interfaces
                continue
            # 5.11 splits out admin and oper status
            if link["link"].get("oper-status"):
                link_states[interface] = link["link"]["oper-status"] == "up"
            elif link["link"].get("state"):
                link_states[interface] = [*link["link"]["state"]][0] == "up"
            else:
                raise NetworkDeviceException(
                    f"No link state information for interface {interface} on device {self._host}"
                )

            if link_states[interface]:
                neighbor_data = lldp_data.get(interface)
                if neighbor_data:
                    neighbors[interface] = neighbor_data

        return DeviceNeighborData(
            neighbors=neighbors,
            link_states=link_states,
            ts_info=ts_info,
        )

    def _get_system(self) -> Any:
        """Get the system data."""
        return self.get(f"{self._base_url}system").json()

    def get_hostname(self) -> str:
        """Get the system hostname."""
        try:
            return str(self._get_system()["hostname"])
        except (KeyError, ValueError) as error:
            raise ApplicationError(
                f"No hostname configured for {self._host}",
                non_retryable=True,
            ) from error

    def execute_ztp(self) -> None:
        """Execute ZTP on the device."""
        # For Cumulus 5.11+ only, the best method is to factory reset the device
        payload = {"@reset": {"state": "start", "parameters": {"force": True}}}
        rsp = self.post(f"{self._base_url}system/factory-default", json=payload)
        rsp.raise_for_status()

    def get_running_image(self) -> str:
        """Get the running image on the device."""
        rsp = self._get_system()
        # 5.11.x stores product-release at the top level
        if "product-release" in rsp:
            return cast(str, rsp["product-release"])
        # 5.14.0 stores product-release in the version object
        if rsp.get("version", {}).get("product-release"):
            return cast(str, rsp["version"]["product-release"])
        raise NetworkDeviceException(
            "Unable to determine running image on the device.", non_retryable=True
        )

    def get_ztp_status(self) -> str:
        """Get the ZTP status of the device."""
        rsp = self.get(f"{self._base_url}system/ztp")
        return cast(str, rsp.json()["status"])

    def get_uptime(self) -> int:
        """Get the device uptime in seconds."""
        rsp = self._get_system()
        return int(rsp["uptime"])

    def get_platform(self) -> Any:
        """Get the platform information."""
        rsp = self.get(f"{self._base_url}platform", timeout=10)
        return rsp.json()

    def get_platform_environment_fan(self) -> Any:
        """Get platform fan information."""
        rsp = self.get(f"{self._base_url}platform/environment/fan", timeout=10)
        return rsp.json()

    def get_platform_environment_led(self) -> Any:
        """Get platform LED information."""
        rsp = self.get(f"{self._base_url}platform/environment/led", timeout=10)
        return rsp.json()

    def get_platform_environment_psu(self) -> Any:
        """Get platform PSU information."""
        rsp = self.get(f"{self._base_url}platform/environment/psu", timeout=10)
        return rsp.json()

    def get_platform_environment_voltage(self) -> Any:
        """Get platform voltage information."""
        rsp = self.get(f"{self._base_url}platform/environment/voltage", timeout=10)
        return rsp.json()

    def get_platform_inventory(self) -> Any:
        """Get platform inventory information."""
        rsp = self.get(f"{self._base_url}platform/inventory", timeout=10)
        return rsp.json()

    def get_firmware_versions(self) -> dict[str, Any]:
        """Get firmware versions for all components."""
        rsp = self.get(f"{self._base_url}platform/firmware", timeout=30)
        return cast(dict[str, Any], rsp.json())

    def reboot(self) -> None:
        """Reboot the device."""
        payload = {"@reboot": {"state": "start", "parameters": {"no-confirm": True}}}
        rsp = self.post(f"{self._base_url}system", json=payload)
        rsp.raise_for_status()

    def diag_get_version(self) -> object:
        # GET /nvue_v1/system — hostname, OS version, uptime, build date
        return self._get_system()

    def diag_get_interfaces(self) -> object:
        # GET /nvue_v1/interface filtered to link/state, oper-status, and type only.
        # Full interface responses crash switches with many interfaces (large body).
        # Uses the same state_only filter as cable validation (get_interfaces).
        return self.get_interfaces(state_only=True)

    def diag_get_bgp_summary(self) -> object:
        # GET /nvue_v1/vrf — all VRFs with BGP neighbor state filtered in
        rsp = self.get(
            f"{self._base_url}vrf",
            params={"include": ["/*/router/bgp/neighbor"]},
            raise_on_failure=False,
        )
        if rsp.status_code == 404:
            return {"bgp": "not configured"}
        rsp.raise_for_status()
        return rsp.json()

    def diag_get_lldp_neighbors(self) -> object:
        # GET /nvue_v1/interface — filtered to only LLDP neighbor data per interface
        rsp = self.get(
            f"{self._base_url}interface",
            params={"include": ["/*/lldp"]},
        )
        return rsp.json()

    def diag_get_platform(self) -> object:
        # GET /nvue_v1/platform — hardware model, serial, component firmware
        return self.get_platform()

    def diag_get_route_table(self) -> object:
        # GET /nvue_v1/vrf — all VRFs with routing table (RIB) filtered in
        rsp = self.get(
            f"{self._base_url}vrf",
            params={"include": ["/*/router/rib"]},
            raise_on_failure=False,
        )
        if rsp.status_code == 404:
            return {"rib": "not available"}
        rsp.raise_for_status()
        return rsp.json()

    def diag_get_vlan(self) -> object:
        # GET /nvue_v1/bridge/domain — all bridge domains with VLAN membership
        # Equivalent to: nv show bridge port-vlan
        rsp = self.get(f"{self._base_url}bridge/domain", raise_on_failure=False)
        if rsp.status_code == 404:
            return {"error": "No bridge domains configured"}
        rsp.raise_for_status()
        return rsp.json()

    def diag_get_mac_table(self) -> object:
        # GET /nvue_v1/bridge/domain/br_default/mac-table
        # Equivalent to: nv show bridge domain br_default mac-table / net show bridge macs
        rsp = self.get(
            f"{self._base_url}bridge/domain/br_default/mac-table",
            raise_on_failure=False,
        )
        if rsp.status_code == 404:
            return {"error": "Bridge domain br_default not configured or MAC table empty"}
        rsp.raise_for_status()
        return rsp.json()

    def diag_get_mlag(self) -> object:
        # GET /nvue_v1/mlag
        # Equivalent to: nv show clag / net show clag
        rsp = self.get(f"{self._base_url}mlag", raise_on_failure=False)
        if rsp.status_code == 404:
            return {"error": "MLAG not configured on this device"}
        rsp.raise_for_status()
        return rsp.json()

    def diag_get_system_health(self) -> object:
        # GET /nvue_v1/system/health
        # Equivalent to: nv show system health
        rsp = self.get(f"{self._base_url}system/health", raise_on_failure=False)
        if rsp.status_code == 404:
            return {"error": "System health endpoint not available on this NVUE version"}
        rsp.raise_for_status()
        return rsp.json()

    def diag_get_interface_counters(self) -> object:
        # GET /nvue_v1/interface?include=/*/counters
        # Equivalent to: nv show interface --view counters
        rsp = self.get(
            f"{self._base_url}interface",
            params={"include": ["/*/counters"]},
        )
        return rsp.json()

    def diag_get_interface_mac(self) -> object:
        # GET /nvue_v1/interface?include=/*/link/mac
        # Equivalent to: nv show interface mac
        rsp = self.get(
            f"{self._base_url}interface",
            params={"include": ["/*/link/mac"]},
        )
        return rsp.json()

    def diag_get_platform_environment(self) -> object:
        # GET /nvue_v1/platform/environment
        # Equivalent to: nv show platform environment / net show system sensors
        rsp = self.get(f"{self._base_url}platform/environment")
        return rsp.json()

    def diag_get_platform_transceiver(self) -> object:
        # GET /nvue_v1/platform/transceiver
        # Equivalent to: nv show platform transceiver
        rsp = self.get(f"{self._base_url}platform/transceiver", raise_on_failure=False)
        if rsp.status_code == 404:
            return {"error": "Transceiver data not available on this device"}
        rsp.raise_for_status()
        return rsp.json()

    def send_ssh_command(
        self,
        command: str,
        password: str,
        read_timeout: int = 900,
    ) -> str:
        """Execute a privileged shell command on the device via SSH and return its output.

        Uses netmiko (linux device type) with enable() for sudo access, mirroring
        the pattern used by MellanoxConnection. This is the single seam to replace
        when a dedicated nv-config-manager SSH service is available in future — callers such as
        get_tech_support_bundle need not change.

        heartbeat_fn, if provided, is called during quiet stretches of output to
        satisfy the Temporal activity heartbeat window.

        Returns:
            The full combined stdout/stderr output of the command as a string.
        """
        device = {
            "device_type": "linux",
            "host": self._host,
            "port": 22,
            "username": self._username,
            "password": password,
            "secret": password,
            "global_delay_factor": 2,
        }
        with ConnectHandler(**device) as conn:
            conn.enable()
            output = conn.send_command(
                command,
                expect_string=r"#",
                read_timeout=read_timeout,
                strip_prompt=False,
                strip_command=False,
            )
            return str(output)

    def _start_heartbeat_thread(
        self, heartbeat_fn: Callable[[], None] | None
    ) -> tuple[threading.Event, threading.Thread]:
        """Start a background thread that calls heartbeat_fn every 15 s.

        Copies the current contextvars context so that activity.heartbeat()
        works from the thread (threading.Thread does not inherit contextvars).
        """
        stop_event = threading.Event()

        def _beat() -> None:
            while not stop_event.wait(15):
                if heartbeat_fn:
                    try:
                        heartbeat_fn()
                    except Exception:
                        pass

        ctx = contextvars.copy_context()
        thread = threading.Thread(target=ctx.run, args=(_beat,), daemon=True)
        thread.start()
        return stop_event, thread

    def _sftp_download(
        self,
        password: str,
        remote_path: str,
        heartbeat_fn: Callable[[], None] | None,
    ) -> bytes:
        """Open a fresh paramiko SSH connection and download remote_path over SFTP."""
        ssh = paramiko.SSHClient()
        ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        try:
            ssh.connect(
                hostname=self._host,
                port=22,
                username=self._username,
                password=password,
                timeout=30,
                banner_timeout=30,
                auth_timeout=30,
                allow_agent=False,
                look_for_keys=False,
            )
            last_hb = [time.monotonic()]

            def _progress(transferred: int, total: int) -> None:
                if heartbeat_fn and time.monotonic() - last_hb[0] >= 10:
                    try:
                        heartbeat_fn()
                    except Exception:
                        pass
                    last_hb[0] = time.monotonic()

            buf = BytesIO()
            sftp = ssh.open_sftp()
            try:
                # 45-second per-read timeout so a stalled transfer surfaces as
                # a clear error rather than a silent heartbeat timeout.
                ch = sftp.get_channel()
                if ch is not None:
                    ch.settimeout(45)
                sftp.getfo(remote_path, buf, callback=_progress)
            finally:
                sftp.close()
            return buf.getvalue()
        finally:
            ssh.close()

    def get_tech_support_bundle(
        self, heartbeat_fn: Callable[[], None] | None = None
    ) -> tuple[bytes, str]:
        """Generate a cl-support bundle and return its content and command log.

        cl-support is a native Cumulus Linux system command — not exposed via
        the NVUE REST API. SSH is used only for this operation; the same device
        credentials are reused (via send_ssh_command / _sftp_download).

        -M requests a "mini" bundle: useful diagnostics without large packet
        captures. The tarball is retrieved over SFTP to avoid base64 overhead.

        heartbeat_fn, if provided, is called every ~15 s in a background thread
        and on each SFTP chunk, keeping the Temporal heartbeat window satisfied.

        Returns:
            (raw bundle bytes, full cl-support command output text)
        """

        def run_with_password(password: str) -> tuple[bytes, str]:
            stop_hb, hb = self._start_heartbeat_thread(heartbeat_fn)
            if heartbeat_fn:
                try:
                    heartbeat_fn()
                except Exception:
                    pass
            try:
                cl_support_log = self.send_ssh_command(
                    "cl-support -M", password=password, read_timeout=900
                )
                bundle_path = _parse_cl_support_path(cl_support_log)
                if not bundle_path:
                    raise NetworkDeviceException(
                        f"cl-support completed but no bundle path found in output:\n{cl_support_log}"
                    )
                content = self._sftp_download(password, bundle_path, heartbeat_fn)
                return content, cl_support_log
            finally:
                stop_hb.set()
                hb.join(timeout=2)

        return cast(
            tuple[bytes, str],
            self._try_passwords_with_callback(
                run_with_password,
                (paramiko.AuthenticationException, NetmikoAuthenticationException),
            ),
        )


# Identical to Cumulus but defaults to port 443
class NVOSConnection(CumulusConnection):
    """NVOS Device Connection."""

    def __init__(
        self,
        host: str,
        port: int = 443,
        username: str | None = None,
        password: str | None = None,
        site: str | None = None,
    ) -> None:
        """Initialize a NVOS Connection."""
        super().__init__(host, port, username, password, site)
