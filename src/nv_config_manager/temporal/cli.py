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
"""
NVIDIA Config Manager Temporal CLI - Generic workflow client with dynamic command generation.

This CLI tool automatically discovers available workflows and generates
corresponding commands with proper help documentation and parameter validation.

Authentication is handled via OIDC PKCE (browser-based), using the shared
OIDCAuth class from nv_config_manager.common.oidc.
"""

import json
import re
import sys
from typing import Any, NoReturn, get_args, get_type_hints

import click
import requests
import urllib3
from pydantic import BaseModel

from nv_config_manager.common.oidc import AuthDiscovery, OIDCAuth, decode_jwt_claims

# Keep workflow imports guarded so a packaging/import issue produces a CLI-friendly error.
try:
    from nv_config_manager.temporal.common.mixins.metadata import WorkflowMetadataMixin
    from nv_config_manager.temporal.hello_world.workflows import (
        REGISTERED_WORKFLOWS as HELLO_WORLD_WORKFLOWS,
    )
    from nv_config_manager.temporal.ngc.workflows import REGISTERED_WORKFLOWS as NGC_WORKFLOWS
except ImportError as e:
    click.echo(f"Error importing workflows: {e}", err=True)
    click.echo("Make sure the nv-config-manager-temporal package is properly installed.", err=True)
    sys.exit(1)


class WorkflowInfo:
    """Container for workflow metadata."""

    def __init__(
        self,
        workflow_class: type,
        input_class: type[BaseModel],
        endpoint: str,
        namespace: str = "",
    ) -> None:
        self.workflow_class = workflow_class
        self.input_class = input_class
        self.endpoint = endpoint
        self.namespace = namespace
        self.name = workflow_class.__name__
        self.description = self._extract_description()
        self.parameters = self._extract_parameters()

    def _extract_description(self) -> str:
        """Extract description from workflow class."""
        # Try to use the metadata mixin first
        if issubclass(self.workflow_class, WorkflowMetadataMixin):
            return self.workflow_class.get_workflow_description()

        # Fallback to docstring
        doc = self.workflow_class.__doc__
        if doc:
            # Get the first line of the docstring
            return doc.strip().split("\n")[0].replace('"""', "").strip()
        return f"Execute {self.name} workflow"

    def _extract_parameters(self) -> dict[str, dict[str, Any]]:
        """Extract parameter information from input model."""
        parameters: dict[str, dict[str, Any]] = {}

        try:
            # Try Pydantic v2 first
            if hasattr(self.input_class, "model_fields"):
                fields = self.input_class.model_fields
                for field_name, field_info in fields.items():
                    required = (
                        field_info.is_required() if hasattr(field_info, "is_required") else True
                    )
                    param_info = {
                        "type": field_info.annotation if hasattr(field_info, "annotation") else str,
                        "required": required,
                        "default": field_info.default
                        if not required and hasattr(field_info, "default")
                        else None,
                        "description": field_info.description
                        if hasattr(field_info, "description")
                        else None,
                        "nullable": _allows_none(field_info.annotation),
                    }
                    parameters[field_name] = param_info
            # Fallback to type hints
            else:
                type_hints = get_type_hints(self.input_class)
                for field_name, field_type in type_hints.items():
                    if field_name.startswith("_"):
                        continue
                    param_info = {
                        "type": field_type,
                        "required": True,  # Default assumption
                        "default": None,
                        "description": None,
                        "nullable": _allows_none(field_type),
                    }
                    parameters[field_name] = param_info
        except Exception:
            # Final fallback - inspect the class annotations
            try:
                annotations = getattr(self.input_class, "__annotations__", {})
                for field_name, field_type in annotations.items():
                    if field_name.startswith("_"):
                        continue
                    param_info = {
                        "type": field_type,
                        "required": True,
                        "default": None,
                        "description": None,
                        "nullable": _allows_none(field_type),
                    }
                    parameters[field_name] = param_info
            except Exception as ex:
                click.echo(
                    f"Warning: Could not extract parameters for {self.input_class.__name__}: {ex}",
                    err=True,
                )

        return parameters


