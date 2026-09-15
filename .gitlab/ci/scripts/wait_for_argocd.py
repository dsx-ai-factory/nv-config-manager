#!/usr/bin/env python3
"""Wait until Argo CD reports that the promoted revisions rolled out successfully."""

import os
import re
import sys
import time
from collections.abc import Callable, Mapping, MutableMapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol, TextIO
from urllib.parse import quote, urlencode, urlsplit

import requests

FULL_GIT_SHA = re.compile(r"^[0-9a-f]{40}$")
MAX_ROLLOUT_TIMEOUT = 1800


@dataclass(frozen=True)
class Config:
    """Validated rollout-observer configuration."""

    server: str
    application: str
    application_namespace: str
    project: str
    expected_chart_revision: str
    expected_git_revision: str
    poll_interval: int
    rollout_timeout: int


class GateFailure(Exception):
    """A fatal configuration, API, or rollout failure."""


class TransientApiError(Exception):
    """An API or transport failure that may clear while the rollout proceeds."""


class ArgoApi(Protocol):
    """Read-only Argo CD API used by the rollout observer."""

    def get_application(self, timeout: tuple[float, float]) -> dict[str, Any]: ...


class HttpArgoApi:
    """Read one Argo CD Application without exposing the token in argv or URLs."""

    def __init__(
        self,
        config: Config,
        token: str,
        *,
        session: requests.Session | None = None,
    ) -> None:
        query = urlencode(
            {
                "appNamespace": config.application_namespace,
                "project": config.project,
            }
        )
        application = quote(config.application, safe="-.")
        self._url = f"{config.server}/api/v1/applications/{application}?{query}"
        self._session = session or requests.Session()
        self._session.headers.update(
            {
                "Authorization": f"Bearer {token}",
                "User-Agent": "nvcm-rollout-observer",
            }
        )

    def get_application(self, timeout: tuple[float, float]) -> dict[str, Any]:
        try:
            response = self._session.get(
                self._url,
                timeout=timeout,
                allow_redirects=False,
            )
        except requests.RequestException as exc:
            raise TransientApiError(
                f"Argo CD API transport failure: {type(exc).__name__}."
            ) from exc

        status = response.status_code
        if 200 <= status < 300:
            try:
                payload = response.json()
            except ValueError as exc:
                raise TransientApiError("Argo CD API returned malformed JSON.") from exc
            if isinstance(payload, dict):
                return payload
            raise TransientApiError("Argo CD API returned malformed JSON.")
        if status == 401:
            raise GateFailure(
                "Argo CD API returned HTTP 401; the rollout token is invalid or expired."
            )
        if status == 403:
            raise GateFailure("Argo CD API returned HTTP 403; the rollout token lacks get access.")
        if status == 404:
            raise GateFailure(
                "Argo CD API returned HTTP 404; verify the application name, namespace, and project."
            )
        if 300 <= status < 500 and status not in (408, 429):
            raise GateFailure(
                f"Argo CD API returned unexpected HTTP {status}; verify its server configuration."
            )
        raise TransientApiError(f"Argo CD API returned transient HTTP {status}.")


def _required(environment: Mapping[str, str], name: str, message: str) -> str:
    value = environment.get(name, "").strip()
    if not value:
        raise GateFailure(message)
    return value


def _positive_integer(environment: Mapping[str, str], name: str, default: int) -> int:
    raw_value = environment.get(name, str(default))
    if not raw_value.isascii() or not raw_value.isdigit() or int(raw_value) <= 0:
        raise GateFailure(f"{name} must be a positive integer")
    return int(raw_value)


def _read_attestation(project_directory: Path) -> dict[str, str]:
    path = project_directory / "deploy.env"
    if not path.is_file():
        raise GateFailure(f"missing deployment attestation {path}")

    values: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        key, separator, value = line.partition("=")
        if separator and key not in values:
            values[key] = value

    for key in (
        "ARGOCD_APPLICATION",
        "ARGOCD_EXPECTED_CHART_REVISION",
        "ARGOCD_EXPECTED_GIT_REVISION",
    ):
        if not values.get(key):
            raise GateFailure(f"{key} missing from deploy.env")
    return values


