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
import { cn } from "@/lib/utils";
import { SiteHeader, SiteFooter } from "@/components/nav";
import { ThemeProvider } from "@/components/theme";
import { Toaster } from "@/components/ui/toaster";
import { useMemo, useState } from "react";
import { HeaderContext } from "./contexts/header";


interface BodyProps {
  children: React.ReactNode;
}

export default function Body({ children }: Readonly<BodyProps>) {
    const [refreshPaused, setRefreshPaused] = useState(false);
    const headerContextValue = useMemo(
      () => ({ refreshPaused, setRefreshPaused }),
      [refreshPaused]
    );
    return (
      <body
        className={cn(
          "min-h-screen bg-background font-sans antialiased"
        )}
      >
        <ThemeProvider attribute="class" defaultTheme="system" enableSystem>
          <div className="relative flex min-h-screen flex-col">
            <HeaderContext.Provider value={headerContextValue}>
              <SiteHeader />
              <Toaster />
              <div className="flex-1">{children}</div>
              <SiteFooter />
            </HeaderContext.Provider>
          </div>
        </ThemeProvider>
      </body>
    );
}
