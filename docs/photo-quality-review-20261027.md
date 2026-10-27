# Fox Way photo quality review — 27 October 2026

Property: PA-MLS-1779799, 1822 Fox Way, Pittsburgh, PA 15203.

## Verified limits of the live result

The v10 result processed 19 of 22 verified source links. This proves response coverage, not independent visual accuracy. The 19 Sierra CDN images are blocked by Cloudflare in the review browser. Do not bypass that block through a different network or route. No new inference was performed during this review.

One of the three independently accessible Preferred Realty / MLS gallery images was downloaded using the browser's ordinary media download. Its displayed gallery label is source photo 20; that gallery index is not a v10 model photo ID. Original filename DSC_6323.JPG; SHA256 b9602aa3dc1454106fe18534cfaba474da0ab47dcc48edcc6ed5c4a5107a83e9. A local review copy is verification/fox-way-accessible-front.jpg. The collection date is not a capture date.

## Independent visual observation

The accessible front-elevation image shows displaced or damaged pieces and local gaps in the lower facade cladding, including the area near the planter and below/left of the lower windows. This warrants checking and repairing the visible cladding locally. It does not establish hidden damage, cause, structural condition, material specification, quantity, cost or a need to replace the entire facade. Weathered appearance alone is not the defect.

The full roof surface is not visible. A checklist must mark that surface unassessable rather than claim that the roof is sound or needs replacement. Vegetation between paving joints alone supports optional cleanup, not a structural diagnosis.

This image was not among the 19 bytes accepted in the v10 request. Its observation must not be inserted into that result under an unrelated model photo ID or used to claim a proven false negative in the same image. It is a separate, provenance-bound review sample that demonstrates why the missing links matter. A future verified-input evaluation should test this sample before claiming visual detection quality.

## Code corrections prepared

- v11 requires six exterior component slots, including explicit unassessable results and evidence bound to exterior views. Without exterior photos, only empty unassessable placeholders are accepted and no exterior rows are published.
- A visual observation is retained separately from conservative condition wording. A narrow text safeguard suppresses explicit operational assurances; it is not a complete semantic or visual validator.
- Old reports remain missing exterior detail; no v11 findings are fabricated for the saved v10 report.
- The proposed UI is 9.0.11. These changes require a live provider-schema check and independent image evaluation before being described as validated photo diagnosis.

## Explicit provider trials

The owner authorized continued quality work and background progress. An isolated trial uses branch code, a separate main checkout for durable reservations/results, and no live-report publication. The expected human-review categories are evaluated after inference and are never included in the model input.

Trial01 stopped before provider inference because the ordinary source-image request was unavailable. The independently acquired, checksum-bound bytes are now a review fixture at test_fixtures/fox_way_front_review.jpg. Subsequent trials use that existing file and make no request to either image host. The source remains bound to a matching inventory property and verified listing-photo record.

Trial02 returned HTTP200 but was rejected as invalid_model_text. Trial03 returned HTTP200 but was rejected as invalid_exterior_assessment. Rejected prose was not retained, so the exact offending fields cannot be reconstructed. Safe diagnostics now record known field paths, types/lengths and evidence-reference counts only. Unassessable exterior components may have empty photo references and empty provider text; the application supplies conservative unknown-condition wording. Assessable claims still require nonempty text and exterior photo evidence. This prevents a hidden roof from requiring invented evidence. Trial04 will validate this contract against the same independently reviewed image.

Trial04 returned HTTP200. Safe diagnostics identified empty work-scope fields for doors/windows, gutters/downspouts, steps/railings and two general items. A no-repair, optional-cosmetic or unassessable classification does not need a model-invented work scope: the application already supplies conservative wording for these categories. The validator now accepts blank scope/rationale in these categories, and blank finding only when unassessable. It continues to require explicit nonempty finding, repair scope and rationale for visible_damage; the per-photo visual finding remains mandatory. Trial05 checks this corrected contract. No rejected trial updates the live report.

Trial05 passed the response contract but failed the actual quality check: facade cladding was classified cosmetic_optional rather than visible_damage. The roof correctly remained not_assessable. This is a real category mismatch for the reviewed sample, not a transport or schema error. Do not publish it as a successful diagnostic result.

Trial06 changes only the per-image resolution allocation to MEDIA_RESOLUTION_ULTRA_HIGH, with the same model, thinking level, prompt and exact image bytes. Google's Generate Content media-resolution documentation lists default Gemini3 images at approximately1120tokens and ultra-high at2240tokens per image; high equals the default, so merely specifying high would not be a meaningful experiment. Ultra-high is a per-part v1beta option. Record the setting in results/cache identity. It changes processing detail, not source pixels, and does not add the human expected answer to the prompt. Reference: https://ai.google.dev/gemini-api/docs/generate-content/media-resolution .

Trial06 passed the response contract but still classified the facade as cosmetic_optional; increasing media resolution alone did not fix the reviewed category mismatch. Trial07 changes thinking level from LOW to HIGH, retaining the same model, prompt, source bytes and ultra-high media setting. Record the reasoning setting in results/cache identity. Supported model settings are documented at https://docs.cloud.google.com/gemini-enterprise-agent-platform/models/gemini/3-5-flash-lite . No production publication follows a failed quality check.

Trial07 also passed the contract but missed the facade category. Under the owner's explicit authorization to improve quality while remaining on the verified Free tier, trial08 compares gemini-3.8-flash on the same image/prompt/HIGH/ultra-high settings. The project screenshot showed this model with a free-project RPD limit20; Google's current pricing page lists Standard input/output free of charge on Free tier: https://ai.google.dev/gemini-api/docs/pricing . The comparison is explicit and isolated, uses a two-model allowlist and restores module settings after the trial. It is not an automatic production fallback, billing upgrade or paid-model substitution.
