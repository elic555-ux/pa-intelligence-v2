# Scanner baseline scope and acceptance

The delivery target is a scanner that collects and displays verifiable public information where a supported source is accessible. It does not require every listing to have every technical or financial field. The user requested that this stage end rather than continue searching indefinitely for unavailable fields.

## Included
- Existing listing inventory and geographic/price filters remain the basis of the board.
- Source facts and up to three identity-bound photo references are collected automatically for supported Allegheny/Tarasa listings.
- Three properties per completion group by default, configurable from three to five; approximately quarter-hour background wakes, a persistent fair queue, cache reuse and the retained default 60 daily listing attempts.
- Full MLS/address/source binding, robots checks, HTTP/time ceilings, cooldowns and no overlapping repository writers.
- The property dialog reports the actual verified field and photo-reference counts, explains a saved failed attempt where one is available, and marks the information as potentially partial.
- Fresh partial snapshots are reused for seven days instead of repeatedly querying for fields that were not collected. A failed refresh cannot discard saved evidence.

## Coverage limits
- Additional automatic source discovery for Erie is not included in this baseline. Existing identity-bound ErieMoves/Howard Hanna snapshots can still render.
- Saved roof material is not roof condition. Occupancy, inspection results, repair cost, rent and after-repair value are not inferred from photos, construction dates or nearby listings.
- Source access and published content determine what can be collected. No guarantee is made that every property, or a majority of all requested fields across the inventory, will be available.
- Photo references are not proof that their remote image files remain accessible; the existing source-link fallback handles unavailable images.
- RentCast is never called by the scanner or completion worker. It remains limited to an explicit expanded-report request under the user's separate free allowance. No paid fallback is added.

## Read-only coverage observation
The production queue state saved at 2026-10-14T10:37:55.325534+00:00 contains 384 entries, of which 261 are marked `cache_used`. This is about 68% of that queue, not of all properties or all desired fields. The latest saved batch reports 13–17 cached facts and two or three photo references for its ten successful results. These observations read existing repository files; no new source requests or production writes were made.

## Acceptance before deployment
Offline Python and real-modal/control tests must pass, including partial/missing-source cases, identity-mismatched failures, saved data after failed refresh, Erie without automatic coverage, request/day/time limits and persistent fair progress. The PR must remain unmerged until publication is authorized; the published preview is not replaced by this source-code change.

Baseline code and queue tests at `1f276293fe1114e44e180ee3d7305094d249e1a8` passed 256 Python tests, 32 source UI checks and seven completion-control checks. The additional coverage-display checks are validated in the follow-up commit.
