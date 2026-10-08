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
"""Pytest configuration for integration tests.

These tests require a running NVIDIA Config Manager deployment with DNS/hosts configured
to resolve the service hostnames (for example, the selected DCIM and Render API routes).

For local/CI environments, add entries to /etc/hosts pointing to the gateway IP.
SFTP is the only service that uses a kubectl port-forward (directly to the ZTP pod)
since it runs on a non-HTTP port that can't go through the gateway.

When --sso is passed, tests authenticate via OIDC PKCE (browser-based) using the
shared OIDCAuth class, and route API requests through svc-* JWT-only hostnames.
"""

import atexit
import base64
import json
import os
import socket
import subprocess
import time
from collections.abc import Callable, Generator

import pytest
import requests
import urllib3

from nv_config_manager.common.oidc import OIDCAuth
from tests.integration.dcim_adapter import DCIMIntegrationAdapter, load_dcim_integration_adapter

# Suppress InsecureRequestWarning for self-signed certs in tests
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

# Default hostnames (should match gateway configuration)
DEFAULT_BASE_HOSTNAME = "config-manager.local"

# Track SFTP port-forward subprocess for cleanup
_port_forward_processes: list[subprocess.Popen] = []


def _cleanup_port_forwards() -> None:
    """Terminate any remaining port-forward processes at exit."""
    for proc in _port_forward_processes:
        try:
            proc.terminate()
            proc.wait(timeout=5)
        except Exception:
            proc.kill()
    _port_forward_processes.clear()


atexit.register(_cleanup_port_forwards)


def pytest_addoption(parser: pytest.Parser) -> None:
    """Add custom command-line options for integration tests."""
    parser.addoption(
        "--dcim-provider",
        action="store",
        default="nautobot-2x",
        help="DCIM provider selected by the deployment (default: nautobot-2x)",
    )
    parser.addoption(
        "--dcim-integration-adapter",
        action="store",
        default="tests.integration.dcim_adapter:NautobotIntegrationAdapter",
        help="Provider inspection adapter as module:class",
    )
    parser.addoption(
        "--nv-config-manager-namespace",
        action="store",
        default="nv-config-manager",
        help="Kubernetes namespace for NVIDIA Config Manager deployment (default: nv-config-manager)",
    )
    parser.addoption(
        "--base-hostname",
        action="store",
        default=DEFAULT_BASE_HOSTNAME,
        help=f"Base hostname for NVIDIA Config Manager services (default: {DEFAULT_BASE_HOSTNAME})",
    )
    parser.addoption(
        "--sso",
        action="store_true",
        default=False,
        help="Authenticate via OIDC PKCE (opens browser). Uses svc-* JWT-only hostnames.",
    )
    parser.addoption(
        "--jira-issue-key",
        action="store",
        default=None,
        help="Jira issue key for diagnostics workflow tests (e.g. GNI-1234). "
        "Also readable from JIRA_ISSUE_KEY env var.",
    )
    parser.addoption(
        "--use-port-forward",
        action="store_true",
        default=False,
        help=(
            "Route Temporal, DCIM, Render, ZTP, and DHCP requests through kubectl "
            "port-forwards. Normal runs use Envoy Gateway hostnames."
        ),
    )
    parser.addoption(
        "--ci",
        action="store_true",
        default=False,
        help="Enable CI-only tests (e.g. git-token env-var checks that require kubectl on the runner).",
    )
    parser.addoption(
        "--spiffe",
        action="store_true",
        default=False,
        help=(
            "Enable live SPIFFE/Workload-Identity tests. Runs assertions from inside a "
            "nv-config-manager pod (via kubectl exec) against the JWT-SVID that spiffe-helper writes "
            "to /var/run/secrets/spiffe/jwt-svid. Requires a tbot DaemonSet on the "
            "cluster and the spiffe-helper sidecar enabled in the chart."
        ),
    )
    parser.addoption(
        "--spiffe-namespace",
        action="store",
        default=None,
        help=(
            "Namespace that has nv-config-manager pods with the spiffe-helper sidecar. "
            "Defaults to --nv-config-manager-namespace when unset."
        ),
    )
    parser.addoption(
        "--spiffe-release",
        action="store",
        default="nv-config-manager",
        help=(
            "Helm release name used as the prefix for in-cluster service DNS "
            "(e.g. '<release>-temporal-api'). Default: nv-config-manager."
        ),
    )
    parser.addoption(
        "--spiffe-expected-role",
        action="store",
        default="nv-config-manager",
        help=(
            "Role that a valid SPIFFE JWT-SVID is expected to resolve to via "
            "the chart's `spiffe.rbac.groupPrefixes` mapping. The test asserts "
            "this role appears in /whoami's 'roles' response. Pass '' to skip "
            "role-mapping assertions (useful for environments that only run "
            "identity extraction without RBAC). Default: nv-config-manager."
        ),
    )
    parser.addoption(
        "--rbac",
        action="store_true",
        default=False,
        help=(
            "Enable live group-mapping RBAC tests (test_rbac_group_mapping.py). "
            "Requires a `make kind-up-sec` deploy with nautobot.rbac.groupMapping "
            "CONFIGURED (see scripts/rbac-local-test/values-configured.yaml) so the "
            "group-mapping ConfigMap is mounted and the seeded nvcm-* Keycloak users "
            "exist. The tests port-forward Keycloak + Nautobot, log users in over the "
            "REST API to trigger the JWT authenticator, and assert group / "
            "ObjectPermission reconciliation via nautobot-server nbshell."
        ),
    )
    parser.addoption(
        "--rbac-release",
        action="store",
        default="nv-config-manager",
        help="Helm release name used to locate the Nautobot pod/service (default: nv-config-manager).",
    )
    parser.addoption(
        "--rbac-keycloak-namespace",
        action="store",
        default="keycloak",
        help="Namespace of the local Keycloak used for token grants (default: keycloak).",
    )
    parser.addoption(
        "--rbac-keycloak-service",
        action="store",
        default="keycloak",
        help="Keycloak Service name to port-forward for token grants (default: keycloak).",
    )
    parser.addoption(
        "--rbac-realm",
        action="store",
        default="nv-config-manager",
        help="Keycloak realm that holds the seeded nvcm-* users (default: nv-config-manager).",
    )
    parser.addoption(
        "--rbac-client-id",
        action="store",
        default="nv-config-manager",
        help=(
            "OIDC client used for the scripted password grant. The confidential "
            "`nv-config-manager` client has direct-access-grants enabled and the "
            "audience mappers the authenticator expects (default: nv-config-manager)."
        ),
    )
    parser.addoption(
        "--rbac-client-secret",
        action="store",
        default="nvcm-local-client-secret",
        help="Client secret for --rbac-client-id (default: nvcm-local-client-secret).",
    )


