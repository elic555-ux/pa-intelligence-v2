# Street View handoff — 17 October 2026

## Resume here
The active task is the inline interactive Street View feature in PR #20, branch `codex/clickable-street-view-preview`. Do not restart the scanner work or the old 14 October research. The user paused for the night and requested preparation for tomorrow. Do not publish or enable the feature while paused.

## Prepared implementation
Reviewed source commit: `87b13730c51cf5a1c77dd335623aacce651de710`.
- `index.html` contains the property-dialog iframe and its external Google Maps link.
- `street-view-preview.js` uses only `https://www.google.com/maps/embed/v1/streetview`.
- `street-view.js` resolves supported county locations and verifies the property identity.
- `street-view-preview-config.json` remains disabled, with provider `maps-embed` and no key.
- `docs/street-view-preview.md` describes activation, limitations and official documentation.
- Existing external STREET VIEW access remains available.
- Source photos, Static Street View, Static metadata, Maps JavaScript, Places and paid fallbacks are not used by this component.
- The imagery is near the verified county point; it does not guarantee the camera faces the property's front. Erie uses parcel centroids.

## Completed checks
Locally ran:
```sh
node --test test_street_view_preview.cjs test_street_view.cjs test_property_maps.cjs
```
All 64 checks passed: 19 component, 32 resolver and 13 property-map checks. They use synthetic credentials and simulated frames; no live keyed Google requests occurred.
The PR's existing validation at the reviewed source commit passed.
No live embedded panorama has been verified. A local Playwright package is available, but its expected Chromium executable is absent. A cloud browser binding was obtained; no property/login test was completed. Do not claim a live screenshot or successful real Google iframe.

## User-completed Google Cloud setup
The user supplied a browser key in an uploaded text file; do not copy its value into this document, chat or logs.
The user's screenshots show:
- Maps Embed API selected as the sole intended API restriction (the user explicitly stated only it remained selected).
- Website restriction `https://elic555-ux.github.io/*`.
- Google warned that removing Street View Static API access could interrupt old usage. The user was instructed to confirm with UPDATE and then said the settings were saved.
The screenshot project is StreetViewProject. No Cloud billing account, service settings or credentials were changed by this agent.
Actual Maps Embed API enablement and the real iframe response still need live verification.

## Exact remaining approval
Automatic approval review rejected creating a GitHub blob containing the supplied key because storing it in a public repository had not been explicitly authorized.
No blob containing the key was created, no branch ref was moved by that attempt, and the key was not published.
The user was asked explicitly whether the restricted browser key may be saved in public `elic555-ux/pa-intelligence-v2/street-view-preview-config.json` and Street View published.
The user then paused for the night; that is not approval. Do not retry via another tool, encoded key, indirect workflow, or other workaround.

## Next authorized sequence
1. Resume with this specific publication question if explicit approval has not yet been supplied; do not ask for the key or setup screenshots again without a new reason.
2. After explicit approval, re-read PR head and preserve concurrent changes. Put only the restricted browser key in the intended browser configuration and enable maps-embed.
3. Keep documentation and PR description accurate about activation; the browser key is visible in client assets and limited to the documented origin/API.
4. Complete required checks on the exact head and preserve inventory, scan settings, Supabase, Deal Room and expanded-report data.
5. Publish the approved feature, verify GitHub Pages deployment, and inspect a real property iframe. Do not trigger a scan or RentCast to test Street View.
6. If login blocks the cloud-browser check, follow browserAuth guidance or request the user's visual verification. Never bypass the application's access controls.
7. Verify only the free Embed service. If Google reports API-disabled/key/billing problems, report the exact response and guide the next setup step; do not enable billing or paid services automatically.

## Cost requirement
The user's rule is no paid services. Google officially lists Maps Embed usage as unlimited and no-charge, while requiring an API key and billing-enabled project. This does not establish zero spend for unrelated Cloud services. Do not use Static, metadata or other chargeable fallbacks. Do not promise a budget alert is a spending cap.
Official references checked on 17 October 2026:
- https://developers.google.com/maps/documentation/embed/usage-and-billing
- https://developers.google.com/maps/documentation/embed/quickstart

## End-of-day state
PR #20 remains draft. Implementation and test files are ready; live activation/publication remains pending the explicit key-storage approval above. Only this secret-free handoff document is added for continuity.
