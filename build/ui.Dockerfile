# NVIDIA Config Manager UI - Next.js Application
#
# Uses NVIDIA distroless Node.js image for minimal attack surface.
# Multi-stage build: deps/builder stages use the official Node.js image,
# runtime uses NVIDIA distroless.

# =============================================================================
# Dependencies stage - install npm packages
# =============================================================================
FROM docker.io/library/node:24.22.1-bookworm-slim@sha256:55fb5f5a527ea2c5c4a95f963e48ef90aa38c7b346054fdc8ee9957a2b55d205 AS deps

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
FROM nvcr.io/nvidia/distroless/node:24-v4.1.8@sha256:eb352ba3f15ebd89191e86bdf9bdea98fdf6c7d02817ac9e069635c51367d866 AS runner

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
