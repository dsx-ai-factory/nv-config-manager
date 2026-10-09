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
import { cleanup, renderHook, waitFor } from "@testing-library/react";
import { SWRConfig } from "swr";
import { afterEach, describe, expect, it, vi } from "vitest";

import useWorkflowCatalog from "@/hooks/useWorkflowCatalog";

const mocked = vi.hoisted(() => ({ fetcher: vi.fn() }));

vi.mock("@/config/runtime", () => ({
  useRuntimeConfig: () => ({
    config: { workflowApiUrl: "http://api.test/" },
  }),
}));

vi.mock("@/lib/fetcher", () => ({ fetcher: mocked.fetcher }));

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

describe("useWorkflowCatalog", () => {
  it("requests and consumes form metadata for generic workflow forms", async () => {
    mocked.fetcher.mockResolvedValue({
      workflows: [
        {
          name: "BackupWorkflow",
          display_name: "Configuration Backup",
          description: "Back up a device.",
          endpoint: "/ngc/backup",
          namespace: "ngc",
          cli_name: "backup",
          input_class: "BackupInput",
          read_roles: ["all"],
          execute_roles: ["nvcm-network"],
          form_id: "configuration-backup",
          has_form: true,
          group: "Backups",
        },
      ],
    });
    const wrapper = ({ children }: PropsWithChildren) => (
      <SWRConfig value={{ provider: () => new Map(), dedupingInterval: 0 }}>
        {children}
      </SWRConfig>
    );

    const { result } = renderHook(() => useWorkflowCatalog(), { wrapper });

    await waitFor(() => expect(result.current.isLoaded).toBe(true));
    expect(mocked.fetcher).toHaveBeenCalledWith(
      "http://api.test/v1/workflow/metadata?include=form"
    );
    expect(result.current.catalog).toMatchObject([
      {
        name: "BackupWorkflow",
        form_id: "configuration-backup",
        has_form: true,
        group: "Backups",
      },
    ]);
  });
});
