# Photo fallback trial — 18 October 2026

The production gallery has three saved Tarasa URLs for both PA-MLS-1774373
(812 Prospect St) and PA-MLS-1774350 (480 Hamil Rd), but the user's browser
reports load failures. Street View is visibly working. These are separate
image sources; the available evidence does not establish a map regression.

## Implementation

* A separate photo receipt under COMPS_REPORTS/listing_photos binds the property
  ID, MLS, complete address/unit/city/state/ZIP, county and current inventory URL.
* Existing bound Tarasa/Clear Choice URLs are checked first. Coldwell Banker
  Homes and Weichert are bounded public fallback candidates.
* Discovery uses observed seed detail URLs for the two trial MLS IDs or one
  public city directory per provider. Detail pages still require a matching
  primary address and labeled MLS. An address hidden by a provider is rejected.
  No guessed pid, image sequence, version timestamp or transformed image URL.
* Image URLs come only from verified listing JSON-LD, its og:image or matching
  image captions. HTTP 200, image MIME, byte signature, size and hash are
  recorded. This is a file-response check, not proof that another browser can
  display it; the UI separately counts actual load events.
* Robots, redirects, fixed origin/host allowlists, minimum 10-second spacing,
  response byte limits, HTTP/time budgets, and Retry-After apply. A 403/429 or
  unreadable robots response is never converted into permission. Actual
  robots 404/410 means no robots document. Provider blocks are not bypassed.
* Up to three properties share the capped request budget fairly. A source
  receives at most two unsuccessful attempts for an unchanged binding, with
  at least a day between attempts. Budget/time exhaustion does not consume an
  unsuccessful attempt. Fresh verified photos are reused for seven days.
* The browser reads a separate receipt without replacing technical facts or
  financial inputs. It tries unused verified backup URLs and existing source
  candidates if an image fails. Only real load events count as browser success.
* No RentCast, paid API, proxy, cookies, signing secret, cloud/deal-room write,
  downloaded-image hosting or inventory mutation.

## Scope and verification

The PR adds an isolated read-only live trial job for the two requested IDs,
after offline validation. It writes only an expiring Actions artifact and
summary, not production data. The CLI defaults to read-only; --save writes
only photo receipts.

Automatic integration shares the existing completion worker and repository write
concurrency, never a second scheduled worker. An enabled photo fallback reserves
one turn from the configured 3–5-property batch; all technical and photo turns
share the same daily property limit, 13 HTTP requests and 240-second deadline.
Settled technical records can still receive photo completion. The idle gate sleeps
until photo retry/expiry or new saved source evidence. The setting is explicit
and preserved by the UI. Production remains unchanged until this PR is approved
and deployed; browser verification is still needed.

Offline: python -m unittest -v test_listing_photo_fallback;
node test_secondary_sources.cjs; node --test test_photo_fallback_ui.cjs.
The existing source/auth/identity regressions also run in CI.

## Observed candidates, not access promises

Coldwell Banker Homes displayed a matching 812 Prospect St / MLS 1774373
listing with 11 photos. One linked image was retrievable through the research
tool. Its 480 Hamil Rd detail URL redirected to an address-not-provided listing,
which the adapter rejects unless a primary full address is independently
present. Weichert's public Verona directory listed MLS 1774350 with 27 photo
links. Its live robots and detail/image access still require the trial.

Candidate references:
* https://www.coldwellbankerhomes.com/pa/brackenridge/812-prospect-st/pid_73713539/
* https://www.coldwellbankerhomes.com/pa/pittsburgh/480-hamil-rd/pid_73712480/
* https://www.weichert.com/PA/Allegheny/Verona/

## First live trial (GitHub run 39132476005)

812 Prospect St: three verified image files from Coldwell Banker Homes after
stored source images failed. 480 Hamil Rd: zero verified photos; stored images
failed, Coldwell rejected by the full-address identity guard, Weichert directory
produced no candidate. This is a partial result, not a universal image solution.
All offline regressions passed on the initial adapter commit. Later scheduling
and discovery changes require their own successful checks and renewed trial.