def pytest_configure(config: pytest.Config) -> None:
    config.addinivalue_line("markers", "integration: marks tests as integration tests")
    config.addinivalue_line(
        "markers", "timeout: set a per-test timeout in seconds (via pytest-timeout)"
    )
    config.addinivalue_line(
        "markers", "ci_only: test runs only when --ci is passed or CI env var is set"
    )
    config.addinivalue_line(
        "markers", "rbac: marks tests that require live group-mapping RBAC (opt-in via --rbac)"
    )
    config.addinivalue_line(
        "markers", "dcim_provider(name): test applies only to the named DCIM provider"
    )


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    selected_provider = str(config.getoption("--dcim-provider"))
    run_ci = bool(config.getoption("--ci") or os.environ.get("CI"))
    for item in items:
        if item.get_closest_marker("ci_only") and not run_ci:
            item.add_marker(
                pytest.mark.skip(reason="CI-only test (pass --ci or set CI env var to run)")
            )
        if marker := item.get_closest_marker("dcim_provider"):
            supported = {str(name) for name in marker.args}
            if selected_provider not in supported:
                item.add_marker(
                    pytest.mark.skip(
                        reason=f"requires DCIM provider {', '.join(sorted(supported))}"
                    )
                )


@pytest.fixture(scope="session")
def config_manager_namespace(request: pytest.FixtureRequest) -> str:
    """Get the Kubernetes namespace for NVIDIA Config Manager deployment."""
    return str(request.config.getoption("--nv-config-manager-namespace"))


@pytest.fixture(scope="session")
def base_hostname(request: pytest.FixtureRequest) -> str:
    """Get the base hostname for NVIDIA Config Manager services."""
    return str(request.config.getoption("--base-hostname"))


@pytest.fixture(scope="session")
def dcim_provider_name(request: pytest.FixtureRequest) -> str:
    """Return the provider selected for this integration run."""
    return str(request.config.getoption("--dcim-provider"))


@pytest.fixture(scope="session")
def dcim_adapter_type(request: pytest.FixtureRequest) -> type[DCIMIntegrationAdapter]:
    """Load the provider-owned test adapter selected on the command line."""
    spec = str(request.config.getoption("--dcim-integration-adapter"))
    try:
        adapter = load_dcim_integration_adapter(spec)
    except (AttributeError, ImportError, ValueError) as exc:
        pytest.fail(f"Could not load DCIM integration adapter {spec!r}: {exc}")
    return adapter


@pytest.fixture(scope="session")
def sso_enabled(request: pytest.FixtureRequest) -> bool:
    """Check if SSO (OIDC PKCE) authentication should be used."""
    return bool(request.config.getoption("--sso"))


@pytest.fixture(scope="session")
def headless_oidc_token() -> str | None:
    """Return a caller-supplied OIDC token for non-interactive SSO tests."""
    return os.environ.get("NVCM_OIDC_ACCESS_TOKEN")


@pytest.fixture(scope="session")
def use_port_forward(request: pytest.FixtureRequest) -> bool:
    """True when --use-port-forward is passed."""
    return bool(request.config.getoption("--use-port-forward"))


def _start_service_port_forward(
    namespace: str,
    service: str,
    local_port: int,
    remote_port: int,
) -> "subprocess.Popen | None":
    """Start a kubectl port-forward for a ClusterIP service.

    Returns the Popen process on success, or None if the port-forward fails to
    become ready within 10 seconds.
    """
    cmd = [
        "kubectl",
        "port-forward",
        "-n",
        namespace,
        f"svc/{service}",
        f"{local_port}:{remote_port}",
    ]
    print(f"\n[port-forward] Starting: {' '.join(cmd)}")
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    _port_forward_processes.append(proc)

    for i in range(10):
        if proc.poll() is not None:
            stdout, stderr = proc.communicate()
            print(f"[port-forward] {service} exited early: {stderr.decode().strip()}")
            _port_forward_processes.remove(proc)
            return None
        if _check_port_open("localhost", local_port):
            print(
                f"[port-forward] {service}:{remote_port} → localhost:{local_port} ready (attempt {i + 1})"
            )
            return proc
        time.sleep(1)

    print(f"[port-forward] {service} port {local_port} not open after 10s")
    proc.terminate()
    _port_forward_processes.remove(proc)
    return None


def _service_port_forward(
    namespace: str,
    service: str,
    local_port: int,
    remote_port: int,
) -> Generator[str]:
    """Yield a local URL backed by a Kubernetes service port-forward."""
    proc = _start_service_port_forward(namespace, service, local_port, remote_port)
    if proc is None:
        pytest.fail(
            f"Failed to start port-forward for {service} in namespace {namespace!r}. "
            "Ensure kubectl is configured and the service exists."
        )
    try:
        yield f"http://localhost:{local_port}"
    finally:
        proc.terminate()
        proc.wait(timeout=5)
        if proc in _port_forward_processes:
            _port_forward_processes.remove(proc)


@pytest.fixture(scope="session")
def temporal_api_port_forward(
    use_port_forward: bool,
    config_manager_namespace: str,
) -> Generator[str]:
    """Port-forward the Temporal API service when --use-port-forward is set.

    Yields the base URL to use (e.g. 'http://localhost:18001'), or '' when
    port-forward is not needed.
    """
    if not use_port_forward:
        yield ""
        return

    yield from _service_port_forward(
        config_manager_namespace, "nv-config-manager-temporal-api", 18001, 9000
    )


@pytest.fixture(scope="session")
def render_api_port_forward(
    use_port_forward: bool,
    config_manager_namespace: str,
) -> Generator[str]:
    """Port-forward the Render API when requested."""
    if not use_port_forward:
        yield ""
        return
    yield from _service_port_forward(
        config_manager_namespace, "nv-config-manager-render-api", 18002, 9000
    )


@pytest.fixture(scope="session")
def ztp_api_port_forward(
    use_port_forward: bool,
    config_manager_namespace: str,
) -> Generator[str]:
    """Port-forward the ZTP API when requested."""
    if not use_port_forward:
        yield ""
        return
    yield from _service_port_forward(
        config_manager_namespace, "nv-config-manager-ztp-api", 18003, 9000
    )


@pytest.fixture(scope="session")
def dhcp_api_port_forward(
    use_port_forward: bool,
    config_manager_namespace: str,
) -> Generator[str]:
    """Port-forward the DHCP API when requested."""
    if not use_port_forward:
        yield ""
        return
    yield from _service_port_forward(
        config_manager_namespace, "nv-config-manager-dhcp-internal", 18004, 9000
    )


