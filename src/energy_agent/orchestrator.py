"""
Agent Orchestrator Module (Section 7 Architecture & Recommended First Milestone).

Coordinates:
- PDF bill parsing and validation (UC-01)
- Market offer discovery (UC-02)
- Deterministic pricing and savings calculation (UC-03)
- Recommendation ranking and explanation (UC-04)
- Daily market monitoring and deduplicated alerting (UC-05, UC-06)
- Exporting validated JSON profiles, ranked CSV tables, and interactive HTML reports.
"""

from __future__ import annotations

import csv
import html
import json
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional, Union

from .bill_parser import BillParser
from .explainer import RecommendationExplainer
from .models import (
    DualFuelComparison,
    FuelType,
    HouseholdProfile,
    OfferComparison,
)
from .monitor import MarketCheckSummary, MarketMonitor
from .offer_collector import OfferCollector, MarketCoverageReport
from .pricing_engine import PricingEngine
from .storage import StorageRepository


@dataclass
class AnalysisResult:
    """Complete result bundle returned by the Agent Orchestrator for a household bill."""

    profile: HouseholdProfile
    electricity_comparisons: List[OfferComparison]
    gas_comparisons: List[OfferComparison]
    dual_fuel_comparison: Optional[DualFuelComparison]
    market_coverage: Optional[MarketCoverageReport] = None


