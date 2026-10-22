# Report collection investigation - 22 October 2026

Two actual user requests for 1822 Fox Way completed successfully as county/profile-only jobs: `e6b7e353-cbc7-47c2-a90d-a1dd38889212` and `018c9166-b24e-48e5-9225-9fa81f6d7d77`. Both have `profile_only: true`, zero reserved/sent RentCast calls and a matched county record. GitHub token dispatch works; workflow success did not mean that financial estimates were requested.

The production report workflow separately disables RentCast requests. The saved usage ledger is unverified and belongs to September 22-October 22; it is not a verified quota for the current cycle. Provider API Dashboard usage is still needed before enabling collection or updating the ledger. No token/key values were read or copied.

## Changes prepared

- Default selection and button explicitly request both current-market value and rent estimates. Collection remains an explicit click; opening the report spends no quota.
- Header collection action opens the correct controls, without asking for a GitHub token again when one is saved. A historical snapshot opens the current property for collection instead of modifying the snapshot.
- Profile-only and rejected/no-estimate results update the property facts/receipt but do not automatically create an empty financial report in history.
- Completion messages report exactly which estimates were accepted. A successful county-only job explicitly says that no financial estimates or rehabilitation costs were collected.
- Frontend checks missing/unverified/expired quota and the two-call budget before dispatch. The backend's existing enforcement is preserved. Old counters do not advertise remaining calls for a new cycle.
- No provider activation, usage-counter reset, schema/policy change, scanner change or new API call is included in this draft. Financial evidence requirements and manual rehabilitation planning are unchanged.

## Validation

17 new offline collection checks and 44 existing report/budget/loading groups pass (61 total). They cover exact subject/mode dispatch, one/two remaining slots, stale/unverified budget, profile-only access, duplicate requests, historical read-only behavior, avoiding empty automatic snapshots, accepted rent evidence and gross-yield calculation, truthful completion messages and token reuse.

An isolated browser preview using copies of real property data verified that the header opens collection controls with both estimate kinds selected. Clicking collection with an unavailable counter gives a visible quota explanation and sends no live request. Production clients/writes are disabled in this preview.

## Still required

Verify the provider dashboard's plan, used count and next reset date, reconcile the counter without losing the previous history, enable bounded collection if supported, then obtain and verify a real current-property result. This draft alone does not fill the user's missing financial analysis. RentCast does not provide property-condition findings or a rehabilitation quote.
