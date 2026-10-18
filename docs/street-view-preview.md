# Street View preview with no charged Google API path

The requested Street View feature must not create Google API charges. This implementation therefore uses **Maps Embed API `streetview` only**, which Google currently lists as unlimited and available at no charge. It does not use the Static Street View image service, Static metadata, Maps JavaScript, Places or Geocoding APIs. The existing keyless external STREET VIEW button stays available.

## Activation

The public `street-view-preview-config.json` stays disabled with an empty key. The website instead loads `street-view-local-settings.js`: users can enter their restricted browser key once per browser in the property card's “הגדרת Street View במכשיר זה” section. No real credential is included in this implementation or sent to the repository, Supabase, telemetry or application servers. With the local-settings module present, public key configuration is never fetched or used. Without a saved local key, no preview-related county or Google request runs.

The explicit save stores the key in this origin's localStorage. This is browser persistence, not encryption or secret isolation: scripts running on the website can access it, and Google receives it in the iframe URL. Only use a browser key restricted to Maps Embed API and this website origin. The form clears its input after submission and offers deletion, which also removes the current frame. Each device/browser requires separate setup; private browsing or clearing website data removes persistence. Other same-origin applications may access the same storage. No automatic transfer or synchronization of credentials is implemented.

Before enabling:

1. Enable **Maps Embed API** in the intended Google Cloud project. Google still requires a valid key and billing-enabled project even though Embed usage itself is free. Do not create or upgrade a billing account automatically.
2. Restrict the browser key to **Maps Embed API only**. Do not leave it unrestricted or permit Street View Static API/other chargeable APIs. If the key is shared with other applications, use a dedicated key after checking its existing use.
3. Restrict the key to the website origin `https://elic555-ux.github.io/*`. Cross-origin referrers may omit the repository path. Never publish a signing secret, server key or unrestricted key.
4. Verify the restrictions in Google Cloud, then enter the restricted browser key in the website's local settings form and choose “שמור במכשיר זה”. Do not put a real key in repository files. Inspect the actual iframe before live activation is considered verified.

The app configuration is not proof that a key is restricted in Google Cloud. No user key is published by this change. Actual coverage and Cloud configuration require a user-side live check.

## Cost boundary

Google's free allowance for Static Street View is not a zero-spend guarantee. Alert-only budgets do not cap Maps charges, and current Cloud spend-cap eligibility does not include Google Maps. This feature avoids that charged service entirely rather than relying on an allowance or budget alert.

The no-charge statement applies to Maps Embed usage under Google's current published pricing, not every unrelated service in a Google Cloud account. Account-wide zero spending cannot be verified from an API key. No billing account or unrelated service is changed by this code.

## Behavior

After explicit configuration, the shared county locator verifies a point for the open property. Only then does the app create a native Google Street View iframe at that location. Allegheny provides address points; Erie provides parcel centroids. Unknown counties, ambiguous addresses or failed lookups retain the external button.

The iframe remains interactive, including Google's controls, attribution and links. There is no overlay that intercepts Google controls and no image extraction or rehosting. An additional link below the iframe opens a keyless Google Maps Street View URL near the same county point. Users can also use Google's native link for the current panorama. Without a metadata request, the app cannot verify the initial camera-to-building bearing or synchronize changes made inside the cross-origin iframe; the wording describes a street view near the address.

Closing or switching property removes the frame source and ignores stale callbacks. A bounded load timeout prevents an indefinitely pending card. A frame load event alone cannot prove a cross-origin API response is successful, so live key/API restrictions must be verified in Google Cloud and the real iframe.

## Verification

Run `node --test test_street_view_local_settings.cjs test_street_view_preview.cjs test_street_view.cjs test_property_maps.cjs`. Offline tests verify local persistence/removal, denied storage, the free Embed endpoint, absence of a chargeable fallback, refresh after deletion and cancellation on property close. Offline tests do not validate a user's billing settings, API restrictions or Google's live iframe contents.

## Official references

- [Embed usage and billing: unlimited, no charge](https://developers.google.com/maps/documentation/embed/usage-and-billing)
- [Embed setup requirements](https://developers.google.com/maps/documentation/embed/quickstart)
- [Street View iframe parameters](https://developers.google.com/maps/documentation/embed/embedding-map)
- [API key restrictions](https://developers.google.com/maps/api-security-best-practices)
- [Alert-only budgets are not spending caps](https://docs.cloud.google.com/billing/docs/how-to/budgets)
- [Spend-cap eligible services](https://docs.cloud.google.com/billing/docs/how-to/budgets-spend-caps#eligible-services)

