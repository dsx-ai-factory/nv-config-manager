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

import useWhoami from "@/hooks/useWhoami";
import { APIError, TokenError } from "@/lib/errors";

const mocked = vi.hoisted(() => ({ fetcher: vi.fn() }));

vi.mock("@/config/runtime", () => ({
  useRuntimeConfig: () => ({
    config: { workflowApiUrl: "http://api.test/" },
  }),
}));

vi.mock("@/lib/fetcher", () => ({ fetcher: mocked.fetcher }));

const wrapper = ({ children }: PropsWithChildren) => (
  <SWRConfig
    value={{
      provider: () => new Map(),
      dedupingInterval: 0,
      shouldRetryOnError: false,
    }}
  >
    {children}
  </SWRConfig>
);

afterEach(() => {
  cleanup();
  mocked.fetcher.mockReset();
});

describe("useWhoami", () => {
  it("loads the user and roles", async () => {
    mocked.fetcher.mockResolvedValue({
      user: "operator@example.com",
      roles: ["reader", "executor"],
    });

    const { result } = renderHook(() => useWhoami(), { wrapper });

    await waitFor(() => expect(result.current.status).toBe("loaded"));
    expect(mocked.fetcher).toHaveBeenCalledWith("http://api.test/whoami");
    expect(result.current.userRoles).toEqual(new Set(["reader", "executor"]));
    expect(result.current.isUnauthorized).toBe(false);
  });

  it.each([
    ["401", new APIError("Unauthenticated", 401)],
    ["403", new APIError("Forbidden", 403)],
    ["expired token", new TokenError("SSO token expired")],
  ])("classifies %s as unauthorized", async (_label, error) => {
    mocked.fetcher.mockRejectedValue(error);

    const { result } = renderHook(() => useWhoami(), { wrapper });

    await waitFor(() => expect(result.current.status).toBe("unauthorized"));
    expect(result.current.isUnauthorized).toBe(true);
    expect(result.current.isLoaded).toBe(true);
  });

  it.each([
    ["server error", new APIError("Unavailable", 503)],
    ["network error", new TypeError("Failed to fetch")],
  ])("classifies a %s as unavailable, not unauthorized", async (_label, error) => {
    mocked.fetcher.mockRejectedValue(error);

    const { result } = renderHook(() => useWhoami(), { wrapper });

    await waitFor(() => expect(result.current.status).toBe("unavailable"));
    expect(result.current.isUnauthorized).toBe(false);
    expect(result.current.isLoaded).toBe(true);
    expect(result.current.userRoles).toEqual(new Set());
  });
});
