import { buildWorkflowRedirects } from "./src/config/workflow-redirects.mjs";
import legacyWorkflowRedirects from "./src/config/legacy-workflow-redirects.json" with { type: "json" };

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
  // Previously shipped form URLs → /workflows/new/<ClassName> (307, query kept).
  redirects() {
    return buildWorkflowRedirects(legacyWorkflowRedirects);
  },
};

export default nextConfig;