def _allows_none(annotation: Any) -> bool:
    """Return whether a workflow input annotation accepts ``None``."""
    if annotation is None or annotation is type(None):
        return True
    return type(None) in get_args(annotation)


def _debug_dump_jwt(access_token: str, context: str = "") -> None:
    """Dump decoded JWT claims to stderr for debugging."""
    click.echo(f"\n[DEBUG] JWT for {context}:", err=True)
    claims = decode_jwt_claims(access_token)
    if claims is None:
        parts = access_token.split(".")
        click.echo(f"  Parts: {len(parts)} (expected 3 for plain JWT)", err=True)
        click.echo(f"  Raw (first 80 chars): {access_token[:80]}...", err=True)
        return
    click.echo(f"  iss: {claims.get('iss')}", err=True)
    click.echo(f"  aud: {claims.get('aud')}", err=True)
    click.echo(f"  sub: {claims.get('sub')}", err=True)
    click.echo(f"  exp: {claims.get('exp')}", err=True)
    click.echo(f"  Token length: {len(access_token)} chars", err=True)


class WorkflowDiscovery:
    """Discovers and organizes available workflows."""

    def __init__(self) -> None:
        self.workflows: dict[str, WorkflowInfo] = {}
        self._discover_workflows()

    def _discover_workflows(self) -> None:
        """Discover all available workflows and their metadata."""
        # Process core workflows shared by all DCIM providers.
        for workflow_class in NGC_WORKFLOWS:
            self._process_workflow(workflow_class, "ngc")

        # Process Hello World workflows.
        for workflow_class in HELLO_WORLD_WORKFLOWS:
            self._process_workflow(workflow_class, "hello_world")

    def _process_workflow(self, workflow_class: type, default_namespace: str) -> None:
        """Process a single workflow class."""
        workflow_name = workflow_class.__name__

        # All workflows must use WorkflowMetadataMixin
        if not issubclass(workflow_class, WorkflowMetadataMixin):
            click.echo(
                f"Error: {workflow_name} does not use WorkflowMetadataMixin - all workflows must use metadata",
                err=True,
            )
            return

        # Use metadata from the mixin
        if not workflow_class.has_complete_metadata():
            click.echo(f"Error: {workflow_name} has incomplete metadata - skipping", err=True)
            return

        endpoint = workflow_class.get_workflow_api_endpoint()
        input_class = workflow_class.get_workflow_input_class()
        namespace = workflow_class.get_workflow_namespace() or default_namespace
        cli_name = workflow_class.get_workflow_cli_name()

        # Ensure we have valid values before creating WorkflowInfo
        if not input_class or not endpoint:
            click.echo(f"Error: {workflow_name} has invalid metadata - skipping", err=True)
            return

        workflow_info = WorkflowInfo(workflow_class, input_class, endpoint, namespace)
        self.workflows[cli_name] = workflow_info

    @staticmethod
    def _camel_to_kebab(name: str) -> str:
        """Convert CamelCase to kebab-case."""
        # Insert hyphens before uppercase letters (except the first one)
        s1 = re.sub("(.)([A-Z][a-z]+)", r"\1-\2", name)
        # Insert hyphens before uppercase letters that follow lowercase letters or numbers
        return re.sub("([a-z0-9])([A-Z])", r"\1-\2", s1).lower()


