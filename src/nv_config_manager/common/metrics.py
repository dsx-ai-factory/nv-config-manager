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
"""Shared Prometheus metrics for SPIFFE credential health.

The spiffe-helper sidecar writes two files into a shared volume that every
service reads:

* ``bundle.json`` (``[auth.spiffe] jwks_uri``) -- the JWKS **trust bundle**
  used to validate *inbound* JWT-SVIDs.  It is only rewritten when the SPIRE
  signing keys rotate (infrequent), so its file age is *not* a useful
  liveness signal.  What matters is that it is present and parseable.
* ``jwt-svid`` (``[auth.spiffe] jwt_svid_path``) -- the short-lived JWT-SVID
  **token** sent on *outbound* service-to-service calls.  spiffe-helper
  refreshes it on a short cycle, so its ``exp`` claim decaying toward now is
  the real "SPIFFE auth is about to break" signal (e.g. spiffe-helper died).

These metrics are emitted by a lazy :class:`~prometheus_client.registry.Collector`
that reads both files at scrape time, so no background poller is needed and
values always reflect current on-disk state:

Trust bundle (inbound validation health):

* ``nv_config_manager_spiffe_trust_bundle_readable`` -- ``1`` if the bundle
  file could be read and parsed at scrape time, else ``0``.
* ``nv_config_manager_spiffe_trust_bundle_keys`` -- number of signing keys in
  the bundle.  ``0`` means a broken/empty bundle.
* ``nv_config_manager_spiffe_trust_bundle_earliest_cert_expiry_timestamp_seconds``
  -- earliest X.509 ``notAfter`` across any key that carries an ``x5c`` chain.
  Only emitted when the bundle actually contains certificate chains.

JWT-SVID (outbound credential freshness / liveness):

* ``nv_config_manager_spiffe_jwt_svid_readable`` -- ``1`` if the JWT-SVID file
  could be read and decoded at scrape time, else ``0``.
* ``nv_config_manager_spiffe_jwt_svid_expiry_timestamp_seconds`` -- unix
  timestamp of the JWT-SVID ``exp`` claim, labelled by ``trust_domain``
  (derived from the SPIFFE ID in ``sub``).  Alert when this approaches now:
  a healthy deployment shows a sawtooth well in the future, a dead
  spiffe-helper decays through the current time.

Each file is inspected independently and the collector no-ops for whichever
source is unconfigured (or, for the trust bundle, served over HTTP with no
local file to stat).
"""

from __future__ import annotations

import base64
import json
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import jwt as pyjwt
from cryptography import x509
from prometheus_client import REGISTRY
from prometheus_client.core import GaugeMetricFamily
from prometheus_client.registry import Collector, CollectorRegistry

from nv_config_manager.common.auth import SpiffeConfig, load_auth_config
from nv_config_manager.common.log import LogCategory, get_logger

logger = get_logger(__name__, category=LogCategory.AUTH)

_BUNDLE_PREFIX = "nv_config_manager_spiffe_trust_bundle"
_SVID_PREFIX = "nv_config_manager_spiffe_jwt_svid"


def _extract_jwks_keys(raw: str) -> list[dict[str, Any]]:
    """Parse signing keys from a spiffe-helper trust bundle.

    Mirrors :func:`nv_config_manager.common.auth._get_signing_key_from_jwks`:
    the bundle is either a raw JWKS (``{"keys": [...]}``) or the Teleport-style
    ``{domain: base64(JWKS), ...}`` wrapper whose values must be base64-decoded
    and merged.
    """
    data = json.loads(raw)
    if "keys" in data:
        return list(data.get("keys") or [])

    merged: list[dict[str, Any]] = []
    for encoded_jwks in data.values():
        domain_jwks = json.loads(base64.b64decode(encoded_jwks))
        merged.extend(domain_jwks.get("keys", []))
    return merged


def _trust_domain_from_sub(sub: str) -> str:
    """Extract the trust domain from a SPIFFE ID (``spiffe://<domain>/<path>``).

    Returns a low-cardinality label value: the trust domain only, never the
    full workload path, so the metric stays bounded and does not leak the
    per-workload identity.  Falls back to ``"unknown"`` for a missing or
    malformed ``sub``.
    """
    if not sub.startswith("spiffe://"):
        return "unknown"
    domain = sub[len("spiffe://") :].split("/", 1)[0]
    return domain or "unknown"


