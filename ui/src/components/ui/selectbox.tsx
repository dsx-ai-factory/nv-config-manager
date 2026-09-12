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
// NOTE: Component from https://github.com/shadcn-ui/ui/issues/927#issuecomment-2272458201
import { ArrowUpDownIcon, CheckIcon, XIcon } from "lucide-react";
import * as React from "react";

import { cn } from "@/lib/utils";

import {
  Command,
  CommandEmpty,
  CommandGroup,
  CommandInput,
  CommandItem,
  CommandList,
} from "@/components/ui/command";
import {
  Popover,
  PopoverContent,
  PopoverTrigger,
} from "@/components/ui/popover";

interface Option {
  value: string;
  key: string;
}

interface SelectBoxProps {
  options: Option[];
  value?: string[] | string;
  onChange?: (values: string[] | string) => void;
  placeholder?: string;
  inputPlaceholder?: string;
  emptyPlaceholder?: string;
  className?: string;
  multiple?: boolean;
  disabled?: boolean;
  searchable?: boolean;
}

const filterOption = (
  value: string,
  search: string,
  keywords: string[] = [],
) => {
  const normalizedSearch = search.trim().toLowerCase();
  if (!normalizedSearch) return 1;

  const candidates = [value, ...keywords].map((candidate) =>
    candidate.toLowerCase(),
  );

  if (candidates.includes(normalizedSearch)) return 1;
  if (candidates.some((candidate) => candidate.startsWith(normalizedSearch))) {
    return 0.75;
  }
  if (candidates.some((candidate) => candidate.includes(normalizedSearch))) {
    return 0.5;
  }

  return 0;
};

