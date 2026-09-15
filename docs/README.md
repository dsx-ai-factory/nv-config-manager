# NVIDIA Config Manager docs

This directory contains the Fern documentation site for NVIDIA Config Manager.

## Local preview

Run these commands from the repository root:

```sh
make docs-live
```

For one-off checks:

```sh
make openapi-check
make docs-lint
make docs-lint-fern
make docs-preview
```

## Editing

- Content pages live under `docs/**/*.mdx`.
- Navigation is defined in `docs/fern/docs.yml`.
- DCIM integrations follow [Contribute a DCIM Provider](development/contributing-dcim-provider.mdx); keep generic service documentation provider-neutral and put bundled Nautobot behavior in the Nautobot documentation.
- OpenAPI specs are generated into `docs/api-specs/` with `make openapi`.
- Installer TUI screenshots are generated into `docs/assets/images/installer/` with `make docs-screenshots`.
- DSX Air sim TUI screenshots are generated into `docs/assets/images/air-sim/` with `make docs-air-sim-screenshots`.
- Next.js UI screenshots are generated into `docs/assets/images/workflows/` and `docs/assets/images/dhcp/` with `make docs-ui-screenshots`.

## Programmatic UI screenshots

The Next.js UI screenshots use `ui/playwright.docs.config.ts` and the
docs-only specs in `ui/tests/docs-screenshots/`. The specs start the UI, serve
mocked workflow API data, pre-populates workflow forms with stable URL
parameters, captures workflow page states, and writes PNGs to
their matching directories under `docs/assets/images/`.

Live screenshots against `https://nvcm.air` are also possible when the browser
is launched through the DSX Air SOCKS tunnel, but those are less reproducible
because they depend on simulation state, credentials, and workflow timing.

Fern publishing in GitHub Actions uses a repository secret named `FERN_TOKEN`.