@pytest.fixture(scope="session")
def dcim_port_forward(
    use_port_forward: bool,
    config_manager_namespace: str,
    dcim_adapter_type: type[DCIMIntegrationAdapter],
) -> Generator[str]:
    """Port-forward the selected DCIM service when requested.

    Yields the base URL to use (e.g. 'http://localhost:18080'), or '' when
    port-forward is not needed.
    """
    if not use_port_forward:
        yield ""
        return

    local_port = 18080
    proc = _start_service_port_forward(
        config_manager_namespace,
        dcim_adapter_type.service_name,
        local_port,
        dcim_adapter_type.service_port,
    )
    if proc is None:
        pytest.fail(
            f"Failed to start port-forward for {dcim_adapter_type.service_name} in namespace "
            f"'{config_manager_namespace}'. Ensure kubectl is configured and the service exists."
        )
    try:
        yield f"http://localhost:{local_port}"
    finally:
        proc.terminate()
        proc.wait(timeout=5)
        if proc in _port_forward_processes:
            _port_forward_processes.remove(proc)


@pytest.fixture(scope="session")
def nautobot_port_forward(
    dcim_provider_name: str,
    dcim_port_forward: str,
) -> str:
    """Compatibility alias for Nautobot-specific tests during adapter migration."""
    if dcim_provider_name != "nautobot-2x":
        pytest.skip("Nautobot port-forward requested for a different DCIM provider")
    return dcim_port_forward


@pytest.fixture(scope="session")
def oidc_auth(
    sso_enabled: bool,
    base_hostname: str,
    headless_oidc_token: str | None,
) -> OIDCAuth | None:
    """Create a session-scoped OIDCAuth instance when --sso is enabled.

    Auto-discovers OIDC config from the gateway and performs browser-based
    authentication once for the entire test session. Set
    ``NVCM_OIDC_ACCESS_TOKEN`` to provide a valid token for a headless run;
    it is used directly without writing a token cache. The interactive token
    cache is reused across browser-based test runs.

    Returns None when --sso is not enabled.
    """
    if not sso_enabled:
        return None

    gateway_url = f"https://workflow.{base_hostname}/v1/workflow"
    print(f"\n[SSO] Auto-discovering OIDC config from {gateway_url}...")

    try:
        auth = OIDCAuth.discover_from_gateway(
            gateway_url,
            verify=False,
        )
    except RuntimeError as e:
        pytest.fail(f"OIDC auto-discovery failed: {e}")

    if auth is None:
        pytest.fail(
            "SSO is not enabled on this environment (gateway did not redirect). "
            "Remove --sso or deploy with SSO enabled."
        )

    print(f"[SSO] Discovered issuer: {auth.issuer_url}")
    print(f"[SSO] Discovered client ID: {auth.client_id}")

    if headless_oidc_token:
        print("[SSO] Using NVCM_OIDC_ACCESS_TOKEN for headless authentication")
        return auth

    # Clear any stale cached token so we always do a fresh browser-based flow.
    # Cached tokens from previous runs (or a different environment) can cause
    # silent 401s without triggering the browser login.
    auth.clear_token()

    # Perform authentication (opens browser, waits for callback)
    try:
        token = auth.get_access_token()
        print(f"[SSO] Authentication successful (token length: {len(token)})")
    except RuntimeError as e:
        pytest.fail(f"OIDC authentication failed: {e}")

    return auth


@pytest.fixture(scope="session")
def dcim_token(
    config_manager_namespace: str,
    dcim_adapter_type: type[DCIMIntegrationAdapter],
) -> str:
    """Resolve the selected provider's API token from the environment or Kubernetes."""
    if token := os.environ.get("DCIM_TOKEN") or os.environ.get(
        dcim_adapter_type.token_environment_variable
    ):
        return token

    try:
        result = subprocess.run(
            [
                "kubectl",
                "get",
                "secret",
                "-n",
                config_manager_namespace,
                dcim_adapter_type.secret_name,
                "-o",
                f"jsonpath={{.data.{dcim_adapter_type.secret_key}}}",
            ],
            capture_output=True,
            text=True,
            check=True,
        )
        return base64.b64decode(result.stdout).decode("utf-8")
    except subprocess.CalledProcessError as exc:
        pytest.fail(
            f"Could not retrieve {dcim_adapter_type.secret_key} from "
            f"{dcim_adapter_type.secret_name}: {exc}"
        )


@pytest.fixture(scope="session")
def nautobot_token(dcim_provider_name: str, dcim_token: str) -> str:
    """Get the Nautobot API token.

    Compatibility alias for the provider-specific tests retained in this suite.
    """
    if dcim_provider_name != "nautobot-2x":
        pytest.skip("Nautobot token requested for a different DCIM provider")
    return dcim_token


# =============================================================================
# URL fixtures - each service has its own hostname
# When --sso is enabled, API URLs use svc-* hostnames (JWT-only, no OIDC redirect).
# Nautobot always uses its regular hostname (token-based auth, not OIDC).
# =============================================================================


@pytest.fixture(scope="session")
def dcim_url(
    base_hostname: str,
    dcim_port_forward: str,
    dcim_adapter_type: type[DCIMIntegrationAdapter],
) -> str:
    """Return the selected DCIM provider's externally reachable URL."""
    if dcim_port_forward:
        return dcim_port_forward
    if env_url := os.environ.get("DCIM_URL") or os.environ.get(
        dcim_adapter_type.url_environment_variable
    ):
        return env_url
    return f"https://{dcim_adapter_type.hostname_prefix}.{base_hostname}"


@pytest.fixture(scope="session")
def dcim_adapter(
    dcim_provider_name: str,
    dcim_adapter_type: type[DCIMIntegrationAdapter],
    dcim_url: str,
    dcim_token: str,
) -> DCIMIntegrationAdapter:
    """Create the provider-owned lifecycle inspection adapter."""
    if dcim_adapter_type.provider_name != dcim_provider_name:
        pytest.fail(
            f"DCIM adapter {dcim_adapter_type.__name__} serves "
            f"{dcim_adapter_type.provider_name!r}, not {dcim_provider_name!r}"
        )
    return dcim_adapter_type(dcim_url, dcim_token)


@pytest.fixture(scope="session")
def dcim_device_ids(dcim_adapter: DCIMIntegrationAdapter) -> list[str]:
    """Fetch up to three managed Cumulus Linux devices through the adapter."""
    devices = dcim_adapter.list_devices(platform="Cumulus Linux")
    device_ids = [str(device["id"]) for device in devices[:3]]
    if not device_ids:
        pytest.fail(
            "No Cumulus Linux devices found in the selected DCIM. Load the mock topology first."
        )
    print(f"\n[fixtures] Found {len(device_ids)} Cumulus Linux device(s) in the DCIM")
    return device_ids


