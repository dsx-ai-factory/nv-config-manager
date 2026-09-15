// SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
// SPDX-License-Identifier: Apache-2.0

const assert = require("node:assert/strict");
const { test } = require("node:test");

const reportKindIntegrationResult = require("./kind-integration-result.js");

const SHA = "a".repeat(40);
const DEFAULT_SERVER_URL = "https://github.com";
const RUN_URL = "https://github.com/dsx-ai-factory/nv-config-manager/actions/runs/123";
const TEMPLATE_BODY = `## Description

Example PR.

## Validation

Passing Kind Integration run, if not automatically reported:
<!-- kind-integration-result:start -->
<!-- Paste the workflow run URL here only if the Kind result was not automatically updated. -->
<!-- kind-integration-result:end -->

## Checklist

- [ ] Standard CI passes.
`;

function createFixture(options = {}) {
  const updates = [];
  const warnings = [];
  const context = {
    repo: { owner: "NVIDIA", repo: "nv-config-manager" },
    serverUrl: options.serverUrl ?? DEFAULT_SERVER_URL,
    payload: {},
  };
  if (!options.omitWorkflowRun) {
    context.payload.workflow_run = {
      conclusion: Object.hasOwn(options, "conclusion") ? options.conclusion : "success",
      event: options.event ?? "workflow_dispatch",
      head_branch: options.headBranch ?? "pull-request/72",
      head_sha: SHA,
      html_url: RUN_URL,
      status: options.status ?? "completed",
    };
  }
  const github = {
    rest: {
      pulls: {
        get: async () => {
          if (options.pullGetError) {
            throw options.pullGetError;
          }

          return {
            data: {
              body: Object.hasOwn(options, "pullBody") ? options.pullBody : TEMPLATE_BODY,
              head: { sha: options.pullHeadSha ?? SHA },
            },
          };
        },
        update: async (args) => {
          if (options.pullUpdateError) {
            throw options.pullUpdateError;
          }

          updates.push(args);
        },
      },
    },
  };
  const core = {
    warning: (message) => warnings.push(message),
  };

  return { context, github, core, updates, warnings };
}

function expectedCommitUrl(context) {
  return `${context.serverUrl}/${context.repo.owner}/${context.repo.repo}/commit/${SHA}`;
}

async function runFixture(options) {
  const fixture = createFixture(options);
  await reportKindIntegrationResult({
    ...fixture,
    run: options?.run,
  });
  return fixture;
}

test("updates the PR body with the completed Kind run", async () => {
  const result = await runFixture();

  assert.equal(result.updates.length, 1);
  assert.deepEqual(result.updates[0], {
    owner: "NVIDIA",
    repo: "nv-config-manager",
    pull_number: 72,
    body: `## Description

Example PR.

## Validation

Passing Kind Integration run, if not automatically reported:
<!-- kind-integration-result:start -->
Kind integration completed with **success** for [\`aaaaaaa\`](${expectedCommitUrl(result.context)}) in [this workflow run](${RUN_URL}).
<!-- kind-integration-result:end -->

## Checklist

- [ ] Standard CI passes.
`,
  });
});

test("replaces an existing automatic Kind result", async () => {
  const result = await runFixture({
    conclusion: "failure",
    pullBody: `## Validation

Passing Kind Integration run, if not automatically reported:
<!-- kind-integration-result:start -->
Kind integration completed with **success** for [\`bbbbbbbbbbbb\`](https://old.example/run).
<!-- kind-integration-result:end -->
`,
  });

  assert.deepEqual(result.updates, [
    {
      owner: "NVIDIA",
      repo: "nv-config-manager",
      pull_number: 72,
      body: `## Validation

Passing Kind Integration run, if not automatically reported:
<!-- kind-integration-result:start -->
Kind integration completed with **failure** for [\`aaaaaaa\`](${expectedCommitUrl(result.context)}) in [this workflow run](${RUN_URL}).
<!-- kind-integration-result:end -->
`,
    },
  ]);
});

