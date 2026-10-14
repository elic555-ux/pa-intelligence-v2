# Small, persistent free-source completion queue

This draft adds quarter-hour source completion without changing production, Supabase or the published preview. The default development profile is three properties per run and 60 listing attempts per America/New_York calendar day. The UI can select three, four or five; the old fixed `batchSize: 10` is read conservatively as three. Legacy uncontrolled manual modes retain their existing hard limits.

The source worker honors the saved size in scan, background and manual completion modes. It retains the 13-HTTP-request and 240-second ceilings, robots checks, at least ten seconds between requests, provider cooldowns and full identity binding. The same persisted daily counter is used by all completion modes.

Queue entries preserve their first queued time and their last actual listing-request turn. When new arrivals and waiting work coexist, the worker serves an oldest waiting entry first, then new arrivals, then fills any spare places from waiting work. Serving the reserved entry first prevents a later blocked request from removing its opportunity. Neither review-required identity mismatches nor future retry times are eligible.

The workflow wakes at UTC minutes 7, 22, 37 and 52; actual work still checks the saved schedule and budget. A delayed wake handles only the latest slot rather than replaying missed slots. Scan-integrated and background work share quarter-hour slots and the existing `pa-repo-write-queue` concurrency group. Schedules execute only on the default branch; this draft cannot start scheduled source requests.

Fresh published snapshots are reused for the existing seven-day TTL even when some facts or photos were not published. Missing snapshots and stale snapshots become eligible; cooldowns remain binding. Existing data is preserved when refresh fails. Missing source fields are not filled with estimates.

96 theoretical wakes yield 288–480 listing opportunities per day before caps. With the retained default cap, at most 60 listing attempts are permitted, and successful snapshots can be fewer. Quarter-hour timing does not resolve Redfin HTTP 405 or Howard Hanna robots HTTP 403.

Automatic new-source coverage remains Allegheny/Tarasa. ErieMoves and Howard Hanna adapters retain their offline-tested identity and photo handling, but automatic Erie discovery is not enabled by this change. A separate controlled access trial is needed before claiming Erie coverage.

Validation must pass the offline Python source/schedule suite, existing 32 source UI checks and the new completion-control checks. Tests cover configured batch sizes, shared daily limits, persistent fair turns under continuous arrivals, zero-request pauses/cooldowns, partial fresh cache reuse, repeated scan/slot guards, delayed wakes, local-midnight accounting and DST transitions. No RentCast calls or live listing requests are part of validation.
