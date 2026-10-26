# Gemini report setup

The owner confirmed Free Tier on 2026-10-25. No credential is stored in this
repository. No live Gemini call was made while implementing the adapter.

1. In Google AI Studio verify that **the project of the chosen key** says Free
   Tier. Do not connect billing for this report. Keys belonging to a paid project
   can incur usage charges even when the same model has a free-tier offering.
2. Repository Settings → Secrets and variables → Actions → Secrets:
   create `GEMINI_API_KEY` with the Gemini key. Never add it to HTML, a GitHub
   variable, an issue, a workflow input, or chat.
3. On the Variables tab create `GEMINI_REPORT_FREE_TIER_ENABLED` = `true`.
   This is the owner's confirmation, not a Google billing API verification.
   Remove it if the key/project/billing status changes.
4. After the new workflow is deployed, collect one report and inspect its source
   receipt and API usage dashboard. Do not claim the connection works before this
   live check succeeds. Installation-check mode never calls Gemini.

The fixed model is `gemini-3.8-flash`; no alternate model, paid fallback, Search,
Maps, external tools, file storage, or automatic retry is used. HTTP 429 stops
analysis, retains other report data, and suppresses another attempt for the same
inputs for one day. A confirmed HTTP503 service failure instead has a15-minute
cooldown, after which another explicit run is allowed; it never auto-retries.
Identical successful findings are cached for 30 days; a daily
local guard permits at most five attempts. This counter is **not** Google's quota
or a provider-side spending cap. Free-tier availability and quotas may change.

Photos and source facts pass the existing gallery's identity/provenance checks.
Image bytes are checked before sending and credentials are supplied only in an
HTTP header to Google's fixed endpoint. The current gallery evidence collectors
cap each source at three photos; the analyzer describes actual photo coverage.

This phase returns photo findings, scope, evidence references and explanations.
The AI cannot return dollar costs. A sourced renovation price book is still
required before totals or a complete budget can be shown. Hidden systems are
not assessed as damaged merely because they are not photographed.

## First connection check from Chrome

The separate `Gemini report connection check` workflow can be used before the new
report UI is published. It only needs the `GEMINI_API_KEY` repository secret.
Open Actions → Gemini report connection check → Run workflow, keep branch `main`
and property `PA-MLS-1779799` (1822 Fox Way), confirm the Free Tier checkbox, and
run once. This explicit confirmation enables only this run; the permanent
repository variable in step 3 above is not needed for the diagnostic.

The check commits the attempt reservation before its sole Gemini request. A
reservation push failure blocks that request. It then preserves results and
prints a safe summary with `connection_verified`, photo/findings counts, and
`rentcast_calls: 0`. It never prints the secret or opens a paid billing flow.
The current report display remains unchanged during this diagnostic phase.

Official references, checked 2026-10-25:

- https://ai.google.dev/gemini-api/docs/pricing
- https://ai.google.dev/gemini-api/docs/billing
- https://ai.google.dev/gemini-api/docs/models/gemini-3.8-flash
- https://ai.google.dev/api/generate-content
