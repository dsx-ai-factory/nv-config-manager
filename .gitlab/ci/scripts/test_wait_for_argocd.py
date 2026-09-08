#!/usr/bin/env python3
"""Deterministic tests for the read-only Argo CD rollout observer."""

import io
import os
import subprocess
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from typing import Any

import responses
from wait_for_argocd import (
    Config,
    GateFailure,
    HttpArgoApi,
    TransientApiError,
    execute_gate,
    load_config,
)

EXPECTED_CHART = "0.0.0-pr999.01234567"
EXPECTED_GIT = "0123456789abcdef0123456789abcdef01234567"
OLD_GIT = "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
STALE_REVISIONS = ["0.0.0-pr998.deadbeef", OLD_GIT]
TEST_TOKEN = ".".join(("test-header", "test-payload", "test-signature"))
SCRIPT_DIRECTORY = Path(__file__).resolve().parent
BASE_CONFIG = Config(
    server="https://argocd.example.test",
    application="test-application",
    application_namespace="argocd",
    project="test-project",
    expected_chart_revision=EXPECTED_CHART,
    expected_git_revision=EXPECTED_GIT,
    poll_interval=1,
    rollout_timeout=10,
)


def application_payload(
    *,
    desired_revisions: list[str] | None = None,
    operation_revisions: list[str] | None = None,
    sync: str = "Synced",
    health: str = "Healthy",
    operation: str = "Succeeded",
) -> dict[str, Any]:
    """Build an Application response with independently controllable terminal state."""

    return {
        "spec": {"sources": [{}, {}]},
        "status": {
            "sync": {
                "status": sync,
                "revisions": desired_revisions or [EXPECTED_CHART, EXPECTED_GIT],
            },
            "health": {"status": health},
            "operationState": {
                "phase": operation,
                "operation": {
                    "sync": {"revisions": operation_revisions or [EXPECTED_CHART, EXPECTED_GIT]}
                },
            },
        },
    }


class FakeClock:
    """Monotonic clock advanced only by the injected sleeper."""

    def __init__(self) -> None:
        self.now = 0.0

    def monotonic(self) -> float:
        return self.now

    def sleep(self, duration: float) -> None:
        self.now += duration


class FakeArgoApi:
    """In-memory sequence of Application observations."""

    def __init__(self, *observations: dict[str, Any] | Exception) -> None:
        self.observations = list(observations)
        self.attempts = 0
        self.timeouts: list[tuple[float, float]] = []

    def get_application(self, timeout: tuple[float, float]) -> dict[str, Any]:
        self.attempts += 1
        self.timeouts.append(timeout)
        observation = self.observations[min(self.attempts - 1, len(self.observations) - 1)]
        if isinstance(observation, Exception):
            raise observation
        return observation


def run_gate(
    client: FakeArgoApi,
    config: Config = BASE_CONFIG,
) -> tuple[int, FakeClock, str, str]:
    """Run the observer with deterministic time and captured output."""

    clock = FakeClock()
    output = io.StringIO()
    errors = io.StringIO()
    status = execute_gate(
        config,
        client,
        monotonic=clock.monotonic,
        sleeper=clock.sleep,
        output=output,
        errors=errors,
    )
    return status, clock, output.getvalue(), errors.getvalue()


