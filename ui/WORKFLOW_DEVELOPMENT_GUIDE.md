# Workflow Form Development

The workflow launcher uses one generic RJSF renderer. Form-enabled API workflows
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
5. Reference form-scoped option providers with `FormOptionSource`; the browser
   owns option loading, dependency changes, request cancellation, and caching.
6. Add focused backend contract tests and UI interaction coverage for behavior
   that is not already exercised by the shared renderer.

The launcher route is `/workflows/new/<form_id>`, using the explicit lowercase
kebab-case `form_id` advertised by workflow metadata. The entries in
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

The workflow API must include a boolean `has_form` and, for form-enabled API
workflows, an explicit lowercase kebab-case `form_id` in every entry returned
by `GET /v1/workflow/metadata?include=form`. The request without `include=form`
retains the legacy response shape. The UI deliberately does not infer either
value from `input_class` or the workflow class name. A missing, `null`, or
invalid `has_form`, or a missing `form_id` when `has_form` is true, disables
launcher links and asks the operator to upgrade the API. This prevents a newer
UI from constructing `/form` URLs an older API does not provide. `false` means
the form is disabled and `/form` returns 404. `true` means the form contract is
enabled; `/form` can still return 503 with a third-party validation diagnostic.

## Generated API artifacts

When the HTTP response model changes, update the source model and run
`make api-generate`. Do not edit `docs/api-specs/`, generated clients, or Go
bindings directly.