@pytest.fixture(scope="session")
def nautobot_device_ids(
    nautobot_url: str,
    nautobot_client: requests.Session,
) -> list[str]:
    """Fetch up to 3 cumulus-linux device IDs from Nautobot.

    Shared across all integration test files that need real device IDs.
    Raises pytest.fail if no devices are found (topology not loaded).
    """
    resp = nautobot_client.post(
        f"{nautobot_url}/api/graphql/",
        json={
            "query": """
            query {
              devices(platform: "Cumulus Linux") {
                id
              }
            }
            """
        },
        timeout=15,
    )
    resp.raise_for_status()
    data = resp.json()
    if "errors" in data:
        pytest.fail(f"Nautobot GraphQL query failed: {data['errors']}")
    device_ids = [d["id"] for d in data.get("data", {}).get("devices", [])][:3]
    if not device_ids:
        pytest.fail(
            "No cumulus-linux devices found in Nautobot. "
            "Load the mock topology first: make topology"
        )
    print(f"\n[fixtures] Found {len(device_ids)} cumulus-linux device(s) in Nautobot")
    return device_ids


@pytest.fixture(scope="session")
def nautobot_url(
    dcim_provider_name: str,
    dcim_url: str,
) -> str:
    """Get the Nautobot API URL.

    Nautobot uses its own token auth, so it always uses the regular hostname.
    When --use-port-forward is set, routes through a kubectl port-forward instead.
    """
    if dcim_provider_name != "nautobot-2x":
        pytest.skip("Nautobot URL requested for a different DCIM provider")
    return dcim_url


@pytest.fixture(scope="session")
def render_api_url(
    base_hostname: str,
    sso_enabled: bool,
    render_api_port_forward: str,
) -> str:
    """Get the Render API URL."""
    if render_api_port_forward:
        return render_api_port_forward
    if env_url := os.environ.get("RENDER_URL"):
        return env_url
    prefix = "svc-render" if sso_enabled else "render"
    return f"https://{prefix}.{base_hostname}"


@pytest.fixture(scope="session")
def ztp_api_url(
    base_hostname: str,
    sso_enabled: bool,
    ztp_api_port_forward: str,
) -> str:
    """Get the ZTP API URL."""
    if ztp_api_port_forward:
        return ztp_api_port_forward
    if env_url := os.environ.get("ZTP_URL"):
        return env_url
    prefix = "svc-ztp" if sso_enabled else "ztp"
    return f"https://{prefix}.{base_hostname}"


@pytest.fixture(scope="session")
def dhcp_api_url(
    base_hostname: str,
    sso_enabled: bool,
    dhcp_api_port_forward: str,
) -> str:
    """Get the DHCP API URL."""
    if dhcp_api_port_forward:
        return dhcp_api_port_forward
    if env_url := os.environ.get("DHCP_URL"):
        return env_url
    prefix = "svc-dhcp" if sso_enabled else "dhcp"
    return f"https://{prefix}.{base_hostname}"


@pytest.fixture(scope="session")
def temporal_api_url(
    base_hostname: str,
    sso_enabled: bool,
    temporal_api_port_forward: str,
) -> str:
    """Get the Temporal API URL.

    When --use-port-forward is set, routes through a kubectl port-forward instead
    of the gateway hostname (useful on machines without config-manager.local DNS resolution).
    """
    if temporal_api_port_forward:
        return temporal_api_port_forward
    if env_url := os.environ.get("TEMPORAL_URL"):
        return env_url
    prefix = "svc-workflow" if sso_enabled else "workflow"
    return f"https://{prefix}.{base_hostname}"


def _check_port_open(host: str, port: int, timeout: float = 1.0) -> bool:
    """Check if a port is open and accepting connections."""
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except (OSError, TimeoutError):
        return False


def _find_ztp_pod(namespace: str) -> str | None:
    """Find a ZTP pod name using label selector."""
    try:
        result = subprocess.run(
            [
                "kubectl",
                "get",
                "pods",
                "-n",
                namespace,
                "-l",
                "app.kubernetes.io/component=network-ztp",
                "-o",
                "jsonpath={.items[0].metadata.name}",
            ],
            capture_output=True,
            text=True,
            check=True,
        )
        pod_name = result.stdout.strip()
        if pod_name:
            return pod_name
    except subprocess.CalledProcessError:
        pass

    # Fallback: try with just app label pattern
    try:
        result = subprocess.run(
            [
                "kubectl",
                "get",
                "pods",
                "-n",
                namespace,
                "-o",
                "jsonpath={.items[*].metadata.name}",
            ],
            capture_output=True,
            text=True,
            check=True,
        )
        # Find pod with "ztp" in name (but not "ztp-api" service)
        for pod in result.stdout.split():
            if "ztp" in pod.lower() and "api" not in pod.lower():
                return pod
            # Also match if it just contains ztp
            if "-ztp-" in pod or pod.endswith("-ztp"):
                return pod
    except subprocess.CalledProcessError:
        pass

    return None


@pytest.fixture(scope="session")
def kind_filestore_deployment(config_manager_namespace: str) -> None:
    """Require an ephemeral Kind ZTP deployment backed by writable file storage."""
    try:
        context_result = subprocess.run(
            ["kubectl", "config", "current-context"],
            capture_output=True,
            text=True,
            check=True,
        )
    except subprocess.CalledProcessError:
        pytest.skip("Could not determine the Kubernetes context")

    context = context_result.stdout.strip()
    if not context.startswith("kind-"):
        pytest.skip(f"FileStore mutation test requires a Kind context, got {context!r}")

    pod_name = _find_ztp_pod(config_manager_namespace)
    if pod_name is None:
        pytest.skip("Could not find the ZTP pod")

    try:
        pod_result = subprocess.run(
            [
                "kubectl",
                "get",
                "pod",
                pod_name,
                "-n",
                config_manager_namespace,
                "-o",
                "json",
            ],
            capture_output=True,
            text=True,
            check=True,
        )
        pod = json.loads(pod_result.stdout)
    except (subprocess.CalledProcessError, json.JSONDecodeError):
        pytest.skip("Could not inspect the ZTP pod storage configuration")

    http_api = next(
        (
            container
            for container in pod.get("spec", {}).get("containers", [])
            if container.get("name") == "http-api"
        ),
        None,
    )
    writable_file_store = http_api is not None and any(
        mount.get("name") == "os-images" and not mount.get("readOnly", False)
        for mount in http_api.get("volumeMounts", [])
    )
    if not writable_file_store:
        pytest.skip("ZTP is not configured with a writable FileStore")