def _earliest_cert_expiry(keys: list[dict[str, Any]]) -> float | None:
    """Return the earliest X.509 ``notAfter`` (unix seconds) across ``x5c`` chains.

    Returns ``None`` when no key carries a parseable certificate chain, in
    which case no expiry metric is emitted (a plain public-key JWKS has no
    certificate to expire).
    """
    earliest: float | None = None
    for key in keys:
        chain = key.get("x5c")
        if not chain:
            continue
        try:
            cert = x509.load_der_x509_certificate(base64.b64decode(chain[0]))
            not_after = cert.not_valid_after_utc.timestamp()
        except Exception:
            logger.debug("Failed to parse x5c certificate from trust bundle", exc_info=True)
            continue
        if earliest is None or not_after < earliest:
            earliest = not_after
    return earliest


class SpiffeCredentialCollector(Collector):
    """Lazily emits SPIFFE trust-bundle and JWT-SVID health metrics at scrape time."""

    def collect(self) -> Iterator[GaugeMetricFamily]:
        spiffe = load_auth_config().spiffe
        if spiffe is None:
            return
        yield from self._collect_trust_bundle(spiffe)
        yield from self._collect_jwt_svid(spiffe)

    def _collect_trust_bundle(self, spiffe: SpiffeConfig) -> Iterator[GaugeMetricFamily]:
        # Only file-based bundles (written by spiffe-helper) have a local file
        # to inspect; HTTP JWKS endpoints produce no bundle metrics.
        if spiffe.jwks_uri.startswith(("http://", "https://")):
            return

        readable = GaugeMetricFamily(
            f"{_BUNDLE_PREFIX}_readable",
            "1 if the SPIFFE trust bundle file could be read and parsed at scrape time, else 0.",
        )
        keys_count = GaugeMetricFamily(
            f"{_BUNDLE_PREFIX}_keys",
            "Number of signing keys currently present in the SPIFFE trust bundle.",
        )

        try:
            keys = _extract_jwks_keys(Path(spiffe.jwks_uri).read_text())
        except Exception:
            logger.debug("SPIFFE trust bundle unreadable at %s", spiffe.jwks_uri, exc_info=True)
            readable.add_metric([], 0.0)
            yield readable
            return

        readable.add_metric([], 1.0)
        keys_count.add_metric([], float(len(keys)))
        yield readable
        yield keys_count

        earliest_expiry = _earliest_cert_expiry(keys)
        if earliest_expiry is not None:
            cert_expiry = GaugeMetricFamily(
                f"{_BUNDLE_PREFIX}_earliest_cert_expiry_timestamp_seconds",
                "Unix timestamp of the earliest x5c certificate notAfter in the trust bundle.",
            )
            cert_expiry.add_metric([], earliest_expiry)
            yield cert_expiry

    def _collect_jwt_svid(self, spiffe: SpiffeConfig) -> Iterator[GaugeMetricFamily]:
        if not spiffe.jwt_svid_path:
            return

        readable = GaugeMetricFamily(
            f"{_SVID_PREFIX}_readable",
            "1 if the SPIFFE JWT-SVID file could be read and decoded at scrape time, else 0.",
        )

        try:
            token = Path(spiffe.jwt_svid_path).read_text().strip()
            claims = pyjwt.decode(token, options={"verify_signature": False})
            exp = claims["exp"]
            trust_domain = _trust_domain_from_sub(str(claims.get("sub", "")))
        except Exception:
            logger.debug(
                "SPIFFE JWT-SVID unreadable/undecodable at %s",
                spiffe.jwt_svid_path,
                exc_info=True,
            )
            readable.add_metric([], 0.0)
            yield readable
            return

        readable.add_metric([], 1.0)
        yield readable

        expiry = GaugeMetricFamily(
            f"{_SVID_PREFIX}_expiry_timestamp_seconds",
            "Unix timestamp of the current SPIFFE JWT-SVID 'exp' claim.",
            labels=["trust_domain"],
        )
        expiry.add_metric([trust_domain], float(exp))
        yield expiry


_collector: SpiffeCredentialCollector | None = None


def install_spiffe_bundle_metrics(registry: CollectorRegistry = REGISTRY) -> None:
    """Register the SPIFFE credential collector on ``registry`` (idempotent).

    Safe to call from every FastAPI service's auth setup regardless of whether
    SPIFFE is configured; the collector no-ops at scrape time when there is no
    local credential to inspect.
    """
    global _collector
    if _collector is not None:
        return
    _collector = SpiffeCredentialCollector()
    registry.register(_collector)
