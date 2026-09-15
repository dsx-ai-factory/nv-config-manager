/*
 * SPDX-FileCopyrightText: Copyright (c) 2024-2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
 * SPDX-License-Identifier: Apache-2.0
 *
 * Licensed under the Apache License, Version 2.0 (the "License");
 * you may not use this file except in compliance with the License.
 * You may obtain a copy of the License at
 *
 * http://www.apache.org/licenses/LICENSE-2.0
 *
 * Unless required by applicable law or agreed to in writing, software
 * distributed under the License is distributed on an "AS IS" BASIS,
 * WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
 * See the License for the specific language governing permissions and
 * limitations under the License.
 */
import { expect } from "@playwright/test";
import { test } from "./shared/utils";
import { FORBIDDEN_WORKFLOW_ID } from "@/mocks/data";
import { createGenericWorkflow } from "@/mocks/data/workflows/genericWorkflow";

const createWorkflowWithStage = ({
  id,
  retryable,
  stageName,
  stageState,
  status,
}: {
  id: string;
  retryable: boolean;
  stageName: string;
  stageState: "FAILED" | "PENDING_APPROVAL";
  status: string;
}) => {
  const workflow = createGenericWorkflow(id);
  workflow.workflow_type = "DeployWorkflow";
  workflow.status = status;
  workflow.pending_approval = stageState === "PENDING_APPROVAL";
  workflow.search_attributes = {
    ExecuteRoles: ["all"],
    ReadRoles: ["all"],
    User: ["joliao"],
  };
  workflow.stages = [
    {
      name: stageName,
      description: `${stageName} description`,
      requires_approval: stageState === "PENDING_APPROVAL",
      state: stageState,
      output: { display: `${stageName} output` },
      depends_on: [],
      approvers: [],
      rejecters: [],
      approval_threshold: stageState === "PENDING_APPROVAL" ? 1 : 0,
      state_history: [
        {
          state: "IN_PROGRESS",
          time: "2025-07-03T22:12:19.012499+00:00",
        },
        {
          state: stageState,
          time: "2025-07-03T22:12:22.864934+00:00",
        },
      ],
      retryable,
      retry_count: 0,
      traceback: stageState === "FAILED" ? "failed stage" : null,
      execution_time: null,
    },
  ];
  return workflow;
};

