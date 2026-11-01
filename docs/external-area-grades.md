# External area grades

Section 4 displays published Niche overall and Crime & Safety letter grades separately. There is no local scoring, averaging, class conversion, or school-grade substitution. Missing grades use an em dash. Grade text remains letters, including the provider's plus/minus suffixes.

The 16 curated source profiles in report-area-grades.js were checked during the October 31 / November 1, 2026 research. Each record includes the source URL, scope, edition, and verification date. These are manual snapshots, not an API integration. No live scraping or paid calls run when opening or refreshing a report. Quarterly official-data refresh does not refresh these external grades; future reviews must verify the source pages and update the snapshot. Entries expire after 400 days and are hidden before their verification date in historical reports.

The overall grade describes livability rather than investment returns. Methodology: https://www.niche.com/places-to-live/rankings/methodology/

Matching uses only the matched county parcel's municipality and county. Postal city and ZIP never select a grade. Pittsburgh ward labels are explicitly recognized. A neighborhood override requires this same parcel's official WPRDC centroid, matching municipality, and an exact curated neighborhood name. Both grades then come from that same profile: missing neighborhood safety is never filled with city safety. Fairview without Township is intentionally unmatched, since its CDP and township are distinct. Girard borough and township retain their separate grades. Unknown subdivisions and unsupported records remain ungraded.

Coverage: Penn Hills, Verona, Pittsburgh, Erie, Millcreek Township, Fairview Township, Harborcreek Township, Wesleyville, Edinboro, Girard Borough, Girard Township, Corry, Wattsburg; Pittsburgh neighborhoods Sheraden and Beechview; Crafton borough. This does not claim coverage of every selected scan location.

Validation: node test_report_area_grades.cjs covers parcel identity, geographic ambiguity, provider provenance, missing safety, dates, and script integration. Existing report checks remain required.
