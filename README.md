# ⚡ AI-Powered Energy Offer Comparison and Savings Agent

An end-to-end modular Python application for Australian residential energy consumers (starting with **New South Wales — Ausgrid, Endeavour Energy, Essential Energy & Jemena Gas**).

## Core Principle

> **AI extracts and explains information; a deterministic `Decimal`-based pricing engine calculates all costs and savings. Recommendations are ranked strictly by user benefit and eligibility, never by referral commission.**

---

## Market Coverage and Cheapest-Offer Claims (Current Limitations)

**Status: prototype; NOT a complete-market or guaranteed-cheapest comparison.** The application ranks only offers it has collected, successfully normalised, and matched to a household. The cheapest ranked offer is therefore **the cheapest among evaluated offers**, not necessarily the cheapest offer available in NSW.

- The orchestrator calls `discover_offers_for_profile(profile)` without enabling live CDR. The collector defaults to `include_live_cdr=False`, so ordinary comparisons use `data/market_offers/nsw_offers.json`.
- Curated offers are excluded when their retrieval timestamps are more than 14 days old (by default); an empty or incomplete comparison may result.
- Opt-in live collection is limited to five hardcoded retailer keys (`agl`, `origin`, `redenergy`, `ovoenergy`, `globird`), requests electricity by default, and requests only the first 10 plans per retailer. It does not establish complete retailer, plan, or gas coverage.
- The live normaliser reads a limited tariff shape and can substitute assumed supply and usage rates (105 cents/day and 30 cents/kWh). **Do not use these inferred rates for customer-facing savings claims.**
- Network matching and pricing do not establish that every publicly available or restricted plan is eligible or correctly priced for a given household.
- API errors may be swallowed and treated as zero offers, rather than surfaced as partial coverage.

**Required production policy:** never label results "market cheapest" or "guaranteed cheapest". Display source, retrieval time, eligibility and pricing status, count of evaluated offers, and incomplete-coverage warnings. Exclude offers with missing or unverified tariff data from monetary ranking. See [`docs/MARKET_COVERAGE_AND_VALIDATION.md`](docs/MARKET_COVERAGE_AND_VALIDATION.md) for the implementation plan and acceptance criteria.

---

## Architecture & Component Mapping

| Component | Module | Responsibility |
| :--- | :--- | :--- |
| **Domain Models** | `src/energy_agent/models.py` | Explicit `Decimal`-based models for `HouseholdProfile`, `ElectricityUsage`, `GasUsage`, `EnergyOffer`, `CostBreakdown`, `OfferComparison`, and `AlertNotification` |
| **Bill Understanding** | `src/energy_agent/bill_parser.py` & `pdf_utils.py` | PDF text extraction (`PyMuPDF` / `pdfplumber` / pure-Python stream fallback), NMI/MIRN PII redaction, **Gemini 3.8 Flash** structured JSON extraction (`google-genai`), deterministic regex fallback, and field validation (`UC-01`, `FR-01`, `FR-02`) |
| **Offer Discovery** | `src/energy_agent/offer_collector.py` | Prototype Australian Energy Regulator (AER) **Consumer Data Right (CDR)** API client (`GET /cds-au/v1/energy/plans`) + curated NSW electricity & gas offers with promotions, staleness checks, and expiry filtering; **coverage is incomplete** (`UC-02`, `FR-03`, `FR-04`) |
| **Deterministic Pricing Engine** | `src/energy_agent/pricing_engine.py` | 100% `Decimal` calculation of Single-Rate, Time-of-Use (Peak/Shoulder/Off-Peak), Controlled Load, Demand tariffs, Tiered Solar Feed-in Tariffs, and Stepped Gas blocks. Separates **Recurring Annual Savings** from **One-Off First-Year Promotions** and flags **Year-2 Loyalty Tax Traps** (`UC-03`, `FR-05`, `FR-10`) |
| **Recommendation Explainer** | `src/energy_agent/explainer.py` | Verifiable cost breakdowns, eligibility checks, referral commission disclosure, and optional Gemini adviser summaries (`UC-04`, `FR-06`) |
| **Daily Monitor & Alerts** | `src/energy_agent/monitor.py` | Daily offer refresh, SHA-256 offer version change tracking, `AUD $100` threshold alerting, and duplicate alert suppression (`UC-05`, `UC-06`, `FR-07`–`FR-09`) |
| **Storage Layer** | `src/energy_agent/storage.py` | SQLite persistence for `household_profiles`, `offer_versions`, `comparison_results`, and `alert_history` |
| **Orchestrator & CLI** | `src/energy_agent/orchestrator.py` & `cli.py` | Coordinates workflows and exports JSON profiles, ranked CSV tables, and interactive HTML reports |
| **Web UI** | `app.py` | Interactive Streamlit dashboard for uploading PDF bills, inspecting assumptions, comparing electricity & gas offers, and running daily checks |

---

## Quick Start

### 1. Run the Command-Line Prototype (Recommended First Milestone)

The core engine runs out-of-the-box with Python 3.9+ (even before installing optional third-party libraries):

```bash
# Run the full end-to-end demonstration (generates 3 sample NSW PDF bills, analyzes them,
# exports JSON/CSV/HTML reports into ./reports, and runs daily monitoring with deduplication)
PYTHONPATH=src python3 -m energy_agent.cli demo

# Analyze a specific PDF energy bill
PYTHONPATH=src python3 -m energy_agent.cli analyze data/sample_bills/nsw_ausgrid_tou_solar_bill.pdf --output-dir reports

# Run the daily market monitor (triggers alerts when estimated Year-1 savings >= AUD $100)
PYTHONPATH=src python3 -m energy_agent.cli monitor --threshold 100.00
```

### 2. Run the Test Suite

```bash
# Using standard library unittest (zero external dependencies required)
PYTHONPATH=src python3 -m unittest discover -s tests -v

# Or with pytest (inside virtual environment)
pytest -v
```

### 3. Optional: Set Up Virtual Environment & Streamlit Web UI

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt

# Launch the interactive Streamlit interface
.venv/bin/streamlit run app.py
```

### 4. Optional: Enable Gemini 3.8 Flash Structured Extraction

Set `GEMINI_API_KEY` in your environment to enable LLM-assisted PDF interpretation and adviser summaries via the official `google-genai` SDK (`client.interactions.create` with `gemini-3.8-flash`):

```bash
export GEMINI_API_KEY="your-api-key"
```
When `GEMINI_API_KEY` is not set, `BillParser` and `RecommendationExplainer` automatically use the deterministic NSW bill parser and template explainer.
