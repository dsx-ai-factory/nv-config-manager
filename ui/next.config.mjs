import { buildWorkflowRedirects } from "./src/config/workflow-redirects.mjs";
import workflowRoutes from "./src/config/workflow-routes.json" with { type: "json" };

/** @type {import('next').NextConfig} */
const nextConfig = {
  output: 'standalone',
  rewrites() {
    return [
      {
        source: "/metrics",
        destination: "/api/metrics",
      },
    ];
  },
  // Legacy form pages of migrated workflows → /workflows/new/<ClassName> (307, query kept).
  redirects() {
    return buildWorkflowRedirects(workflowRoutes);
  },
};

export default nextConfig;
