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

import { useCallback, useEffect, useRef, useState } from "react";
import {
  Activity,
  BarChart3,
  CircleHelp,
  Clock3,
  Database,
  Network,
  RefreshCw,
  Search,
  ShieldCheck,
  Trash2,
} from "lucide-react";

import {
  clearDhcpLease,
  useDhcpConfigSyncTimestamp,
  useDhcpLeases,
  useDhcpPools,
  useDhcpReservations,
  useDhcpSummary,
} from "@/hooks/useDhcpDashboard";
import type {
  DhcpLease,
  DhcpPool,
  DhcpReservation,
  DhcpSummary,
} from "@/types/dhcp.types";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Skeleton } from "@/components/ui/skeleton";
import { Input } from "@/components/ui/input";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import {
  Tooltip,
  TooltipContent,
  TooltipProvider,
  TooltipTrigger,
} from "@/components/ui/tooltip";
import { useToast } from "@/components/ui/use-toast";
import { cn } from "@/lib/utils";

const CONFIG_SYNC_WARNING_AGE_SECONDS = 10 * 60;
const CONFIG_SYNC_ERROR_AGE_SECONDS = 30 * 60;

interface LeaseDashboardProps {
  readonly dhcpUrl: string;
  readonly grafanaUrl?: string;
}

interface MetricProps {
  readonly icon: React.ReactNode;
  readonly label: string;
  readonly value: string;
  readonly detail: string;
  readonly tooltip?: string;
  readonly valueClassName?: string;
}

interface InfiniteScrollStatusProps {
  readonly completeLabel?: string;
  readonly hasMore: boolean;
  readonly hasLoadError?: boolean;
  readonly isValidating: boolean;
  readonly itemCount: number;
  readonly loadErrorMessage?: string;
  readonly onLoadMore: () => Promise<unknown>;
  readonly resourceLabel: string;
  readonly totalCount?: number;
}

interface DashboardHeaderProps {
  readonly configSyncTimestamp?: number | null;
  readonly data: DhcpSummary;
  readonly grafanaUrl?: string;
  readonly isReloading: boolean;
  readonly onReload: () => Promise<void>;
}

interface LeasesTabProps {
  readonly activeSearchQuery: string;
  readonly hasMore: boolean;
  readonly isValidating: boolean;
  readonly leases: DhcpLease[];
  readonly loadMore: () => Promise<unknown>;
  readonly onClearLease: (lease: DhcpLease) => void;
  readonly searchQuery: string;
}

interface ReservationsTabProps {
  readonly activeSearchQuery: string;
  readonly error: unknown;
  readonly hasMore: boolean;
  readonly isLoading: boolean;
  readonly isValidating: boolean;
  readonly loadMore: () => Promise<unknown>;
  readonly reservations: DhcpReservation[];
  readonly searchQuery: string;
  readonly totalCount: number;
}

interface PoolsTabProps {
  readonly activeSearchQuery: string;
  readonly error: unknown;
  readonly hasMore: boolean;
  readonly isLoading: boolean;
  readonly isValidating: boolean;
  readonly loadMore: () => Promise<unknown>;
  readonly pools: DhcpPool[];
  readonly searchQuery: string;
  readonly totalCount: number;
}

interface ClearLeaseDialogProps {
  readonly isClearing: boolean;
  readonly lease: DhcpLease | null;
  readonly onClear: () => Promise<void>;
  readonly onClose: () => void;
}

/** Render one summary metric in the DHCP dashboard header. */
function Metric({
  icon,
  label,
  value,
  detail,
  tooltip,
  valueClassName,
}: MetricProps) {
  return (
    <fieldset
      aria-label={label}
      className="rounded-lg border bg-background/60 p-4"
    >
      <div className="flex items-center justify-between gap-3">
        <div className="flex items-center gap-1.5">
          <p className="text-sm font-medium text-muted-foreground">{label}</p>
          {tooltip && (
            <TooltipProvider delayDuration={0}>
              <Tooltip>
                <TooltipTrigger asChild>
                  <button
                    type="button"
                    aria-label={`About ${label.toLowerCase()}`}
                    className="cursor-help text-muted-foreground hover:text-foreground"
                  >
                    <CircleHelp className="h-3.5 w-3.5" />
                  </button>
                </TooltipTrigger>
                <TooltipContent className="max-w-sm">
                  {tooltip}
                </TooltipContent>
              </Tooltip>
            </TooltipProvider>
          )}
        </div>
        <div className="rounded-md bg-primary/10 p-2 text-primary">{icon}</div>
      </div>
      <p
        className={cn(
          "mt-3 text-2xl font-semibold tracking-tight",
          valueClassName,
        )}
      >
        {value}
      </p>
      <p className="mt-1 text-xs text-muted-foreground">{detail}</p>
    </fieldset>
  );
}

