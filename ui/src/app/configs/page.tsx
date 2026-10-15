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

import { useState, useEffect, useMemo } from "react";
import { Input } from "@/components/ui/input";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { Badge } from "@/components/ui/badge";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { Button } from "@/components/ui/button";
import { Label } from "@/components/ui/label";
import { Switch } from "@/components/ui/switch";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Search, Loader2, ChevronRight, Download, Trash2 } from "lucide-react";
import Link from "next/link";
import { useRuntimeConfig } from "@/config/runtime";
import { searchDevices, fetchDeviceConfigs, deleteDevice } from "@/lib/config-store-api";
import { downloadConfigsAsZip } from "@/lib/download-config";
import { useToast } from "@/components/ui/use-toast";
import { DeviceWithLatestConfig } from "@/types/config-store.types";
import { formatRelativeTime } from "@/lib/utils";

export default function ConfigsPage() {
  const { config, isLoading: configLoading } = useRuntimeConfig();
  const { toast } = useToast();
  const [searchQuery, setSearchQuery] = useState("");
  const [devices, setDevices] = useState<DeviceWithLatestConfig[]>([]);
  const [isLoading, setIsLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [fileType, setFileType] = useState<"intended" | "backup">("intended");
  const [isDownloading, setIsDownloading] = useState(false);
  const [showInactive, setShowInactive] = useState(false);

  const [deleteTarget, setDeleteTarget] = useState<DeviceWithLatestConfig | null>(null);
  const [isDeleting, setIsDeleting] = useState(false);

  const handleDownloadAll = async () => {
    if (!config?.configStoreApiUrl || filteredDevices.length === 0) return;
    setIsDownloading(true);
    try {
      const allConfigs: { filename: string; content: string; deviceName: string }[] = [];
      for (const device of filteredDevices) {
        const configs = await fetchDeviceConfigs(
          config.configStoreApiUrl,
          device.uuid,
          fileType
        );
        for (const c of configs) {
          allConfigs.push({
            filename: c.filename,
            content: c.content,
            deviceName: device.name,
          });
        }
      }
      if (allConfigs.length > 0) {
        await downloadConfigsAsZip(allConfigs);
      } else {
        toast({
          title: "No configs to download",
          description: "No configuration files found for the selected devices.",
          variant: "destructive",
        });
      }
    } catch (err) {
      console.error("Download error:", err);
      toast({
        title: "Download failed",
        description: err instanceof Error ? err.message : "Failed to download configs.",
        variant: "destructive",
      });
    } finally {
      setIsDownloading(false);
    }
  };

  const handleDeleteConfirm = async () => {
    if (!config?.configStoreApiUrl || !deleteTarget) return;
    setIsDeleting(true);
    try {
      const result = await deleteDevice(config.configStoreApiUrl, deleteTarget.uuid);
      toast({
        title: "Device configs deleted",
        description: result.message,
      });
      setDevices((prev) => prev.filter((d) => d.uuid !== deleteTarget.uuid));
    } catch (err) {
      console.error("Delete error:", err);
      toast({
        title: "Delete failed",
        description: err instanceof Error ? err.message : "Failed to delete device configs.",
        variant: "destructive",
      });
    } finally {
      setIsDeleting(false);
      setDeleteTarget(null);
    }
  };

  // Ignore leading/trailing whitespace so a trailing space does not miss matches.
  // The input keeps the raw value so a space between words can still be typed.
  const normalizedSearch = searchQuery.trim();

  // Debounced search effect
  useEffect(() => {
    if (!config?.configStoreApiUrl) return;

    const timeoutId = setTimeout(async () => {
      setIsLoading(true);
      setError(null);

      try {
        const results = await searchDevices(
          config.configStoreApiUrl,
          normalizedSearch,
          100,
          fileType,
          showInactive
        );
        setDevices(results);
      } catch (err) {
        console.error("Search error:", err);
        setError(err instanceof Error ? err.message : "Failed to load devices");
        setDevices([]);
      } finally {
        setIsLoading(false);
      }
    }, 300);

    return () => clearTimeout(timeoutId);
  }, [normalizedSearch, fileType, showInactive, config?.configStoreApiUrl]);

  // Client-side filtering for instant feedback as user types
  const filteredDevices = useMemo(() => {
    if (!normalizedSearch) return devices;
    const query = normalizedSearch.toLowerCase();
    return devices.filter(device => device.name.toLowerCase().includes(query));
  }, [devices, normalizedSearch]);

  if (configLoading) {
    return (
      <div className="container py-6">
        <div className="flex items-center justify-center">
          <Loader2 className="h-6 w-6 animate-spin" />
          <p className="ml-2">Loading configuration...</p>
        </div>
      </div>
    );
  }

  return (
    <div className="container py-6">
      <div className="flex flex-col gap-6">
        <div>
          <h1 className="text-3xl font-bold tracking-tight">Device Configurations</h1>
          <p className="text-muted-foreground mt-2">
            Search and browse device configuration files
          </p>
        </div>

        {/* File Type Tabs */}
        <Tabs value={fileType} onValueChange={(value) => setFileType(value as "intended" | "backup")}>
          <TabsList className="grid w-full max-w-md grid-cols-2">
            <TabsTrigger value="intended">Intended Configs</TabsTrigger>
            <TabsTrigger value="backup">Backup Configs</TabsTrigger>
          </TabsList>

          <TabsContent value={fileType} className="space-y-6">
            {/* Search Bar */}
            <Card>
              <CardHeader>
                <div className="flex items-start justify-between gap-4">
                  <div>
                    <CardTitle>Search Devices</CardTitle>
                    <CardDescription>
                      Search by device name for {fileType} configs
                    </CardDescription>
                  </div>
                  <div className="flex items-center gap-2">
                    <Switch
                      id="show-inactive"
                      checked={showInactive}
                      onCheckedChange={setShowInactive}
                    />
                    <Label htmlFor="show-inactive" className="text-sm whitespace-nowrap cursor-pointer">
                      Show inactive devices
                    </Label>
                  </div>
                </div>
              </CardHeader>
              <CardContent>
                <div className="relative">
                  <Search className="absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-muted-foreground" />
                  <Input
                    placeholder="Start typing to search devices..."
                    value={searchQuery}
                    onChange={(e) => setSearchQuery(e.target.value)}
                    className="pl-10"
                  />
                </div>
              </CardContent>
            </Card>

        {/* Error State */}
        {error && (
          <Card className="border-destructive">
            <CardHeader>
              <CardTitle className="text-destructive">Error</CardTitle>
              <CardDescription>{error}</CardDescription>
            </CardHeader>
          </Card>
        )}

        {/* Devices Table */}
        <Card>
          <CardHeader>
            <div className="flex items-start justify-between gap-4">
              <div>
                <CardTitle>
                  {normalizedSearch ? `Search Results (${filteredDevices.length})` : `All Devices (${devices.length})`}
                </CardTitle>
                <CardDescription>
                  {normalizedSearch 
                    ? `Devices matching "${normalizedSearch}" sorted by most recent update`
                    : "Most recently updated devices"}
                </CardDescription>
              </div>
              {filteredDevices.length > 0 && (
                <Button
                  variant="outline"
                  onClick={handleDownloadAll}
                  disabled={isDownloading}
                >
                  <Download className="mr-2 h-4 w-4" />
                  {isDownloading ? "Preparing..." : "Download All Configs"}
                </Button>
              )}
            </div>
          </CardHeader>
          <CardContent>
            {isLoading && (
              <div className="flex items-center justify-center py-8">
                <Loader2 className="h-6 w-6 animate-spin" />
                <p className="ml-2">Loading devices...</p>
              </div>
            )}
            {!isLoading && filteredDevices.length === 0 && (
              <div className="text-center py-8 text-muted-foreground">
                {normalizedSearch 
                  ? `No devices found matching "${normalizedSearch}"`
                  : "No devices found"}
              </div>
            )}
            {!isLoading && filteredDevices.length > 0 && (
              <div className="rounded-md border">
                <Table>
                  <TableHeader>
                    <TableRow>
                      <TableHead>Device Name</TableHead>
                      <TableHead>Site</TableHead>
                      <TableHead>Last Updated</TableHead>
                      <TableHead>Author</TableHead>
                      <TableHead>Latest Change</TableHead>
                      <TableHead className="w-[50px]">
                        <span className="sr-only">Actions</span>
                      </TableHead>
                    </TableRow>
                  </TableHeader>
                  <TableBody>
                    {filteredDevices.map((device) => (
                      <TableRow
                        key={device.uuid}
                        className={`group cursor-pointer hover:bg-muted/50 ${!device.active ? "opacity-60" : ""}`}
                      >
                        <TableCell>
                          <div className="flex items-center gap-2">
                            <Link 
                              href={`/device/${device.uuid}?file_type=${fileType}`}
                              className="font-medium hover:underline"
                            >
                              {device.name}
                            </Link>
                            {!device.active && (
                              <Badge variant="secondary" className="text-xs">
                                Inactive
                              </Badge>
                            )}
                          </div>
                        </TableCell>
                        <TableCell>
                          <Badge variant="outline">{device.site}</Badge>
                        </TableCell>
                        <TableCell className="text-sm text-muted-foreground">
                          {formatRelativeTime(device.latest_update)}
                        </TableCell>
                        <TableCell className="text-sm">
                          {device.latest_author}
                        </TableCell>
                        <TableCell className="text-sm text-muted-foreground max-w-md truncate">
                          {device.latest_message}
                        </TableCell>
                        <TableCell>
                          <div className="flex items-center gap-1">
                            <Link href={`/device/${device.uuid}?file_type=${fileType}`} className="!border-b-0">
                              <ChevronRight className="h-4 w-4 text-muted-foreground group-hover:text-foreground" />
                            </Link>
                            {!device.active && (
                              <Button
                                variant="ghost"
                                size="icon"
                                className="h-8 w-8 text-destructive hover:text-destructive"
                                onClick={(e) => {
                                  e.preventDefault();
                                  setDeleteTarget(device);
                                }}
                              >
                                <Trash2 className="h-4 w-4" />
                              </Button>
                            )}
                          </div>
                        </TableCell>
                      </TableRow>
                    ))}
                  </TableBody>
                </Table>
              </div>
            )}
          </CardContent>
        </Card>
          </TabsContent>
        </Tabs>
      </div>

      {/* Delete Confirmation Dialog */}
      <Dialog open={deleteTarget !== null} onOpenChange={(open) => { if (!open) setDeleteTarget(null); }}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>Permanently Delete Device Configs?</DialogTitle>
            <DialogDescription>
              This will permanently delete all configuration files and version history for{" "}
              <span className="font-semibold text-foreground">{deleteTarget?.name}</span>.
              This action cannot be undone.
            </DialogDescription>
          </DialogHeader>
          <DialogFooter>
            <Button
              variant="outline"
              onClick={() => setDeleteTarget(null)}
              disabled={isDeleting}
            >
              Cancel
            </Button>
            <Button
              variant="destructive"
              onClick={handleDeleteConfirm}
              disabled={isDeleting}
            >
              {isDeleting ? (
                <>
                  <Loader2 className="mr-2 h-4 w-4 animate-spin" />
                  Deleting...
                </>
              ) : (
                "Delete Permanently"
              )}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  );
}