class EnergySavingsOrchestrator:
    """Single-agent orchestrator coordinating all specialised components."""

    def __init__(
        self,
        db_path: Optional[Union[str, Path]] = None,
        offers_path: Optional[Union[str, Path]] = None,
        use_llm: bool = True,
    ) -> None:
        self.pricing_engine = PricingEngine()
        self.bill_parser = BillParser(
            use_llm=use_llm, pricing_engine=self.pricing_engine
        )
        self.collector = OfferCollector(offers_path=offers_path)
        self.explainer = RecommendationExplainer(use_llm=use_llm)
        self.storage = StorageRepository(db_path=db_path)
        self.monitor = MarketMonitor(
            storage=self.storage,
            collector=self.collector,
            pricing_engine=self.pricing_engine,
            explainer=self.explainer,
        )

    def analyze_pdf_bill(
        self,
        pdf_path: Union[str, Path],
        household_id: Optional[str] = None,
        rank_by_recurring: bool = False,
        persist: bool = True,
        include_live_cdr: bool = False,
    ) -> AnalysisResult:
        """Parse a PDF energy bill, validate profile, discover offers, and rank savings."""
        profile = self.bill_parser.parse_pdf_bill(
            pdf_path=pdf_path, household_id=household_id
        )
        return self.analyze_profile(
            profile=profile,
            rank_by_recurring=rank_by_recurring,
            persist=persist,
            include_live_cdr=include_live_cdr,
        )

    def analyze_profile(
        self,
        profile: HouseholdProfile,
        rank_by_recurring: bool = False,
        persist: bool = True,
    ) -> AnalysisResult:
        """Run full validation, offer discovery, pricing, and explanation for a HouseholdProfile."""
        self.bill_parser.validate_profile(profile)
        offers = self.collector.discover_offers_for_profile(
            profile, include_live_cdr=include_live_cdr
        )

        elec_comparisons: List[OfferComparison] = []
        gas_comparisons: List[OfferComparison] = []

        if profile.electricity_usage and profile.current_electricity_plan:
            elec_comparisons = self.pricing_engine.rank_offers(
                profile,
                offers,
                fuel_type=FuelType.ELECTRICITY,
                rank_by_recurring=rank_by_recurring,
            )
            self.explainer.populate_explanations(profile, elec_comparisons)

        if profile.gas_usage and profile.current_gas_plan:
            gas_comparisons = self.pricing_engine.rank_offers(
                profile,
                offers,
                fuel_type=FuelType.GAS,
                rank_by_recurring=rank_by_recurring,
            )
            self.explainer.populate_explanations(profile, gas_comparisons)

        dual_fuel = None
        if elec_comparisons and gas_comparisons:
            dual_fuel = self.pricing_engine.compare_dual_fuel(profile, offers)

        if persist:
            self.storage.save_household_profile(profile)
            for o in offers:
                self.storage.record_offer_version(o)
            if elec_comparisons:
                self.storage.save_comparison_run(
                    run_id=f"run-elec-{uuid.uuid4().hex[:8]}",
                    household_id=profile.household_id,
                    fuel_type=FuelType.ELECTRICITY.value,
                    comparisons=elec_comparisons,
                )
            if gas_comparisons:
                self.storage.save_comparison_run(
                    run_id=f"run-gas-{uuid.uuid4().hex[:8]}",
                    household_id=profile.household_id,
                    fuel_type=FuelType.GAS.value,
                    comparisons=gas_comparisons,
                )

        return AnalysisResult(
            profile=profile,
            electricity_comparisons=elec_comparisons,
            gas_comparisons=gas_comparisons,
            dual_fuel_comparison=dual_fuel,
            market_coverage=self.collector.last_coverage_report,
        )

    def run_daily_monitoring(self) -> MarketCheckSummary:
        """Execute daily market monitoring and threshold alerting across stored profiles (UC-05)."""
        return self.monitor.run_daily_check()

    @staticmethod
    def export_profile_json(
        profile: HouseholdProfile, output_path: Union[str, Path]
    ) -> Path:
        """Write validated HouseholdProfile to a formatted JSON file."""
        path = Path(output_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(profile.to_json_dict(), indent=2), encoding="utf-8"
        )
        return path

    @staticmethod
    def export_comparison_csv(
        comparisons: List[OfferComparison], output_path: Union[str, Path]
    ) -> Path:
        """Export ranked OfferComparison list to a verifiable CSV report."""
        path = Path(output_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        fieldnames = [
            "rank_first_year",
            "rank_recurring",
            "is_eligible",
            "retailer",
            "plan_name",
            "fuel_type",
            "tariff_type",
            "distributor",
            "baseline_annual_cost_aud",
            "first_year_net_cost_aud",
            "first_year_savings_aud",
            "recurring_annual_cost_aud",
            "recurring_annual_savings_aud",
            "one_off_promotions_aud",
            "supply_cost_aud",
            "usage_cost_aud",
            "controlled_load_cost_aud",
            "discount_amount_aud",
            "solar_fit_credit_aud",
            "annual_fees_aud",
            "is_partner_referral",
            "source_url",
            "warning_flags",
            "explanation",
        ]
        with path.open("w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames)
            writer.writeheader()
            for c in comparisons:
                b = c.breakdown
                writer.writerow(
                    {
                        "rank_first_year": c.rank_first_year or "N/A",
                        "rank_recurring": c.rank_recurring or "N/A",
                        "is_eligible": c.is_eligible,
                        "retailer": c.offer.retailer,
                        "plan_name": c.offer.plan_name,
                        "fuel_type": c.offer.fuel_type.value,
                        "tariff_type": c.offer.tariff_type.value,
                        "distributor": c.offer.distributor,
                        "baseline_annual_cost_aud": str(c.baseline_annual_cost_aud),
                        "first_year_net_cost_aud": str(b.first_year_net_cost_aud),
                        "first_year_savings_aud": str(c.first_year_savings_aud),
                        "recurring_annual_cost_aud": str(b.recurring_annual_cost_aud),
                        "recurring_annual_savings_aud": str(
                            c.recurring_annual_savings_aud
                        ),
                        "one_off_promotions_aud": str(b.one_off_promotions_aud),
                        "supply_cost_aud": str(b.supply_cost_aud),
                        "usage_cost_aud": str(b.usage_cost_aud),
                        "controlled_load_cost_aud": str(b.controlled_load_cost_aud),
                        "discount_amount_aud": str(b.discount_amount_aud),
                        "solar_fit_credit_aud": str(b.solar_fit_credit_aud),
                        "annual_fees_aud": str(b.annual_fees_aud),
                        "is_partner_referral": c.offer.is_partner_referral,
                        "source_url": c.offer.source_url,
                        "warning_flags": " | ".join(c.warning_flags),
                        "explanation": c.explanation,
                    }
                )
        return path

    @staticmethod
    def export_comparison_html(
        result: AnalysisResult, output_path: Union[str, Path]
    ) -> Path:
        """Generate a rich, self-contained HTML comparison report for household verification."""
        path = Path(output_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        p = result.profile

        rows_html: List[str] = []
        for c in result.electricity_comparisons + result.gas_comparisons:
            b = c.breakdown
            badge = (
                f"<span class='badge badge-rank'>#{c.rank_first_year} (Yr1) / #{c.rank_recurring} (Ongoing)</span>"
                if c.is_eligible
                else "<span class='badge badge-ineligible'>Ineligible</span>"
            )
            fy_class = (
                "pos" if str(c.first_year_savings_aud) > "0" and not str(c.first_year_savings_aud).startswith("-") else "neg"
            )
            rec_class = (
                "pos"
                if str(c.recurring_annual_savings_aud) > "0"
                and not str(c.recurring_annual_savings_aud).startswith("-")
                else "neg"
            )
            warnings_html = "".join(
                f"<div class='warn-box'>⚠️ {html.escape(w)}</div>"
                for w in c.warning_flags
            )
            referral_tag = (
                "Partner Referral (Disclosed)"
                if c.offer.is_partner_referral
                else "Direct Official Link (No Commission)"
            )
            rows_html.append(
                f"""
                <tr>
                    <td>{badge}<br><small>{html.escape(c.offer.fuel_type.value)}</small></td>
                    <td>
                        <strong>{html.escape(c.offer.retailer)}</strong><br>
                        {html.escape(c.offer.plan_name)}<br>
                        <small>{html.escape(c.offer.tariff_type.value)} • {html.escape(c.offer.distributor)}</small>
                    </td>
                    <td><strong>AUD ${b.first_year_net_cost_aud}</strong><br><small>Promo: -${b.one_off_promotions_aud}</small></td>
                    <td class='{fy_class}'><strong>AUD ${c.first_year_savings_aud}</strong></td>
                    <td><strong>AUD ${b.recurring_annual_cost_aud}</strong></td>
                    <td class='{rec_class}'><strong>AUD ${c.recurring_annual_savings_aud}/yr</strong></td>
                    <td>
                        <small>
                            Supply: ${b.supply_cost_aud} | Usage: ${b.usage_cost_aud}<br>
                            Discounts: -${b.discount_amount_aud} | Solar FiT: -${b.solar_fit_credit_aud}
                        </small>
                        {warnings_html}
                    </td>
                    <td>
                        <small>{html.escape(c.explanation)}</small><br>
                        <a href='{html.escape(c.offer.source_url)}' target='_blank'>Official Plan Source</a>
                        <span class='ref-tag'>({referral_tag})</span>
                    </td>
                </tr>
                """
            )

        issues_html = "".join(
            f"<li><strong>[{html.escape(i.severity.value)}] {html.escape(i.field_name)}:</strong> "
            f"{html.escape(i.message)} <em>({html.escape(i.suggested_action)})</em></li>"
            for i in p.validation_issues
        ) or "<li>All required bill fields validated with high confidence.</li>"

        dual_html = ""
        if result.dual_fuel_comparison:
            df = result.dual_fuel_comparison
            dual_html = f"""
            <div class="card highlight">
                <h3>🔥⚡ Combined Dual-Fuel Summary (Electricity + Gas, FR-10)</h3>
                <p>
                    <strong>Combined Baseline:</strong> AUD ${df.combined_baseline_aud}/yr &nbsp;|&nbsp;
                    <strong>Best Combined Year-1 Cost:</strong> AUD ${df.combined_first_year_cost_aud}/yr
                    (<strong>Save AUD ${df.combined_first_year_savings_aud}</strong>) &nbsp;|&nbsp;
                    <strong>Ongoing Recurring Savings:</strong> AUD ${df.combined_recurring_savings_aud}/yr
                </p>
            </div>
            """

        doc = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>Energy Offer Comparison & Savings Report — {html.escape(p.household_id)}</title>
<style>
    body {{ font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif; margin: 24px; background: #f8fafc; color: #0f172a; }}
    .header {{ background: #0f4c5c; color: white; padding: 20px 24px; border-radius: 10px; margin-bottom: 20px; }}
    .card {{ background: white; padding: 18px 22px; border-radius: 10px; box-shadow: 0 1px 3px rgba(0,0,0,0.08); margin-bottom: 18px; }}
    .card.highlight {{ border-left: 5px solid #0d9488; background: #f0fdfa; }}
    table {{ width: 100%; border-collapse: collapse; background: white; border-radius: 8px; overflow: hidden; }}
    th, td {{ padding: 12px 14px; text-align: left; border-bottom: 1px solid #e2e8f0; vertical-align: top; }}
    th {{ background: #1e293b; color: white; font-size: 13px; text-transform: uppercase; }}
    .pos {{ color: #15803d; }}
    .neg {{ color: #b91c1c; }}
    .badge {{ display: inline-block; padding: 3px 8px; border-radius: 4px; font-size: 12px; font-weight: 600; }}
    .badge-rank {{ background: #dcfce7; color: #166534; }}
    .badge-ineligible {{ background: #fee2e2; color: #991b1b; }}
    .warn-box {{ margin-top: 6px; padding: 6px 8px; background: #fef3c7; color: #92400e; border-radius: 4px; font-size: 12px; }}
    .ref-tag {{ color: #64748b; font-size: 11px; }}
</style>
</head>
<body>
    <div class="header">
        <h1>AI-Powered Energy Offer Comparison & Savings Report</h1>
        <p>Household ID: <strong>{html.escape(p.household_id)}</strong> • Location: {html.escape(p.suburb)}, {html.escape(p.state)} {html.escape(p.postcode)} • Electricity Network: {html.escape(p.distributor_electricity)}</p>
        <p><small>Core Principle: Deterministic Decimal Pricing Engine • Independent Ranking by User Benefit (Never by Commission)</small></p>
    </div>

    <div class="card">
        <h3>1. Validated Household Baseline & Confidence (UC-01)</h3>
        <p>
            <strong>Electricity Baseline Cost:</strong> AUD ${p.baseline_electricity_annual_aud or 'N/A'}/yr &nbsp;|&nbsp;
            <strong>Gas Baseline Cost:</strong> AUD ${p.baseline_gas_annual_aud or 'N/A'}/yr &nbsp;|&nbsp;
            <strong>Solar Installed:</strong> {'Yes (' + str(p.solar_capacity_kw) + ' kW)' if p.has_solar else 'No'} &nbsp;|&nbsp;
            <strong>Validation Confidence:</strong> {float(p.confidence_score)*100:.0f}%
        </p>
        <ul>{issues_html}</ul>
    </div>

    {dual_html}

    <div class="card">
        <h3>2. Ranked Market Offers & Verifiable Breakdown (UC-02, UC-03, UC-04)</h3>
        <table>
            <thead>
                <tr>
                    <th>Rank</th>
                    <th>Retailer & Plan</th>
                    <th>Year-1 Net Cost</th>
                    <th>Year-1 Savings</th>
                    <th>Ongoing Annual Cost</th>
                    <th>Ongoing Savings</th>
                    <th>Cost Breakdown & Warnings</th>
                    <th>Adviser Explanation & Source</th>
                </tr>
            </thead>
            <tbody>
                {''.join(rows_html)}
            </tbody>
        </table>
    </div>
</body>
</html>
"""
        path.write_text(doc, encoding="utf-8")
        return path
