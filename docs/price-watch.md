# Bounded price-drop monitoring

MLS discovery uses the user's maximum asking price plus 10%, rounded down to
whole dollars. A $190,000 ceiling therefore queries up to $209,000. The
five-request source budget and saved price-window continuation remain intact;
changing the query ceiling starts a new coverage cycle.

Listings above the visible ceiling and within the watch ceiling are saved only
when all the original non-price filters pass. They are counted separately as
`background_price_watch_rows` and do not enter ordinary accepted-result counts
or the scan's financial-analysis context. Their current published price and
price history use the same inventory merge routine as ordinary listings.
When a subsequent scan observes a drop into the original range, the listing
becomes `within_budget` and keeps its price-drop history.

Ordinary MLS results honor the saved price ceiling even if view filters are
broader. An explicit all-inventory search still exposes saved history and
labels above-budget listings as background watch or above the watch ceiling.
Previously saved listings above the band are not deleted. They are outside new
MLS discovery and outside new parameter-completion requests under the saved
completion profile. Saved snapshots remain available. Completion favors
within-budget properties within each waiting group, retaining the reserved
backlog turn and existing request, retry and daily caps.

The 10% band is a monitoring policy, not a prediction of a seller's discount.
Existing identity guards remain unchanged; this feature does not join separate
MLS numbers or relisted property histories.
