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
"""Live-cluster integration tests for the SPIFFE credential health metrics.

These verify the collector wired in ``nv_config_manager.common.metrics`` (via
``install_identity_probe``) actually surfaces on each service's unauthenticated
``/metrics`` endpoint, reading the real spiffe-helper artifacts on disk:

1.  ``spiffe_trust_bundle_readable`` / ``_keys`` reflect the JWKS trust bundle
    that spiffe-helper writes to ``/var/run/secrets/spiffe/bundle.json``.
2.  ``spiffe_jwt_svid_readable`` / ``_expiry_timestamp_seconds`` reflect the
    JWT-SVID at ``/var/run/secrets/spiffe/jwt-svid``, and the expiry gauge
    carries a ``trust_domain`` label matching the SVID's trust domain.

Metrics are scraped *from inside* a nv-config-manager pod via ``kubectl exec`` so the
scrape shares the same volumes and network path as production. ``/metrics`` is
unauthenticated, so no token is injected.

Enable with::

    pytest src/tests/integration/test_spiffe_metrics.py \\
        --spiffe \\
        --nv-config-manager-namespace nv-config-manager-test01 \\
        --spiffe-release nv-config-manager-test01
"""

from __future__ import annotations

import subprocess
import time
from collections.abc import Callable

import pytest
from prometheus_client.parser import text_string_to_metric_families

pytestmark = [pytest.mark.integration, pytest.mark.spiffe]


_BUNDLE = "nv_config_manager_spiffe_trust_bundle"
_SVID = "nv_config_manager_spiffe_jwt_svid"


# Every FastAPI service wires the collector through install_identity_probe and
# exposes it on its own /metrics, so we assert coverage across all of them.
_SERVICE_CASES = [
    pytest.param("temporal", "spiffe_temporal_url", id="temporal"),
    pytest.param("render", "spiffe_render_url", id="render"),
    pytest.param("ztp", "spiffe_ztp_url", id="ztp"),
    pytest.param("dhcp", "spiffe_dhcp_url", id="dhcp"),
    pytest.param("config-store", "spiffe_config_store_url", id="config-store"),
]


def _fetch_script(url: str) -> str:
    """Return a python snippet that GETs ``url`` and prints the response body."""
    return (
        "import urllib.request, sys\n"
        f"url = {url!r}\n"
        "req = urllib.request.Request(url)\n"
        "try:\n"
        "    with urllib.request.urlopen(req, timeout=8) as r:\n"
        "        sys.stdout.write('OK\\n')\n"
        "        sys.stdout.write(r.read().decode())\n"
        "except urllib.error.HTTPError as e:\n"
        "    sys.stdout.write(str(e.code) + '\\n')\n"
        "    sys.stdout.write(e.read().decode())\n"
    )


def _scrape_metrics(
    runner: Callable[[str], subprocess.CompletedProcess[str]],
    url: str,
) -> dict[str, list[tuple[dict[str, str], float]]]:
    """Scrape ``url`` in-pod and return ``{metric_name: [(labels, value), ...]}``.

    Only ``nv_config_manager_spiffe_*`` samples are retained so the assertions
    stay focused and the return value small.
    """
    result = runner(_fetch_script(f"{url}/metrics"))
    if result.returncode != 0:
        pytest.fail(f"kubectl exec failed: rc={result.returncode} stderr={result.stderr.strip()!r}")

    status, _, body = result.stdout.partition("\n")
    if status.strip() != "OK":
        pytest.fail(f"/metrics scrape at {url} returned status {status.strip()!r}: {body[:500]!r}")

    out: dict[str, list[tuple[dict[str, str], float]]] = {}
    for family in text_string_to_metric_families(body):
        for sample in family.samples:
            if sample.name.startswith("nv_config_manager_spiffe_"):
                out.setdefault(sample.name, []).append((dict(sample.labels), sample.value))
    return out


class TestSpiffeMetricsExposed:
    """The SPIFFE credential collector must surface on every service /metrics."""

    @pytest.mark.parametrize("service,url_fixture", _SERVICE_CASES)
    def test_trust_bundle_metrics_present(
        self,
        request: pytest.FixtureRequest,
        exec_python_in_spiffe_pod: Callable[[str], subprocess.CompletedProcess[str]],
        service: str,
        url_fixture: str,
    ) -> None:
        """Trust-bundle health must be readable with at least one signing key."""
        url = request.getfixturevalue(url_fixture)
        metrics = _scrape_metrics(exec_python_in_spiffe_pod, url)

        readable = metrics.get(f"{_BUNDLE}_readable")
        assert readable, f"[{service}] {_BUNDLE}_readable missing from /metrics"
        assert readable[0][1] == 1.0, (
            f"[{service}] trust bundle not readable: {readable!r}. Check that "
            f"spiffe-helper wrote bundle.json and jwks_uri points at it."
        )

        keys = metrics.get(f"{_BUNDLE}_keys")
        assert keys, f"[{service}] {_BUNDLE}_keys missing from /metrics"
        assert keys[0][1] >= 1.0, f"[{service}] trust bundle has no signing keys: {keys!r}"

    @pytest.mark.parametrize("service,url_fixture", _SERVICE_CASES)
    def test_jwt_svid_expiry_present_and_future(
        self,
        request: pytest.FixtureRequest,
        exec_python_in_spiffe_pod: Callable[[str], subprocess.CompletedProcess[str]],
        spiffe_trust_domain: str,
        service: str,
        url_fixture: str,
    ) -> None:
        """JWT-SVID must be readable and its expiry must be in the future.

        Also asserts the ``trust_domain`` label matches the SVID's trust domain
        (derived independently from the ``aud`` claim by the fixture), proving
        the label is the low-cardinality domain rather than the workload path.
        """
        url = request.getfixturevalue(url_fixture)
        metrics = _scrape_metrics(exec_python_in_spiffe_pod, url)

        readable = metrics.get(f"{_SVID}_readable")
        assert readable, f"[{service}] {_SVID}_readable missing from /metrics"
        assert readable[0][1] == 1.0, (
            f"[{service}] JWT-SVID not readable/decodable: {readable!r}. "
            f"spiffe-helper may have stopped refreshing jwt-svid."
        )

        expiry = metrics.get(f"{_SVID}_expiry_timestamp_seconds")
        assert expiry, f"[{service}] {_SVID}_expiry_timestamp_seconds missing from /metrics"
        labels, value = expiry[0]
        assert value > time.time(), (
            f"[{service}] JWT-SVID already expired (exp={value}, now={time.time()}). "
            f"spiffe-helper is not rotating the SVID."
        )
        assert labels.get("trust_domain") == spiffe_trust_domain, (
            f"[{service}] trust_domain label {labels.get('trust_domain')!r} does not match "
            f"the SVID trust domain {spiffe_trust_domain!r}"
        )
        # The label must never carry the full workload path (cardinality/leak).
        assert "/" not in labels.get("trust_domain", ""), (
            f"[{service}] trust_domain label leaks a path: {labels.get('trust_domain')!r}"
        )
