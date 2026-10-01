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
import {
  FormField,
  FormItem,
  FormControl,
  FormLabel,
  FormDescription,
  FormMessage,
} from "@/components/ui/form"
import {
  Command,
  CommandEmpty,
  CommandGroup,
  CommandInput,
  CommandItem,
} from "@/components/ui/command";
import {
  Popover,
  PopoverContent,
  PopoverTrigger,
} from "@/components/ui/popover";
import { Check, ChevronsUpDown } from "lucide-react";
import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";
import { useState } from "react";
import { UseFormReturn } from "react-hook-form";

export type Option = {
  label: string;
  value: string | number;
}

export type ComboBoxInputProps = {
  // eslint-disable-next-line @typescript-eslint/no-explicit-any -- UseFormReturn<any> is the accepted pattern for generic form components (contravariance prevents UseFormReturn<FieldValues>)
  form: UseFormReturn<any>;
  name: string;
  label: string;
  options: Option[];
  description?: string;
  selectMsg?: string;
  searchMsg?: string;
  notFoundMsg?: string;
  disabled?: boolean;
}

export default function ComboBoxInput({
  form,
  name,
  label, 
  options,
  description,
  disabled,
  selectMsg = "Select item",
  searchMsg = "Search items...",
  notFoundMsg = "No items found.",
}: Readonly<ComboBoxInputProps>) {
  const [open, setOpen] = useState<boolean>(false);
  return (
    <FormField
    control={form.control}
    name={name}
    render={({ field }) => (
    <FormItem className="flex flex-col">
      <FormLabel>{label}</FormLabel>
      <Popover open={open} onOpenChange={setOpen}>
      <PopoverTrigger asChild disabled={disabled}>
        <FormControl>
        <Button
          variant="outline"
          role="combobox"
          className={cn(
            "justify-between",
            !field.value && "text-muted-foreground",
          )}
        >
          {field.value
          ? options.find(
              (item) => item.value === field.value,
          )?.label
          : selectMsg}
          <ChevronsUpDown className="ml-2 h-4 w-4 shrink-0 opacity-50" />
        </Button>
        </FormControl>
      </PopoverTrigger>
      <PopoverContent className="p-0">
        <Command>
        <CommandInput placeholder={searchMsg} />
        <CommandEmpty>{notFoundMsg}</CommandEmpty>
        <CommandGroup>
          {options.map((item) => (
          <CommandItem
            value={item.label}
            key={item.value}
            onSelect={() => {
              form.setValue(name, item.value);
              setOpen(false);
            }}
          >
            <Check
            className={
              cn("mr-2 h-4 w-4", item.value === field.value ? "opacity-100" : "opacity-0")
            }
            />
            {item.label}
          </CommandItem>
          ))}
        </CommandGroup>
        </Command>
      </PopoverContent>
      </Popover>
      {description && <FormDescription>{description}</FormDescription>}
      <FormMessage />
    </FormItem>
    )}
    />
  );
}