class WorkflowClient:
    """Client for invoking workflows via the gateway.

    When auth is provided, uses svc-* JWT-only hostnames with Bearer tokens.
    When auth is None (SSO not enabled), uses the regular workflow.* hostname
    without authentication headers.
    """

    def __init__(
        self,
        base_hostname: str,
        auth: OIDCAuth | None = None,
        insecure: bool = False,
        base_url: str | None = None,
    ) -> None:
        self.base_hostname = base_hostname
        self.auth = auth
        self.verify = not insecure

        if base_url:
            self.base_url = base_url.rstrip("/")
        elif auth:
            self.base_url = f"https://svc-workflow.{base_hostname}/v1/workflow"
        else:
            self.base_url = f"https://workflow.{base_hostname}/v1/workflow"
        self.api_root_url = self._derive_api_root_url(self.base_url)

        if insecure:
            urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

    @staticmethod
    def _derive_api_root_url(workflow_base_url: str) -> str:
        """Return the /v1 API root from a /v1/workflow URL."""
        suffix = "/workflow"
        if workflow_base_url.endswith(suffix):
            return workflow_base_url[: -len(suffix)]
        return workflow_base_url.rstrip("/")

    def _prepare_auth_headers(
        self,
        verbose: bool,
        force_auth_refresh: bool,
    ) -> dict[str, str]:
        """Build request headers, adding Bearer token when auth is configured."""
        headers: dict[str, str] = {}
        if self.auth:
            access_token = self.auth.get_access_token(force_refresh=force_auth_refresh)
            headers["Authorization"] = f"Bearer {access_token}"
            if verbose:
                click.echo("Using OIDC PKCE authentication")
                _debug_dump_jwt(access_token, "workflow request")
        elif verbose:
            click.echo("No SSO — sending request without authentication")
        return headers

    def _report_workflow_result(
        self,
        result: dict[str, Any],
        verbose: bool,
    ) -> None:
        """Print workflow result summary and optional verbose details."""
        workflow_id = result.get("id")
        if workflow_id:
            ui_url = f"https://{self.base_hostname}/workflows/{workflow_id}"
            click.echo("Workflow started successfully!")
            click.echo(f"View progress: {ui_url}")
            if verbose:
                click.echo(f"Workflow ID: {workflow_id}")
        else:
            click.echo("Workflow invoked successfully!")

        if verbose:
            click.echo(f"Response: {json.dumps(result, indent=2)}")

    @staticmethod
    def _handle_invoke_error(e: requests.exceptions.RequestException) -> NoReturn:
        """Log error details and raise a ClickException."""
        click.echo(f"Failed to invoke workflow: {e}", err=True)
        if hasattr(e, "response") and e.response is not None:
            try:
                error_detail = e.response.json()
                click.echo(f"Error details: {json.dumps(error_detail, indent=2)}", err=True)
            except Exception:
                click.echo(f"Response text: {e.response.text}", err=True)
        raise click.ClickException("Workflow invocation failed") from e

    def invoke_workflow(
        self,
        workflow_info: WorkflowInfo,
        parameters: dict[str, Any],
        verbose: bool = False,
        force_auth_refresh: bool = False,
    ) -> dict[str, Any]:
        """Invoke a workflow with the given parameters."""
        url = f"{self.base_url}{workflow_info.endpoint}"

        if verbose:
            click.echo(f"Invoking {workflow_info.name} workflow...")
            click.echo(f"URL: {url}")
            click.echo(f"Parameters: {json.dumps(parameters, indent=2)}")

        headers = self._prepare_auth_headers(verbose, force_auth_refresh)

        try:
            response = requests.post(
                url,
                json=parameters,
                headers=headers,
                timeout=30,
                allow_redirects=False,
                verify=self.verify,
            )

            if response.status_code in (301, 302, 303, 307, 308):
                raise click.ClickException(
                    "Gateway rejected JWT (redirect to login). "
                    "Check gateway JWT config (issuer, jwksUri, audiences)."
                )

            response.raise_for_status()
            result: dict[str, Any] = response.json()
            self._report_workflow_result(result, verbose)
            return result
        except requests.exceptions.RequestException as e:
            self._handle_invoke_error(e)

    def get_device_id_by_name(
        self, device_name: str, force_auth_refresh: bool = False, verbose: bool = False
    ) -> str:
        """Convert device name to device ID using the API.

        Args:
            device_name: The hostname/name of the device.
            force_auth_refresh: Force token refresh.

        Returns:
            The device ID (UUID).

        Raises:
            click.ClickException: If conversion fails.
        """
        param_url = f"{self.api_root_url}/parameter/device-id"

        headers: dict[str, str] = {}
        if self.auth:
            access_token = self.auth.get_access_token(force_refresh=force_auth_refresh)
            headers["Authorization"] = f"Bearer {access_token}"
            if verbose:
                _debug_dump_jwt(access_token, "device-id request")

        try:
            response = requests.get(
                param_url,
                params={"device_name": device_name},
                headers=headers,
                timeout=30,
                allow_redirects=False,
                verify=self.verify,
            )

            # If we got a redirect, gateway rejected our token
            if response.status_code in (301, 302, 303, 307, 308):
                location = response.headers.get("Location", "")
                click.echo("\nGateway rejected JWT (redirect to login):", err=True)
                click.echo(f"  Status: {response.status_code}", err=True)
                click.echo(f"  Location: {location[:120]}...", err=True)
                raise click.ClickException(
                    "Gateway is redirecting to login — JWT not accepted. "
                    "Check gateway JWT config (issuer, jwksUri, audiences) and ensure you are using the svc-* hostname."
                )

            response.raise_for_status()

            try:
                result = response.json()
            except ValueError as json_err:
                click.echo("\nDevice lookup returned invalid JSON:", err=True)
                click.echo(f"  URL: {param_url}", err=True)
                click.echo(f"  Device name: {device_name}", err=True)
                click.echo(f"  Status code: {response.status_code}", err=True)
                click.echo(f"  Content-Type: {response.headers.get('Content-Type')}", err=True)
                click.echo("  Response body (first 500 chars):", err=True)
                click.echo(f"    {response.text[:500]}", err=True)
                raise click.ClickException(
                    "API returned non-JSON (login page). The gateway is not accepting the CLI JWT. "
                    "Check gateway JWT validation: issuer must match token 'iss' exactly, and for "
                    "Azure AD set oidc.jwksUri to https://login.microsoftonline.com/<tenant>/discovery/v2.0/keys"
                ) from json_err

            return str(result["id"])
        except requests.exceptions.RequestException as e:
            if hasattr(e, "response") and e.response is not None:
                click.echo("\nDevice lookup failed:", err=True)
                click.echo(f"  URL: {param_url}", err=True)
                click.echo(f"  Device name: {device_name}", err=True)
                click.echo(f"  Status code: {e.response.status_code}", err=True)
                click.echo(f"  Response headers: {dict(e.response.headers)}", err=True)
                click.echo("  Response body (first 500 chars):", err=True)
                click.echo(f"    {e.response.text[:500]}", err=True)
                try:
                    error_detail = e.response.json()
                    raise click.ClickException(f"API Error: {error_detail.get('detail', str(e))}")
                except ValueError:
                    raise click.ClickException(
                        f"Non-JSON response from API (status {e.response.status_code}). "
                        f"Check if authentication is working correctly."
                    ) from e
            raise click.ClickException(f"Failed to convert device name: {e}") from e


