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
import useSWRImmutable from "swr/immutable";

import { useRuntimeConfig } from "@/config/runtime";
import {
  fetchWorkflowForm,
  type WorkflowFormResult,
} from "@/lib/workflow-form";

const WORKFLOW_FORM_SWR_KEY = "workflow-form";

export interface UseWorkflowFormReturn {
  result: WorkflowFormResult | undefined;
  isLoading: boolean;
  reload: () => Promise<WorkflowFormResult | undefined>;
}

const useWorkflowForm = (name: string | null | undefined): UseWorkflowFormReturn => {
  const { config } = useRuntimeConfig();
  const apiURL = config?.workflowApiUrl;
  const { data, isLoading, mutate } = useSWRImmutable(
    apiURL && name ? ([WORKFLOW_FORM_SWR_KEY, apiURL, name] as const) : null,
    ([, url, workflowName]: readonly [string, string, string]) =>
      fetchWorkflowForm(url, workflowName)
  );

  return { result: data, isLoading, reload: () => mutate() };
};

export default useWorkflowForm;
