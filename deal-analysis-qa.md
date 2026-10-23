# Report investment scenarios — 8.23.1

The separate report now provides a rental and flip calculator using accepted, dated financial inputs as its initial source values. Scenario assumptions are editable, visibly labelled, stored separately in `deal_data.investment_scenario`, included in report snapshots and PDF exports, and never promoted to `financial_evidence` or inventory analysis fields.

## Calculation contract

- Rental NOI = annual rent minus operating costs including vacancy. A complete documented budget requires both operating expenses and vacancy. An explicitly selected expense percentage replaces that budget. Missing costs remain missing; zero requires explicit input or existing documented zero evidence.
- Cap rate = NOI / purchase price. Yield on cost = NOI / (purchase + rental rehabilitation + acquisition costs). The rental acquisition ceiling targets yield on cost, not cap rate: NOI / target yield minus rental rehabilitation and acquisition costs.
- Flip net profit = ARV minus purchase, flip rehabilitation, acquisition, holding, financing and sale costs. Net MAO subtracts all of those costs except purchase, plus the explicit profit target.
- The adjustable 70% rule is a separate preliminary ceiling, `ARV × rule percentage − flip rehabilitation`. It is labelled as a rule of thumb, not net profit or a cost-complete recommendation.
- Rental financing is a fixed-payment amortizing purchase loan. Principal = purchase × LTV, including the explicit cash-purchase and zero-interest cases. Cash required includes rental rehabilitation, acquisition and loan setup fees. NOI already includes property tax/insurance/maintenance/vacancy; debt service is deducted once. No refinance or income-tax model is claimed.
- The document preset requires a deliberate click: flip rehabilitation $45/sqft, rental $27/sqft, total operating/vacancy 26%, and $5,000 acquisition cost if not already entered. These are user-reviewable scenario assumptions, not local cost observations. ARV and rent are never synthesized from price, bedroom count or city.

## Persistence and privacy

- Edits recalculate immediately, keep a property/identity-bound browser draft, and debounce cloud saves. Failed cloud saves retain the draft and show an error; a recovered draft requires an explicit save. Latest cloud data is merged before writing; simultaneous scenario edits from another device reject overwrite. Unrelated tracker/source fields are preserved.
- Saved report snapshots remain read-only. Historical reports without a scenario do not gain a newly invented scenario. PDF/history actions flush a pending scenario and refuse to export a known unsaved/invalid draft as though it had been saved.
- Listing gallery reads existing identity-verified original/secondary photo receipts. It does not dispatch collection, spend RentCast calls or introduce browser extensions. Actual image loads, errors and timeouts determine displayed image count. Historical snapshots use their stored media evidence, not live receipts.
- No schema, owner policies, scanner, Street View counters, provider ledgers or paid-provider configuration changes.

## Validation

Offline checks cover missing vs zero, unit/identity mismatch, expense precedence, negative ceilings, amortization, source/assumption separation, output escaping, gallery identity, historical isolation, merge preservation, save conflicts and the prior report/quota lifecycle. Browser QA uses a separate local database copy of the Fox Way record. All entered ARV, price and financing figures in that environment are test assumptions; no production property was modified by QA.

## Remaining roadmap from the supplied document

The calculator/gallery release does not claim to implement automatic interior Vision inspection, a comparable-sales-supported ARV engine, new Census collection, a multi-property opportunity map or LOI generation. Those require separate implementation and evidence. Public listing comparables are still clearly labelled as listings rather than closed-sale proof. Existing source collection and quota controls remain available on demand.
