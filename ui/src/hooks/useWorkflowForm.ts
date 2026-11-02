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
import { preload } from "swr";
import useSWRImmutable from "swr/immutable";

import { useRuntimeConfig } from "@/config/runtime";
import {
  fetchWorkflowForm,
  type WorkflowFormResult,
} from "@/lib/workflow-form";

const WORKFLOW_FORM_SWR_KEY = "workflow-form";

export type WorkflowFormSWRKey = readonly [
  typeof WORKFLOW_FORM_SWR_KEY,
  string,
  string,
];

export const getWorkflowFormKey = (
  apiURL: string,
  formId: string
): WorkflowFormSWRKey => [WORKFLOW_FORM_SWR_KEY, apiURL, formId] as const;

export const workflowFormFetcher = ([
  ,
  apiURL,
  formId,
]: WorkflowFormSWRKey): Promise<WorkflowFormResult> =>
  fetchWorkflowForm(apiURL, formId);

export const preloadWorkflowForm = (
  apiURL: string,
  formId: string
): void => {
  void preload(
    getWorkflowFormKey(apiURL, formId),
    workflowFormFetcher
  ).catch(() => undefined);
};

export interface UseWorkflowFormReturn {
  result: WorkflowFormResult | undefined;
  isLoading: boolean;
  reload: () => Promise<WorkflowFormResult | undefined>;
}

const useWorkflowForm = (
  formId: string,
  enabled = true
): UseWorkflowFormReturn => {
  const { config } = useRuntimeConfig();
  const apiURL = config?.workflowApiUrl;
  const key =
    enabled && apiURL && formId ? getWorkflowFormKey(apiURL, formId) : null;
  const { data, isLoading, mutate } = useSWRImmutable(
    key,
    workflowFormFetcher
  );

  return { result: data, isLoading, reload: () => mutate() };
};

export default useWorkflowForm;