test("reports an unsuccessful Kind conclusion", async () => {
  const result = await runFixture({ conclusion: "failure" });

  assert.match(result.updates[0].body, /\*\*failure\*\*/);
  assert.ok(result.updates[0].body.includes(RUN_URL));
});

test("updates from explicit run details when workflow_run did not fire", async () => {
  const result = await runFixture({
    omitWorkflowRun: true,
    run: {
      conclusion: "success",
      event: "workflow_dispatch",
      head_branch: "pull-request/72",
      head_sha: SHA,
      html_url: RUN_URL,
      status: "completed",
    },
  });

  assert.equal(result.updates.length, 1);
  assert.match(result.updates[0].body, /\*\*success\*\*/);
  assert.ok(result.updates[0].body.includes(RUN_URL));
});

test("uses the GitHub Enterprise server URL for commit links", async () => {
  const result = await runFixture({ serverUrl: "https://github.example.com" });

  assert.ok(
    result.updates[0].body.includes(
      `[\`aaaaaaa\`](${expectedCommitUrl(result.context)})`,
    ),
  );
});

test("does nothing without workflow_run or explicit run details", async () => {
  const result = await runFixture({ omitWorkflowRun: true });

  assert.deepEqual(result.updates, []);
});

test("uses unknown when a completed Kind run has no conclusion", async () => {
  const result = await runFixture({ conclusion: null });

  assert.match(result.updates[0].body, /\*\*unknown\*\*/);
  assert.ok(result.updates[0].body.includes(RUN_URL));
});

test("warns instead of failing when fetching the PR fails", async () => {
  const result = await runFixture({ pullGetError: new Error("Not Found") });

  assert.deepEqual(result.updates, []);
  assert.match(result.warnings[0], /Failed to update PR #72/);
  assert.match(result.warnings[0], /Not Found/);
});

test("warns instead of failing when updating the PR body fails", async () => {
  const result = await runFixture({ pullUpdateError: new Error("Bad credentials") });

  assert.deepEqual(result.updates, []);
  assert.match(result.warnings[0], /Failed to update PR #72/);
  assert.match(result.warnings[0], /Bad credentials/);
});

test("falls back to appending when the PR body lacks the Kind result slot", async () => {
  const result = await runFixture({ pullBody: "## Description\n\nNo validation section.\n" });

  assert.equal(
    result.updates[0].body,
    `## Description

No validation section.

## Kind Integration

<!-- kind-integration-result:start -->
Kind integration completed with **success** for [\`aaaaaaa\`](${expectedCommitUrl(result.context)}) in [this workflow run](${RUN_URL}).
<!-- kind-integration-result:end -->
`,
  );
});

test("fallback append includes replaceable Kind result markers", async () => {
  const firstResult = await runFixture({ pullBody: "## Description\n\nNo validation section.\n" });
  const secondResult = await runFixture({
    conclusion: "failure",
    pullBody: firstResult.updates[0].body,
  });

  assert.equal(
    secondResult.updates[0].body,
    `## Description

No validation section.

## Kind Integration

<!-- kind-integration-result:start -->
Kind integration completed with **failure** for [\`aaaaaaa\`](${expectedCommitUrl(secondResult.context)}) in [this workflow run](${RUN_URL}).
<!-- kind-integration-result:end -->
`,
  );
});

test("ignores Kind runs that are not for a trusted PR branch", async () => {
  const result = await runFixture({ headBranch: "main" });

  assert.deepEqual(result.updates, []);
});

test("ignores Kind runs that have not completed", async () => {
  const result = await runFixture({ status: "in_progress" });

  assert.deepEqual(result.updates, []);
});

test("ignores Kind runs that were not manually dispatched", async () => {
  const result = await runFixture({ event: "push" });

  assert.deepEqual(result.updates, []);
});

test("ignores canceled Kind runs for stale PR commits", async () => {
  const result = await runFixture({
    conclusion: "cancelled",
    pullHeadSha: "b".repeat(40),
  });

  assert.deepEqual(result.updates, []);
});