class RolloutObserverTests(unittest.TestCase):
    """Regression tests for rollout identity, terminal state, and API failures."""

    def test_environment_target_requires_exactly_seven_fields(self) -> None:
        base_environment = os.environ.copy()
        valid = (
            "test|test-branch|test-namespace|test-release|baseline.yaml|state-dir|test-application"
        )
        result = subprocess.run(
            ["bash", str(SCRIPT_DIRECTORY / "test_env_config.sh"), "test"],
            check=True,
            capture_output=True,
            text=True,
            env={**base_environment, "NVCM_TEST_ENV_TARGETS": valid},
        )
        self.assertIn("export NVCM_ENV_ARGOCD_APPLICATION=test-application", result.stdout)

        for target in (valid.rsplit("|", 1)[0], f"{valid}|extra"):
            with self.subTest(target=target):
                result = subprocess.run(
                    ["bash", str(SCRIPT_DIRECTORY / "test_env_config.sh"), "test"],
                    check=False,
                    capture_output=True,
                    text=True,
                    env={**base_environment, "NVCM_TEST_ENV_TARGETS": target},
                )
                self.assertNotEqual(result.returncode, 0)

    def test_configuration_is_required_and_token_is_removed(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            (Path(temporary_directory) / "deploy.env").write_text(
                "\n".join(
                    (
                        "ARGOCD_APPLICATION=test-application",
                        f"ARGOCD_EXPECTED_CHART_REVISION={EXPECTED_CHART}",
                        f"ARGOCD_EXPECTED_GIT_REVISION={EXPECTED_GIT}",
                    )
                ),
                encoding="utf-8",
            )
            environment = {
                "CI_PROJECT_DIR": temporary_directory,
                "NVCM_ARGOCD_SERVER": "https://argocd.example.test",
                "NVCM_ARGOCD_AUTH_TOKEN": TEST_TOKEN,
                "NVCM_ARGOCD_PROJECT": "test-project",
            }
            config, token = load_config(environment)
            self.assertEqual(config.application, "test-application")
            self.assertEqual(token, TEST_TOKEN)
            self.assertNotIn("NVCM_ARGOCD_AUTH_TOKEN", environment)

            for name, value in (
                ("NVCM_ARGOCD_PROJECT", None),
                ("NVCM_ARGOCD_SERVER", "http://argocd.example.test"),
                ("NVCM_ARGOCD_SERVER", "https://argocd.example.test:not-a-port"),
                ("NVCM_ARGOCD_POLL_INTERVAL", "١٠"),
                ("NVCM_ARGOCD_SYNC_TIMEOUT", "1801"),
            ):
                with self.subTest(name=name):
                    invalid = {
                        "CI_PROJECT_DIR": temporary_directory,
                        "NVCM_ARGOCD_SERVER": "https://argocd.example.test",
                        "NVCM_ARGOCD_AUTH_TOKEN": TEST_TOKEN,
                        "NVCM_ARGOCD_PROJECT": "test-project",
                    }
                    if value is None:
                        invalid.pop(name, None)
                    else:
                        invalid[name] = value
                    with self.assertRaises(GateFailure):
                        load_config(invalid)

    def test_exact_terminal_rollout_succeeds(self) -> None:
        result, clock, output, _ = run_gate(FakeArgoApi(application_payload()))
        self.assertEqual(result, 0)
        self.assertEqual(clock.now, 0)
        self.assertIn("Rollout completed successfully", output)

    def test_single_source_application_fails_immediately(self) -> None:
        payload = application_payload()
        payload["spec"] = {"source": {}}
        client = FakeArgoApi(payload)
        result, clock, _, errors = run_gate(client)
        self.assertEqual(result, 1)
        self.assertEqual(client.attempts, 1)
        self.assertEqual(clock.now, 0)
        self.assertIn("requires a multi-source Argo CD Application", errors)

    def test_every_exact_terminal_condition_is_required(self) -> None:
        incomplete = {
            "desired revisions": application_payload(desired_revisions=STALE_REVISIONS),
            "operation revisions": application_payload(operation_revisions=STALE_REVISIONS),
            "sync status": application_payload(sync="OutOfSync"),
            "health status": application_payload(health="Progressing"),
            "operation phase": application_payload(operation="Running"),
        }
        config = replace(BASE_CONFIG, poll_interval=30, rollout_timeout=1)
        for condition, payload in incomplete.items():
            with self.subTest(condition=condition):
                result, clock, _, errors = run_gate(FakeArgoApi(payload), config)
                self.assertEqual(result, 1)
                self.assertEqual(clock.now, 1)
                self.assertIn("timed out waiting for rollout", errors)

    def test_transient_api_failure_is_observed_then_retried(self) -> None:
        client = FakeArgoApi(
            TransientApiError("temporary API failure"),
            application_payload(),
        )
        result, clock, output, _ = run_gate(client)
        self.assertEqual(result, 0)
        self.assertEqual(client.attempts, 2)
        self.assertEqual(clock.now, 1)
        self.assertIn("temporary API failure", output)

    def test_poll_sleep_stops_at_the_rollout_deadline(self) -> None:
        config = replace(BASE_CONFIG, poll_interval=30, rollout_timeout=3)
        result, clock, output, errors = run_gate(
            FakeArgoApi(application_payload(health="Progressing")),
            config,
        )
        self.assertEqual(result, 1)
        self.assertEqual(clock.now, 3)
        self.assertIn("health=Progressing", output)
        self.assertIn("last observed", errors)

    def test_http_client_is_read_only_and_classifies_statuses(self) -> None:
        application_url = "https://argocd.example.test/api/v1/applications/test-application"
        query = responses.matchers.query_param_matcher(
            {"appNamespace": "argocd", "project": "test-project"}
        )
        headers = responses.matchers.header_matcher({"Authorization": f"Bearer {TEST_TOKEN}"})

        with responses.RequestsMock() as mock:
            mock.get(
                application_url,
                match=[query, headers],
                status=200,
                json=application_payload(),
            )
            client = HttpArgoApi(BASE_CONFIG, TEST_TOKEN)
            self.assertEqual(
                client.get_application((1, 1))["status"]["health"]["status"],
                "Healthy",
            )
            request_url = mock.calls[0].request.url
            if request_url is None:
                self.fail("prepared Argo CD request has no URL")
            self.assertNotIn(TEST_TOKEN, request_url)
            self.assertEqual(mock.calls[0].request.method, "GET")

        for status, expected_error in (
            (302, GateFailure),
            (401, GateFailure),
            (403, GateFailure),
            (404, GateFailure),
            (500, TransientApiError),
        ):
            with self.subTest(status=status):
                with responses.RequestsMock() as mock:
                    mock.get(application_url, match=[query], status=status, json={})
                    with self.assertRaises(expected_error):
                        HttpArgoApi(BASE_CONFIG, TEST_TOKEN).get_application((1, 1))
                    self.assertEqual(len(mock.calls), 1)


if __name__ == "__main__":
    unittest.main(verbosity=2)
