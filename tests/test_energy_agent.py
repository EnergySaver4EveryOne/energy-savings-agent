"""
Comprehensive Test Suite for the AI-Powered Energy Offer Comparison and Savings Agent.

Covers Functional Requirements FR-01 through FR-10, Business Use Cases UC-01 through UC-06,
and the Core Principle (Deterministic Decimal pricing, independent ranking regardless of commission).

Compatible with both `pytest` and standard library `python3 -m unittest`.
"""

from __future__ import annotations

import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

from energy_agent.bill_parser import (
    BillParser,
    infer_nsw_distributor,
    redact_sensitive_bill_text,
)
from energy_agent.models import (
    CurrentPlanDetails,
    ElectricityUsage,
    EligibilityRule,
    EnergyOffer,
    FuelType,
    GasUsage,
    HouseholdProfile,
    PromotionCredit,
    TariffType,
    ValidationSeverity,
)
from energy_agent.monitor import MarketMonitor
from energy_agent.offer_collector import OfferCollector
from energy_agent.orchestrator import EnergySavingsOrchestrator
from energy_agent.pricing_engine import PricingEngine
from energy_agent.sample_bills import generate_all_sample_bills
from energy_agent.storage import StorageRepository


class TestEnergySavingsAgent(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp_dir = tempfile.TemporaryDirectory()
        self.base_path = Path(self.tmp_dir.name)
        self.db_path = self.base_path / "test_agent.db"
        self.engine = PricingEngine()

    def tearDown(self) -> None:
        self.tmp_dir.cleanup()

    def test_fr01_fr02_pdf_bill_extraction_and_redaction(self) -> None:
        """FR-01 & FR-02: Generate PDF bills, extract text, redact NMI/MIRN, and build validated profile."""
        sample_bills = generate_all_sample_bills(self.base_path / "bills")
        parser = BillParser(use_llm=False, pricing_engine=self.engine)

        raw_sample = (
            "Customer Name: Jane Doe\nAccount Number: 12345678\n"
            "NMI: 4102938475\nMIRN: 5209988776\nTotal Electricity Usage: 900 kWh"
        )
        redacted = redact_sensitive_bill_text(raw_sample)
        self.assertNotIn("4102938475", redacted)
        self.assertNotIn("5209988776", redacted)
        self.assertIn("[REDACTED_NMI]", redacted)
        self.assertIn("[REDACTED_MIRN]", redacted)

        profile = parser.parse_pdf_bill(
            sample_bills["ausgrid_tou_solar"], household_id="hh-test-01"
        )
        self.assertEqual(profile.postcode, "2000")
        self.assertEqual(profile.distributor_electricity, "Ausgrid")
        self.assertTrue(profile.has_solar)
        self.assertIsNotNone(profile.electricity_usage)
        assert profile.electricity_usage is not None
        self.assertEqual(profile.electricity_usage.total_kwh, Decimal("1350.0"))
        self.assertEqual(profile.electricity_usage.peak_kwh, Decimal("420.0"))
        self.assertIsNotNone(profile.baseline_electricity_annual_aud)
        assert profile.baseline_electricity_annual_aud is not None
        self.assertGreater(profile.baseline_electricity_annual_aud, Decimal("1800.00"))

    def test_fr02_validation_flags_inconsistent_tou_and_missing_rates(self) -> None:
        """FR-02: Validator flags TOU sum mismatch and missing tariff rates for user clarification."""
        parser = BillParser(use_llm=False, pricing_engine=self.engine)
        profile = HouseholdProfile(
            household_id="hh-invalid",
            postcode="2150",
            distributor_electricity=infer_nsw_distributor("2150"),
            electricity_usage=ElectricityUsage(
                billing_days=90,
                total_kwh=Decimal("1000"),
                peak_kwh=Decimal("500"),
                shoulder_kwh=Decimal("300"),
                off_peak_kwh=Decimal("300"),  # Sum = 1100 != 1000
            ),
            current_electricity_plan=CurrentPlanDetails(
                retailer="Origin",
                plan_name="Test TOU",
                tariff_type=TariffType.TIME_OF_USE,
                peak_rate_cents_kwh=None,  # Missing required rate
            ),
        )
        issues = parser.validate_profile(profile)
        self.assertEqual(profile.distributor_electricity, "Endeavour Energy")
        self.assertTrue(profile.needs_clarification)
        fields_flagged = {i.field_name for i in issues}
        self.assertIn("tou_usage_breakdown", fields_flagged)
        self.assertIn("peak_rate_cents_kwh", fields_flagged)

    def test_fr04_fr05_deterministic_pricing_and_promo_separation(self) -> None:
        """
        FR-04 & FR-05 (Section 8):
        Verify exact Decimal calculation:
        Annual cost = 365 * daily_supply + annual_consumption * usage_rate - discounts - credits + fees.
        Also verify separation of recurring savings vs Year-1 promotional credits and Loyalty Tax warning.
        """
        profile = HouseholdProfile(
            household_id="hh-math",
            postcode="2000",
            distributor_electricity="Ausgrid",
            has_solar=True,
            solar_capacity_kw=Decimal("6.6"),
            electricity_usage=ElectricityUsage(
                billing_days=365,
                total_kwh=Decimal("4000"),
                solar_export_kwh=Decimal("2000"),
            ),
            current_electricity_plan=CurrentPlanDetails(
                retailer="Incumbent",
                plan_name="Base Plan",
                tariff_type=TariffType.SINGLE_RATE,
                daily_supply_charge_cents=Decimal("100.0"),  # $365.00/yr
                single_rate_cents_kwh=Decimal("30.0"),       # $1200.00/yr
                solar_fit_cents_kwh=Decimal("5.0"),          # -$100.00/yr
            ),
        )
        base_bd = self.engine.calculate_baseline_electricity_cost(
            profile.electricity_usage, profile.current_electricity_plan
        )
        # 365 + 1200 - 100 = 1465.00
        self.assertEqual(base_bd.recurring_annual_cost_aud, Decimal("1465.00"))

        # Offer with higher ongoing rate ($1550/yr) but a huge $300 one-off credit ($1250 Year-1)
        teaser_offer = EnergyOffer(
            offer_id="TEASER-1",
            retailer="TeaserRetail",
            plan_name="Cashback Trap",
            fuel_type=FuelType.ELECTRICITY,
            tariff_type=TariffType.SINGLE_RATE,
            distributor="Ausgrid",
            daily_supply_charge_cents=Decimal("100.0"),  # $365.00
            single_rate_cents_kwh=Decimal("32.125"),     # 4000 * 0.32125 = $1285.00
            solar_fit_cents_kwh=Decimal("5.0"),          # -$100.00 -> Recurring = $1550.00
            promotions=[
                PromotionCredit(
                    title="Welcome Bonus",
                    amount_aud=Decimal("300.00"),
                    is_recurring=False,
                )
            ],
        )
        comp = self.engine.calculate_electricity_offer(
            profile, teaser_offer, base_bd.recurring_annual_cost_aud
        )
        self.assertEqual(comp.breakdown.recurring_annual_cost_aud, Decimal("1550.00"))
        self.assertEqual(comp.breakdown.first_year_net_cost_aud, Decimal("1250.00"))
        self.assertEqual(comp.first_year_savings_aud, Decimal("215.00"))
        self.assertEqual(comp.recurring_annual_savings_aud, Decimal("-85.00"))
        self.assertTrue(
            any("Year-2 Loyalty Tax Warning" in w for w in comp.warning_flags)
        )

    def test_core_principle_ranking_independent_of_commission(self) -> None:
        """Core Principle: Offers must be ranked strictly by user benefit and eligibility, NEVER by commission."""
        profile = HouseholdProfile(
            household_id="hh-comm",
            postcode="2000",
            distributor_electricity="Ausgrid",
            electricity_usage=ElectricityUsage(
                billing_days=365, total_kwh=Decimal("3650")
            ),
            current_electricity_plan=CurrentPlanDetails(
                retailer="OldCo",
                plan_name="Old",
                tariff_type=TariffType.SINGLE_RATE,
                daily_supply_charge_cents=Decimal("120.0"),
                single_rate_cents_kwh=Decimal("35.0"),
            ),
        )
        # Offer A: Zero commission, cheaper for the user (100c/day + 25c/kWh = $365 + $912.50 = $1277.50)
        offer_zero_comm = EnergyOffer(
            offer_id="ZERO-COMM",
            retailer="CommunityPower",
            plan_name="Fair Rate",
            fuel_type=FuelType.ELECTRICITY,
            tariff_type=TariffType.SINGLE_RATE,
            distributor="Ausgrid",
            daily_supply_charge_cents=Decimal("100.0"),
            single_rate_cents_kwh=Decimal("25.0"),
            solar_fit_cents_kwh=Decimal("0"),
            is_partner_referral=False,
            referral_commission_aud=Decimal("0.00"),
        )
        # Offer B: High $200 commission, more expensive for the user (110c/day + 28c/kWh)
        offer_high_comm = EnergyOffer(
            offer_id="HIGH-COMM",
            retailer="BigPartner",
            plan_name="Partner Plan",
            fuel_type=FuelType.ELECTRICITY,
            tariff_type=TariffType.SINGLE_RATE,
            distributor="Ausgrid",
            daily_supply_charge_cents=Decimal("110.0"),
            single_rate_cents_kwh=Decimal("28.0"),
            solar_fit_cents_kwh=Decimal("0"),
            is_partner_referral=True,
            referral_commission_aud=Decimal("200.00"),
        )
        ranked = self.engine.rank_offers(
            profile, [offer_high_comm, offer_zero_comm], FuelType.ELECTRICITY
        )
        self.assertEqual(ranked[0].offer.offer_id, "ZERO-COMM")
        self.assertEqual(ranked[0].rank_first_year, 1)
        self.assertEqual(ranked[1].offer.offer_id, "HIGH-COMM")
        self.assertEqual(ranked[1].rank_first_year, 2)

    def test_fr03_staleness_and_expired_promotions(self) -> None:
        """FR-03 & Risk Mitigation: Stale offers are detected and expired promotions are stripped."""
        collector = OfferCollector(max_stale_days=14)
        stale_date = (datetime.now(timezone.utc) - timedelta(days=30)).isoformat()
        stale_offer = EnergyOffer(
            offer_id="STALE-1",
            retailer="OldRetailer",
            plan_name="Stale Plan",
            fuel_type=FuelType.ELECTRICITY,
            tariff_type=TariffType.SINGLE_RATE,
            distributor="Ausgrid",
            retrieved_at=stale_date,
        )
        self.assertTrue(collector.is_offer_stale(stale_offer))

        fresh_offer = EnergyOffer(
            offer_id="FRESH-1",
            retailer="FreshRetailer",
            plan_name="Fresh Plan",
            fuel_type=FuelType.ELECTRICITY,
            tariff_type=TariffType.SINGLE_RATE,
            distributor="Ausgrid",
            retrieved_at=datetime.now(timezone.utc).isoformat(),
            promotions=[
                PromotionCredit(
                    title="Expired Promo",
                    amount_aud=Decimal("100"),
                    expiry_date="2020-01-01",
                ),
                PromotionCredit(
                    title="Active Promo",
                    amount_aud=Decimal("150"),
                    expiry_date="2030-01-01",
                ),
            ],
        )
        self.assertFalse(collector.is_offer_stale(fresh_offer))
        self.assertEqual(len(fresh_offer.promotions), 1)
        self.assertEqual(fresh_offer.promotions[0].title, "Active Promo")

    def test_fr07_fr08_fr09_fr10_orchestrator_and_alert_deduplication(self) -> None:
        """
        FR-07, FR-08, FR-09, FR-10:
        End-to-end test of Dual-Fuel analysis, daily monitoring, AUD $100 threshold alerting,
        and duplicate alert suppression when offer versions have not changed.
        """
        sample_bills = generate_all_sample_bills(self.base_path / "samples")
        orchestrator = EnergySavingsOrchestrator(
            db_path=self.db_path, use_llm=False
        )

        result = orchestrator.analyze_pdf_bill(
            sample_bills["dual_fuel_elec_gas"],
            household_id="hh-dual-01",
            persist=True,
        )
        self.assertIsNotNone(result.dual_fuel_comparison)
        assert result.dual_fuel_comparison is not None
        self.assertGreater(
            result.dual_fuel_comparison.combined_first_year_savings_aud,
            Decimal("100.00"),
        )

        # First daily monitor run: should trigger alerts for Electricity & Gas (each saving >= $100)
        run1 = orchestrator.run_daily_monitoring()
        self.assertGreaterEqual(len(run1.new_alerts_triggered), 1)
        self.assertEqual(run1.duplicate_alerts_suppressed, 0)

        # Second daily monitor run with unchanged offers: should suppress duplicate alerts!
        run2 = orchestrator.run_daily_monitoring()
        self.assertEqual(len(run2.new_alerts_triggered), 0)
        self.assertGreaterEqual(run2.duplicate_alerts_suppressed, 1)


if __name__ == "__main__":
    unittest.main()
