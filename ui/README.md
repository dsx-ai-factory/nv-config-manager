## Temporal UI

This is a [Next.js](https://nextjs.org/) project bootstrapped with
[`create-next-app`](https://github.com/vercel/next.js/tree/canary/packages/create-next-app).

## Getting Started

First, install the dependencies:

```bash
npm install
```

Second, run the development server:

```bash
npm run dev
```

Open [http://localhost:3000](http://localhost:3000) with your browser to see the
result.

You can start editing the page by modifying `app/page.tsx`. The page
auto-updates as you edit the file.

This project uses
[`next/font`](https://nextjs.org/docs/basic-features/font-optimization) to
automatically optimize and load Inter, a custom Google Font.

## Core Components

The core components of this project utilize [shadcn](https://ui.shadcn.com/) for
building UI elements along with [Radix](https://www.radix-ui.com/).

## Styling

This project uses [TailwindCSS](https://tailwindcss.com/) for styling, providing
a utility-first approach to design.

## Learn More

To learn more about Next.js, take a look at the following resources:

- [Next.js Documentation](https://nextjs.org/docs) - learn about Next.js
  features and API.
- [Learn Next.js](https://nextjs.org/learn) - an interactive Next.js tutorial.

You can check out
[the Next.js GitHub repository](https://github.com/vercel/next.js/) - your
feedback and contributions are welcome!

## Environment Variables

This application uses runtime environment variables (read via `/api/config` route) for configuration.

### For Local Development

1. **First time setup:**
   ```bash
   npm run test:setup
   ```
   This creates `.env.local` from `.env.example`

2. **Edit `.env.local`** with your values:
   - `WORKFLOW_API_URL` - Backend API URL (default: http://localhost:9000)
   - `DCIM_URL` - Selected DCIM provider's public URL
   - `DCIM_PROVIDER` - Provider identifier (defaults to `nautobot`)
   - `DCIM_DISPLAY_NAME` - Optional label shown for the provider in the UI
   - Roles, tenants, and statuses for form dropdowns are fetched from parameter
     endpoints (`/v1/parameter/role`, `/v1/parameter/tenant`, `/v1/parameter/status`)

**Note:** These variables are only needed when running the actual application (`npm run dev`). Tests mock the `/api/config` endpoint and don't require environment variables.

## Testing

### E2E Tests (Playwright)

**Prerequisites:**
```bash
# Install Playwright browsers (first time only)
npx playwright install
```

**Run tests:**
```bash
# Interactive UI mode (recommended for development)
npm run test:e2e

# Headless mode (Chromium only)
npm run test:e2e:chromium

# Watch mode with browser visible
npm run test:e2e:headed

# Debug mode (step through tests)
npm run test:e2e:debug

# CI mode (all browsers, used in GitLab CI)
npm run test:e2e:ci
```

**Note:** Tests use Playwright's route mocking (including the `/api/config` endpoint) and don't require environment variables or the backend API to be running.

## Deploy on K8s
This UI cannot run standalone as-is, you must also be running the NVIDIA Config Manager Temporal Service to serve API content. The makefile handles launching everything together, just be sure to checkout the nv-config-manager-temporal repo at the same level as this checkout (i.e. ../nv-config-manager-temporal must exist), and follow all of the secret setup from its README.

```
make local-update
```
