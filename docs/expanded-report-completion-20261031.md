# Expanded report 10.1

Completes explicit manual BRRRR, break-even and sensitivity calculations without
restoring automatic ARV, renovation inference or a neighborhood/deal score.
Empty scenarios are concise. Inputs are saved only in this browser under the
full property identity and clearly labeled manual. Source refreshes preserve
unsaved edits. Historical reports ignore present local edits and remain frozen.

Net rental calculations now use the same chosen rent as the visible gross data,
plus documented operating and vacancy expenses; no missing cost becomes zero.

The Census source uses a quarterly shared tract cache and published margins of
error, rejects corrupt/future/all-suppressed snapshots, and never persists keys.
The independent official refresh supports an exact property id without paid AVM,
RentCast or Gemini calls. It cannot resume the paused listing scans. Live reports
can request this refresh from the area section; archived reports cannot.

Schools are matched by the county parcel's recorded school district and sourced
from the official Pennsylvania performance workbook. The report lists district
schools and does not claim attendance assignment. Current crime metrics remain
explicitly unavailable: the old Pittsburgh blotter ceased updates in 2023 and
is not used as current evidence. The official state dashboard is linked.

Release checks must include actual official workbook and Census requests in CI,
alongside offline identity, formulas, source-boundary and controller tests.
