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

import { useRuntimeConfig } from "@/config/runtime";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import {
  ExternalLink,
  Loader2,
  Workflow,
  FileText,
  Database,
  Clock,
  Zap,
  Server,
  Globe,
  BarChart3,
  Network,
} from "lucide-react";
import Link from "next/link";

// Force dynamic rendering since we need runtime config
export const dynamic = "force-dynamic";

interface ServiceLink {
  name: string;
  description: string;
  url: string;
  icon: React.ReactNode;
  type: "internal" | "api" | "external";
}

interface ServiceCardProps {
  readonly service: ServiceLink;
}

function ServiceCard({ service }: Readonly<ServiceCardProps>) {
  const isExternal = service.type !== "internal";
  const iconBgClass =
    service.type === "api"
      ? "bg-secondary/50 text-secondary-foreground"
      : "bg-primary/10 text-primary";

  const cardContent = (
    <Card className="cursor-pointer transition-all hover:shadow-lg hover:border-primary h-full">
      <CardHeader>
        <div className="flex items-center gap-3">
          <div className={`rounded-lg p-2 ${iconBgClass}`}>{service.icon}</div>
          <div className="flex-1">
            <div className="flex items-center gap-2">
              <CardTitle className="text-lg">{service.name}</CardTitle>
              {isExternal && (
                <ExternalLink className="h-4 w-4 text-muted-foreground" />
              )}
            </div>
          </div>
        </div>
      </CardHeader>
      <CardContent>
        <CardDescription className="text-sm">
          {service.description}
        </CardDescription>
      </CardContent>
    </Card>
  );

  if (isExternal) {
    return (
      <a
        href={service.url}
        target="_blank"
        rel="noopener noreferrer"
        className="card-link"
      >
        {cardContent}
      </a>
    );
  }

  return (
    <Link href={service.url} className="card-link">
      {cardContent}
    </Link>
  );
}

interface ServiceSectionProps {
  readonly title: string;
  readonly services: ServiceLink[];
}

function ServiceSection({ title, services }: Readonly<ServiceSectionProps>) {
  if (services.length === 0) return null;

  return (
    <section className="space-y-4">
      <h2 className="text-2xl font-semibold tracking-tight">{title}</h2>
      <div className="grid gap-4 md:grid-cols-2 lg:grid-cols-3">
        {services.map((service) => (
          <ServiceCard key={service.name} service={service} />
        ))}
      </div>
    </section>
  );
}

/** Render the Config Manager splash page and service health overview. */
export default function HomePage() {
  const { config, isLoading } = useRuntimeConfig();

  if (isLoading || !config) {
    return (
      <div className="container py-6">
        <div className="flex items-center justify-center min-h-[60vh]">
          <Loader2 className="h-8 w-8 animate-spin" />
          <p className="ml-2">Loading services...</p>
        </div>
      </div>
    );
  }

  const services: ServiceLink[] = [
    // Internal UI Pages
    {
      name: "Workflows",
      description: "View and run Temporal workflows",
      url: "/workflows",
      icon: <Workflow className="h-6 w-6" />,
      type: "internal",
    },
    {
      name: "Device Configs",
      description: "Browse and search device configurations",
      url: "/configs",
      icon: <FileText className="h-6 w-6" />,
      type: "internal",
    },
    {
      name: "DHCP Dashboard",
      description: "Inspect active leases, reservations, and configured pools",
      url: "/dhcp",
      icon: <Network className="h-6 w-6" />,
      type: "internal",
    },
    // External Services
    {
      name: config.dcimDisplayName,
      description: "Network Source of Truth",
      url: config.dcimUrl,
      icon: <Database className="h-6 w-6" />,
      type: "external",
    },
    // Temporal UI is conditionally added below if configured
    // API Documentation
    {
      name: "Workflow API",
      description: "Temporal workflow API",
      url: `${config.workflowApiUrl}/docs`,
      icon: <Server className="h-6 w-6" />,
      type: "api",
    },
    {
      name: "Config Store API",
      description: "Device configuration API",
      url: `${config.configStoreApiUrl}/docs`,
      icon: <Server className="h-6 w-6" />,
      type: "api",
    },
    {
      name: "Render Service API",
      description: "Configuration rendering service",
      url: `${config.renderServiceUrl}/docs`,
      icon: <Server className="h-6 w-6" />,
      type: "api",
    },
    {
      name: "ZTP API",
      description: "Zero Touch Provisioning service",
      url: `${config.ztpUrl}/docs`,
      icon: <Zap className="h-6 w-6" />,
      type: "api",
    },
    {
      name: "DHCP API",
      description: "DHCP configuration service",
      url: `${config.dhcpUrl}/docs`,
      icon: <Globe className="h-6 w-6" />,
      type: "api",
    },
  ];

  // Add Temporal UI if configured (external service - not a NVIDIA Config Manager-built UI)
  if (config.temporalUiUrl) {
    services.push({
      name: "Temporal UI",
      description: "Native Temporal workflow management interface",
      url: config.temporalUiUrl,
      icon: <Clock className="h-6 w-6" />,
      type: "external",
    });
  }

  if (config.grafanaUrl) {
    services.push({
      name: "Grafana Dashboard",
      description:
        "Platform observability dashboards for service health, error rates, and logs",
      url: config.grafanaUrl,
      icon: <BarChart3 className="h-6 w-6" />,
      type: "external",
    });
  }

  const internalServices = services.filter((s) => s.type === "internal");
  const externalServices = services.filter((s) => s.type === "external");
  const apiServices = services.filter((s) => s.type === "api");

  return (
    <div className="container py-8">
      <div className="flex flex-col gap-8">
        {/* Header */}
        <div className="space-y-2">
          <h1 className="text-4xl font-bold tracking-tight">NVIDIA Config Manager</h1>
          <p className="text-xl text-muted-foreground">
            Network automation and configuration management platform
          </p>
        </div>

        <ServiceSection title="User Interfaces" services={internalServices} />
        <ServiceSection title="External Services" services={externalServices} />
        <ServiceSection title="API Documentation" services={apiServices} />
      </div>
    </div>
  );
}