@pytest.fixture(scope="session")
def sftp_port_forward(config_manager_namespace: str) -> Generator[tuple[str, int] | None]:
    """Set up a dedicated port-forward for the SFTP server.

    SFTP runs on a non-HTTP port (2222) that can't go through the HTTP gateway,
    so it always needs a port-forward unless SFTP_HOST env var is set.

    Note: The SFTP port is only exposed via LoadBalancer service (nv-config-manager-ztp-service)
    which may not exist in local/CI environments. We port-forward directly to the
    pod using label selector instead of service name.

    Yields (host, port) tuple for connecting to SFTP, or None if env vars are used.
    """
    # Check for env var override first
    if os.environ.get("SFTP_HOST"):
        yield None
        return

    local_port = 18222
    target_port = 2222

    # Find the ZTP pod dynamically since deployment name varies by release
    pod_name = _find_ztp_pod(config_manager_namespace)
    if not pod_name:
        print(f"[SFTP port-forward] Could not find ZTP pod in namespace {config_manager_namespace}")
        # List all pods for debugging
        result = subprocess.run(
            ["kubectl", "get", "pods", "-n", config_manager_namespace, "-o", "wide"],
            capture_output=True,
            text=True,
        )
        print(f"[SFTP port-forward] Available pods:\n{result.stdout}")
        yield None
        return

    print(f"[SFTP port-forward] Found ZTP pod: {pod_name}")

    # Port-forward directly to pod since the ClusterIP service (nv-config-manager-ztp-api)
    # doesn't expose the SFTP port - only the LoadBalancer service does
    cmd = [
        "kubectl",
        "port-forward",
        "-n",
        config_manager_namespace,
        f"pod/{pod_name}",
        f"{local_port}:{target_port}",
    ]

    print(f"[SFTP port-forward] Starting: {' '.join(cmd)}")

    proc: subprocess.Popen | None = None
    try:
        proc = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        _port_forward_processes.append(proc)

        # Wait for port-forward to establish with retries
        max_wait = 10
        for i in range(max_wait):
            # Check if process died
            if proc.poll() is not None:
                stdout, stderr = proc.communicate()
                print(f"[SFTP port-forward] Process exited with code {proc.returncode}")
                print(f"[SFTP port-forward] stdout: {stdout.decode()}")
                print(f"[SFTP port-forward] stderr: {stderr.decode()}")
                yield None
                return

            # Check if port is open
            if _check_port_open("localhost", local_port):
                print(f"[SFTP port-forward] Port {local_port} is now open (attempt {i + 1})")
                break

            time.sleep(1)
        else:
            # Port never opened - check process status
            print(f"[SFTP port-forward] Port {local_port} not open after {max_wait}s")
            if proc.poll() is None:
                print("[SFTP port-forward] Process still running but port not accessible")
            else:
                stdout, stderr = proc.communicate()
                print(f"[SFTP port-forward] Process died. stderr: {stderr.decode()}")
            yield None
            return

        yield ("localhost", local_port)
    except Exception as e:
        print(f"[SFTP port-forward] Exception: {e}")
        yield None
    finally:
        if proc:
            try:
                proc.terminate()
                proc.wait(timeout=5)
            except Exception:
                proc.kill()
            if proc in _port_forward_processes:
                _port_forward_processes.remove(proc)


@pytest.fixture(scope="session")
def sftp_host_port(sftp_port_forward: tuple[str, int] | None) -> tuple[str, int]:
    """Get the SFTP server host and port.

    Returns a tuple of (host, port) for connecting to the SFTP server.
    Uses dedicated port-forward since SFTP can't go through HTTP gateway.
    """
    if env_host := os.environ.get("SFTP_HOST"):
        env_port = int(os.environ.get("SFTP_PORT", "2222"))
        return (env_host, env_port)

    if sftp_port_forward:
        return sftp_port_forward

    pytest.fail(
        "SFTP port-forward failed to start. Set SFTP_HOST and SFTP_PORT env vars, "
        "or ensure kubectl is configured correctly."
    )


# =============================================================================
# Client fixtures - pre-configured sessions for each service
#
# When --sso is enabled, API clients authenticate with a real JWT obtained via
# OIDC PKCE and route through svc-* hostnames. Otherwise they use mock
# X-AUTH-REQUEST-EMAIL headers through the regular hostnames.
# =============================================================================


def _make_sso_session(
    oidc_auth: OIDCAuth,
    headless_oidc_token: str | None = None,
) -> requests.Session:
    """Create a requests session with JWT Bearer auth from OIDCAuth."""
    session = requests.Session()
    token = headless_oidc_token or oidc_auth.get_access_token()
    session.headers.update(
        {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        }
    )
    session.verify = False
    return session


def _make_mock_sso_session(close_connections: bool = False) -> requests.Session:
    """Create a requests session with mock X-AUTH-REQUEST-EMAIL header.

    Pass close_connections=True when requests go through kubectl port-forward.
    Port-forward silently drops keep-alive connections; setting Connection: close
    forces a fresh TCP connection per request, preventing 'Remote end closed
    connection without response' errors between poll cycles.
    """
    session = requests.Session()
    headers: dict[str, str] = {
        "Content-Type": "application/json",
        "X-AUTH-REQUEST-EMAIL": "integration-test@nvidia.com",
    }
    if close_connections:
        headers["Connection"] = "close"
    session.headers.update(headers)
    session.verify = False
    return session


@pytest.fixture(scope="session")
def nautobot_client(nautobot_url: str, nautobot_token: str) -> requests.Session:
    """Create a configured requests session for Nautobot API.

    Nautobot always uses its own Token auth, regardless of --sso.
    """
    session = requests.Session()
    session.headers.update(
        {
            "Authorization": f"Token {nautobot_token}",
            "Content-Type": "application/json",
        }
    )
    session.verify = False
    return session


@pytest.fixture(scope="session")
def render_client(
    render_api_url: str,
    sso_enabled: bool,
    oidc_auth: OIDCAuth | None,
    headless_oidc_token: str | None,
) -> requests.Session:
    """Create a configured requests session for Render API."""
    if sso_enabled and oidc_auth:
        return _make_sso_session(oidc_auth, headless_oidc_token)
    session = requests.Session()
    session.headers.update({"Content-Type": "application/json"})
    session.verify = False
    return session


@pytest.fixture(scope="session")
def ztp_client(
    ztp_api_url: str,
    sso_enabled: bool,
    oidc_auth: OIDCAuth | None,
    headless_oidc_token: str | None,
) -> requests.Session:
    """Create a configured requests session for ZTP API."""
    if sso_enabled and oidc_auth:
        return _make_sso_session(oidc_auth, headless_oidc_token)
    return _make_mock_sso_session()


@pytest.fixture(scope="session")
def dhcp_client(
    dhcp_api_url: str,
    sso_enabled: bool,
    oidc_auth: OIDCAuth | None,
    headless_oidc_token: str | None,
) -> requests.Session:
    """Create a configured requests session for DHCP API."""
    if sso_enabled and oidc_auth:
        return _make_sso_session(oidc_auth, headless_oidc_token)
    return _make_mock_sso_session()