# ── Helpers for resolving base hostname and building OIDCAuth ────────────


def _resolve_base_hostname(
    hostname: str | None,
    environment: str | None,
    domain: str,
) -> str:
    """Resolve base hostname from --hostname or --environment/--domain."""
    if hostname:
        return hostname
    if environment:
        return f"{environment}.{domain}"
    raise click.ClickException("Either --hostname or --environment is required.")


def _default_workflow_url(base_hostname: str, auth_required: bool) -> str:
    """Return the default workflow API URL for an auth mode."""
    if not base_hostname:
        return ""
    prefix = "svc-workflow" if auth_required else "workflow"
    return f"https://{prefix}.{base_hostname}/v1/workflow"


def _auth_discovery_url(base_hostname: str) -> str:
    """Return the base-host auth discovery URL."""
    return f"https://{base_hostname}/auth/discovery"


def _workflow_url_from_discovery(
    discovery: AuthDiscovery | None,
    base_hostname: str,
    auth_required: bool,
) -> str:
    """Resolve workflow URL from discovery metadata or defaults."""
    if discovery:
        workflow_url = discovery.services.get("workflow")
        if workflow_url:
            return workflow_url.rstrip("/")
    return _default_workflow_url(base_hostname, auth_required)


def _build_auth(
    base_hostname: str,
    issuer: str | None,
    client_id: str | None,
    insecure: bool,
) -> tuple[OIDCAuth | None, str]:
    """Build an OIDCAuth instance, auto-discovering if needed.

    Returns an auth helper and the workflow API URL. The auth helper is None
    when SSO is not enabled on the target environment.
    """
    if issuer and client_id:
        # Explicit config — always use SSO
        return (
            OIDCAuth(issuer_url=issuer, client_id=client_id, verify=not insecure),
            _default_workflow_url(base_hostname, auth_required=True),
        )

    # Auto-discover from the public base-host metadata endpoint first.
    discovery_url = _auth_discovery_url(base_hostname)
    click.echo(f"Auto-discovering OIDC configuration from {base_hostname}...")
    try:
        auth_discovery = OIDCAuth.discover_auth_config(discovery_url, verify=not insecure)
    except RuntimeError as e:
        raise click.ClickException(
            f"OIDC auto-discovery failed: {e}\nPlease provide --issuer and --client-id manually."
        ) from e

    if auth_discovery is not None:
        if not auth_discovery.auth_required:
            click.echo("  SSO is not enabled — skipping authentication.")
            return None, _workflow_url_from_discovery(
                auth_discovery,
                base_hostname,
                auth_required=False,
            )

        discovered_issuer = auth_discovery.issuer_url
        discovered_client_id = auth_discovery.client_id
        issuer = issuer or discovered_issuer
        client_id = client_id or discovered_client_id
        if not issuer or not client_id:
            raise click.ClickException("Auth discovery did not include complete OIDC settings.")
        click.echo(f"  Discovered issuer: {issuer}")
        click.echo(f"  Discovered client ID: {client_id}")
        return (
            OIDCAuth(
                issuer_url=issuer,
                client_id=client_id,
                scopes=list(auth_discovery.scopes) or None,
                verify=not insecure,
            ),
            _workflow_url_from_discovery(
                auth_discovery,
                base_hostname,
                auth_required=True,
            ),
        )

    # Fallback for older deployments that predate /auth/discovery.
    gateway_url = f"https://workflow.{base_hostname}/v1/workflow"
    try:
        result = OIDCAuth.discover_oidc_config(gateway_url, verify=not insecure)
    except RuntimeError as e:
        raise click.ClickException(
            f"OIDC auto-discovery failed: {e}\nPlease provide --issuer and --client-id manually."
        ) from e

    if result is None:
        click.echo("  SSO is not enabled — skipping authentication.")
        return None, _default_workflow_url(base_hostname, auth_required=False)

    discovered_issuer, discovered_client_id = result
    issuer = issuer or discovered_issuer
    client_id = client_id or discovered_client_id
    click.echo(f"  Discovered issuer: {issuer}")
    click.echo(f"  Discovered client ID: {client_id}")

    return (
        OIDCAuth(issuer_url=issuer, client_id=client_id, verify=not insecure),
        _default_workflow_url(base_hostname, auth_required=True),
    )