/** Render a consistent empty state for a dashboard tab. */
function EmptyState({ message }: Readonly<{ message: string }>) {
  return (
    <div className="flex min-h-32 items-center justify-center rounded-md border border-dashed p-6 text-sm text-muted-foreground">
      {message}
    </div>
  );
}

/** Render a compact loading state for a lazily fetched dashboard tab. */
function CollectionLoading() {
  return (
    <div className="space-y-3 rounded-md border p-4">
      {[0, 1, 2].map((item) => (
        <Skeleton key={item} className="h-10 w-full" />
      ))}
    </div>
  );
}

/** Format a nullable lease expiry for the operator's locale. */
function formatExpiry(expiresAt?: string | null): string {
  if (!expiresAt) return "Never";
  return new Intl.DateTimeFormat(undefined, {
    dateStyle: "medium",
    timeStyle: "short",
  }).format(new Date(expiresAt));
}

/** Calculate the non-negative age of a Prometheus configuration timestamp. */
function getConfigSyncAgeSeconds(timestamp?: number | null): number | null {
  if (timestamp == null) return null;
  return Math.max(0, Math.floor(Date.now() / 1000 - timestamp));
}

/** Format a configuration sync age as a compact duration. */
function formatConfigSyncAge(ageSeconds: number | null): string {
  if (ageSeconds == null) return "Unknown";
  if (ageSeconds < 60) return `${ageSeconds}s`;
  if (ageSeconds < 3600) return `${Math.floor(ageSeconds / 60)}m`;
  if (ageSeconds < 86400) return `${Math.floor(ageSeconds / 3600)}h`;
  return `${Math.floor(ageSeconds / 86400)}d`;
}

/** Highlight increasingly stale DHCP configuration syncs. */
function getConfigSyncAgeClassName(ageSeconds: number | null): string {
  if (ageSeconds == null) return "";
  if (ageSeconds > CONFIG_SYNC_ERROR_AGE_SECONDS) {
    return "text-red-600 dark:text-red-400";
  }
  if (ageSeconds > CONFIG_SYNC_WARNING_AGE_SECONDS) {
    return "text-yellow-600 dark:text-yellow-400";
  }
  return "";
}

/** Build the pagination status without nested conditionals or templates. */
function getInfiniteScrollSummary({
  completeLabel,
  hasMore,
  itemCount,
  resourceLabel,
  totalCount,
}: Pick<
  InfiniteScrollStatusProps,
  "completeLabel" | "hasMore" | "itemCount" | "resourceLabel" | "totalCount"
>): string {
  const loadedCount = itemCount.toLocaleString();
  if (totalCount !== undefined) {
    return `Loaded ${loadedCount} of ${totalCount.toLocaleString()} ${resourceLabel}`;
  }

  const loadedSummary = `Loaded ${loadedCount} ${resourceLabel}`;
  if (hasMore) return loadedSummary;

  const completionSummary = completeLabel ?? `All ${resourceLabel} loaded`;
  return `${loadedSummary} · ${completionSummary}`;
}

