# Market Coverage, Pricing Validation, and Cheapest-Offer Claims

**Document status:** Implementation review and development requirements  
**Reviewed:** 2026-11-10  
**Scope:** NSW residential electricity and gas; source-code review only. This document does not assert that current market endpoints or regulatory statements have been independently verified online.

## Executive conclusion

The current prototype **cannot guarantee the cheapest available offer across other providers**. It can rank the offers it discovers and can price under its supported assumptions, but completeness, eligibility, and tariff accuracy are not established. Any customer-facing claim must be limited to **"lowest estimated cost among verified, eligible offers evaluated"**, with an explicit coverage statement.

## Verified implementation observations

| Area | Current behaviour | Risk / consequence |
| --- | --- | --- |
| Default discovery | `EnergyOfferCollector.discover_offers_for_profile(..., include_live_cdr=False)` loads local `data/market_offers/nsw_offers.json`; `EnergySavingsOrchestrator` calls it without enabling live CDR | No automatic market-wide refresh in normal analysis |
| Curated freshness | `max_stale_days=14`; stale offers excluded by default | Comparisons may become empty as fixtures age |
| Live retailer scope | Hardcoded five keys: `agl`, `origin`, `redenergy`, `ovoenergy`, `globird` | Other retailers omitted |
| Live fuel | `fetch_cdr_plans_summary` defaults to `fuel_type="ELECTRICITY"` | Gas not collected by that call unless explicitly requested |
| Pagination | List URL uses `page-size=10`; no page traversal | Only an initial subset can be retrieved |
| Detail retrieval | Attempts v3 plan details; falls back to list metadata if detail is unavailable | Rates may be incomplete |
| Normalisation | Uses first tariff period / single-rate shape, defaults distributor to Ausgrid and state to NSW | Complex tariffs, geography, and eligibility can be misrepresented |
| Missing rates | Defaults to 105 cents/day and 30 cents/kWh | Synthetic prices can contaminate rankings |
| Failure handling | Network and parsing exceptions can return `[]` or `None` | Partial coverage is not visible to users |
| Results | Rankings are relative to collected/matched offers | No market-wide optimality guarantee |

References: `src/energy_agent/offer_collector.py`, `src/energy_agent/orchestrator.py`, `src/energy_agent/pricing_engine.py`, and `data/market_offers/nsw_offers.json`.

## Definitions and customer-facing wording

- **Discovered:** an offer returned by a source, before filtering or pricing.
- **Verified:** offer has a traceable source, successful detail retrieval, retrieval timestamp, and validated rate fields.
- **Eligible:** known to match household location, network, fuel, meter/tariff and relevant restrictions. Unknown eligibility is not equivalent to eligible.
- **Priced:** all mandatory charge components for the household are available and supported by the calculation engine.
- **Comparable:** verified, eligible and priced using consistent usage assumptions and comparison period.
- **Lowest evaluated cost:** minimum estimated cost among comparable offers in a recorded collection run.
- **Market-complete:** an evidence-backed scope claim only after a defined source roster, pagination, coverage and exception audit; it still does not encompass private, negotiated or unpublished plans.

**Permitted wording:** "Lowest estimated annual cost among 142 verified eligible offers evaluated; 3 retailer sources were unavailable." (Numbers illustrative only.)

**Not permitted:** "Guaranteed cheapest electricity in Australia", "100% market coverage" or "cheapest of every provider" without substantiation.

## Proposed collection and validation architecture

1. **Retailer registry:** maintain a versioned roster of expected retailers and source endpoints, including official product reference data; reconcile periodically against authoritative sources. Never infer complete coverage from a hardcoded array.
2. **Collection:** fetch both fuels, all result pages and individual plan details; implement retries, bounded concurrency, timeouts and rate-limit handling. Record run ID, HTTP status, page counts, plan counts, source URL, timestamps and failures.
3. **Raw-data retention:** persist raw responses and content hashes for reproducibility, with appropriate privacy controls; never overwrite the original source record during normalisation.
4. **Strict normalisation:** map geographic restrictions, distributor(s), tariffs, billing units, effective dates, eligibility, discounts and fees. Validate source units and rate structure. Reject missing mandatory charges instead of using estimated defaults.
5. **Household eligibility:** verify postcode/address zone, distributor, meter, solar export, controlled load, time-of-use, demand, gas availability and conditional discounts. Mark unknown conditions as `requires_confirmation`.
6. **Deterministic pricing:** calculate annual and first-year costs separately; support time windows, blocks, demand, feed-in credits, taxes, promotions and eligibility-specific fees where applicable. Maintain reproducible fixtures and expected results.
7. **Coverage report:** show expected, attempted, successful and failed sources; pages fetched; discovered, filtered, unpriceable and ranked offers; age of source data; and reasons for exclusions.
8. **Recommendation gating:** suppress market-wide superlatives if any required source fails, pages remain unfetched, data are stale, or material tariff/eligibility fields are unknown. Show comparison limitations alongside savings.