# ── Global workflow discovery ────────────────────────────────────────────

discovery = WorkflowDiscovery()


# ── Dynamic workflow command factory ─────────────────────────────────────


def create_workflow_command(workflow_name: str, workflow_info: WorkflowInfo) -> click.Command:
    """Create a Click command for a workflow."""

    def workflow_command(**kwargs: Any) -> None:
        """Execute the workflow with provided parameters."""
        hostname = kwargs.pop("hostname", None)
        environment = kwargs.pop("environment", None)
        domain = kwargs.pop("domain", "config-manager.example.com")
        verbose = kwargs.pop("verbose", False)
        issuer = kwargs.pop("issuer", None)
        client_id = kwargs.pop("client_id", None)
        force_auth_refresh = kwargs.pop("force_auth_refresh", False)
        insecure = kwargs.pop("insecure", False)

        base_hostname = _resolve_base_hostname(hostname, environment, domain)
        auth, workflow_api_url = _build_auth(base_hostname, issuer, client_id, insecure)

        # Handle device name to device ID conversion
        device_name = kwargs.pop("device_name", None)
        device_id = kwargs.get("device_id")

        if "device_id" in workflow_info.parameters:
            # Handle Pydantic undefined values
            if device_id == "PydanticUndefined":
                device_id = None
                kwargs["device_id"] = None

            # Validate that either device_id or device_name is provided, but not both
            if device_name and device_id:
                raise click.ClickException(
                    "Cannot specify both --device-id and --device-name. Please use only one."
                )

            if not device_name and not device_id:
                raise click.ClickException("Either --device-id or --device-name must be provided.")

            # Convert device name to device ID if needed
            if device_name:
                if verbose:
                    click.echo(f"Converting device name '{device_name}' to device ID...")
                try:
                    client = WorkflowClient(
                        base_hostname,
                        auth=auth,
                        insecure=insecure,
                        base_url=workflow_api_url,
                    )
                    device_id = client.get_device_id_by_name(
                        device_name, force_auth_refresh=force_auth_refresh, verbose=verbose
                    )
                    kwargs["device_id"] = device_id
                    if verbose:
                        click.echo(f"Found device ID: {device_id}")
                except Exception as e:
                    raise click.ClickException(f"Failed to convert device name to ID: {e}") from e

        # Preserve nulls for nullable workflow fields. A missing required-but-nullable
        # field is not equivalent to an explicit JSON null and produces a 422.
        parameters: dict[str, Any] = {}
        for key, value in kwargs.items():
            param_info = workflow_info.parameters.get(key, {})
            if value is None:
                if param_info.get("nullable", False):
                    parameters[key] = None
                continue

            # Handle list parameters (comma-separated strings)
            param_type = param_info.get("type", str)

            if hasattr(param_type, "__origin__") and param_type.__origin__ is list:
                # Convert comma-separated string to list
                if isinstance(value, str):
                    parameters[key] = [item.strip() for item in value.split(",") if item.strip()]
                else:
                    parameters[key] = value
            else:
                parameters[key] = value

        # Create client and invoke workflow
        client = WorkflowClient(
            base_hostname,
            auth=auth,
            insecure=insecure,
            base_url=workflow_api_url,
        )
        client.invoke_workflow(
            workflow_info, parameters, verbose=verbose, force_auth_refresh=force_auth_refresh
        )

    # ── Common options ────────────────────────────────────────────────
    options = []

    options.append(
        click.option(
            "--hostname",
            "-H",
            default=None,
            help="Base hostname (e.g., config-manager.local, qa.config-manager.example.com). Takes precedence over --environment/--domain.",
            envvar="NV_CONFIG_MANAGER_HOSTNAME",
        )
    )
    options.append(
        click.option(
            "--environment",
            "-e",
            default=None,
            help="Environment name (e.g., qa, sitea). Combined with --domain to form <env>.<domain>.",
        )
    )
    options.append(
        click.option(
            "--domain",
            "-d",
            default="config-manager.example.com",
            help="API domain (default: config-manager.example.com). Used with --environment.",
        )
    )
    options.append(
        click.option(
            "--verbose",
            "-v",
            is_flag=True,
            help="Show detailed output including API calls and parameters",
        )
    )

    # ── Authentication options ────────────────────────────────────────
    options.append(
        click.option(
            "--issuer",
            help="OIDC issuer URL (optional, auto-discovered from gateway if not provided)",
            envvar="NV_CONFIG_MANAGER_OIDC_ISSUER",
        )
    )
    options.append(
        click.option(
            "--client-id",
            help="OIDC client ID (optional, auto-discovered from gateway if not provided)",
            envvar="NV_CONFIG_MANAGER_OIDC_CLIENT_ID",
        )
    )
    options.append(
        click.option(
            "--force-auth-refresh",
            is_flag=True,
            help="Force re-authentication even if a cached token exists",
        )
    )
    options.append(
        click.option(
            "--insecure",
            "-k",
            is_flag=True,
            help="Disable TLS certificate verification (for local dev with self-signed certs)",
        )
    )

    # ── Device-name shortcut ──────────────────────────────────────────
    if "device_id" in workflow_info.parameters:
        options.append(
            click.option(
                "--device-name",
                help="Device hostname (alternative to --device-id, will be converted to device ID automatically)",
            )
        )

    # ── Workflow-specific parameters ──────────────────────────────────
    for param_name, param_info in workflow_info.parameters.items():
        option_name = f"--{param_name.replace('_', '-')}"

        param_type = param_info.get("type", str)
        required = param_info.get("required", True)
        default = param_info.get("default")
        description = param_info.get("description") or f"{param_name} parameter"

        click_type: type = param_type

        if hasattr(param_type, "__origin__") and param_type.__origin__ is list:
            click_type = str
            description += " (comma-separated list)"
        elif str(param_type).startswith("typing.Union") or "|" in str(param_type):
            click_type = str
            if "None" in str(param_type):
                required = False
        elif not hasattr(param_type, "__name__"):
            click_type = str

        # Skip auto-populated fields
        if param_name in ["user", "user_domain", "trigger", "workflow_id"]:
            required = False

        # Make device_id optional if device_name option is available
        if param_name == "device_id" and "device_id" in workflow_info.parameters:
            required = False
            description += " (optional if --device-name is provided)"

        option = click.option(
            option_name,
            type=click_type,
            required=required,
            default=default,
            help=description,
        )
        options.append(option)

    # Apply options to the command function
    for option in reversed(options):
        workflow_command = option(workflow_command)

    # Create the command
    command = click.command(name=workflow_name, help=workflow_info.description)(workflow_command)

    return command