test.describe("Workflow Detail Page", () => {
  test("Test workflow detail page loads correctly", async ({ page }) => {
    await page.goto("/workflows/some-workflow-id");

    // Check for the main heading
    await expect(
      page.getByRole("heading", { name: "Workflow Details" })
    ).toBeVisible();

    // Check for the Stages section by text content
    await expect(page.getByText("Stages")).toBeVisible();

    // Check for the stage details in the table using role
    const stageCell = page.getByRole("cell", {
      name: /Get the list of devices to validate for this site/,
    });
    await expect(stageCell).toBeVisible();

    // Check for the stage status badge within the cell
    await expect(
      stageCell.getByText("COMPLETE", { exact: true })
    ).toBeVisible();
  });

  test("displays stage details correctly", async ({ page }) => {
    await page.goto("/workflows/some-workflow-id");

    // Check the stage table structure
    const stageTable = page.getByRole("table");
    await expect(stageTable).toBeVisible();

    // Verify stage description in table cell
    const stageCell = page.getByRole("cell", {
      name: /Get the list of devices to validate for this site/,
    });
    await expect(stageCell).toBeVisible();

    // Verify stage status within the cell
    const statusBadge = stageCell.getByText("COMPLETE", { exact: true });
    await expect(statusBadge).toBeVisible();
  });

  test("displays forbidden error page when accessing unauthorized workflow", async ({
    page,
  }) => {
    await page.goto(`/workflows/${FORBIDDEN_WORKFLOW_ID}`);

    // Check for error heading in the card
    await expect(
      page.getByRole("heading", { name: "Access Denied", level: 3 })
    ).toBeVisible();

    // Check for alert section with error content
    const alert = page.getByRole("alert").filter({
      has: page.getByText(
        "You do not have permission to access this resource."
      ),
    });
    await expect(alert).toBeVisible();

    // Check alert contents
    await expect(
      alert.getByRole("heading", { name: "Access Denied", level: 5 })
    ).toBeVisible();
    await expect(
      alert.getByText("You do not have permission to access this resource.")
    ).toBeVisible();

    // Check for action buttons
    const tryAgainButton = page.getByRole("button", { name: "Try Again" });
    await expect(tryAgainButton).toBeVisible();
    await expect(tryAgainButton).toHaveClass(/bg-primary/);

    // Check for workflows link
    const workflowsLink = page.getByRole("link", { name: "Go to Workflows" });
    await expect(workflowsLink).toBeVisible();
    await expect(workflowsLink).toHaveAttribute("href", "/workflows");

    // Click try again and verify we're still on error page (since it's forbidden)
    await tryAgainButton.click();
    await expect(
      page.getByRole("heading", { name: "Access Denied", level: 3 })
    ).toBeVisible();
  });

  test("shows pending approval stage actions", async ({ page }) => {
    const workflowId = "pending-approval-workflow";
    const stageName = "review_config";
    let approveCalled = false;
    const workflow = createWorkflowWithStage({
      id: workflowId,
      retryable: false,
      stageName,
      stageState: "PENDING_APPROVAL",
      status: "RUNNING",
    });

    await page.route(`**/v1/workflow/${workflowId}`, async (route) => {
      await route.fulfill({ status: 200, json: workflow });
    });
    await page.route(
      `**/v1/workflow/${workflowId}/approve/${stageName}`,
      async (route) => {
        approveCalled = true;
        await route.fulfill({
          status: 200,
          json: {
            id: workflowId,
            href: `https://url-to-temporal.com/namespaces/default/workflows/${workflowId}`,
          },
        });
      }
    );

    await page.goto(`/workflows/${workflowId}`);

    await expect(
      page.getByRole("heading", {
        name: `${stageName} description`,
        exact: true,
      }).last()
    ).toBeVisible();
    await expect(page.getByText("Read Roles: all")).toBeVisible();
    await expect(page.getByText("Execute Roles: all")).toBeVisible();

    await page.getByRole("button", { name: "Approve" }).first().click();
    await expect.poll(() => approveCalled).toBe(true);
  });

  test("requires confirmation before terminating a workflow", async ({ page }) => {
    const workflowId = "running-workflow";
    let terminateCalled = false;
    const workflow = createWorkflowWithStage({
      id: workflowId,
      retryable: false,
      stageName: "wait_for_approval",
      stageState: "PENDING_APPROVAL",
      status: "RUNNING",
    });

    await page.route(`**/v1/workflow/${workflowId}`, async (route) => {
      await route.fulfill({ status: 200, json: workflow });
    });
    await page.route(`**/v1/workflow/${workflowId}/terminate`, async (route) => {
      terminateCalled = true;
      await route.fulfill({
        status: 200,
        json: {
          id: workflowId,
          href: `https://url-to-temporal.com/namespaces/default/workflows/${workflowId}`,
        },
      });
    });

    await page.goto(`/workflows/${workflowId}`);
    await page.getByRole("button", { name: "Terminate" }).click();

    await expect(page.getByRole("dialog")).toBeVisible();
    await expect(page.getByText("Terminate Workflow?")).toBeVisible();
    await expect.poll(() => terminateCalled).toBe(false);

    await page.getByRole("button", { name: "Terminate Workflow" }).click();
    await expect.poll(() => terminateCalled).toBe(true);
  });

  test("refreshes the selected stage detail panel when stage data updates", async ({
    page,
  }) => {
    const workflowId = "refreshing-stage-workflow";
    const stageName = "render_config";
    let currentOutput = "initial stage output";

    const createRefreshingWorkflow = () => {
      const workflow = createGenericWorkflow(workflowId);
      workflow.workflow_type = "DeployWorkflow";
      workflow.status = "RUNNING";
      workflow.search_attributes = {
        ExecuteRoles: ["all"],
        ReadRoles: ["all"],
        User: ["joliao"],
      };
      workflow.stages = [
        {
          name: stageName,
          description: "Render config description",
          requires_approval: false,
          state: "IN_PROGRESS",
          output: { display: currentOutput },
          depends_on: [],
          approvers: [],
          rejecters: [],
          approval_threshold: 0,
          state_history: [
            {
              state: "IN_PROGRESS",
              time: "2025-07-03T22:12:19.012499+00:00",
            },
          ],
          retryable: false,
          retry_count: 0,
          traceback: null,
          execution_time: null,
        },
      ];
      return workflow;
    };

    await page.route(`**/v1/workflow/${workflowId}`, async (route) => {
      await route.fulfill({ status: 200, json: createRefreshingWorkflow() });
    });

    await page.goto(`/workflows/${workflowId}`);
    await expect(page.getByText("initial stage output")).toBeVisible();

    currentOutput = "refreshed stage output";
    await expect(page.getByText("refreshed stage output")).toBeVisible({
      timeout: 10000,
    });
    await expect(page.getByText("initial stage output")).toHaveCount(0);
  });

  test("does not show retry as an action for non-retryable failed stages", async ({
    page,
  }) => {
    const workflowId = "non-retryable-failed-workflow";
    const workflow = createWorkflowWithStage({
      id: workflowId,
      retryable: false,
      stageName: "apply_config",
      stageState: "FAILED",
      status: "FAILED",
    });

    await page.route(`**/v1/workflow/${workflowId}`, async (route) => {
      await route.fulfill({ status: 200, json: workflow });
    });

    await page.goto(`/workflows/${workflowId}`);

    await expect(
      page.getByText("This failed stage cannot be retried.")
    ).toBeVisible();
    await expect(page.getByRole("button", { name: "Retry" })).toHaveCount(0);
  });

  test("renders chained traceback errors with their frames and full messages", async ({
    page,
  }) => {
    const workflowId = "failed-workflow-with-traceback";
    const workflow = createWorkflowWithStage({
      id: workflowId,
      retryable: false,
      stageName: "apply_config",
      stageState: "FAILED",
      status: "FAILED",
    });
    workflow.stages[0].traceback = `Traceback (most recent call last):
  File "/app/inner.py", line 12, in load_config
    load_from_database()
ConnectionError: database: host: unreachable

The above exception was the direct cause of the following exception:

Traceback (most recent call last):
  File "/app/outer.py", line 28, in apply_config
    raise ServiceUnavailableError() from error
ServiceUnavailableError: service: retry later`;

    await page.route(`**/v1/workflow/${workflowId}`, async (route) => {
      await route.fulfill({ status: 200, json: workflow });
    });

    await page.goto(`/workflows/${workflowId}`);

    const tracebackCard = page.getByTestId("error-traceback-card");
    const errorTypes = tracebackCard.getByTestId("error-traceback-type");

    await expect(errorTypes).toHaveText([
      "ServiceUnavailableError",
      "ConnectionError",
    ]);
    await expect(
      tracebackCard.getByText("service: retry later", { exact: true })
    ).toBeVisible();
    await expect(
      tracebackCard.getByText("database: host: unreachable", { exact: true })
    ).toBeVisible();
    await expect(
      tracebackCard.getByText("/app/outer.py", { exact: true })
    ).toBeVisible();
    await expect(
      tracebackCard.getByText("/app/inner.py", { exact: true })
    ).toBeVisible();
    await expect(
      tracebackCard.getByText("Unknown Error", { exact: true })
    ).toHaveCount(0);
  });

  test("renders actionable Markdown output for a failed stage", async ({ page }) => {
    const workflowId = "failed-workflow-with-guidance";
    const workflow = createWorkflowWithStage({
      id: workflowId,
      retryable: true,
      stageName: "pre_reprovision_backup",
      stageState: "FAILED",
      status: "RUNNING",
    });
    workflow.stages[0].output = {
      display:
        "The intended configuration is invalid. Check the intended configuration [here](https://config.example.com/device/test/startup.yaml). Once it is fixed this stage can be retried. Review the [backup workflow](https://temporal.example.com/workflows/backup-test) for details.",
    };

    await page.route(`**/v1/workflow/${workflowId}`, async (route) => {
      await route.fulfill({ status: 200, json: workflow });
    });

    await page.goto(`/workflows/${workflowId}`);

    await expect(
      page.getByRole("link", { name: "here" })
    ).toHaveAttribute(
      "href",
      "https://config.example.com/device/test/startup.yaml"
    );
    await expect(
      page.getByRole("link", { name: "backup workflow" })
    ).toHaveAttribute(
      "href",
      "https://temporal.example.com/workflows/backup-test"
    );
    await expect(page.getByTestId("error-traceback-card")).toBeVisible();
    await expect(page.getByRole("button", { name: "Retry" })).toBeVisible();
  });
});