/** Load the next cursor page when the shared collection footer enters view. */
function InfiniteScrollStatus({
  completeLabel,
  hasMore,
  hasLoadError = false,
  isValidating,
  itemCount,
  loadErrorMessage,
  onLoadMore,
  resourceLabel,
  totalCount,
}: InfiniteScrollStatusProps) {
  const sentinelRef = useRef<HTMLOutputElement | null>(null);
  const loadRequestedRef = useRef(false);

  const requestLoadMore = useCallback(() => {
    if (loadRequestedRef.current) return;
    loadRequestedRef.current = true;
    onLoadMore().then(
      () => {
        loadRequestedRef.current = false;
      },
      () => {
        loadRequestedRef.current = true;
      },
    );
  }, [onLoadMore]);

  const requestLoadMoreManually = useCallback(() => {
    loadRequestedRef.current = false;
    requestLoadMore();
  }, [requestLoadMore]);

  useEffect(() => {
    const sentinel = sentinelRef.current;
    if (
      !sentinel ||
      !hasMore ||
      hasLoadError ||
      isValidating ||
      typeof IntersectionObserver === "undefined"
    ) {
      return;
    }

    const observer = new IntersectionObserver(
      ([entry]) => {
        if (!entry.isIntersecting) return;
        requestLoadMore();
      },
      { rootMargin: "200px" },
    );
    observer.observe(sentinel);
    return () => observer.disconnect();
  }, [hasLoadError, hasMore, isValidating, requestLoadMore]);

  if (itemCount === 0) return null;

  const summary = getInfiniteScrollSummary({
    completeLabel,
    hasMore,
    itemCount,
    resourceLabel,
    totalCount,
  });

  return (
    <output
      ref={sentinelRef}
      aria-live="polite"
      className="mt-4 block border-t pt-4 text-center text-sm"
    >
      <span className="block text-xs text-muted-foreground">{summary}</span>
      {loadErrorMessage && (
        <span className="mt-1 block text-xs text-destructive">
          {loadErrorMessage}
        </span>
      )}
      {hasMore && (
        <Button
          className="mt-2"
          variant="ghost"
          size="sm"
          aria-label={`Load more ${resourceLabel}`}
          onClick={requestLoadMoreManually}
          disabled={isValidating}
        >
          {isValidating && <RefreshCw className="mr-2 h-4 w-4 animate-spin" />}
          {isValidating ? "Loading more" : "Load more"}
        </Button>
      )}
    </output>
  );
}

/** Render the dashboard skeleton while its required collections load. */
function DashboardLoading() {
  return (
    <Card data-testid="dhcp-dashboard-loading">
      <CardHeader>
        <Skeleton className="h-7 w-48" />
        <Skeleton className="h-4 w-72" />
      </CardHeader>
      <CardContent className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
        {[0, 1, 2, 3].map((item) => (
          <Skeleton key={item} className="h-28" />
        ))}
      </CardContent>
    </Card>
  );
}

/** Render an actionable error when required dashboard data is unavailable. */
function DashboardError({
  error,
  onRetry,
}: Readonly<{ error: unknown; onRetry: () => Promise<void> }>) {
  let description = "Lease data is unavailable.";
  if (error instanceof Error) description = error.message;

  return (
    <Card className="border-dashed" data-testid="dhcp-dashboard-error">
      <CardHeader className="flex-row items-start justify-between space-y-0">
        <div className="space-y-1.5">
          <CardTitle className="flex items-center gap-2 text-xl">
            <Network className="h-5 w-5" /> DHCP leases
          </CardTitle>
          <CardDescription>{description}</CardDescription>
        </div>
        <Button variant="outline" size="sm" onClick={onRetry}>
          <RefreshCw className="mr-2 h-4 w-4" /> Retry
        </Button>
      </CardHeader>
    </Card>
  );
}

