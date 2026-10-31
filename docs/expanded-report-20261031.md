# Concise expanded report — 2026-10-31

Owner-approved scope: factual property data, current value and rent comparisons,
official area data, documented financials, saved manual scenarios, observed price
history, published financing terms, and a short missing-data summary.

Removed from the report: visual condition assessments, automatic renovation scope,
pricing and schedule, automatic ARV, computed neighborhood grades and deal scores.
Collection no longer invokes Gemini or the renovation price book. Existing caches
and the separate investment calculator remain intact.

## Source and scenario boundaries

- Asking price and source rent keep their existing identity/date checks.
- Current value is labeled current value; it never supplies manual sale value.
- Only a matching saved scenario provides manual sale value and renovation inputs.
  Inputs copied from the old renovation plan are excluded from this report.
- Scenario assumptions, documented inputs and default model settings are labeled.
- Missing costs are not zero; missing inputs block dependent calculations.
- Price history shows stored observations, not inferred price continuity or sales.
- Print includes both tabs; no computed overall investment recommendation is shown.

## Official area data

`report_area.py` collects Census ACS 5-year tract estimates plus published margins
of error. It uses parcel geography from the existing Allegheny lookup, not the
listing's city name. Suppressed values remain null. No grade is calculated.

Snapshots are shared per tract and calendar quarter. A new request reuses a current
snapshot; the independent quarterly workflow refreshes known cached tracts. The
client reads newer snapshots for live reports while historical saved reports stay
frozen. Old snapshots are labeled stale when a refresh fails or a quarter changes.

The free `CENSUS_API_KEY` must be stored in GitHub Actions secrets. Census requires
keys for all data queries as of May 2026. No key is sent to the browser or persisted
in source links. Without it, the area section remains missing; other sections work.
Reference: https://www.census.gov/library/video/2026/adrm/requesting-a-census-data-api-key.html

Pennsylvania school and crime sources are provided as reference links. Address-to-
school/agency matching and automated ingestion are NOT implemented by this change.
Neither BRRRR refinancing nor automatic conservative/optimistic scenario generation
is implemented in the existing calculator; this report does not claim otherwise.

## Validation

Offline source/identity/quota regressions and controller simulations are required.
No new paid provider calls, Gemini inference, quota reset, or listing-scan resume.
Local browser preview was blocked by browser URL policy; DOM/controller checks
provide coverage but do not constitute screenshot or live Census validation.
