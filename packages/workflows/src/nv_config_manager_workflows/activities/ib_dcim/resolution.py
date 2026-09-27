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
"""Pure GUID, PKey, and location-resolution helpers retained by IB/DCIM activities."""

import re
from typing import Any

from temporalio.exceptions import ApplicationError

from nv_config_manager_workflows.activities.ib_dcim.models import ResolvedInterface

_PKEY_PATTERN = re.compile(r"^0[xX][0-9a-fA-F]{1,4}$")

_RESOLVE_GUIDS_QUERY = """
query ($guids: [String]) {
  interfaces(cf_ib_guid__ie: $guids) {
    id
    name
    cf_ib_guid
    device {
      name
    }
  }
}
"""


def normalize_ib_guid(guid: str) -> str:
    """Normalize an IB GUID for matching: trim, drop an optional ``0x`` prefix, lowercase.

    UFM and the DCIM store port GUIDs as bare hex (e.g. ``946dae0300598000``),
    but users commonly enter the ``0x``-prefixed form. Normalizing both sides
    lets either representation resolve.
    """
    normalized = (guid or "").strip().lower()
    if normalized.startswith("0x"):
        normalized = normalized[2:]
    return normalized


def index_resolved_interfaces(
    interfaces: list[dict[str, Any]],
    default_membership: str,
    membership_by_guid: dict[str, str] | None = None,
) -> dict[str, ResolvedInterface]:
    """Group GraphQL interface results by normalized GUID.

    Skips entries with no ``cf_ib_guid`` set. Raises ``ApplicationError`` if
    any GUID has more than one matching interface. Each match takes its per-GUID
    membership from ``membership_by_guid`` (keyed by normalized GUID), falling
    back to ``default_membership``.
    """
    membership_by_guid = membership_by_guid or {}
    grouped: dict[str, list[ResolvedInterface]] = {}
    for iface in interfaces:
        original_guid = iface.get("cf_ib_guid") or ""
        guid_key = normalize_ib_guid(original_guid)
        if not guid_key:
            continue
        device = (iface.get("device") or {}).get("name") or ""
        grouped.setdefault(guid_key, []).append(
            ResolvedInterface(
                device=device,
                interface=iface.get("name") or "",
                interface_id=iface.get("id") or "",
                guid=original_guid,
                membership=membership_by_guid.get(guid_key, default_membership),
            )
        )

    duplicates = {
        g: [r.interface_id for r in matches] for g, matches in grouped.items() if len(matches) > 1
    }
    if duplicates:
        raise ApplicationError(
            f"GUID(s) matched multiple DCIM interfaces: {duplicates}",
            non_retryable=True,
        )
    return {g: matches[0] for g, matches in grouped.items()}


def _is_auto_created_overlay_name(overlay_name: str, pkey: str) -> bool:
    """True when the overlay matches the member-add auto-created naming scheme.

    Auto-created overlays are named ``ib-pkey-overlay-<pkey>`` and exist solely as
    a container for one PKey with no members, so the delete workflow owns their lifecycle.
    Operator- or VPC-owned overlays use other names and are left untouched.
    """
    return overlay_name == f"ib-pkey-overlay-{pkey}"


SITE_LOCATION_TYPE_NAME = "Site"

_RESOLVE_BY_NAME_QUERY = """
query ($host: [String]) {
  devices(name: $host) {
    id
    name
    role { name }
    primary_ip4 { host }
    tenant { id name }
    location {
      id
      name
      location_type { name }
      overlays(isolation_type: ["ib_pkey"]) {
        id
        name
        pkeys {
          id
          pkey
        }
      }
      parent {
        id
        name
        location_type { name }
        overlays(isolation_type: ["ib_pkey"]) {
          id
          name
          pkeys {
            id
            pkey
          }
        }
        parent {
          id
          name
          location_type { name }
          overlays(isolation_type: ["ib_pkey"]) {
            id
            name
            pkeys {
              id
              pkey
            }
          }
        }
      }
    }
  }
}
"""

