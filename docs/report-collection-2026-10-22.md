# Report collection investigation - 22 October 2026

Two actual user requests for 1822 Fox Way completed successfully as county/profile-only jobs: `e6b7e353-cbc7-47c2-a90d-a1dd38889212` and `018c9166-b24e-48e5-9225-9fa81f6d7d77`. Both have `profile_only: true`, zero reserved/sent RentCast calls and a matched county record. GitHub token dispatch works; workflow success did not mean that financial estimates were requested.

The production report workflow separately disabled RentCast requests. The saved usage ledger was unverified and belonged to September 22-October 22. The owner then supplied an API Dashboard screenshot showing 0 used of 50 and current billing-period day 0 of 31 on October 22. This verifies a new period consistent with the configured billing day 22: October 22-November 22. No token/key values were read or copied.

## Changes prepared

- Default selection and button explicitly request both current-market value and rent estimates. Collection remains an explicit click; opening the report spends no quota.
- Header collection action opens the correct controls, without asking for a GitHub token again when one is saved. A historical snapshot opens the current property for collection instead of modifying the snapshot.
- Profile-only and rejected/no-estimate results update the property facts/receipt but do not automatically create an empty financial report in history.
- Completion messages report exactly which estimates were accepted. A successful county-only job explicitly says that no financial estimates or rehabilitation costs were collected.
- Frontend checks missing/unverified/expired quota and the two-call budget before dispatch. The backend's existing enforcement is preserved. Old counters do not advertise remaining calls for a new cycle.
- The preceding ledger is preserved verbatim in `COMPS_REPORTS/rentcast_usage_history/closed-2026-09-22-2026-10-22.json`. The current ledger records the provider's verified zero use and the evidence basis. Existing request history is retained. No Street View counter is changed.
- Enable provider collection only for the existing explicit report job. Maintenance still disables provider calls. The shared 50-call plan, 45-call stop, reservations before execution, replay prevention and fail-closed cycle checks remain enforced.
- Financial evidence requirements, manual rehabilitation planning, schema/policies and scanner settings are unchanged.

## Validation

17 new offline collection checks, 6 provider-budget checks and 44 existing report/budget/loading groups pass (67 total). They cover exact subject/mode dispatch, one/two remaining slots, stale/unverified budget, profile-only access, duplicate requests, historical read-only behavior, avoiding empty automatic snapshots, accepted rent evidence and gross-yield calculation, truthful completion messages, token reuse, durable holds, HTTP errors, replay protection and archival preservation.

An isolated browser preview using copies of real property data verified that the header opens collection controls with both estimate kinds selected. Clicking collection with an unavailable counter gives a visible quota explanation and sends no live request. Production clients/writes are disabled in this preview.

## Live verification

After deployment, obtain and verify a real value/rent result for the user's current Fox Way property, with no automatic retries or increased quota. Offline tests do not prove that the stored provider key is valid or that source estimates pass property/comparable checks. RentCast does not provide property-condition findings or a rehabilitation quote.
