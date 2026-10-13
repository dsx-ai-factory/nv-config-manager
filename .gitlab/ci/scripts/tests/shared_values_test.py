#!/usr/bin/env python3
"""Exercise optional shared overlays with Helm and temporary local Git remotes."""

import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from ruamel.yaml import YAML

ROOT = Path(__file__).resolve().parents[4]
YAML_READER = YAML(typ="safe")
JOBS = YAML_READER.load((ROOT / ".gitlab/ci/promote-test-envs.yml").read_text())
STATE_DIR = "cells/qa"
BASELINE = "cells/qa.yaml"
SHARED = "cells/shared.yaml"
STATE = f"{STATE_DIR}/deploy-state.yaml"
OVERRIDES = f"{STATE_DIR}/values-aws-kiwi-qa.overrides.yaml"
RECORD = f"kiwi-qa|env-qa|qa|qa-release|{BASELINE}|{STATE_DIR}|qa-app"


class SharedValuesTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.bin = self.root / "bin"
        self.bin.mkdir()
        self.env = dict(os.environ)
        self.env.update(
            PATH=f"{self.bin}:{os.environ['PATH']}",
            GIT_CONFIG_GLOBAL=os.devnull,
            GIT_CONFIG_NOSYSTEM="1",
            GIT_CONFIG_COUNT="3",
            GIT_CONFIG_KEY_0="user.name",
            GIT_CONFIG_VALUE_0="Test Operator",
            GIT_CONFIG_KEY_1="user.email",
            GIT_CONFIG_VALUE_1="operator@example.invalid",
            GIT_CONFIG_KEY_2="commit.gpgsign",
            GIT_CONFIG_VALUE_2="false",
        )
        if os.environ.get("NVCM_TEST_REAL_YQ") == "1":
            self.assertIsNotNone(shutil.which("yq"))
            return
        # The jobs already install yq. Stub only its state/audit transforms in
        # this test so no additional tool installation is needed in public CI.
        yq = self.bin / "yq"
        yq.write_text(
            f"#!{sys.executable}\n"
            "import os, re, sys\n"
            "from ruamel.yaml import YAML\n"
            "from io import StringIO\n"
            "y = YAML(typ='safe'); args = sys.argv[1:]\n"
            "edit = args[0] == '-i'\n"
            "if edit: args = args[1:]\n"
            "null_input = args[0] == '-n'\n"
            "if null_input: args = args[1:]\n"
            "if args[0] == '-r':\n"
            "    data = y.load(open(args[2]))\n"
            "    key = args[1].split()[0].lstrip('.')\n"
            "    value = data.get(key, False if key == 'hold' else 'none')\n"
            "    print(str(value).lower() if isinstance(value, bool) else value)\n"
            "    sys.exit(0)\n"
            "expr = args[0]\n"
            "data = {} if null_input else (y.load(open(args[1])) if len(args) > 1 else y.load(sys.stdin))\n"
            "for key, name in re.findall(r'\\.([\\w.]+) = strenv\\((\\w+)\\)', expr):\n"
            "    target = data; parts = key.split('.')\n"
            "    for part in parts[:-1]: target = target.setdefault(part, {})\n"
            "    target[parts[-1]] = os.environ[name]\n"
            "if null_input: data['hold'] = os.environ['current_hold'] == 'true'\n"
            "for key in re.findall(r'\\.(\\w+) = \"\"', expr): data[key] = ''\n"
            "buffer = StringIO(); y.dump(data, buffer)\n"
            "if edit: open(args[1], 'w').write(buffer.getvalue())\n"
            "else: print(buffer.getvalue(), end='')\n"
        )
        yq.chmod(0o755)

    def run_command(
        self,
        args: list[str],
        cwd: Path | None = None,
        success: bool = True,
        input_text: str | None = None,
    ) -> subprocess.CompletedProcess[str]:
        result = subprocess.run(
            args,
            cwd=cwd or self.root,
            env=self.env,
            input=input_text,
            text=True,
            capture_output=True,
            timeout=30,
        )
        if success:
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        else:
            self.assertNotEqual(result.returncode, 0)
        return result

    def git(self, *args: str, cwd: Path | None = None) -> str:
        return self.run_command(["git", *args], cwd=cwd).stdout.strip()

    def config(self, record: str, success: bool = True) -> str:
        self.env["NVCM_TEST_ENV_TARGETS"] = record
        return self.run_command(
            ["bash", str(ROOT / ".gitlab/ci/scripts/test_env_config.sh"), "kiwi-qa"],
            success=success,
        ).stdout

    def test_config_accepts_legacy_optional_and_empty_overlay(self) -> None:
        self.assertIn("NVCM_ENV_SHARED_VALUES=''", self.config(RECORD))
        self.assertIn(f"NVCM_ENV_SHARED_VALUES={SHARED}", self.config(RECORD + "|" + SHARED))
        self.assertIn("NVCM_ENV_SHARED_VALUES=''", self.config(RECORD + "|"))

    def test_config_rejects_missing_application_or_extra_fields(self) -> None:
        self.config(RECORD.rsplit("|", 1)[0], success=False)
        self.config(RECORD + "||extra", success=False)

    def prepare_repository(self, with_shared: bool = True, env_has_shared: bool = True) -> None:
        remote = self.root / "remote.git"
        self.git("init", "--bare", str(remote))
        self.repo = self.root / "seed"
        self.git("init", "-b", "main", str(self.repo))
        (self.repo / STATE_DIR).mkdir(parents=True)
        state = {
            "env": "kiwi-qa",
            "namespace": "qa",
            "envBranch": "env-qa",
            "releaseName": "qa-release",
            "chartRepo": "https://charts.example.invalid",
            "chartVersion": "0.1.0",
            "baselineRevision": "",
            "images": {},
            "sourceSHA": "a" * 40,
            "pr": 1,
            "occupant": "test",
            "updatedAt": "old",
            "hold": False,
        }
        for file, value in [
            (STATE, state),
            (BASELINE, {"cell": "old"}),
            (OVERRIDES, {"human": "keep"}),
        ]:
            with (self.repo / file).open("w") as stream:
                YAML(typ="safe").dump(value, stream)
        if with_shared:
            (self.repo / SHARED).write_text("repository: old\n")
        self.git("add", ".", cwd=self.repo)
        self.git("commit", "-m", "Seed", cwd=self.repo)
        self.git("remote", "add", "origin", str(remote), cwd=self.repo)
        self.git("push", "origin", "main", cwd=self.repo)
        self.git("symbolic-ref", "HEAD", "refs/heads/main", cwd=remote)
        self.git("switch", "-c", "env-qa", cwd=self.repo)
        if with_shared and not env_has_shared:
            self.git("rm", SHARED, cwd=self.repo)
            self.git("commit", "-m", "Environment has no shared snapshot yet", cwd=self.repo)
        self.env_revision = self.git("rev-parse", "HEAD", cwd=self.repo)
        self.git("push", "origin", "env-qa", cwd=self.repo)
        self.git("switch", "main", cwd=self.repo)
        (self.repo / BASELINE).write_text("cell: validated\n")
        if with_shared:
            (self.repo / SHARED).write_text("repository: validated\n")
        self.git("add", ".", cwd=self.repo)
        self.git("commit", "-m", "Validated baseline", cwd=self.repo)
        self.baseline_revision = self.git("rev-parse", "HEAD", cwd=self.repo)
        self.git("push", "origin", "main", cwd=self.repo)
        self.project = self.root / "project"
        self.project.mkdir()
        (self.project / ".gitlab/ci/scripts").mkdir(parents=True)
        shutil.copy(
            ROOT / ".gitlab/ci/scripts/test_env_config.sh", self.project / ".gitlab/ci/scripts"
        )
        self.env.update(
            CI_PROJECT_DIR=str(self.project),
            CI_SERVER_HOST="example.invalid",
            CI_PIPELINE_URL="local",
            NV_CONFIG_MANAGER_VALUES_REPO_URL=str(remote),
            NVCM_PROMOTE_ENV="kiwi-qa",
            NVCM_ENV="kiwi-qa",
            NVCM_ENV_BRANCH="env-qa",
            NVCM_ENV_NAMESPACE="qa",
            NVCM_ENV_RELEASE_NAME="qa-release",
            NVCM_ENV_BASELINE_VALUES=BASELINE,
            NVCM_ENV_STATE_DIR=STATE_DIR,
            NVCM_ENV_ARGOCD_APPLICATION="qa-app",
            NVCM_ENV_SHARED_VALUES=SHARED if with_shared else "",
            NVCM_CHART_REPO="https://charts.example.invalid",
            GITLAB_USER_LOGIN="test",
            NVCM_TEST_ENV_TARGETS=RECORD + ("|" + SHARED if with_shared else ""),
        )
        (self.project / "promote.env").write_text(f"PR_NUM=1\nPR_SHA={'b' * 40}\n")
        (self.project / "chart.env").write_text(
            f"PROMOTE_VERSION=0.2.0\nBASELINE_REVISION={self.baseline_revision}\n"
            f"ENV_BRANCH_REVISION={self.env_revision}\n"
        )
        digest_keys = [
            "NV_CONFIG_MANAGER",
            "NV_CONFIG_MANAGER_UI",
            "NV_CONFIG_MANAGER_KEA",
            "NV_CONFIG_MANAGER_KEA_ADMIN",
            "NV_CONFIG_MANAGER_NAUTOBOT",
            "NV_CONFIG_MANAGER_NATS_READY",
            "NV_CONFIG_MANAGER_TEMPORAL",
            "NV_CONFIG_MANAGER_TEMPORAL_BOOTSTRAP",
            "NV_CONFIG_MANAGER_TEMPORAL_UI",
        ]
        (self.project / "digests.env").write_text(
            "".join(f"DIGEST_{key}=sha256:{'c' * 64}\n" for key in digest_keys)
        )

    def remote_file(self, path: str) -> str:
        return self.git("show", f"origin/env-qa:{path}", cwd=self.repo)

    def promote(self, success: bool = True) -> subprocess.CompletedProcess[str]:
        return self.run_command(
            ["bash", str(ROOT / ".gitlab/ci/scripts/write_deploy_state.sh")],
            cwd=self.project,
            success=success,
        )

    def test_promote_uses_validated_revision_and_preserves_overrides(self) -> None:
        self.prepare_repository(env_has_shared=False)
        (self.repo / SHARED).write_text("repository: newer-unvalidated\n")
        self.git("commit", "-am", "Main moved after render", cwd=self.repo)
        self.git("push", "origin", "main", cwd=self.repo)
        self.promote()
        self.git("fetch", "origin", "env-qa", cwd=self.repo)
        self.assertEqual(self.remote_file(SHARED), "repository: validated")
        self.assertEqual(self.remote_file(BASELINE), "cell: validated")
        self.assertEqual(YAML_READER.load(self.remote_file(OVERRIDES)), {"human": "keep"})
        revision = self.git("rev-parse", "origin/env-qa", cwd=self.repo)
        self.assertIn(revision, (self.project / "deploy.env").read_text())

    def test_legacy_promote_needs_no_shared_file(self) -> None:
        self.prepare_repository(with_shared=False)
        self.promote()
        self.git("fetch", "origin", "env-qa", cwd=self.repo)
        self.assertEqual(self.remote_file(BASELINE), "cell: validated")

    def test_missing_validated_overlay_does_not_push(self) -> None:
        self.prepare_repository(with_shared=False)
        self.env["NVCM_ENV_SHARED_VALUES"] = SHARED
        result = self.promote(success=False)
        self.assertIn(f"does not contain {SHARED}", result.stderr)
        self.git("fetch", "origin", "env-qa", cwd=self.repo)
        self.assertEqual(self.git("rev-parse", "origin/env-qa", cwd=self.repo), self.env_revision)

    def test_overlay_only_rollback_restores_snapshot_and_is_idempotent(self) -> None:
        self.prepare_repository()
        self.git("switch", "env-qa", cwd=self.repo)
        (self.repo / SHARED).write_text("repository: other\n")
        self.git("commit", "-am", "Only overlay changed", cwd=self.repo)
        self.git("push", "origin", "env-qa", cwd=self.repo)
        self.env["NVCM_ROLLBACK_TO"] = self.env_revision
        script = JOBS["test-rollback-env"]["script"][0]
        self.run_command(["sh"], cwd=self.project, input_text=script)
        self.git("fetch", "origin", "env-qa", cwd=self.repo)
        self.assertEqual(self.remote_file(SHARED), "repository: old")
        self.assertEqual(YAML_READER.load(self.remote_file(OVERRIDES)), {"human": "keep"})
        revision = self.git("rev-parse", "origin/env-qa", cwd=self.repo)
        shutil.rmtree(self.project / "values-repo")
        self.run_command(["sh"], cwd=self.project, input_text=script)
        self.git("fetch", "origin", "env-qa", cwd=self.repo)
        self.assertEqual(self.git("rev-parse", "origin/env-qa", cwd=self.repo), revision)

    def test_release_refreshes_shared_overlay_and_is_idempotent(self) -> None:
        self.prepare_repository()
        script = JOBS["test-release-env"]["script"][0]
        self.run_command(["sh"], cwd=self.project, input_text=script)
        self.git("fetch", "origin", "env-qa", cwd=self.repo)
        self.assertEqual(self.remote_file(SHARED), "repository: validated")
        self.assertEqual(self.remote_file(BASELINE), "cell: validated")
        revision = self.git("rev-parse", "origin/env-qa", cwd=self.repo)
        shutil.rmtree(self.project / "values-repo")
        self.run_command(["sh"], cwd=self.project, input_text=script)
        self.git("fetch", "origin", "env-qa", cwd=self.repo)
        self.assertEqual(self.git("rev-parse", "origin/env-qa", cwd=self.repo), revision)

    def test_render_layers_shared_then_cell_then_human_overrides(self) -> None:
        self.assertIsNotNone(shutil.which("helm"))
        values = self.root / "values"
        (values / "cells").mkdir(parents=True)
        (values / SHARED).write_text("repository: shared\nprecedence: shared\n")
        (values / BASELINE).write_text("precedence: cell\n")
        overrides = self.root / "overrides.yaml"
        overrides.write_text("precedence: human\n")
        chart = self.root / "chart"
        (chart / "templates").mkdir(parents=True)
        (chart / "Chart.yaml").write_text("apiVersion: v2\nname: overlay-test\nversion: 0.1.0\n")
        (chart / "templates/config.yaml").write_text(
            "apiVersion: v1\nkind: ConfigMap\nmetadata:\n  name: layered\ndata:\n"
            '  repository: {{ .Values.repository | default "default" | quote }}\n'
            "  precedence: {{ .Values.precedence | quote }}\n"
        )
        job = JOBS["test-promote-chart"]["script"][0]
        function = job[job.index("render_chart() {") : job.index('\necho "🔍 Validating render')]
        function = function.replace("/tmp/values-repo", str(values))
        for key in [
            "NV_CONFIG_MANAGER",
            "NV_CONFIG_MANAGER_UI",
            "NV_CONFIG_MANAGER_KEA",
            "NV_CONFIG_MANAGER_KEA_ADMIN",
            "NV_CONFIG_MANAGER_NAUTOBOT",
            "NV_CONFIG_MANAGER_NATS_READY",
            "NV_CONFIG_MANAGER_TEMPORAL",
            "NV_CONFIG_MANAGER_TEMPORAL_BOOTSTRAP",
            "NV_CONFIG_MANAGER_TEMPORAL_UI",
        ]:
            self.env[f"DIGEST_{key}"] = "sha256:" + "c" * 64
        self.env.update(
            NVCM_ENV_RELEASE_NAME="qa-release",
            NVCM_ENV_BASELINE_VALUES=BASELINE,
            overrides_file=str(overrides),
        )
        output = self.root / "render.yaml"
        for shared, expected in [(SHARED, "shared"), ("", "default")]:
            self.env["NVCM_ENV_SHARED_VALUES"] = shared
            script = function + '\nrender_chart "$TEST_CHART" "$TEST_OUTPUT"\n'
            self.env.update(TEST_CHART=str(chart), TEST_OUTPUT=str(output))
            self.run_command(["sh"], input_text=script)
            rendered = YAML_READER.load(output.read_text())
            self.assertEqual(rendered["data"], {"repository": expected, "precedence": "human"})


if __name__ == "__main__":
    unittest.main(verbosity=2)