@pytest.fixture(scope="session")
def temporal_client(
    temporal_api_url: str,
    sso_enabled: bool,
    oidc_auth: OIDCAuth | None,
    headless_oidc_token: str | None,
    temporal_api_port_forward: str,
) -> requests.Session:
    """Create a configured requests session for Temporal Workflow API.

    When --use-port-forward is active, disables HTTP keep-alive so that each
    request opens a fresh connection. kubectl port-forward silently drops idle
    keep-alive connections, causing 'RemoteDisconnected' errors on reuse.
    """
    if sso_enabled and oidc_auth:
        return _make_sso_session(oidc_auth, headless_oidc_token)
    return _make_mock_sso_session(close_connections=bool(temporal_api_port_forward))


# =============================================================================
# SPIFFE fixtures — enabled with --spiffe
#
# These fixtures drive the live-cluster SPIFFE tests in test_spiffe.py. Unlike
# the OIDC/SSO fixtures above, they do not make HTTP requests from the test
# runner itself; instead they use `kubectl exec` to run small probes inside a
# nv-config-manager pod that already has the spiffe-helper sidecar. That keeps the probe
# runtime identical to production app code (same volumes, same DNS, same
# kernel) without requiring the test runner to also be on-cluster.
# =============================================================================


@pytest.fixture(scope="session")
def spiffe_enabled(request: pytest.FixtureRequest) -> bool:
    """True when --spiffe is passed."""
    return bool(request.config.getoption("--spiffe"))


@pytest.fixture(scope="session")
def spiffe_namespace(request: pytest.FixtureRequest, config_manager_namespace: str) -> str:
    """Namespace holding nv-config-manager pods with the spiffe-helper sidecar."""
    override = request.config.getoption("--spiffe-namespace")
    return str(override) if override else config_manager_namespace


@pytest.fixture(scope="session")
def spiffe_release(request: pytest.FixtureRequest) -> str:
    """Helm release name, used to build in-cluster service DNS names."""
    return str(request.config.getoption("--spiffe-release"))


@pytest.fixture(scope="session")
def spiffe_expected_role(request: pytest.FixtureRequest) -> str | None:
    """Role the SPIFFE prefix-to-group mapping is expected to resolve to.

    Returns ``None`` when the operator explicitly wants to skip role-mapping
    assertions (empty string from the CLI).  The assertion-gated tests skip
    themselves when this is None, so CI doesn't fail on environments that
    haven't wired up ``spiffe.rbac.groupPrefixes`` yet.
    """
    value = str(request.config.getoption("--spiffe-expected-role") or "").strip()
    return value or None


def _kubectl_run(*args: str, timeout: int = 30) -> subprocess.CompletedProcess[str]:
    """Run a kubectl command and capture stdout/stderr as text."""
    return subprocess.run(
        ["kubectl", *args],
        capture_output=True,
        text=True,
        timeout=timeout,
        check=False,
    )


def _find_config_manager_pod_with_spiffe_helper(namespace: str) -> str | None:
    """Return the name of a Running nv-config-manager pod that has the spiffe-helper sidecar.

    The spiffe-helper sidecar is only injected when the Helm chart is rendered
    with ``spiffe.enabled=true``.  Any pod that has it will have the JWT-SVID
    and trust bundle files at /var/run/secrets/spiffe/, which is all the probes
    need.  Picking the first match keeps the fixture simple; pods are
    equivalent for SPIFFE purposes.
    """
    result = _kubectl_run(
        "get",
        "pods",
        "-n",
        namespace,
        "--field-selector=status.phase=Running",
        "-o",
        "jsonpath={range .items[*]}{.metadata.name}{'|'}"
        "{range .spec.containers[*]}{.name},{end}{'\\n'}{end}",
    )
    if result.returncode != 0:
        return None
    for line in result.stdout.splitlines():
        if "|" not in line:
            continue
        pod, containers = line.split("|", 1)
        if "spiffe-helper" in containers.split(",") and "api" in containers.split(","):
            return pod
    return None


@pytest.fixture(scope="session")
def spiffe_pod(spiffe_enabled: bool, spiffe_namespace: str) -> str:
    """Pod name of any running nv-config-manager pod with the spiffe-helper sidecar.

    Skips the whole SPIFFE suite when --spiffe is off or no such pod exists.
    Session-scoped so we pay the kubectl cost exactly once.
    """
    if not spiffe_enabled:
        pytest.skip("SPIFFE tests require --spiffe")
    pod = _find_config_manager_pod_with_spiffe_helper(spiffe_namespace)
    if pod is None:
        pytest.skip(
            f"No Running nv-config-manager pod with spiffe-helper sidecar found in namespace "
            f"'{spiffe_namespace}'. Deploy the chart with spiffe.enabled=true."
        )
    print(f"\n[spiffe] using pod: {spiffe_namespace}/{pod}")
    return pod


def _exec_python_in_pod(
    namespace: str,
    pod: str,
    script: str,
    container: str = "api",
    timeout: int = 20,
) -> subprocess.CompletedProcess[str]:
    """Run ``python3 -c <script>`` inside a pod container.

    Using the ``api`` container (not ``spiffe-helper``) because spiffe-helper
    ships as a distroless image with no shell or python.  The ``api`` container
    shares the same /var/run/secrets/spiffe/ emptyDir, so it sees the same
    JWT-SVID and trust bundle files.
    """
    return _kubectl_run(
        "exec",
        "-n",
        namespace,
        pod,
        "-c",
        container,
        "--",
        "python3",
        "-c",
        script,
        timeout=timeout,
    )


@pytest.fixture(scope="session")
def spiffe_jwt(spiffe_namespace: str, spiffe_pod: str) -> str:
    """Raw JWT-SVID currently on disk in the chosen pod.

    Reading it once per session is fine because we only use the returned string
    to construct Authorization headers in immediately-subsequent tests — all
    validity checks (signature, aud, exp) happen on the server side per
    request.  If the JWT rotates mid-run that's a plus, not a problem.
    """
    script = "import pathlib,sys; sys.stdout.write(pathlib.Path('/var/run/secrets/spiffe/jwt-svid').read_text().strip())"
    res = _exec_python_in_pod(spiffe_namespace, spiffe_pod, script)
    if res.returncode != 0 or not res.stdout.strip():
        pytest.fail(
            f"Could not read JWT-SVID from pod {spiffe_pod}: "
            f"rc={res.returncode} stderr={res.stderr.strip()!r}"
        )
    return res.stdout.strip()


