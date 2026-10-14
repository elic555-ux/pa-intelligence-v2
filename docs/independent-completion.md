# Independent property completion

The central scanner discovers listings and updates their published inventory
state. It does not request MLS detail pages or run the completion worker.
Missing parameters are counted in `needs_details`, independently of discovery
status. Source coverage failures still make the discovery scan partial.

`Automatic MLS Source Completion` runs on its own saved schedule or through
Actions → Run workflow → `batch`. It has no `workflow_run` trigger. Its enabled
switch, interval, batch size, daily budget and retry state are independent of
the discovery master switch. Existing request and retry caps remain in force.
The old `scan` CLI mode is retained for compatibility; no scanner workflow
invokes it.

The worker reads only saved MLS listings in `properties.json` and saved MLS
review candidates marked `needs_details` in
`COMPS_REPORTS/scan_review/records.json`. It does not search listing directories,
create new subjects, or promote review candidates into the active inventory.
Tarasa requests retain the Allegheny, listing-date, URL and identity gates.
Unsupported or incomplete identities are counted as exclusions rather than
guessed. Duplicate identities are excluded before source access.

Bound caches are reused first. New source evidence is saved with provenance.
For review candidates, `record` remains the original observation;
`completed_record` fills missing area, bedrooms, bathrooms and an explicitly
published property type. Documented values are not replaced. The completed
candidate is checked against `filter_context` captured during its original
scan. The separate `completion` receipt distinguishes `ready_for_review`,
`outside_filters`, `needs_details` and `needs_filter_review`. Older candidates
without saved filter context require filter review. Nothing is automatically
added to `properties.json` or the deal room.

The worker writes its own queue and status, source snapshots and completed
review records. It does not write scanner status, financial estimates or
RentCast data. The UI displays its status separately from discovery status.

Verification is offline: `python -m unittest -q test_independent_completion`
tests saved review completion, cache reuse, original filter checks, protected
inventory, identity conflicts, independent activation and discovery status.
