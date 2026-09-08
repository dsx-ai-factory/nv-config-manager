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
import { Workflow } from "@/types/data-table.types";
import { TRACEBACKS } from "@/mocks/data/tracebacks";
import { TEST_CSV_WORKFLOWS } from "@/mocks/data/workflows/csv/testCsvWorkflows";
import { REAL_CSV_WORKFLOWS } from "@/mocks/data/workflows/csv/realCsvWorkflows";

export const createGenericWorkflow = (id: string) => {
  return {
    id: id,
    workflow_type: "Some Workflow",
    workflow_input: {
      device_type_ids: [],
      raise_for_invalid: false,
      roles: ["SMN-Leaf", "SMN-Spine", "SMN-Core"],
      site: "507bbab4-3aae-4779-9214-cf822630b4e2",
      status: ["Provisioned"],
      tenant: "TenantB",
    },
    status: "COMPLETED",
    stages: [
      ...REAL_CSV_WORKFLOWS.workflows[0].stages,
      ...TEST_CSV_WORKFLOWS.workflows[0].stages,
      {
        name: "Real Example",
        description: "Real Example",
        requires_approval: false,
        state: "FAILED",
        output: null,
        depends_on: [],
        approvers: [],
        rejecters: [],
        approval_threshold: 0,
        state_history: [
          {
            state: "NOT_STARTED",
            time: "2025-07-03T22:12:19.012499+00:00",
          },
          {
            state: "IN_PROGRESS",
            time: "2025-07-03T22:12:19.012499+00:00",
          },
          {
            state: "FAILED",
            time: "2025-07-03T22:12:22.864934+00:00",
          },
        ],
        retryable: true,
        retry_count: 0,
        traceback:
          'temporalio.exceptions.ApplicationError: No primary IPv4 or IPv6 IP set for spine2-gp2-tan1-sitea in the DCIM.\n\nThe above exception was the direct cause of the following exception:\n\nTraceback (most recent call last):\n  File "/code/nv-config-manager_temporal/src/nv_config_manager_temporal/common/mixins/stage.py", line 43, in wrap_stage\n    result = await func(self, stage_input)\n             ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^\n  File "/code/nv-config-manager_temporal/src/nv_config_manager_temporal/ngc/workflows/backup.py", line 99, in load_running_config\n    device_data = await workflow.execute_activity(\n                  ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^\n  File "/usr/local/lib/python3.11/site-packages/temporalio/workflow.py", line 2360, in execute_activity\n    return await _Runtime.current().workflow_start_activity(\n           ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^\n  File "/usr/local/lib/python3.11/site-packages/temporalio/worker/_workflow_instance.py", line 1559, in run_activity\n    return await asyncio.shield(handle._result_fut)\n           ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^\ntemporalio.exceptions.ActivityError: Activity task failed\n',
        execution_time: null,
      },
      {
        name: "Division by Zero",
        description: "Division by Zero",
        requires_approval: false,
        state: "FAILED",
        output: null,
        depends_on: [],
        approvers: [],
        rejecters: [],
        approval_threshold: 0,
        state_history: [
          {
            state: "NOT_STARTED",
            time: "2025-07-03T22:12:19.012499+00:00",
          },
          {
            state: "IN_PROGRESS",
            time: "2025-07-03T22:12:19.012499+00:00",
          },
          {
            state: "FAILED",
            time: "2025-07-03T22:12:22.864934+00:00",
          },
        ],
        retryable: true,
        retry_count: 0,
        traceback: TRACEBACKS.division_by_zero,
        execution_time: null,
      },
      {
        name: "Nested Exception",
        description: "Nested Exception",
        requires_approval: false,
        state: "FAILED",
        output: null,
        depends_on: [],
        approvers: [],
        rejecters: [],
        approval_threshold: 0,
        state_history: [
          {
            state: "NOT_STARTED",
            time: "2025-07-03T22:12:19.012499+00:00",
          },
          {
            state: "IN_PROGRESS",
            time: "2025-07-03T22:12:19.012499+00:00",
          },
          {
            state: "FAILED",
            time: "2025-07-03T22:12:22.864934+00:00",
          },
        ],
        retryable: true,
        retry_count: 0,
        traceback: TRACEBACKS.nested_exception,
        execution_time: null,
      },
      {
        name: "Syntax Error",
        description: "Syntax Error",
        requires_approval: false,
        state: "FAILED",
        output: null,
        depends_on: [],
        approvers: [],
        rejecters: [],
        approval_threshold: 0,
        state_history: [
          {
            state: "NOT_STARTED",
            time: "2025-07-03T22:12:19.012499+00:00",
          },
          {
            state: "IN_PROGRESS",
            time: "2025-07-03T22:12:19.012499+00:00",
          },
          {
            state: "FAILED",
            time: "2025-07-03T22:12:22.864934+00:00",
          },
        ],
        retryable: true,
        retry_count: 0,
        traceback: TRACEBACKS.syntax_error,
        execution_time: null,
      },
      {
        name: "Complex multi-level",
        description: "Complex multi-level",
        requires_approval: false,
        state: "FAILED",
        output: null,
        depends_on: [],
        approvers: [],
        rejecters: [],
        approval_threshold: 0,
        state_history: [
          {
            state: "NOT_STARTED",
            time: "2025-07-03T22:12:19.012499+00:00",
          },
          {
            state: "IN_PROGRESS",
            time: "2025-07-03T22:12:19.012499+00:00",
          },
          {
            state: "FAILED",
            time: "2025-07-03T22:12:22.864934+00:00",
          },
        ],
        retryable: true,
        retry_count: 0,
        traceback: TRACEBACKS.complex_multi_level,
        execution_time: null,
      },
      {
        name: "approved_config",
        description: "Approved Config.",
        requires_approval: true,
        state: "APPROVED",
        output: "Approved",
        depends_on: [],
        approvers: [],
        rejecters: [],
        approval_threshold: 0,
        state_history: [
          {
            state: "NOT_STARTED",
            time: "2025-07-03T22:12:19.012499+00:00",
          },
          {
            state: "IN_PROGRESS",
            time: "2025-07-03T22:12:19.012499+00:00",
          },
          {
            state: "APPROVED",
            time: "2025-07-03T22:12:22.864934+00:00",
          },
        ],
        retryable: true,
        retry_count: 0,
        traceback: null,
        execution_time: 62.8650498390198,
      },
      {
        name: "rejected_config",
        description: "Rejected Config.",
        requires_approval: true,
        state: "REJECTED",
        output: "Rejected",
        depends_on: [],
        approvers: [],
        rejecters: [],
        approval_threshold: 0,
        state_history: [
          {
            state: "NOT_STARTED",
            time: "2025-07-03T22:12:19.012499+00:00",
          },
          {
            state: "IN_PROGRESS",
            time: "2025-07-03T22:12:19.012499+00:00",
          },
          {
            state: "FAILED",
            time: "2025-07-03T22:12:22.864934+00:00",
          },
        ],
        retryable: true,
        retry_count: 0,
        traceback: null,
        execution_time: 62.8650498390198,
      },
    ],
    result: null,
    search_attributes: {},
    href: `https://url-to-temporal.com/namespaces/default/workflows/${id}`,
    started_by: "",
    start_time: "",
    close_time: "",
    pending_approval: false,
  } as Workflow;
};