@pytest.fixture(scope="session")
def spiffe_trust_domain(spiffe_jwt: str) -> str:
    """Trust domain decoded from the JWT's audience claim.

    The audience is always ``spiffe://<trust-domain>`` for Teleport-issued
    JWT-SVIDs, so we extract it once here rather than threading another CLI
    flag through every test.
    """
    _, payload, _ = spiffe_jwt.split(".")
    payload += "=" * (-len(payload) % 4)
    claims = json.loads(base64.urlsafe_b64decode(payload))
    aud = claims.get("aud")
    if isinstance(aud, list):
        aud = aud[0] if aud else ""
    if not isinstance(aud, str) or not aud.startswith("spiffe://"):
        pytest.fail(f"Unexpected aud claim in JWT-SVID: {aud!r}")
    return aud[len("spiffe://") :]


@pytest.fixture(scope="session")
def spiffe_temporal_url(spiffe_release: str, spiffe_namespace: str) -> str:
    """In-cluster URL for temporal-api, the service with SPIFFE auth middleware."""
    return f"http://{spiffe_release}-temporal-api.{spiffe_namespace}.svc.cluster.local:9000"


@pytest.fixture(scope="session")
def spiffe_render_url(spiffe_release: str, spiffe_namespace: str) -> str:
    """In-cluster URL for render-api (used by the client-injection tests)."""
    return f"http://{spiffe_release}-render-api.{spiffe_namespace}.svc.cluster.local:9000"


@pytest.fixture(scope="session")
def spiffe_config_store_url(spiffe_release: str, spiffe_namespace: str) -> str:
    """In-cluster URL for config-store-api."""
    return f"http://{spiffe_release}-config-store-api.{spiffe_namespace}.svc.cluster.local:9000"


@pytest.fixture(scope="session")
def spiffe_ztp_url(spiffe_release: str, spiffe_namespace: str) -> str:
    """In-cluster URL for ztp-api (used by the client-injection tests)."""
    return f"http://{spiffe_release}-ztp-api.{spiffe_namespace}.svc.cluster.local:9000"


@pytest.fixture(scope="session")
def spiffe_dhcp_url(spiffe_release: str, spiffe_namespace: str) -> str:
    """In-cluster URL for dhcp-api."""
    return f"http://{spiffe_release}-dhcp-internal.{spiffe_namespace}.svc.cluster.local:9000"


@pytest.fixture(scope="session")
def exec_python_in_spiffe_pod(
    spiffe_namespace: str,
    spiffe_pod: str,
) -> Callable[[str], subprocess.CompletedProcess[str]]:
    """Callable that runs a python snippet inside the chosen nv-config-manager pod.

    Tests use this to make HTTP requests *from within* the pod so that the
    request's source IP, DNS resolution, and volume mounts all match a real
    nv-config-manager-to-nv-config-manager call.
    """

    def _run(script: str, timeout: int = 20) -> subprocess.CompletedProcess[str]:
        return _exec_python_in_pod(spiffe_namespace, spiffe_pod, script, timeout=timeout)

    return _run


# =============================================================================
# Group-mapping RBAC fixtures — enabled with --rbac
#
# These drive the live-cluster tests in test_rbac_group_mapping.py. Unlike the
# SPIFFE suite (which execs probes inside a pod), the RBAC tests act like a real
# operator: they fetch a JWT from the local Keycloak via password grant, hit the
# Nautobot REST API to *trigger* the JWT authenticator + rbac sync (reconcile
# only runs on login), and inspect the resulting Django Group / ObjectPermission
# state through `nautobot-server nbshell`. It covers the code paths unit tests
# can't: Nautobot's change-logging signals firing on ObjectPermission.delete()
# during the revoke path.
#
# Requires a deploy in the CONFIGURED state (nautobot.rbac.groupMapping set), so
# the group-mapping ConfigMap is mounted and the seeded nvcm-* Keycloak users
# exist. In CI the kind-integration workflow applies values-configured.yaml
# after `make kind-up-sec`; locally, apply
# `scripts/rbac-local-test/values-configured.yaml` (helm upgrade --reuse-values)
# before `pytest --rbac`.
# =============================================================================

RBAC_NB_SELECTOR_TMPL = (
    "app.kubernetes.io/name={release}-nautobot,app.kubernetes.io/instance={release}"
)


@pytest.fixture(scope="session")
def rbac_enabled(request: pytest.FixtureRequest) -> bool:
    """True when --rbac is passed."""
    return bool(request.config.getoption("--rbac"))


@pytest.fixture(scope="session")
def rbac_release(request: pytest.FixtureRequest) -> str:
    """Helm release name, used to locate the Nautobot pod/service by label."""
    return str(request.config.getoption("--rbac-release"))


@pytest.fixture(scope="session")
def rbac_nautobot_selector(rbac_release: str) -> str:
    """Label selector matching the Nautobot Deployment/Service/Pod for the release."""
    return RBAC_NB_SELECTOR_TMPL.format(release=rbac_release)


def _rbac_skip_unless_enabled(rbac_enabled: bool) -> None:
    if not rbac_enabled:
        pytest.skip("group-mapping RBAC tests require --rbac")


@pytest.fixture(scope="session")
def rbac_nautobot_pod(
    rbac_enabled: bool,
    config_manager_namespace: str,
    rbac_nautobot_selector: str,
) -> str:
    """Name of a running Nautobot pod for the release.

    Skips the whole RBAC suite when --rbac is off or no Nautobot pod is found.
    """
    _rbac_skip_unless_enabled(rbac_enabled)
    res = _kubectl_run(
        "get",
        "pod",
        "-n",
        config_manager_namespace,
        "-l",
        rbac_nautobot_selector,
        "-o",
        "jsonpath={.items[0].metadata.name}",
    )
    pod = res.stdout.strip()
    if res.returncode != 0 or not pod:
        pytest.skip(
            f"No Nautobot pod matching '{rbac_nautobot_selector}' in namespace "
            f"'{config_manager_namespace}'. Deploy with `make kind-up-sec`."
        )
    print(f"\n[rbac] using nautobot pod: {config_manager_namespace}/{pod}")
    return pod


@pytest.fixture(scope="session")
def rbac_keycloak_url(
    rbac_enabled: bool,
    request: pytest.FixtureRequest,
) -> Generator[str]:
    """Port-forward the local Keycloak Service and yield its base URL.

    Keycloak is not exposed through the app gateway in the local security stack,
    so a kubectl port-forward is the reliable way to reach the token endpoint
    from the test runner.
    """
    _rbac_skip_unless_enabled(rbac_enabled)
    ns = str(request.config.getoption("--rbac-keycloak-namespace"))
    svc = str(request.config.getoption("--rbac-keycloak-service"))
    local_port = 18080
    proc = _start_service_port_forward(ns, svc, local_port, 80)
    if proc is None:
        pytest.fail(
            f"Failed to port-forward Keycloak svc/{svc} in namespace '{ns}'. "
            "Ensure `make kind-up-sec` completed and Keycloak is running."
        )
    try:
        yield f"http://localhost:{local_port}"
    finally:
        proc.terminate()
        proc.wait(timeout=5)
        if proc in _port_forward_processes:
            _port_forward_processes.remove(proc)