/** Render the dashboard title, reload action, and summary metrics. */
function DashboardHeader({
  configSyncTimestamp,
  data,
  grafanaUrl,
  isReloading,
  onReload,
}: DashboardHeaderProps) {
  let configSyncDetail = "Since last successful config sync";
  if (configSyncTimestamp == null) {
    configSyncDetail = "Config sync metric unavailable";
  }
  const configSyncAgeSeconds = getConfigSyncAgeSeconds(configSyncTimestamp);
  const reloadIconClassName = `mr-2 h-4 w-4${isReloading ? " animate-spin" : ""}`;

  return (
    <CardHeader className="border-b bg-card/70">
      <div className="flex flex-col gap-4 sm:flex-row sm:items-start sm:justify-between">
        <div>
          <CardTitle className="flex items-center gap-2 text-2xl">
            <Network className="h-6 w-6 text-primary" /> DHCP lease activity
          </CardTitle>
          <CardDescription className="mt-2">
            Live address allocations, configured reservations, and address pools.
          </CardDescription>
        </div>
        <div className="flex flex-wrap gap-2">
          {grafanaUrl && (
            <Button variant="outline" size="sm" asChild>
              <a href={grafanaUrl} target="_blank" rel="noreferrer">
                <BarChart3 className="mr-2 h-4 w-4" />
                View Grafana
              </a>
            </Button>
          )}
          <Button
            aria-label="Reload DHCP data"
            title="Re-fetch the latest data shown here. This does not run the background DHCP config sync."
            variant="outline"
            size="sm"
            onClick={onReload}
            disabled={isReloading}
          >
            <RefreshCw className={reloadIconClassName} />
            Reload data
          </Button>
        </div>
      </div>
      <div className="mt-4 grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
        <Metric
          icon={<Activity className="h-4 w-4" />}
          label="Active leases"
          value={data.active_lease_count.toLocaleString()}
          detail="Current active allocations"
        />
        <Metric
          icon={<ShieldCheck className="h-4 w-4" />}
          label="Reservations"
          value={data.reservation_count.toLocaleString()}
          detail="Configured static addresses"
        />
        <Metric
          icon={<Database className="h-4 w-4" />}
          label="Pools"
          value={data.pool_count.toLocaleString()}
          detail="Configured address pools"
        />
        <Metric
          icon={<Clock3 className="h-4 w-4" />}
          label="Config sync age"
          value={formatConfigSyncAge(configSyncAgeSeconds)}
          valueClassName={getConfigSyncAgeClassName(configSyncAgeSeconds)}
          detail={configSyncDetail}
          tooltip="An old config likely means there is an issue with DHCP config generation. Consult the logs for further details."
        />
      </div>
    </CardHeader>
  );
}

/** Render a lease's configured subnet or its removed-subnet marker. */
function LeaseSubnet({ subnet }: Readonly<{ subnet?: string | null }>) {
  if (subnet) return <Badge variant="outline">{subnet}</Badge>;

  return (
    <span
      className="text-sm text-muted-foreground"
      title="This lease's subnet ID is not present in the current DHCP configuration."
    >
      Removed
    </span>
  );
}

/** Render the active lease table and its pagination status. */
function LeasesTab({
  activeSearchQuery,
  hasMore,
  isValidating,
  leases,
  loadMore,
  onClearLease,
  searchQuery,
}: LeasesTabProps) {
  let content: React.ReactNode;
  if (leases.length === 0) {
    const message = activeSearchQuery
      ? `No active leases match “${searchQuery.trim()}”.`
      : "No active leases.";
    content = <EmptyState message={message} />;
  } else {
    content = (
      <Table>
        <TableHeader>
          <TableRow>
            <TableHead>IP address</TableHead>
            <TableHead>Device</TableHead>
            <TableHead>MAC / client ID</TableHead>
            <TableHead>Subnet</TableHead>
            <TableHead>Expires</TableHead>
            <TableHead className="w-14">
              <span className="sr-only">Actions</span>
            </TableHead>
          </TableRow>
        </TableHeader>
        <TableBody>
          {leases.map((lease) => (
            <TableRow key={lease.ip_address}>
              <TableCell className="font-mono font-medium">
                {lease.ip_address}
              </TableCell>
              <TableCell>{lease.hostname || "Unknown device"}</TableCell>
              <TableCell className="font-mono text-xs">
                {lease.hw_address || lease.client_id || lease.duid || "—"}
              </TableCell>
              <TableCell>
                <LeaseSubnet subnet={lease.subnet} />
              </TableCell>
              <TableCell className="whitespace-nowrap">
                {formatExpiry(lease.expires_at)}
              </TableCell>
              <TableCell>
                <Button
                  aria-label={`Clear lease ${lease.ip_address}`}
                  title={`Clear lease ${lease.ip_address}`}
                  variant="ghost"
                  size="icon"
                  onClick={() => onClearLease(lease)}
                >
                  <Trash2 className="h-4 w-4 text-destructive" />
                </Button>
              </TableCell>
            </TableRow>
          ))}
        </TableBody>
      </Table>
    );
  }

  const resourceLabel = activeSearchQuery
    ? "matching active leases"
    : "active leases";
  const completeLabel = activeSearchQuery
    ? "All matches loaded"
    : "All active leases loaded";

  return (
    <TabsContent value="leases" className="mt-4">
      {content}
      <InfiniteScrollStatus
        completeLabel={completeLabel}
        hasMore={hasMore}
        isValidating={isValidating}
        itemCount={leases.length}
        onLoadMore={loadMore}
        resourceLabel={resourceLabel}
      />
    </TabsContent>
  );
}