_RESOLVE_BY_IP_QUERY = """
query ($ip: [String]) {
  ip_addresses(address: $ip) {
    address
    interfaces {
      device {
        id
        name
        role { name }
        primary_ip4 { host }
        tenant { id name }
        location {
          id
          name
          location_type { name }
          overlays(isolation_type: ["ib_pkey"]) {
            id
            name
            pkeys {
              id
              pkey
            }
          }
          parent {
            id
            name
            location_type { name }
            overlays(isolation_type: ["ib_pkey"]) {
              id
              name
              pkeys {
                id
                pkey
              }
            }
            parent {
              id
              name
              location_type { name }
              overlays(isolation_type: ["ib_pkey"]) {
                id
                name
                pkeys {
                  id
                  pkey
                }
              }
            }
          }
        }
      }
    }
  }
}
"""


def _normalize_pkey(value: str) -> str:
    """Canonicalize an IB PKey to '0x' + 4 lowercase hex digits."""
    if not value or not _PKEY_PATTERN.match(value):
        raise ApplicationError(
            f"pkey {value!r} does not match required format (e.g. '0x8001')",
            non_retryable=True,
        )
    return f"0x{int(value, 16):04x}"


def _walk_location_chain(location: dict[str, Any] | None) -> list[dict[str, Any]]:
    """Return [location, parent, grandparent, ...] until parent is missing."""
    chain: list[dict[str, Any]] = []
    current = location
    while current:
        chain.append(current)
        current = current.get("parent")
    return chain


def _find_site_in_chain(chain: list[dict[str, Any]]) -> dict[str, Any] | None:
    """Return the first Site-typed location in the chain, or None."""
    for location in chain:
        if (location.get("location_type") or {}).get("name") == SITE_LOCATION_TYPE_NAME:
            return location
    return None


def _require_device_site(device: dict[str, Any]) -> dict[str, Any]:
    """Return the device's Site-typed location or raise a non-retryable error."""
    chain = _walk_location_chain(device.get("location") or {})
    site = _find_site_in_chain(chain)
    if site is not None:
        return site

    chain_repr = " -> ".join(
        f"{loc.get('name', '?')}:{(loc.get('location_type') or {}).get('name', '?')}"
        for loc in chain
    )
    raise ApplicationError(
        f"No {SITE_LOCATION_TYPE_NAME}-typed location in hierarchy for device "
        f"{device.get('name')!r}: {chain_repr}",
        non_retryable=True,
    )


def _iter_pkey_matches(
    chain: list[dict[str, Any]], canonical_pkey: str
) -> list[tuple[dict[str, Any], dict[str, Any], dict[str, Any]]]:
    """Collect every (location, overlay, pkey_record) triple in the chain matching canonical_pkey."""
    matches: list[tuple[dict[str, Any], dict[str, Any], dict[str, Any]]] = []
    for location in chain:
        for overlay in location.get("overlays") or []:
            for pkey_record in overlay.get("pkeys") or []:
                stored = pkey_record.get("pkey") or ""
                if not _PKEY_PATTERN.match(stored):
                    continue
                if f"0x{int(stored, 16):04x}" == canonical_pkey:
                    matches.append((location, overlay, pkey_record))
    return matches


def _select_pkey_match(
    device: dict[str, Any], canonical_pkey: str
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    """Select the (location, overlay, pkey_record) triple matching canonical_pkey."""
    device_location = device.get("location") or {}
    chain = _walk_location_chain(device_location)
    matches = _iter_pkey_matches(chain, canonical_pkey)

    device_loc_name = device_location.get("name") or "<unknown>"
    if not matches:
        raise ApplicationError(
            f"PKey {canonical_pkey!r} not found at or above location {device_loc_name!r}",
            non_retryable=True,
        )
    if len(matches) > 1:
        candidates = ", ".join(
            f"{loc.get('name', '<unnamed>')}/{ovl.get('name', '<unnamed>')}"
            for loc, ovl, _ in matches
        )
        raise ApplicationError(
            f"PKey {canonical_pkey!r} ambiguous near location {device_loc_name!r}: "
            f"matches [{candidates}]. Resolve the duplicate PKey/Overlay "
            f"entries in the DCIM before retrying.",
            non_retryable=True,
        )
    return matches[0]