def load_config(environment: MutableMapping[str, str]) -> tuple[Config, str]:
    """Load observer settings and remove the token from the process environment."""

    token = environment.pop("NVCM_ARGOCD_AUTH_TOKEN", "").strip()
    if not token:
        raise GateFailure(
            "Set NVCM_ARGOCD_AUTH_TOKEN to a protected, masked, and hidden Argo CD token"
        )

    project_directory = Path(_required(environment, "CI_PROJECT_DIR", "CI_PROJECT_DIR is required"))
    server = _required(
        environment,
        "NVCM_ARGOCD_SERVER",
        "Set NVCM_ARGOCD_SERVER to the Argo CD API base URL",
    ).rstrip("/")
    project = _required(
        environment,
        "NVCM_ARGOCD_PROJECT",
        "Set NVCM_ARGOCD_PROJECT to the Argo CD project containing the Application",
    )
    attestation = _read_attestation(project_directory)

    try:
        parsed_server = urlsplit(server)
        _ = parsed_server.port
    except ValueError as exc:
        raise GateFailure("NVCM_ARGOCD_SERVER must be a valid HTTPS base URL") from exc
    if (
        parsed_server.scheme != "https"
        or not parsed_server.hostname
        or parsed_server.username
        or parsed_server.password
        or parsed_server.query
        or parsed_server.fragment
    ):
        raise GateFailure("NVCM_ARGOCD_SERVER must be a valid HTTPS base URL")

    expected_git_revision = attestation["ARGOCD_EXPECTED_GIT_REVISION"]
    if not FULL_GIT_SHA.fullmatch(expected_git_revision):
        raise GateFailure("expected Git revision is not a full SHA-1")

    poll_interval = _positive_integer(environment, "NVCM_ARGOCD_POLL_INTERVAL", 10)
    rollout_timeout = _positive_integer(environment, "NVCM_ARGOCD_SYNC_TIMEOUT", 1800)
    if rollout_timeout > MAX_ROLLOUT_TIMEOUT:
        raise GateFailure(f"NVCM_ARGOCD_SYNC_TIMEOUT must not exceed {MAX_ROLLOUT_TIMEOUT} seconds")

    return (
        Config(
            server=server,
            application=attestation["ARGOCD_APPLICATION"],
            application_namespace=environment.get("NVCM_ARGOCD_APPLICATION_NAMESPACE", "argocd"),
            project=project,
            expected_chart_revision=attestation["ARGOCD_EXPECTED_CHART_REVISION"],
            expected_git_revision=expected_git_revision,
            poll_interval=poll_interval,
            rollout_timeout=rollout_timeout,
        ),
        token,
    )


def _nested(payload: Mapping[str, Any], *keys: str) -> Any:
    value: Any = payload
    for key in keys:
        if not isinstance(value, Mapping):
            return None
        value = value.get(key)
    return value


def _status(payload: Mapping[str, Any], *keys: str, default: str = "Unknown") -> str:
    value = _nested(payload, *keys)
    return value if isinstance(value, str) and value else default


def _revisions(payload: Mapping[str, Any], *keys: str) -> list[str]:
    value = _nested(payload, *keys)
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        return []
    return value


def _matches_expected(revisions: list[str], config: Config) -> bool:
    return config.expected_chart_revision in revisions and config.expected_git_revision in revisions


def _summary(payload: Mapping[str, Any]) -> str:
    sync = _status(payload, "status", "sync", "status")
    health = _status(payload, "status", "health", "status")
    operation = _status(payload, "status", "operationState", "phase", default="None")
    return f"sync={sync}, health={health}, operation={operation}"


def _rollout_succeeded(payload: Mapping[str, Any], config: Config) -> bool:
    spec = _nested(payload, "spec")
    if isinstance(spec, Mapping) and "source" in spec and "sources" not in spec:
        raise GateFailure("the rollout observer requires a multi-source Argo CD Application")

    desired_revisions = _revisions(payload, "status", "sync", "revisions")
    operation_revisions = _revisions(
        payload,
        "status",
        "operationState",
        "operation",
        "sync",
        "revisions",
    )
    return (
        _matches_expected(desired_revisions, config)
        and _matches_expected(operation_revisions, config)
        and _status(payload, "status", "sync", "status") == "Synced"
        and _status(payload, "status", "health", "status") == "Healthy"
        and _status(payload, "status", "operationState", "phase") == "Succeeded"
    )


def wait_for_rollout(
    config: Config,
    client: ArgoApi,
    *,
    monotonic: Callable[[], float] = time.monotonic,
    sleeper: Callable[[float], None] = time.sleep,
    output: TextIO = sys.stdout,
) -> None:
    """Observe one Application until the exact promoted revisions are healthy."""

    deadline = monotonic() + config.rollout_timeout
    last_observation = "no Application state received"
    print(
        f"Waiting for rollout of {config.application_namespace}/{config.application}...",
        file=output,
    )

    while (remaining := deadline - monotonic()) > 0:
        phase_timeout = max(0.001, min(30.0, remaining / 2))
        try:
            payload = client.get_application((phase_timeout, phase_timeout))
        except TransientApiError as exc:
            observation = str(exc)
        else:
            observation = _summary(payload)
            if _rollout_succeeded(payload, config):
                print("Rollout completed successfully at the promoted revisions.", file=output)
                return

        if observation != last_observation:
            print(f"  {observation}", file=output)
            last_observation = observation

        remaining = deadline - monotonic()
        if remaining <= 0:
            break
        sleeper(min(config.poll_interval, remaining))

    raise GateFailure(
        f"timed out waiting for rollout of {config.application}; last observed {last_observation}"
    )


def execute_gate(
    config: Config,
    client: ArgoApi,
    *,
    monotonic: Callable[[], float] = time.monotonic,
    sleeper: Callable[[float], None] = time.sleep,
    output: TextIO = sys.stdout,
    errors: TextIO = sys.stderr,
) -> int:
    """Run the rollout observer and render a concise failure."""

    try:
        wait_for_rollout(
            config,
            client,
            monotonic=monotonic,
            sleeper=sleeper,
            output=output,
        )
    except GateFailure as exc:
        print(f"ERROR: {exc}", file=errors)
        return 1
    return 0


def main() -> int:
    """CLI entry point."""

    try:
        config, token = load_config(os.environ)
    except GateFailure as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    return execute_gate(config, HttpArgoApi(config, token))


if __name__ == "__main__":
    raise SystemExit(main())