/** Render the static reservation table and its pagination status. */
function ReservationsTab({
  activeSearchQuery,
  error,
  hasMore,
  isLoading,
  isValidating,
  loadMore,
  reservations,
  searchQuery,
  totalCount,
}: ReservationsTabProps) {
  let content: React.ReactNode;
  if (isLoading) {
    content = <CollectionLoading />;
  } else if (error && reservations.length === 0) {
    content = <EmptyState message="Reservation data is unavailable." />;
  } else if (reservations.length === 0) {
    const message = activeSearchQuery
      ? `No reservations match “${searchQuery.trim()}”.`
      : "No reservations are configured.";
    content = <EmptyState message={message} />;
  } else {
    content = (
      <Table>
        <TableHeader>
          <TableRow>
            <TableHead>IP address</TableHead>
            <TableHead>Hostname</TableHead>
            <TableHead>Identifier type</TableHead>
            <TableHead>Identifier</TableHead>
            <TableHead>Subnet</TableHead>
          </TableRow>
        </TableHeader>
        <TableBody>
          {reservations.map((reservation, index) => (
            <TableRow
              key={`${reservation.ip_address || "reservation"}-${index}`}
            >
              <TableCell className="font-mono font-medium">
                {reservation.ip_address || "—"}
              </TableCell>
              <TableCell>{reservation.hostname || "—"}</TableCell>
              <TableCell>{reservation.identifier_type || "—"}</TableCell>
              <TableCell className="font-mono text-xs">
                {reservation.identifier || "—"}
              </TableCell>
              <TableCell>{reservation.subnet || "Global"}</TableCell>
            </TableRow>
          ))}
        </TableBody>
      </Table>
    );
  }

  const resourceLabel = activeSearchQuery
    ? "matching reservations"
    : "reservations";
  return (
    <TabsContent value="reservations" className="mt-4">
      {content}
      <InfiniteScrollStatus
        hasMore={hasMore}
        hasLoadError={Boolean(error)}
        isValidating={isValidating}
        itemCount={reservations.length}
        loadErrorMessage={
          error ? "Reservation data is unavailable." : undefined
        }
        onLoadMore={loadMore}
        resourceLabel={resourceLabel}
        totalCount={totalCount}
      />
    </TabsContent>
  );
}

/** Render the configured pool table and its pagination status. */
function PoolsTab({
  activeSearchQuery,
  error,
  hasMore,
  isLoading,
  isValidating,
  loadMore,
  pools,
  searchQuery,
  totalCount,
}: PoolsTabProps) {
  let content: React.ReactNode;
  if (isLoading) {
    content = <CollectionLoading />;
  } else if (error && pools.length === 0) {
    content = <EmptyState message="Pool data is unavailable." />;
  } else if (pools.length === 0) {
    const message = activeSearchQuery
      ? `No pools match “${searchQuery.trim()}”.`
      : "No address pools are configured.";
    content = <EmptyState message={message} />;
  } else {
    content = (
      <Table>
        <TableHeader>
          <TableRow>
            <TableHead>Subnet</TableHead>
            <TableHead>Pool</TableHead>
          </TableRow>
        </TableHeader>
        <TableBody>
          {pools.map((pool) => (
            <TableRow key={`${pool.subnet}-${pool.pool}`}>
              <TableCell className="font-medium">{pool.subnet}</TableCell>
              <TableCell className="font-mono text-xs">{pool.pool}</TableCell>
            </TableRow>
          ))}
        </TableBody>
      </Table>
    );
  }

  const resourceLabel = activeSearchQuery ? "matching pools" : "pools";
  return (
    <TabsContent value="pools" className="mt-4">
      {content}
      <InfiniteScrollStatus
        hasMore={hasMore}
        hasLoadError={Boolean(error)}
        isValidating={isValidating}
        itemCount={pools.length}
        loadErrorMessage={error ? "Pool data is unavailable." : undefined}
        onLoadMore={loadMore}
        resourceLabel={resourceLabel}
        totalCount={totalCount}
      />
    </TabsContent>
  );
}