# ── CLI group ────────────────────────────────────────────────────────────


@click.group()
@click.version_option(version="1.0.0")
def cli() -> None:
    """
    NVIDIA Config Manager Temporal CLI - Generic workflow client.

    This tool automatically discovers available workflows and provides
    commands to invoke them with proper parameter validation and help.

    Authentication is auto-detected: if the gateway redirects to an OIDC
    provider, the CLI uses PKCE (browser-based) auth via svc-* hostnames.
    If no redirect is detected, requests are sent without authentication.
    """
    pass


# ── login ────────────────────────────────────────────────────────────────


@cli.command()
@click.option(
    "--hostname",
    "-H",
    default=None,
    help="Base hostname (e.g., config-manager.local, qa.config-manager.example.com). Takes precedence over --environment/--domain.",
    envvar="NV_CONFIG_MANAGER_HOSTNAME",
)
@click.option(
    "--environment",
    "-e",
    default=None,
    help="Environment name (e.g., qa, sitea). Combined with --domain to form <env>.<domain>.",
)
@click.option(
    "--domain",
    "-d",
    default="config-manager.example.com",
    help="API domain (default: config-manager.example.com). Used with --environment.",
)
@click.option(
    "--issuer",
    help="OIDC issuer URL (optional, auto-discovered from gateway if not provided)",
    envvar="NV_CONFIG_MANAGER_OIDC_ISSUER",
)
@click.option(
    "--client-id",
    help="OIDC client ID (optional, auto-discovered from gateway if not provided)",
    envvar="NV_CONFIG_MANAGER_OIDC_CLIENT_ID",
)
@click.option("--force", is_flag=True, help="Force re-authentication even if token is cached")
@click.option(
    "--insecure",
    "-k",
    is_flag=True,
    help="Disable TLS certificate verification (for local dev with self-signed certs)",
)
def login(
    hostname: str | None,
    environment: str | None,
    domain: str,
    issuer: str | None,
    client_id: str | None,
    force: bool,
    insecure: bool,
) -> None:
    """Authenticate using OIDC PKCE flow (opens browser).

    If --issuer and --client-id are not provided, the CLI will auto-discover
    them from the gateway using --hostname or --environment.
    """
    if insecure:
        urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

    # Allow fully manual config (no hostname needed if issuer+client_id given)
    if issuer and client_id:
        base_hostname = hostname or (f"{environment}.{domain}" if environment else None)
    else:
        base_hostname = _resolve_base_hostname(hostname, environment, domain)

    auth, _workflow_api_url = _build_auth(base_hostname or "", issuer, client_id, insecure)

    if auth is None:
        click.echo("SSO is not enabled on this environment — no login required.")
        return

    try:
        auth.get_access_token(force_refresh=force)
        click.echo("Authentication successful!")
    except Exception as e:
        raise click.ClickException(f"Authentication failed: {e}") from e