const SelectBox = React.forwardRef<HTMLInputElement, SelectBoxProps>(
  (
    {
      inputPlaceholder,
      emptyPlaceholder,
      placeholder,
      className,
      options,
      value,
      onChange,
      multiple,
      disabled,
      searchable = true,
    },
    ref,
  ) => {
    const [searchTerm, setSearchTerm] = React.useState<string>("");
    const [isOpen, setIsOpen] = React.useState(false);

    const handleSelect = (selectedValue: string) => {
      if (multiple) {
        let newValue: string[];
        if (!Array.isArray(value)) {
          newValue = [selectedValue];
        } else if (value.includes(selectedValue)) {
          newValue = value.filter(
            (currentValue) => currentValue !== selectedValue,
          );
        } else {
          newValue = [...value, selectedValue];
        }
        onChange?.(newValue);
      } else {
        onChange?.(selectedValue);
        setIsOpen(false);
      }
    };

    const handleClear = () => {
      onChange?.(multiple ? [] : "");
    };

    const selectedLabels = options
      .filter((option) =>
        Array.isArray(value)
          ? value.includes(option.value)
          : option.value === value,
      )
      .map((option) => option.key);
    const triggerLabel = selectedLabels.length
      ? `${selectedLabels.join(", ")}. Open options`
      : (placeholder ?? "Open options");
    const hasSelection = Boolean(value && value.length > 0);

    let selectedContent: React.ReactNode;
    if (hasSelection && multiple) {
      selectedContent = options
        .filter(
          (option) => Array.isArray(value) && value.includes(option.value),
        )
        .map((option) => (
          <span
            key={option.value}
            className="inline-flex items-center gap-1 rounded-md border py-0.5 pl-2 pr-1 text-xs font-medium text-foreground transition-colors"
          >
            <span aria-hidden="true">{option.key}</span>
            <button
              type="button"
              disabled={disabled}
              aria-label={`Remove ${option.key}`}
              onClick={() => handleSelect(option.value)}
              className="pointer-events-auto flex items-center rounded-sm px-[1px] text-muted-foreground/60 hover:bg-accent hover:text-muted-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
            >
              <XIcon aria-hidden="true" />
            </button>
          </span>
        ));
    } else if (hasSelection) {
      selectedContent = (
        <span aria-hidden="true">
          {options.find((option) => option.value === value)?.key}
        </span>
      );
    } else {
      selectedContent = (
        <span className="mr-auto text-muted-foreground" aria-hidden="true">
          {placeholder}
        </span>
      );
    }

    return (
      <Popover open={isOpen} onOpenChange={setIsOpen}>
        <div
          className={cn(
            "relative flex min-h-[36px] h-full w-full cursor-pointer items-center justify-between rounded-md border border-input bg-background px-3 py-1 text-sm font-medium text-foreground ring-offset-background transition-colors hover:bg-accent hover:text-accent-foreground",
            isOpen && "border-ring",
            disabled && "pointer-events-none opacity-50",
            className,
          )}
        >
          <PopoverTrigger asChild>
            <button
              type="button"
              disabled={disabled}
              aria-label={triggerLabel}
              className="absolute inset-0 rounded-md focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2"
            />
          </PopoverTrigger>
          <div
            className={cn(
              "pointer-events-none relative z-10 items-center gap-1 overflow-hidden text-sm",
              multiple
                ? "flex flex-grow flex-wrap "
                : "inline-flex whitespace-nowrap",
            )}
          >
            {selectedContent}
          </div>
          <div className="pointer-events-none relative z-10 flex items-center self-stretch pl-1 text-muted-foreground/60 hover:text-foreground">
            {hasSelection ? (
              <button
                type="button"
                disabled={disabled}
                aria-label="Clear selection"
                className="pointer-events-auto flex items-center self-stretch focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
                onClick={handleClear}
              >
                <XIcon className="size-4" aria-hidden="true" />
              </button>
            ) : (
              <div className="flex items-center self-stretch" aria-hidden="true">
                <ArrowUpDownIcon className="size-4" />
              </div>
            )}
          </div>
        </div>
        <PopoverContent
          className="w-[var(--radix-popover-trigger-width)] p-0"
          align="start"
        >
          <Command filter={filterOption}>
            {searchable ? (
              <div className="relative">
                <CommandInput
                  value={searchTerm}
                  onValueChange={(e) => setSearchTerm(e)}
                  ref={ref}
                  placeholder={inputPlaceholder ?? "Search..."}
                  className="h-9"
                />
                {searchTerm && (
                  <button
                    type="button"
                    aria-label="Clear search"
                    className="absolute inset-y-0 right-0 flex cursor-pointer items-center pr-3 text-muted-foreground hover:text-foreground"
                    onClick={() => setSearchTerm("")}
                  >
                    <XIcon className="size-4" aria-hidden="true" />
                  </button>
                )}
              </div>
            ) : null}

            <CommandList className="max-h-64">
              <CommandEmpty>
                {emptyPlaceholder ?? "No results found."}
              </CommandEmpty>
              <CommandGroup>
                {options?.map((option) => {
                  const isSelected =
                    Array.isArray(value) && value.includes(option.value);
                  return (
                    <CommandItem
                      key={option.value}
                      value={option.value}
                      keywords={[option.key]}
                      onSelect={() => handleSelect(option.value)}
                    >
                      {multiple && (
                        <div
                          className={cn(
                            "mr-2 flex h-4 w-4 items-center justify-center rounded-sm border border-primary",
                            isSelected
                              ? "bg-primary text-primary-foreground"
                              : "opacity-50 [&_svg]:invisible",
                          )}
                        >
                          <CheckIcon />
                        </div>
                      )}
                      <span>{option.key}</span>
                      {!multiple && option.value === value && (
                        <CheckIcon
                          className={cn(
                            "ml-auto",
                            option.value === value
                              ? "opacity-100"
                              : "opacity-0",
                          )}
                        />
                      )}
                    </CommandItem>
                  );
                })}
              </CommandGroup>
            </CommandList>
          </Command>
        </PopoverContent>
      </Popover>
    );
  },
);

SelectBox.displayName = "SelectBox";

export { SelectBox };