/** Confirm an active lease deletion while preserving in-flight state. */
function ClearLeaseDialog({
  isClearing,
  lease,
  onClear,
  onClose,
}: ClearLeaseDialogProps) {
  return (
    <Dialog
      open={lease !== null}
      onOpenChange={(open) => {
        if (!open && !isClearing) onClose();
      }}
    >
      <DialogContent>
        <DialogHeader>
          <DialogTitle>Clear DHCP lease?</DialogTitle>
          <DialogDescription>
            Delete the lease for{" "}
            <span className="font-mono font-medium text-foreground">
              {lease?.ip_address}
            </span>
            {". The address can be assigned again immediately."}
          </DialogDescription>
        </DialogHeader>
        <DialogFooter>
          <Button variant="outline" onClick={onClose} disabled={isClearing}>
            Cancel
          </Button>
          <Button
            variant="destructive"
            onClick={onClear}
            disabled={isClearing}
          >
            {isClearing && (
              <RefreshCw className="mr-2 h-4 w-4 animate-spin" />
            )}
            Clear lease
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

/** Debounce dashboard searches before they trigger API collection requests. */
function useDebouncedSearchQuery(searchQuery: string): string {
  const [debouncedSearchQuery, setDebouncedSearchQuery] = useState("");

  useEffect(() => {
    const timeout = globalThis.setTimeout(
      () => setDebouncedSearchQuery(searchQuery.trim()),
      300,
    );
    return () => globalThis.clearTimeout(timeout);
  }, [searchQuery]);

  return debouncedSearchQuery;
}

/** Only apply the shared search query to the currently visible collection. */
function getActiveSearchQuery(
  activeTab: string,
  targetTab: string,
  searchQuery: string,
): string {
  if (activeTab !== targetTab) return "";
  return searchQuery;
}

/** Render live DHCP leases, reservations, and configured pools. */
export function LeaseDashboard({ dhcpUrl, grafanaUrl }: LeaseDashboardProps) {
  const { data, error, isLoading, isValidating, mutate } = useDhcpSummary(dhcpUrl);
  const {
    data: configSyncTimestamp,
    isValidating: isConfigSyncAgeValidating,
    mutate: mutateConfigSyncTimestamp,
  } = useDhcpConfigSyncTimestamp(dhcpUrl);
  const { toast } = useToast();
  const [leaseToClear, setLeaseToClear] = useState<DhcpLease | null>(null);
  const [isClearing, setIsClearing] = useState(false);
  const [activeTab, setActiveTab] = useState("leases");
  const [searchQuery, setSearchQuery] = useState("");
  const debouncedSearchQuery = useDebouncedSearchQuery(searchQuery);
  const activeLeaseSearchQuery = getActiveSearchQuery(
    activeTab,
    "leases",
    debouncedSearchQuery,
  );
  const activeReservationSearchQuery = getActiveSearchQuery(
    activeTab,
    "reservations",
    debouncedSearchQuery,
  );
  const activePoolSearchQuery = getActiveSearchQuery(
    activeTab,
    "pools",
    debouncedSearchQuery,
  );
  const {
    error: leaseError,
    isLoading: areLeasesLoading,
    isValidating: areLeasesValidating,
    hasMore: hasMoreLeases,
    leases,
    loadMore: loadMoreLeases,
    mutate: mutateLeases,
  } = useDhcpLeases(dhcpUrl, activeLeaseSearchQuery);
  const {
    error: reservationError,
    hasMore: hasMoreReservations,
    isLoading: areReservationsLoading,
    isValidating: areReservationsValidating,
    loadMore: loadMoreReservations,
    mutate: mutateReservations,
    reservations,
    totalCount: reservationTotalCount,
  } = useDhcpReservations(
    dhcpUrl,
    activeReservationSearchQuery,
    activeTab === "reservations",
  );
  const {
    error: poolError,
    hasMore: hasMorePools,
    isLoading: arePoolsLoading,
    isValidating: arePoolsValidating,
    loadMore: loadMorePools,
    mutate: mutatePools,
    pools,
    totalCount: poolTotalCount,
  } = useDhcpPools(
    dhcpUrl,
    activePoolSearchQuery,
    activeTab === "pools",
  );
  const areCollectionsValidating =
    areLeasesValidating || areReservationsValidating || arePoolsValidating;

  const reloadData = async () => {
    await Promise.allSettled([
      mutate(),
      mutateConfigSyncTimestamp(),
      mutateLeases(),
      mutateReservations(),
      mutatePools(),
    ]);
  };

  const clearLease = async () => {
    if (!leaseToClear) return;
    setIsClearing(true);
    try {
      await clearDhcpLease(dhcpUrl, leaseToClear.ip_address);
      toast({
        title: "Lease cleared",
        description: `${leaseToClear.ip_address} is available for reassignment.`,
      });
      setLeaseToClear(null);
      await Promise.allSettled([mutate(), mutateLeases()]);
    } catch (clearError) {
      let description = "The request failed.";
      if (clearError instanceof Error) description = clearError.message;
      toast({
        title: "Unable to clear lease",
        description,
        variant: "destructive",
      });
    } finally {
      setIsClearing(false);
    }
  };

  if (isLoading || areLeasesLoading) {
    return <DashboardLoading />;
  }

  if (error || leaseError || !data) {
    const dashboardError = error || leaseError;
    return <DashboardError error={dashboardError} onRetry={reloadData} />;
  }

  const isReloading =
    isValidating || isConfigSyncAgeValidating || areCollectionsValidating;
  return (
    <Card className="overflow-hidden" data-testid="dhcp-dashboard">
      <DashboardHeader
        configSyncTimestamp={configSyncTimestamp}
        data={data}
        grafanaUrl={grafanaUrl}
        isReloading={isReloading}
        onReload={reloadData}
      />
      <CardContent className="p-6">
        <div className="relative mb-4 max-w-xl">
          <Search
            className="pointer-events-none absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-muted-foreground"
            aria-hidden="true"
          />
          <Input
            type="search"
            aria-label="Filter displayed DHCP data"
            placeholder="Filter by IP, hostname, MAC address, client ID, or subnet"
            value={searchQuery}
            onChange={(event) => setSearchQuery(event.target.value)}
            className="pl-9"
          />
        </div>
        <Tabs value={activeTab} onValueChange={setActiveTab}>
          <TabsList className="grid w-full grid-cols-3 sm:w-auto">
            <TabsTrigger value="leases">Active leases</TabsTrigger>
            <TabsTrigger value="reservations">Reservations</TabsTrigger>
            <TabsTrigger value="pools">Pools</TabsTrigger>
          </TabsList>

          <LeasesTab
            activeSearchQuery={activeLeaseSearchQuery}
            hasMore={hasMoreLeases}
            isValidating={areLeasesValidating}
            leases={leases}
            loadMore={loadMoreLeases}
            onClearLease={setLeaseToClear}
            searchQuery={searchQuery}
          />

          <ReservationsTab
            activeSearchQuery={activeReservationSearchQuery}
            error={reservationError}
            hasMore={hasMoreReservations}
            isLoading={areReservationsLoading}
            isValidating={areReservationsValidating}
            loadMore={loadMoreReservations}
            reservations={reservations}
            searchQuery={searchQuery}
            totalCount={reservationTotalCount}
          />

          <PoolsTab
            activeSearchQuery={activePoolSearchQuery}
            error={poolError}
            hasMore={hasMorePools}
            isLoading={arePoolsLoading}
            isValidating={arePoolsValidating}
            loadMore={loadMorePools}
            pools={pools}
            searchQuery={searchQuery}
            totalCount={poolTotalCount}
          />
        </Tabs>
      </CardContent>

      <ClearLeaseDialog
        isClearing={isClearing}
        lease={leaseToClear}
        onClear={clearLease}
        onClose={() => setLeaseToClear(null)}
      />
    </Card>
  );
}