def _select_service_port(raw: str) -> tuple[int, str]:
    """Pick the app HTTP(S) port + scheme from kubectl ``name=port ...`` output.

    Prefer a port named ``https``/``http`` over the positional first port: if a
    metrics (or other) port is ever added ahead of the app port, blindly taking
    ``.spec.ports[0]`` would point the port-forward at the wrong target. Falls
    back to the first port when nothing is usefully named, deriving the scheme
    from 443, and to ``80/http`` when the service reports no ports at all.
    """
    pairs: list[tuple[str, int]] = []
    for tok in raw.split():
        name, _, port = tok.partition("=")
        if port.isdigit():
            pairs.append((name, int(port)))
    if not pairs:
        return 80, "http"
    by_name = dict(pairs)
    if "https" in by_name:
        return by_name["https"], "https"
    if "http" in by_name:
        port = by_name["http"]
        return port, ("https" if port == 443 else "http")
    port = pairs[0][1]
    return port, ("https" if port == 443 else "http")


@pytest.fixture(scope="session")
def rbac_nautobot_url(
    rbac_enabled: bool,
    config_manager_namespace: str,
    rbac_release: str,
    rbac_nautobot_selector: str,
) -> Generator[str]:
    """Port-forward the Nautobot Service and yield its base URL.

    We hit Nautobot directly (bypassing the gateway) so a raw JWT Bearer token
    reaches the app's authenticator without the gateway's OIDC redirect getting
    in the way. The scheme is derived from the chosen Service port (443 → https).
    """
    _rbac_skip_unless_enabled(rbac_enabled)
    svc_res = _kubectl_run(
        "get",
        "svc",
        "-n",
        config_manager_namespace,
        "-l",
        rbac_nautobot_selector,
        "-o",
        "jsonpath={.items[0].metadata.name}",
    )
    svc = svc_res.stdout.strip() or f"{rbac_release}-nautobot"
    port_res = _kubectl_run(
        "get",
        "svc",
        "-n",
        config_manager_namespace,
        svc,
        "-o",
        "jsonpath={range .spec.ports[*]}{.name}={.port} {end}",
    )
    remote_port, scheme = _select_service_port(port_res.stdout)
    local_port = 18443
    proc = _start_service_port_forward(config_manager_namespace, svc, local_port, remote_port)
    if proc is None:
        pytest.fail(
            f"Failed to port-forward Nautobot svc/{svc} in namespace '{config_manager_namespace}'."
        )
    try:
        yield f"{scheme}://localhost:{local_port}"
    finally:
        proc.terminate()
        proc.wait(timeout=5)
        if proc in _port_forward_processes:
            _port_forward_processes.remove(proc)


@pytest.fixture(scope="session")
def rbac_get_token(
    rbac_enabled: bool,
    rbac_keycloak_url: str,
    request: pytest.FixtureRequest,
) -> Callable[[str], str]:
    """Return a callable that fetches an access token for a seeded nvcm-* user.

    Uses the OIDC Resource Owner Password grant against the confidential
    `nv-config-manager` client (seeded users have password == username). The
    returned token carries the `roles` claim the authenticator maps to Django
    groups.
    """
    _rbac_skip_unless_enabled(rbac_enabled)
    realm = str(request.config.getoption("--rbac-realm"))
    client_id = str(request.config.getoption("--rbac-client-id"))
    client_secret = str(request.config.getoption("--rbac-client-secret"))
    token_url = f"{rbac_keycloak_url}/realms/{realm}/protocol/openid-connect/token"

    def _get(username: str, password: str | None = None) -> str:
        resp = requests.post(
            token_url,
            data={
                "client_id": client_id,
                "client_secret": client_secret,
                "grant_type": "password",
                "scope": "openid",
                "username": username,
                "password": password if password is not None else username,
            },
            timeout=15,
        )
        if resp.status_code != 200:
            pytest.fail(
                f"Keycloak token grant for {username!r} failed "
                f"({resp.status_code}): {resp.text[:300]}"
            )
        token = resp.json().get("access_token")
        if not token:
            pytest.fail(f"Keycloak returned no access_token for {username!r}: {resp.text[:300]}")
        return str(token)

    return _get


@pytest.fixture(scope="session")
def rbac_api_login(
    rbac_get_token: Callable[[str], str],
    rbac_nautobot_url: str,
) -> Callable[..., int]:
    """Return a callable that logs a user in over the Nautobot REST API.

    Fetches the user's JWT then GETs an authenticated endpoint with it, which is
    what triggers `nv_config_manager_auth.jwt_authentication` + the rbac sync
    (the sync runs inside `authenticate()`, so it fires regardless of the
    subsequent authorization outcome). Returns the HTTP status code so tests can
    assert authorization results.

    The default probe hits a data endpoint (`/api/dcim/devices/`) rather than
    `/api/users/users/`: a mapped user with `view: all` is *intentionally* not
    granted `users.user` (privilege-model exclusion), so listing users 403s by
    design. Pass an explicit `path` to probe a specific endpoint (e.g. to assert
    that exclusion).
    """

    def _login(username: str, path: str = "/api/dcim/devices/") -> int:
        token = rbac_get_token(username)
        resp = requests.get(
            f"{rbac_nautobot_url}{path}",
            headers={"Authorization": f"Bearer {token}"},
            verify=False,
            timeout=30,
        )
        return resp.status_code

    return _login


@pytest.fixture(scope="session")
def rbac_nbshell(
    config_manager_namespace: str,
    rbac_nautobot_pod: str,
) -> Callable[[str], str]:
    """Return a callable that runs a Python snippet in the Nautobot Django shell.

    Uses ``nautobot-server shell --command <script>`` (non-interactive) inside
    the Nautobot pod, giving tests full ORM access to assert Group /
    ObjectPermission state without the interactive REPL echoing prompts or the
    Shell-Plus auto-import banner into stdout. Fails the test on a non-zero exit
    so ORM errors surface loudly.
    """

    def _run(script: str, timeout: int = 60) -> str:
        proc = subprocess.run(
            [
                "kubectl",
                "exec",
                "-i",
                "-n",
                config_manager_namespace,
                rbac_nautobot_pod,
                "--",
                "nautobot-server",
                "shell",
                "--command",
                script,
            ],
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
        if proc.returncode != 0:
            pytest.fail(
                f"nautobot-server shell exec failed (rc={proc.returncode}):\n"
                f"stdout:\n{proc.stdout}\nstderr:\n{proc.stderr}"
            )
        return proc.stdout

    return _run
