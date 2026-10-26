# Gemini report setup

The owner confirmed Free Tier on 2026-10-25 and approved testing an alternate
free Gemini model on 2026-10-26. The live check succeeded that day. No credential
is stored in this repository.

1. In Google AI Studio verify that **the project of the chosen key** says Free
   Tier. Do not connect billing for this report. Keys belonging to a paid project
   can incur usage charges even when the same model has a free-tier offering.
2. Repository Settings → Secrets and variables → Actions → Secrets:
   create `GEMINI_API_KEY` with the Gemini key. Never add it to HTML, a GitHub
   variable, an issue, a workflow input, or chat.
3. The report workflow now records the owner's existing confirmation and enables
   this adapter by default. On the Variables tab set
   `GEMINI_REPORT_FREE_TIER_ENABLED` = `false` to disable it, including if the
   key/project/billing status changes. This is not a Google billing API check.
4. After the new workflow is deployed, collect one report and inspect its source
   receipt and API usage dashboard. Do not claim the connection works before this
   live check succeeds. Installation-check mode never calls Gemini.

The fixed model is `gemini-3.5-flash-lite`; no alternate model, paid fallback, Search,
Maps, external tools, file storage, or automatic retry is used. HTTP 429 stops
analysis, retains other report data, and suppresses another attempt for the same
inputs for one day. A confirmed HTTP503 service failure instead has a15-minute
cooldown, after which another explicit run is allowed; it never auto-retries.
Identical successful findings are cached for 30 days; a daily
local guard permits at most five attempts. This counter is **not** Google's quota
or a provider-side spending cap. Free-tier availability and quotas may change.

Photos and source facts pass the existing gallery's identity/provenance checks.
Image bytes are checked before sending and credentials are supplied only in an
HTTP header to Google's fixed endpoint. The report gallery retains up to 60 verified source photo links; the basic modal
still defaults to three. Gemini remains capped at 12 candidate images and its
existing byte/attempt limits. Gallery collection does not itself call Gemini.

This phase returns photo findings, scope, evidence references and explanations.
The AI cannot return dollar costs. The connected national unit-price catalog
includes source dates and scope exclusions; it is not a Pittsburgh contractor
quote. A priced scope still requires documented quantities. Hidden systems are
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

On2026-10-26 the owner explicitly approved testing another free-tier Gemini
model after repeated503 failures from3.8Flash. The selected3.5Flash-Lite supports
image input and structured output and has free input/output on Google's Free
Tier. This is an explicit model selection, not an automatic fallback chain.

The3.5Flash-Lite live check succeeded on2026-10-26: HTTP200, three verified
images and three validated finding records. Its listing-reported equipment text
is grounded back to exact source values; equipment presence is never described
as proof of condition. Successful cached findings are reused for the subsequent
public-source integration audit, with no additional inference or RentCast call.

The diagnostic now also checks county records, parcel geography, closed sales,
published lender terms and the sourced national unit-price catalog. It preserves
the original rental receipt and its dates while attaching report_analysis.
The shared persistence helper saves all controlled source-cache changes in the
clean Actions checkout, so unrelated unstaged county/cache updates cannot prevent
the durable AI reservation. A failed source connection fails the check; missing
ARV, operating-cost evidence, renovation quantities and neighborhood class remain
explicit data gaps, not invented results. The report UI is deployed separately.

Official references, checked 2026-10-25:

- https://ai.google.dev/gemini-api/docs/pricing
- https://ai.google.dev/gemini-api/docs/billing
- https://ai.google.dev/gemini-api/docs/models/gemini-3.8-flash
- https://ai.google.dev/api/generate-content