## Suggested data contracts

`CollectionRun`: `run_id`, `started_at`, `completed_at`, `fuel`, `state`, `expected_sources`, `attempted_sources`, `successful_sources`, `failed_sources`, `pages_fetched`, `pages_expected_or_unknown`, `raw_plan_count`, `normalised_count`, `verified_count`, `priced_count`, `coverage_status`, `errors`.

`OfferValidation`: `offer_id`, `source_id`, `source_url`, `retrieved_at`, `effective_from`, `effective_to`, `detail_status`, `tariff_validation_status`, `eligibility_status`, `pricing_status`, `exclusion_reasons`, `source_hash`.

Use explicit states such as `complete_within_defined_scope`, `partial`, `unknown` rather than a misleading numerical percentage when the denominator is unknown.

## Delivery priorities and acceptance criteria

### P0 — Correctness and transparency

- Remove synthetic tariff defaults from ranked live offers; invalid offers must be excluded with a reason.
- Fix stale fixture-dependent tests by injecting a deterministic clock or frozen test data. Keep production freshness checks enabled.
- Expose a coverage summary in CLI, exports, and Streamlit; display "partial/unknown" when source collection fails.
- Add tests asserting that failed sources, missing details, and unsupported tariff structures never silently yield verified price comparisons.

### P1 — Source completeness

- Implement a maintained, auditable retailer registry and validate official endpoints and schema versions against current published specifications before deployment.
- Traverse every page for each requested fuel, fetch every plan detail, and deduplicate stable plan identifiers.
- Collect electricity and gas separately and reconcile all known distributor/geography restrictions.
- Add integration fixtures for pagination, errors, partial responses and source changes.

### P1 — Price and eligibility fidelity

- Implement strict tariff schema validation and unit tests for single-rate, TOU, stepped gas, controlled load, demand, solar feed-in, discounts and promotions.
- Require user confirmation where bills do not establish eligibility or interval usage.
- Add independent reference examples and regression tests for first-year versus ongoing cost.

### P2 — Operational assurance

- Add scheduled refreshes, stale-data alerts, source health dashboards and manual audit sampling.
- Version comparison runs and attach provenance to every recommendation and exported report.
- Define data retention and consumer privacy requirements before collecting production bills.

## Release gates

1. All automated unit/integration tests pass using deterministic fixtures.
2. No ranked offer contains assumed rates or unsupported tariff structures.
3. All attempted source failures and unknown coverage are surfaced to the user.
4. Full pagination and both-fuel retrieval pass controlled integration tests.
5. Independent validation confirms calculation outputs for representative NSW households.
6. Product wording remains scoped to verified eligible offers evaluated, even if the collection run is complete within its defined source roster.

## External verification required before production

Verify current AER/Consumer Data Standards endpoints, schema versions, source roster, coverage rules, product eligibility requirements, Energy Made Easy comparison disclosures and relevant consumer-protection obligations from primary official sources. Existing business-analysis market statistics and regulatory assertions should be treated as research claims until independently rechecked and dated.


## Implementation status — reliability and coverage branch (November 2026)

The collector now attempts both electricity and gas for each of the 13 configured
CDR retailer slugs when `include_live_cdr=True`. It follows CDR `links.next`
pagination, enforces a page limit and rejects cross-host pagination links.
Failures are retained in `collector.last_coverage_report.failures` rather than
being silently interpreted as zero offers. The report includes attempted sources,
seen, accepted and rejected plan counts, and whether live collection was requested.

**Important limitations:** The registry is not dynamically discovered, and no
complete-market claim is possible. Live gas plans are deliberately rejected
until a verified MJ-based gas tariff normaliser is implemented. Electricity
offers with missing prices, unverified NSW/distributor geography, or unsupported
complex tariff structures are rejected instead of using invented fallback rates.
The live normaliser currently supports only a narrow, verified single-rate shape;
many valid CDR tariffs will remain unpriced. Collection remains opt-in and the
default application still uses curated data with a 14-day freshness policy.
No claim of full NSW market coverage or guaranteed cheapest plan is warranted.

The automated integration test refreshes timestamps **only in a temporary test
fixture**, not in production offer data. This keeps the stale-offer policy intact.
