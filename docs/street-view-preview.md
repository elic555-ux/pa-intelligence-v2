# Clickable Street View preview

Property details can show a native Google Street View image. Clicking the image opens the same panorama and viewing direction in Google Maps. The existing STREET VIEW button remains available if imagery cannot load.

## Activation

The feature is disabled in `street-view-preview-config.json`. With `enabled: false` or an empty/invalid key, opening a property does not request Google metadata or an image.

Before enabling it:

1. Use a Google Cloud project with billing and Street View Static API enabled.
2. Create a dedicated browser key, restricted to the website origin `https://elic555-ux.github.io` and to Street View Static API. Browsers may send only the origin as the cross-origin referrer, so a repository-path-only restriction may reject legitimate requests. Configure quotas for the intended budget.
3. Provide the site's public Terms of Use and Privacy Policy links required by Google's policies. This change does not create legal policies on the operator's behalf.
4. Set `enabled` to `true` and supply that restricted browser key as `apiKey`. Browser keys are public by design; never put a signing secret, server key or unrestricted key in this file.
5. Test on the actual website origin, including no-coverage, quota/error fallback, a property switch and a modal close. Google metadata and native imagery have been tested privately with the supplied key; its website restrictions still need configuration before public activation.

Current pricing lists 10,000 static Street View images per month without charge; additional use may be billed. Confirm the current pricing before activation. Metadata requests are not billed but require a key.

## Behavior and limits

Only the property whose details are open requests imagery. After configuration is enabled, the shared address locator verifies a county address point or parcel centroid. Images load directly from Google and are neither downloaded into the repository nor cached by app code. The native image is shown in full so attribution remains visible. The caption is outside the image.

Metadata selects a panorama near the supplied address. Its camera position must be 1–50 meters from the verified county point. The preview calculates the bearing from that camera to the point, and both the native image and its external link use the same panorama ID, derived heading, pitch and field of view. Allegheny provides address points; Erie provides parcel centroids. This improves the camera direction but does not verify the pictured building's identity. Unsupported counties, unavailable or ambiguous county matches, implausible camera distance, missing coverage or request failure keep the preview hidden. The existing button remains available. Switching properties or closing details cancels lookups and discards late results. Each Allegheny JSONP request has a distinct callback to isolate late responses.

Run `node test_street_view_preview.cjs`, `node test_street_view.cjs` and `node test_property_maps.cjs` for offline regressions. These tests do not contact Google and cannot validate billing, referrer restrictions or live coverage.

## Official references

- [Street View Static API setup](https://developers.google.com/maps/documentation/streetview/overview)
- [Metadata](https://developers.google.com/maps/documentation/streetview/metadata)
- [Pricing](https://developers.google.com/maps/billing-and-pricing/pricing)
- [API key security](https://developers.google.com/maps/api-security-best-practices)
- [Attribution and application policy requirements](https://developers.google.com/maps/documentation/streetview/policies)
