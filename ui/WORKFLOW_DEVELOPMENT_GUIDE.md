# Workflow Form Development

The workflow launcher uses one generic RJSF renderer. API-enabled workflows
declare their form in Python on the workflow input model; do not add a
workflow-specific page or React form component.

The complete contract, supported helpers, field markers, compatibility rules,
and plugin behavior are documented in
[`packages/workflows/README.md`](../packages/workflows/README.md#workflow-forms).

## Add or change a form

1. Keep the workflow input model and execution endpoint backward compatible.
2. Add or update `rjsf_ui_schema` on the input model.
3. Use the helpers in `nv_config_manager_workflows.ui` for dynamic controls:
   `api_options`, `device_field`, `location_field`, and `variant_rows`.
4. Use `ServerOwned`, `FormExcluded`, and `FormSchema` for form-only projection
   behavior. These markers must not change Temporal payload validation.
5. Reference direct parameter API paths with `OptionSource`; the browser owns
   option loading, dependency changes, request cancellation, and caching.
6. Add focused backend contract tests and UI interaction coverage for behavior
   that is not already exercised by the shared renderer.

The launcher route is `/workflows/new/<WorkflowClassName>`. The entries in
`src/config/legacy-workflow-redirects.json` preserve redirects from previously
shipped form URLs; they are compatibility data, not form implementations.

## UI mocks and tests

Mock the form response and every direct option endpoint the declaration uses.
Shared workflow-form data lives under `src/mocks/`, and Playwright request
helpers live under `tests/e2e/shared/`.

Cover the user-visible contract rather than RJSF internals:

- defaults, URL prefill, and dependent option clearing;
- loading, empty, and failed option states;
- field-specific interactions such as device filters and variant rows;
- the exact workflow start payload;
- server validation errors and submission state;
- stale or out-of-order option responses when dependencies change.

The unit test in `tests/unit/workflow-form.test.ts` also verifies that the UI's
workflow form JSON Schema and capability manifest are byte-for-byte identical
to the canonical copies in `packages/workflows`.

## Generated API artifacts

When the HTTP response model changes, update the source model and run
`make api-generate`. Do not edit `docs/api-specs/`, generated clients, or Go
bindings directly.
