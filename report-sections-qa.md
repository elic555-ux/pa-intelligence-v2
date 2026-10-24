# Numbered interactive report — 8.24.1

Each report has ten stable content IDs and user-facing numbers, independent of the tab containing it. A top-level contents navigation opens the correct existing tab and focuses the selected section. The section registry provides a stable basis for discussing individual sections and choosing export contents in a later task.

| Number | Content ID | Contents |
|---|---|---|
| 01 | property | Property identity, asking price, attributes and observed-data summary |
| 02 | photos | Source and uploaded property images |
| 03 | county | Parcel/county record and comparison to stored property facts |
| 04 | neighborhood | Available neighborhood/area metrics, with missing evidence retained |
| 05 | value | Current-market value, separate documented ARV and sale-listing comparisons |
| 06 | rent | Saved rental input, source/date and rental-listing comparisons |
| 07 | rehab | Rehabilitation planning, inspection evidence and contractor quotes |
| 08 | scenario | Editable rental/flip assumptions, calculations and financing |
| 09 | history | Observed listing-price history |
| 10 | sources | Evidence, missing-data status, dates and explicit collection controls |

The source value cards now belong to section 05. Property photographs, county facts, area metrics and price history have distinct destinations. The price-gap placeholder is hidden on the interactive overview; actual scenario gaps remain in section 08. Source gaps and evidence editors are consolidated under section 10. The rent section exposes its actual saved rent value without requiring users to open the scenario assumption editor.

Existing nodes are moved rather than cloned or replaced, retaining all input IDs, event handlers, unsaved drafts and read-only behavior. Repeated source refreshes are idempotent. Numbered URL fragments can be opened directly. Plain click navigation does not call providers or write cloud data. Modified link clicks remain available for opening a section in another tab.

This is the first numbered working structure. It does not supply a new neighborhood dataset, local cost book, Vision findings, additional source images, appraisals or unknown financial values. Those remain separate content work. The user explicitly deferred PDF design/selection; the PDF serialization code is unchanged, and new navigation/heading decorations are marked as interactive-only.

Validation covers catalog identity, real HTML targets, heading/link uniqueness, actual-node preservation, repeated mounts, data refresh without resetting an offer, missing evidence, navigation/focus, initial URL anchors and the existing report/calculation/persistence/quota regression checks. Browser testing uses an isolated local copy of the existing Fox Way record, without production writes or provider dispatch.
