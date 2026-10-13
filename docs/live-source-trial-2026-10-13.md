# Limited live source trial — 13 October 2026

Production rollout remains pending. The source collector successfully saved one new identity-matched property snapshot, but the tested properties did not all receive technical details.

The trial used the same MLS CSV reader, primary detail reader and scan-integrated source collector as development commit `aa0e03abec15e148722b5d07cf78ce1409ebd54a`. An independent Howard Hanna probe checked whether the existing Erie adapter could access its public source. Each run was capped at three selected properties and 12 HTTP requests. All inventory, queue and cache writes stayed in temporary directories.

| Property | County | Result |
| --- | --- | --- |
| 43 Kenwood Ave | Allegheny | Primary detail request blocked by Redfin (HTTP 405); no technical snapshot available. |
| 1014 Sadowski Ave | Allegheny | Existing Tarasa snapshot reused: 17 fields and three photo references. No new source request. |
| 271 Kohen St | Allegheny | Missing technical details; previous source wait remained binding. |
| 11 E Amanda Ave | Allegheny | New Tarasa snapshot collected: 11 fields and three photo references, bound to MLS 1768988 and the full address. |
| 917 W 34th St | Erie | No technical snapshot available. Independent Howard Hanna access stopped at robots.txt (HTTP 403). |

Both public Redfin CSV requests succeeded. The single Pittsburgh price window reached the CSV result cap and was marked partial; the trial did not claim complete listing coverage. The Erie window completed. The production scanner's bounded price-window splitting was unchanged.

Across the two runs, eight HTTP requests were made: four CSV requests, one blocked Redfin detail request, two successful Tarasa requests (robots and listing), and one blocked Howard Hanna robots request. New RentCast calls: **0**. Production inventory writes: **0**. Provider blocks were respected; no challenge bypass or paid fallback was used.

The first run tested cached source handling and missing data. The follow-up prioritized uncached listings whose known wait/review state permitted access, and skipped the already-blocked Redfin detail reader. Erie automatic additional-source integration remains incomplete; its independent probe is not counted as a scan-integrated collection success.

A queue issue was identified: `listing_not_available` was treated as a permanent manual-review state even after its saved retry time expired. The development fix retains the saved wait and retries after it expires. `source_identity_mismatch` remains excluded from automatic requests. A regression test covers early suppression, later automatic recovery and inventory/RentCast preservation.

Validation: the earlier candidate passed 219 offline Python tests and 32 source UI checks. The retry fix has its own offline regression check; its result is recorded in the development PR.

Evidence:
- [Initial live trial](https://github.com/elic555-ux/pa-intelligence-v2/actions/runs/38465809311)
- [Uncached-source and Erie follow-up](https://github.com/elic555-ux/pa-intelligence-v2/actions/runs/38466717871)
- [Pending application change](https://github.com/elic555-ux/pa-intelligence-v2/pull/2)

The user's condition for production publication was that the change work fully. The sample did not meet that condition, so the application change is not merged into main.
