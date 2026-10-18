# Completion timing audit and heartbeat — 18 October 2026

## Observed production behavior

Both scan_config.json and automatic-listing-sources.yml request quarter-hour
completion. Actual scheduled starts on 18 October were 05:45, 09:56 and 13:55 UTC
(08:45, 12:56 and 16:55 Israel time). The last two complete jobs finished in about
one minute, used three property attempts each and were not waiting for the writer
lock. The saved budget was nine of sixty attempts with 124 technical completions
pending. Eight scheduled completion runs appeared in the preceding 24 hours.

Thus the configured frequency was not the observed frequency. GitHub documents
that scheduled events may be delayed or dropped; this is a possible explanation,
not a confirmed account-specific root cause.

## Change

A separate public-repository-only heartbeat job starts on installation, a manual
request or an hourly best-effort schedule. While active, it checks current main
settings and state every 900 seconds for a bounded 330-minute session. A separate
concurrency group allows only one heartbeat; its waiting never holds the shared
scanner/source writer lock.

Each due tick dispatches only the existing automatic-listing-sources.yml worker,
on main, with mode=automatic. The existing worker still serializes with the
scanner, checks pause, due slot, batch size and daily budget against fresh main
data, applies source/robots/HTTP/time restrictions and persists ordinary results.
A delayed tick is skipped, never replayed as a burst. Original scheduled worker
events remain as a fallback. Automatic dispatches use the cheap schedule gate
instead of bypassing it as manual check/batch requests do.

The heartbeat has contents:read and actions:write through its ephemeral repository
GITHUB_TOKEN. It cannot commit repository data and needs no user password, PAT or
new secret. It has no listing-source, image, RentCast or Supabase requests. API
destinations, repository, branch and dispatched workflow/mode are fixed.
Authorization denial stops the session; transient API failures and Retry-After
are handled without rapid retries. Pause and daily budget are reread each tick.

This is not a guaranteed scheduler service: runner outages, API failures or a gap
between bounded sessions can still delay completion. Verify actual dispatch and
worker times after deployment instead of presenting configured timing as proof.

## Cost and rollback

The repository is public and uses ubuntu-latest, a standard hosted runner, with
no cache or artifact upload. Standard hosted runner use in public repositories is
free according to GitHub's documentation. The heartbeat refuses private,
archived, disabled, foreign or non-main-default repositories, checks that boundary
again during the session and stays below the six-hour hosted-job limit.

Pausing completion through the existing setting prevents source work, including
from the heartbeat. To remove the dispatcher, disable the Completion Heartbeat
workflow and cancel its current job, or revert this PR; the original bounded
worker and its source queue remain usable. No data rollback is required.

## Validation

Offline clock/client tests cover fixed 900-second cadence, no drift or catch-up
bursts, current settings/state, pause, budget and timezone day reset, unsupported
settings, public-repository cost boundary, fixed dispatch scope, token-safe
errors, redirects, bounded responses, session limit and API cooldowns.
The existing full Python and source/map/authentication UI regressions run on the
PR, without live source requests. Production verification must record the first
accepted dispatch, the worker's saved result and a later timed dispatch.

Official references:
- https://docs.github.com/en/actions/reference/workflows-and-actions/events-that-trigger-workflows#schedule
- https://docs.github.com/en/actions/how-tos/write-workflows/choose-when-workflows-run/trigger-a-workflow
- https://docs.github.com/en/actions/reference/workflows-and-actions/workflow-syntax#concurrency
- https://docs.github.com/en/billing/concepts/product-billing/github-actions
- https://docs.github.com/en/actions/reference/limits
