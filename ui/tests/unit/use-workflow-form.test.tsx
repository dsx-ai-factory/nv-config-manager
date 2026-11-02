// @vitest-environment jsdom
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
import type { PropsWithChildren } from "react";
import { act, cleanup, renderHook, waitFor } from "@testing-library/react";
import { SWRConfig } from "swr";
import { afterEach, describe, expect, it, vi } from "vitest";

import useWorkflowForm, {
  preloadWorkflowForm,
} from "@/hooks/useWorkflowForm";
import type { WorkflowFormResult } from "@/lib/workflow-form";

const mocked = vi.hoisted(() => ({
  apiURL: "http://api.test" as string | undefined,
  fetchWorkflowForm: vi.fn(),
}));

vi.mock("@/config/runtime", () => ({
  useRuntimeConfig: () => ({
    config: mocked.apiURL ? { workflowApiUrl: mocked.apiURL } : undefined,
  }),
}));

vi.mock("@/lib/workflow-form", () => ({
  fetchWorkflowForm: mocked.fetchWorkflowForm,
}));

const deferred = <T,>() => {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>((resolvePromise) => {
    resolve = resolvePromise;
  });
  return { promise, resolve };
};

const notFound = (formId: string): WorkflowFormResult => ({
  kind: "not_found",
  message: `${formId} was not found`,
});

const createWrapper = () => {
  const cache = new Map();
  return function Wrapper({ children }: PropsWithChildren) {
    return <SWRConfig value={{ provider: () => cache }}>{children}</SWRConfig>;
  };
};

afterEach(() => {
  cleanup();
  mocked.apiURL = "http://api.test";
  vi.clearAllMocks();
});

describe("useWorkflowForm preloading", () => {
  it("reuses one in-flight request for repeated preloads and the destination hook", async () => {
    const request = deferred<WorkflowFormResult>();
    mocked.fetchWorkflowForm.mockReturnValue(request.promise);
    const formId = "in-flight-backup";

    preloadWorkflowForm("http://api.test", formId);
    preloadWorkflowForm("http://api.test", formId);

    const { result } = renderHook(() => useWorkflowForm(formId), {
      wrapper: createWrapper(),
    });

    expect(mocked.fetchWorkflowForm).toHaveBeenCalledTimes(1);
    expect(mocked.fetchWorkflowForm).toHaveBeenCalledWith(
      "http://api.test",
      formId
    );

    const response = notFound(formId);
    act(() => request.resolve(response));

    await waitFor(() => expect(result.current.result).toEqual(response));
    expect(mocked.fetchWorkflowForm).toHaveBeenCalledTimes(1);
  });

  it("keeps different form IDs independent", async () => {
    mocked.fetchWorkflowForm.mockImplementation(async (_apiURL, formId) =>
      notFound(formId)
    );
    const wrapper = createWrapper();

    preloadWorkflowForm("http://api.test", "independent-backup");
    preloadWorkflowForm("http://api.test", "independent-restore");

    const backup = renderHook(() => useWorkflowForm("independent-backup"), {
      wrapper,
    });
    const restore = renderHook(() => useWorkflowForm("independent-restore"), {
      wrapper,
    });

    await waitFor(() =>
      expect(backup.result.current.result).toEqual(
        notFound("independent-backup")
      )
    );
    await waitFor(() =>
      expect(restore.result.current.result).toEqual(
        notFound("independent-restore")
      )
    );
    expect(mocked.fetchWorkflowForm).toHaveBeenCalledTimes(2);
    expect(mocked.fetchWorkflowForm).toHaveBeenCalledWith(
      "http://api.test",
      "independent-backup"
    );
    expect(mocked.fetchWorkflowForm).toHaveBeenCalledWith(
      "http://api.test",
      "independent-restore"
    );
  });

  it("does not request a form when loading is disabled or config is unavailable", () => {
    const wrapper = createWrapper();
    const disabled = renderHook(() => useWorkflowForm("disabled", false), {
      wrapper,
    });

    mocked.apiURL = undefined;
    const missingConfig = renderHook(() => useWorkflowForm("missing-config"), {
      wrapper,
    });

    expect(disabled.result.current.isLoading).toBe(false);
    expect(missingConfig.result.current.isLoading).toBe(false);
    expect(mocked.fetchWorkflowForm).not.toHaveBeenCalled();
  });

  it("reuses a preloaded classified error result", async () => {
    const request = deferred<WorkflowFormResult>();
    mocked.fetchWorkflowForm.mockReturnValue(request.promise);

    preloadWorkflowForm("http://api.test", "unreachable");
    const { result } = renderHook(() => useWorkflowForm("unreachable"), {
      wrapper: createWrapper(),
    });

    const response: WorkflowFormResult = {
      kind: "network_error",
      message: "The workflow API could not be reached.",
    };
    act(() => request.resolve(response));

    await waitFor(() => expect(result.current.result).toEqual(response));
    expect(mocked.fetchWorkflowForm).toHaveBeenCalledTimes(1);
  });
});
