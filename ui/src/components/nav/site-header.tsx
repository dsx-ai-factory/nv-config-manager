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
import Link from "next/link";

import { siteConfig } from "@/config/site";
import { MainNav } from "@/components/nav";
import { ThemeToggle } from "@/components/theme";
import { Fragment, useId, useMemo, useState } from "react";
import { LogOut, PlusIcon, UserCircle } from "lucide-react";
import {
  Popover,
  PopoverContent,
  PopoverTrigger,
} from "@/components/ui/popover";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  Tooltip,
  TooltipContent,
  TooltipProvider,
  TooltipTrigger,
} from "@/components/ui/tooltip";
import useWhoami from "@/hooks/useWhoami";
import { cn } from "@/lib/utils";
import useWorkflowCatalog from "@/hooks/useWorkflowCatalog";
import {
  buildWorkflowLauncherItems,
  getWorkflowExecutePermission,
  groupWorkflowLauncherItems,
  type WorkflowLauncherItem,
} from "@/lib/workflow-launcher";

const WorkflowLauncherEntry = ({
  isUnauthorized,
  item,
  onSelect,
  userRoles,
}: {
  isUnauthorized: boolean;
  item: WorkflowLauncherItem;
  onSelect: () => void;
  userRoles: ReadonlySet<string>;
}) => {
  const reasonId = useId();
  const permission = getWorkflowExecutePermission(
    item.metadata,
    userRoles,
    isUnauthorized
  );

  return permission.allowed ? (
    <Link
      href={item.href}
      className="flex rounded-sm border-none px-3 py-2 hover:border-none hover:bg-accent hover:text-accent-foreground"
      onClick={onSelect}
    >
      {item.display_name}
    </Link>
  ) : (
    <TooltipProvider delayDuration={0}>
      <Tooltip>
        <TooltipTrigger asChild>
          <button
            aria-describedby={reasonId}
            aria-disabled="true"
            className={cn(
              "flex w-full cursor-not-allowed rounded-sm border-none bg-transparent px-3 py-2 text-left opacity-50",
              "hover:border-none hover:bg-accent hover:text-accent-foreground"
            )}
            type="button"
          >
            {item.display_name}
          </button>
        </TooltipTrigger>
        <TooltipContent side="left">
          <p>{permission.reason}</p>
        </TooltipContent>
        <span className="sr-only" id={reasonId}>
          {permission.reason}
        </span>
      </Tooltip>
    </TooltipProvider>
  );
};

const NewWorkflowChooser = () => {
  const [isOpen, setIsOpen] = useState(false);
  const { catalog } = useWorkflowCatalog();
  const { isUnauthorized, userRoles } = useWhoami();
  const sections = useMemo(
    () =>
      groupWorkflowLauncherItems(
        buildWorkflowLauncherItems(catalog, siteConfig.workflowOverrides)
      ),
    [catalog]
  );

  const showSectionHeadings = sections.length > 1;

  return (
    <div className="relative inline-block text-left">
      <Popover open={isOpen} onOpenChange={setIsOpen}>
        <PopoverTrigger asChild>
          <Button
            aria-label="New workflow"
            size="icon"
            title="New workflow"
            variant="ghost"
          >
            <PlusIcon size={24} />
          </Button>
        </PopoverTrigger>

        <PopoverContent align="end" className="max-h-[70vh] overflow-y-auto">
          {sections.map((section) => (
            <Fragment key={section.group}>
              {showSectionHeadings && (
                <div className="px-3 pb-1 pt-2 text-xs font-medium text-muted-foreground">
                  {section.group}
                </div>
              )}
              {section.items.map((item) => (
                <WorkflowLauncherEntry
                  isUnauthorized={isUnauthorized}
                  item={item}
                  key={item.name}
                  onSelect={() => setIsOpen(false)}
                  userRoles={userRoles}
                />
              ))}
            </Fragment>
          ))}
        </PopoverContent>
      </Popover>
    </div>
  );
};

const UserRolesMenu = () => {
  const { userInfo, isUnauthorized } = useWhoami();
  const username = isUnauthorized
    ? "Unauthorized"
    : userInfo?.user ?? "Unknown user";
  const roles = (isUnauthorized ? [] : (userInfo?.roles ?? [])).filter(
    (role) => role.toLowerCase() !== "all"
  );

  return (
    <Popover>
      <PopoverTrigger asChild>
        <Button
          aria-label="User roles"
          size="icon"
          title="User roles"
          variant="ghost"
        >
          <UserCircle size={24} />
        </Button>
      </PopoverTrigger>
      <PopoverContent align="end" className="w-80">
        <div className="space-y-4">
          <div className="space-y-1">
            <div className="text-xs font-medium text-muted-foreground">
              Username
            </div>
            <div className="break-all text-sm font-medium">{username}</div>
          </div>
          <div className="space-y-2">
            <div className="text-xs font-medium text-muted-foreground">
              Roles
            </div>
            <div className="flex flex-wrap gap-2">
              {roles.length > 0 ? (
                roles.map((role) => (
                  <Badge key={role} variant="secondary">
                    {role}
                  </Badge>
                ))
              ) : (
                <span className="text-sm text-muted-foreground">No roles</span>
              )}
            </div>
          </div>
          <Button
            asChild
            className="w-full justify-start gap-2"
            variant="outline"
          >
            <a href="/auth/logout">
              <LogOut size={16} />
              Logout
            </a>
          </Button>
        </div>
      </PopoverContent>
    </Popover>
  );
};

export function SiteHeader() {
  return (
    <header className="bg-background sticky top-0 z-40 w-full border-b">
      <div className="container flex h-16 items-center space-x-4 sm:justify-between sm:space-x-0">
        <MainNav items={siteConfig.mainNav} />
        <div className="flex flex-1 items-center justify-end space-x-4">
          <nav className="flex items-center space-x-1">
            <NewWorkflowChooser />
            <UserRolesMenu />
            <ThemeToggle />
          </nav>
        </div>
      </div>
    </header>
  );
}
