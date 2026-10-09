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

import useOptionSource from "@/hooks/useOptionSource";
import type { OptionSource } from "@/types/workflow-catalog.types";

const mocked = vi.hoisted(() => ({ fetcher: vi.fn() }));

vi.mock("@/config/runtime", () => ({
  useRuntimeConfig: () => ({
    config: { workflowApiUrl: "http://api.test" },
  }),
}));

vi.mock("@/lib/fetcher", () => ({ fetcher: mocked.fetcher }));

const SOURCE = {
  endpoint: "/v1/parameter/device",
  label_key: "name",
  value_key: "id",
} satisfies OptionSource;

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

describe("useOptionSource", () => {
  it("revalidates on remount but not on rerender", async () => {
    const cache = new Map();
    const wrapper = ({ children }: PropsWithChildren) => (
      <SWRConfig
        value={{
          provider: () => cache,
          dedupingInterval: 0,
          focusThrottleInterval: 0,
        }}
      >
        {children}
      </SWRConfig>
    );
    mocked.fetcher
      .mockResolvedValueOnce([{ id: "device-1", name: "Device 1" }])
      .mockResolvedValueOnce([{ id: "device-2", name: "Device 2" }]);

    const first = renderHook(() => useOptionSource(SOURCE, {}), { wrapper });

    await waitFor(() => expect(first.result.current.status).toBe("success"));
    expect(first.result.current.options.map(({ value }) => value)).toEqual([
      "device-1",
    ]);
    expect(mocked.fetcher).toHaveBeenCalledTimes(1);

    first.rerender();
    expect(mocked.fetcher).toHaveBeenCalledTimes(1);

    first.unmount();
    const revisited = renderHook(() => useOptionSource(SOURCE, {}), { wrapper });

    await waitFor(() => expect(mocked.fetcher).toHaveBeenCalledTimes(2));
    await waitFor(() =>
      expect(revisited.result.current.options.map(({ value }) => value)).toEqual([
        "device-2",
      ])
    );
  });
});
