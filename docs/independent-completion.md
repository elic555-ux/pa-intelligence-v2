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

When every eligible subject has a saved successful specification, has exhausted
two unsuccessful source attempts, or needs manual identity review, the worker
records `worker_state: idle`. An empty eligible queue also becomes idle. The
scheduled entry point compares saved subject inputs with the idle fingerprint
before installing dependencies, running tests, contacting sources, rebuilding
the registry or committing data. GitHub still starts a lightweight scheduled
check; the workflow is not disabled, so arrival of new inventory or a saved
review candidate can wake completion automatically on its next configured slot.

Observation timestamps, scan IDs and days on market do not wake an idle worker.
New subjects or changed listing inputs do; existing exhausted attempts remain
exhausted for the same listing binding. Successful specifications no longer
reenter automatic completion solely because their cache has aged. Source retry
delays, cooldowns, daily caps and the user's explicit pause remain in force.
Discovery keeps its own schedule so it can find the new subjects that wake
completion. Completing a source specification does not promise that all required
fields were published, and does not promote a review candidate into inventory.

The idle gate also tracks each review candidate's original filter context and
revalidation flag, changes to relevant ambiguous-identity entries, and the point
at which a future publication date becomes eligible. Unrelated alias changes
and the passage of another day alone do not wake the worker. A previously
completed review can be revalidated from its retained snapshot after cache
expiry only when the saved completion identity, source URL and retrieval time
still match; stale evidence cannot supply a first completion.

Verification is offline: `python -m unittest -q test_independent_completion`
tests saved review completion, cache reuse, original filter checks, protected
inventory, identity conflicts, independent activation and discovery status.
