# Expanded report redesign - 20 October 2026

The overview, financial/comparable sections and rehabilitation report use a light report layout with blue headings. Current-market RentCast value is shown separately from documented after-repair value (ARV). The existing financial evidence rules remain authoritative.

The rehabilitation plan has 24 items in three groups. Each item stores a condition, scope and optional manual cost. Blank costs remain missing; explicit zero is priced; excluded items do not add to the total. The optional reserve is computed on priced items only. The manual plan is saved as `deal_data.rehab_plan` and does not feed the verified investment calculations. Contractor entries and documented totals remain separate.

## Validation

- 17 offline rehabilitation test groups: parsing, rounding, missing/zero/excluded costs, HTML escaping, property binding, read-only state, pending and failed saves, and late responses.
- 14 offline report integration checks: accepted/stale/mismatched market estimates, ARV distinction, merging the latest property record, preserving financial inputs and unrelated server fields, saved-report write rejection, print cleanup, PDF success/failure cleanup and RTL restoration.
- Isolated local browser: edited the example roof cost to $7,000, set reserve to 10%, saved, reloaded and verified $43,658 subtotal + $4,365.80 reserve = $48,023.80. The saved historical fixture retained its original values and exposed zero rehabilitation editors/save buttons.
- Local browser: overview, finance and rehabilitation navigation; narrow and desktop layout checks; no document-level horizontal overflow in observed layouts. Comparison tables and tabs retain their own scrolling.
- Actual PDF downloads were rendered with Poppler for visual review. Fixed a reproducible RTL/narrow-window crop, empty gallery controls/frames, Hebrew word spacing and table header contrast. The final seven-page example contains all three report sections. Export restores the page direction, removes its temporary content and unlocks the controls after success or failure.

## Limits

Browser persistence was tested with the existing isolated preview storage and synthetic/reference data. No production Supabase writes, live RentCast calls or Google key changes were made. Cloud schema, RLS and real authenticated persistence were not revalidated in this pass. Production deployment requires merging the reviewed change and a final smoke check on the live report.

## Empty-report investigation - 21 October 2026

The production page loads real property details. The four existing saved snapshots contain property data; none contains accepted financial evidence. One Saltsburg snapshot contains one matching rental comparable, below the required minimum of three. Missing returns and purchase ceilings are therefore missing inputs, not lost calculated values. Historical snapshots remain immutable.

The production PDF renderer could clip content in narrow RTL windows. The export anchors its capture coordinates, preserves Hebrew word spacing, removes empty upload frames and keeps comparison headings/tables together. The overview now explains exactly which source results and financial inputs are missing. Rendering failure keeps a visible error and disables export; a later supplementary-source failure preserves the populated report with a visible warning. Empty snapshots and mismatched property records cannot enable export.

- 17 rehabilitation groups, 14 integration checks and 13 new loading/error/snapshot checks pass offline (44 total), with no network calls or credentials.
- Authenticated production read: 43 Kenwood Ave displays the property, price and facts. The real saved Saltsburg report displays its historical property and county data. Owner-only RLS remains enabled for pipeline_deals and saved_reports; no schema/policy changes were made.
- An isolated local adapter seeded with copies of the three real properties and four snapshots verified the revised historical report and actual PDF export at desktop and 390px widths. No live database writes or paid research calls were made. Page renders were inspected with Poppler.
- Only report/UI/test/documentation files are included. Photos, Street View, scanner settings, source receipts and live property data are preserved.
