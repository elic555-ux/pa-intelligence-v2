# Monthly Street View attempt counter

One server counter is shared by all browsers of the application's existing authorized owner. It warns at 8,000 attempts and reserves at most 9,000 in each calendar month in Asia/Jerusalem. The server chooses the period and thresholds. It stores only random attempt IDs, the month and timestamps; Google keys, addresses and coordinates are not sent to the counter.

The preview resolves its county point first and reserves one slot immediately before assigning the Google Embed iframe URL. A reservation is never refunded: timeouts, failed frames and modal cancellation can consume a slot without displaying an image. A repeated render of the same selection does not create another attempt. Replayed request IDs never authorize another frame. Panning/zooming inside an existing iframe and external keyless Maps links are outside this counter.

The database serializes reservations with a row lock before incrementing. The last reservation at 9,000 is allowed; later reservations are denied. A new month uses a separate row. Tables have RLS and no anonymous/authenticated direct grants. Public RPCs run as the caller and delegate to privileged functions in the non-exposed street_view_private schema. Both privileged functions check the owner identity already configured in index.html; anonymous callers have no schema or function access.

If authentication, the RPC, its schema, secure UUID generation or the counter module is unavailable, the preview does not assign a Google URL. There is no local fallback, automatic retry, user-editable threshold or reset button. Signing out clears counter UI state and cancels the current preview. Delayed responses cannot restore a signed-out session or a previously closed property.

## Validation

Local checks: 9 new counter tests, 25 preview tests (including 4 new integration cases), 2 local-key tests, 32 county resolver tests and 14 existing authentication checks pass. The isolated demo verifies warning at 8,000, final-slot use at 9,000, blocking of a further attempt and offline blocking without connecting to Google or production Supabase.

The dedicated workflow runs real PostgreSQL 17 in an empty local CI service. It applies the actual migration and checks role/owner restrictions, direct table denial, replay denial, eight separate connections competing for the final slot and monthly bucket separation. Its DB test runner refuses non-local hosts or execution without the isolated-test flag.

## Activation

Apply `supabase/migrations/20261020190000_street_view_usage.sql` only to the Supabase project already configured in the site. Access to the correct project and the configured owner's existence have been verified. Backend activation and frontend deployment must be verified before declaring the counter live.

After access is corrected, check existing object names and the configured owner, apply the additive migration, verify permissions and zero/current status, and then deploy the frontend. The application starts counting at activation; it does not reconstruct earlier usage. The new frontend fails closed if deployed before the RPC exists.

This is an application usage cap, not Google's billing counter or a Google Cloud quota. The implementation still uses Maps Embed only. Google documents Embed as no-charge with unlimited requests: https://developers.google.com/maps/documentation/embed/usage-and-billing . No paid service or fallback is enabled. The previously unfinished report remains a separate change.