# ── logout ───────────────────────────────────────────────────────────────


@cli.command()
def logout() -> None:
    """Clear cached authentication token."""
    auth = OIDCAuth(issuer_url="", client_id="")
    auth.clear_token()
    click.echo("Cached token cleared.")


# ── auth-status ──────────────────────────────────────────────────────────


@cli.command()
def auth_status() -> None:
    """Check authentication status."""
    auth = OIDCAuth(issuer_url="", client_id="")
    status = auth.token_status()

    if not status["authenticated"]:
        click.echo("Not authenticated (no valid cached token)")
        return

    remaining = status["expires_in_seconds"] or 0
    hours, remainder = divmod(int(remaining), 3600)
    minutes, seconds = divmod(remainder, 60)
    click.echo("Authenticated")
    click.echo(f"Token expires in: {hours}h {minutes}m {seconds}s")


# ── list-workflows ───────────────────────────────────────────────────────


@cli.command()
def list_workflows() -> None:
    """List all available workflows."""
    click.echo("Available workflows:")
    click.echo("=" * 50)

    for workflow_name, workflow_info in sorted(discovery.workflows.items()):
        click.echo(f"\n{workflow_name}")
        click.echo(f"  Description: {workflow_info.description}")
        click.echo(f"  Namespace: {workflow_info.namespace}")
        click.echo(f"  Endpoint: {workflow_info.endpoint}")

        if workflow_info.parameters:
            click.echo("  Parameters:")
            for param_name, param_info in workflow_info.parameters.items():
                required_str = "required" if param_info.get("required", True) else "optional"
                param_type = param_info.get("type", str)

                try:
                    if hasattr(param_type, "__name__"):
                        type_name = param_type.__name__
                    else:
                        type_name = str(param_type)
                except Exception:
                    type_name = "str"

                click.echo(f"    --{param_name.replace('_', '-')} ({type_name}, {required_str})")


