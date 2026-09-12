"use client";
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

import { useEffect, useState } from "react";
import { handlers } from "@/mocks/handlers";

async function startMocking(): Promise<void> {
  if (
    typeof globalThis.window === "undefined" ||
    globalThis.window.BYPASS_MSW ||
    process.env.NODE_ENV !== 'development'
  ) {
    return;
  }

  const { worker } = await import("@/mocks/browser");
  await worker.start({
    onUnhandledRequest(request, print) {
      if (request.url.includes("_next")) {
        return;
      }
      print.warning();
    },
  });
  worker.use(...handlers);
}

let mockingEnabledPromise: Promise<void> | undefined;

function getMockingEnabledPromise(): Promise<void> {
  mockingEnabledPromise ??= startMocking();
  return mockingEnabledPromise;
}

export function MSWProvider({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  const [isReady, setIsReady] = useState(
    process.env.NODE_ENV !== "development"
  );
  const [startupError, setStartupError] = useState<unknown>(null);

  useEffect(() => {
    let isMounted = true;

    const initializeMocking = async () => {
      try {
        await getMockingEnabledPromise();
        if (isMounted) {
          setIsReady(true);
        }
      } catch (error) {
        if (isMounted) {
          setStartupError(error);
        }
      }
    };

    void initializeMocking();
    return () => {
      isMounted = false;
    };
  }, []);

  if (startupError) {
    throw startupError;
  }

  return isReady ? children : null;
}
