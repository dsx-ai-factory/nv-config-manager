import { PHASE_PRODUCTION_BUILD } from "next/constants.js";

import { buildWorkflowRedirects } from "./src/config/workflow-redirects.mjs";
import legacyWorkflowRedirects from "./src/config/legacy-workflow-redirects.json" with { type: "json" };
import workflowFormIds from "./src/config/workflow-form-ids.json" with { type: "json" };

/**
 * @param {string} phase
 * @returns {import('next').NextConfig}
 */
const nextConfig = (phase) => ({
  output: 'standalone',
  typescript: {
    tsconfigPath:
      phase === PHASE_PRODUCTION_BUILD ? "tsconfig.build.json" : "tsconfig.json",
  },
  rewrites() {
    return [
      {
        source: "/metrics",
        destination: "/api/metrics",
      },
    ];
  },
  // Previously shipped form URLs → /workflows/new/<form_id> (307, query kept).
  redirects() {
    return buildWorkflowRedirects(legacyWorkflowRedirects, workflowFormIds);
  },
});

export default nextConfig;
