// SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
// SPDX-License-Identifier: Apache-2.0

const assert = require("node:assert/strict");
const { test } = require("node:test");

const dispatchKindIntegration = require("./kind-integration-command.js");

const CURRENT_SHA = "a".repeat(40);
const STALE_SHA = "b".repeat(40);

function createFixture(options = {}) {
  const comments = [];
  const dispatches = [];
  const failures = [];
  const reactions = [];
  const warnings = [];
  const context = {
    repo: { owner: "NVIDIA", repo: "nv-config-manager" },
    payload: {
      issue: { number: 123 },
      comment: { id: 456, user: { login: "reviewer" } },
    },
  };

  const github = {
    rest: {
      issues: {
        createComment: async (args) => comments.push(args),
      },
      reactions: {
        createForIssueComment: async (args) => {
          if (options.reactionError) {
            throw options.reactionError;
          }
          reactions.push(args);
        },
      },
      repos: {
        getCollaboratorPermissionLevel: async () => ({
          data: { permission: options.permission ?? "write" },
        }),
      },
      pulls: {
        get: async () => ({
          data: {
            state: options.pullState ?? "open",
            head: { sha: options.currentSha ?? CURRENT_SHA },
          },
        }),
      },
      git: {
        getRef: async () => {
          if (options.missingTrustedBranch) {
            const error = new Error("Not Found");
            error.status = 404;
            throw error;
          }
          return {
            data: {
              object: { sha: options.trustedSha ?? CURRENT_SHA },
            },
          };
        },
      },
      actions: {
        listWorkflowRuns: async () => ({
          data: { workflow_runs: options.workflowRuns ?? [] },
        }),
        createWorkflowDispatch: async (args) => dispatches.push(args),
      },
    },
  };
  const core = {
    setFailed: (message) => failures.push(message),
  };
  if (!options.omitCoreWarning) {
    core.warning = (message) => warnings.push(message);
  }

  return {
    comments,
    context,
    core,
    dispatches,
    failures,
    github,
    reactions,
    warnings,
  };
}

async function runFixture(options) {
  const fixture = createFixture(options);
  await dispatchKindIntegration(fixture);
  return fixture;
}

test("dispatches Kind integration for the trusted current PR commit", async () => {
  const result = await runFixture();

  assert.deepEqual(result.failures, []);
  assert.equal(result.dispatches.length, 1);
  assert.deepEqual(result.reactions, [
    {
      owner: "NVIDIA",
      repo: "nv-config-manager",
      comment_id: 456,
      content: "+1",
    },
  ]);
  assert.deepEqual(result.dispatches[0], {
    owner: "NVIDIA",
    repo: "nv-config-manager",
    workflow_id: "kind-integration.yml",
    ref: "pull-request/123",
    inputs: {
      test_path: "src/tests/integration/",
      observability: "false",
      approved_sha: CURRENT_SHA,
    },
  });
  assert.match(result.comments[0].body, /Queued Kind integration/);
});

test("rejects a commenter without write access", async () => {
  const result = await runFixture({ permission: "read" });

  assert.equal(result.dispatches.length, 0);
  assert.equal(result.reactions.length, 0);
  assert.match(result.failures[0], /needs write access/);
  assert.match(result.comments[0].body, /ERROR/);
});

test("rejects starting Kind integration for a non-open PR", async () => {
  const result = await runFixture({ pullState: "closed" });

  assert.equal(result.dispatches.length, 0);
  assert.equal(result.reactions.length, 0);
  assert.match(result.failures[0], /only be started for an open PR/);
  assert.match(result.comments[0].body, /ERROR/);
});

test("requires the copy-pr-bot trusted branch", async () => {
  const result = await runFixture({ missingTrustedBranch: true });

  assert.equal(result.dispatches.length, 0);
  assert.equal(result.reactions.length, 0);
  assert.match(result.failures[0], /does not exist/);
  assert.match(result.comments[0].body, /\/ok to test/);
});

