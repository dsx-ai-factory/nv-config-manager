# NVIDIA Config Manager UI - Next.js Application
#
# Uses NVIDIA distroless Node.js image for minimal attack surface.
# Multi-stage build: deps/builder stages use the official Node.js image,
# runtime uses NVIDIA distroless.

# =============================================================================
# Dependencies stage - install npm packages
# =============================================================================
FROM docker.io/library/node:24-bookworm-slim@sha256:6f7b03f7c2c8e2e784dcf9295400527b9b1270fd37b7e9a7285cf83b6951452d AS deps

WORKDIR /app

# Install dependencies based on the preferred package manager
# Note: Build context is ui/
COPY package.json package-lock.json* ./
RUN npm ci

# =============================================================================
# Builder stage - build the Next.js application
# =============================================================================
FROM deps AS builder
WORKDIR /app
COPY next-env.d.ts next.config.mjs postcss.config.mjs tailwind.config.ts tsconfig.json ./
COPY public/ ./public/
COPY src/ ./src/

# Next.js telemetry disabled
ENV NEXT_TELEMETRY_DISABLED=1

RUN npm run build

# =============================================================================
# Runtime stage - NVIDIA distroless Node.js
# =============================================================================
FROM nvcr.io/nvidia/distroless/node:24-v4.1.4@sha256:1d5ae00993416d7d908c127024dee119e0d0dd8b92618762780f03c7063c8ca1 AS runner

WORKDIR /app

ENV NODE_ENV=production
ENV NEXT_TELEMETRY_DISABLED=1

# Copy public assets
COPY --from=builder --chown=1000:1000 /app/public ./public

# Copy the standalone build output
COPY --from=builder --chown=1000:1000 /app/.next/standalone ./
COPY --from=builder --chown=1000:1000 /app/.next/static ./.next/static

USER nvs

EXPOSE 3000

ENV PORT=3000
ENV HOSTNAME="0.0.0.0"

CMD ["node", "server.js"]