# ── examples ─────────────────────────────────────────────────────────────


@cli.command()
def examples() -> None:
    """Show usage examples for common workflows."""
    click.echo("NVIDIA Config Manager CLI Usage Examples:")
    click.echo("=" * 50)

    examples_text = """
# ============================================================================
# AUTHENTICATION
# ============================================================================

# Authenticate with auto-discovery (simplest - no config needed!)
workflow-cli login -H config-manager.local -k
workflow-cli login -e qa

# Authenticate with explicit OIDC config
workflow-cli login --issuer "https://login.microsoftonline.com/<tenant>/v2.0" \\
                   --client-id "<your-client-id>"

# Check authentication status
workflow-cli auth-status

# Logout (clear cached token)
workflow-cli logout

# ============================================================================
# BASIC USAGE
# ============================================================================

# List all available workflows
workflow-cli list-workflows

# Get help for any specific workflow
workflow-cli backup --help
workflow-cli deploy --help

# ============================================================================
# WORKFLOW EXAMPLES
# ============================================================================

# Run backup using device hostname (simplest)
workflow-cli backup -H config-manager.local --device-name switch001.example.com -k

# Run backup using environment/domain
workflow-cli backup -e qa --device-name switch001.example.com

# Run backup using device ID
workflow-cli backup -e qa --device-id 12345678-1234-1234-1234-123456789abc

# Run hello-world workflow
workflow-cli hello-world -e qa --name "World"

# Run deploy workflow
workflow-cli deploy -e qa \\
  --device-name switch001.example.com \\
  --intended-config-commit-id abc123

# Show detailed output
workflow-cli backup -e qa --device-name switch001.example.com --verbose

# Force token refresh before running
workflow-cli backup -e qa --device-name switch001 --force-auth-refresh

# ============================================================================
# ENVIRONMENT VARIABLES
# ============================================================================

# Set hostname once instead of passing every time
export NV_CONFIG_MANAGER_HOSTNAME="qa.config-manager.example.com"
workflow-cli backup --device-name switch001

# Or set OIDC config if auto-discovery is unavailable
export NV_CONFIG_MANAGER_OIDC_ISSUER="https://login.microsoftonline.com/<tenant>/v2.0"
export NV_CONFIG_MANAGER_OIDC_CLIENT_ID="<your-client-id>"
workflow-cli backup -e qa --device-name switch001
    """
    click.echo(examples_text)


# ── Register dynamic workflow commands ───────────────────────────────────

for workflow_name, workflow_info in discovery.workflows.items():
    command = create_workflow_command(workflow_name, workflow_info)
    cli.add_command(command)


def main() -> None:
    """Main entry point for the CLI."""
    cli()


if __name__ == "__main__":
    main()