test("rejects a trusted branch that does not match the current PR head", async () => {
  const result = await runFixture({ trustedSha: STALE_SHA });

  assert.equal(result.dispatches.length, 0);
  assert.equal(result.reactions.length, 0);
  assert.match(result.failures[0], /is stale/);
  assert.match(result.comments[0].body, /\/ok to test/);
});

test("does not duplicate an active Kind integration run", async () => {
  const result = await runFixture({
    workflowRuns: [
      {
        head_sha: CURRENT_SHA,
        status: "in_progress",
        html_url: "https://github.com/dsx-ai-factory/nv-config-manager/actions/runs/1",
      },
    ],
  });

  assert.deepEqual(result.failures, []);
  assert.equal(result.dispatches.length, 0);
  assert.equal(result.reactions.length, 1);
  assert.match(result.comments[0].body, /already running/);
});

test("still reports queued run when acknowledgement reaction fails", async () => {
  const reactionError = new Error("Resource not accessible by integration");
  reactionError.status = 403;

  const result = await runFixture({ reactionError });

  assert.deepEqual(result.failures, []);
  assert.equal(result.dispatches.length, 1);
  assert.equal(result.reactions.length, 0);
  assert.match(result.warnings[0], /acknowledgement reaction failed/);
  assert.match(result.comments[0].body, /Queued Kind integration/);
});

test("still reports already-running run when acknowledgement reaction fails", async () => {
  const reactionError = new Error("Resource not accessible by integration");
  reactionError.status = 403;

  const result = await runFixture({
    reactionError,
    workflowRuns: [
      {
        head_sha: CURRENT_SHA,
        status: "in_progress",
        html_url: "https://github.com/dsx-ai-factory/nv-config-manager/actions/runs/1",
      },
    ],
  });

  assert.deepEqual(result.failures, []);
  assert.equal(result.dispatches.length, 0);
  assert.equal(result.reactions.length, 0);
  assert.match(result.warnings[0], /acknowledgement reaction failed/);
  assert.match(result.comments[0].body, /already running/);
  assert.match(result.comments[0].body, /actions\/runs\/1/);
});

test("warns to console when acknowledgement reaction fails without core.warning", async () => {
  const originalWarn = console.warn;
  const consoleWarnings = [];
  const reactionError = new Error("Resource not accessible by integration");
  reactionError.status = 403;

  console.warn = (message) => consoleWarnings.push(message);
  try {
    const result = await runFixture({
      omitCoreWarning: true,
      reactionError,
    });

    assert.deepEqual(result.failures, []);
    assert.equal(result.dispatches.length, 1);
    assert.equal(result.reactions.length, 0);
    assert.deepEqual(result.warnings, []);
    assert.match(consoleWarnings[0], /acknowledgement reaction failed/);
    assert.match(result.comments[0].body, /Queued Kind integration/);
  } finally {
    console.warn = originalWarn;
  }
});

test("already-running acknowledgement failure falls back to console without core.warning", async () => {
  const originalWarn = console.warn;
  const consoleWarnings = [];
  const reactionError = new Error("Resource not accessible by integration");
  reactionError.status = 403;

  console.warn = (message) => consoleWarnings.push(message);
  try {
    const result = await runFixture({
      omitCoreWarning: true,
      reactionError,
      workflowRuns: [
        {
          head_sha: CURRENT_SHA,
          status: "in_progress",
          html_url: "https://github.com/dsx-ai-factory/nv-config-manager/actions/runs/1",
        },
      ],
    });

    assert.deepEqual(result.failures, []);
    assert.equal(result.dispatches.length, 0);
    assert.equal(result.reactions.length, 0);
    assert.deepEqual(result.warnings, []);
    assert.match(consoleWarnings[0], /acknowledgement reaction failed/);
    assert.match(result.comments[0].body, /already running/);
    assert.match(result.comments[0].body, /actions\/runs\/1/);
  } finally {
    console.warn = originalWarn;
  }
});
