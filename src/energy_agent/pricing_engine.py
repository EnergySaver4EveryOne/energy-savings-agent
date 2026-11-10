"""
Deterministic Pricing and Savings Engine (UC-03, FR-04, FR-05, FR-10, Section 8).

Core Principle:
All arithmetic is executed deterministically using Python's `Decimal` type.
LLMs are never used for cost or savings arithmetic.
Recommendations are ranked strictly by user benefit and eligibility, never by commission.
"""

from __future__ import annotations

from decimal import Decimal
from typing import List, Optional, Tuple

from .models import (
    CostBreakdown,
    CurrentPlanDetails,
    DualFuelComparison,
    ElectricityUsage,
    EnergyOffer,
    FuelType,
    GasUsage,
    HouseholdProfile,
    OfferComparison,
    TariffType,
    round_currency,
)

CENTS_PER_DOLLAR = Decimal("100")
DAYS_IN_YEAR = Decimal("365")

# Standard NSW residential TOU load profile fallback (when only single-rate total kWh is available)
DEFAULT_NSW_PEAK_SHARE = Decimal("0.30")
DEFAULT_NSW_SHOULDER_SHARE = Decimal("0.20")
DEFAULT_NSW_OFF_PEAK_SHARE = Decimal("0.50")


class PricingEngine:
    """Deterministic calculator for baseline costs, offer costs, eligibility, and rankings."""

    def __init__(self, exclude_uncertain_tou_from_definitive: bool = False) -> None:
        self.exclude_uncertain_tou_from_definitive = (
            exclude_uncertain_tou_from_definitive
        )

    def calculate_baseline_electricity_cost(
        self,
        usage: ElectricityUsage,
        current_plan: CurrentPlanDetails,
    ) -> CostBreakdown:
        """
        Calculate annualized baseline electricity cost from the household's current bill (UC-01).
        """
        assumptions: List[str] = []
        factor = usage.annualization_factor
        if usage.billing_days < 300:
            assumptions.append(
                f"Annualized from {usage.billing_days}-day bill (factor {factor:.2f}x); "
                "single-season bills may not reflect full 12-month seasonal heating/cooling variance."
            )

        # 1. Supply charge: 365 * daily_supply_charge
        supply_cost_aud = (
            DAYS_IN_YEAR * current_plan.daily_supply_charge_cents
        ) / CENTS_PER_DOLLAR

        # 2. Usage charge
        annual_total_kwh = usage.total_kwh * factor
        if (
            current_plan.tariff_type == TariffType.TIME_OF_USE
            and usage.has_tou_breakdown
            and current_plan.peak_rate_cents_kwh is not None
        ):
            annual_peak = (usage.peak_kwh or Decimal("0")) * factor
            annual_shoulder = (usage.shoulder_kwh or Decimal("0")) * factor
            annual_off_peak = (usage.off_peak_kwh or Decimal("0")) * factor
            shoulder_rate = (
                current_plan.shoulder_rate_cents_kwh
                if current_plan.shoulder_rate_cents_kwh is not None
                else current_plan.peak_rate_cents_kwh
            )
            off_peak_rate = (
                current_plan.off_peak_rate_cents_kwh
                if current_plan.off_peak_rate_cents_kwh is not None
                else current_plan.peak_rate_cents_kwh
            )
            usage_cost_aud = (
                annual_peak * current_plan.peak_rate_cents_kwh
                + annual_shoulder * shoulder_rate
                + annual_off_peak * off_peak_rate
            ) / CENTS_PER_DOLLAR
        else:
            single_rate = (
                current_plan.single_rate_cents_kwh
                or current_plan.peak_rate_cents_kwh
                or Decimal("32.0")
            )
            usage_cost_aud = (annual_total_kwh * single_rate) / CENTS_PER_DOLLAR

        # 3. Controlled load
        annual_cl_kwh = usage.controlled_load_kwh * factor
        controlled_load_cost_aud = (
            annual_cl_kwh * current_plan.controlled_load_rate_cents_kwh
        ) / CENTS_PER_DOLLAR

        # 4. Demand tariff
        demand_cost_aud = (
            DAYS_IN_YEAR
            * usage.peak_demand_kw
            * current_plan.demand_rate_cents_kw_day
        ) / CENTS_PER_DOLLAR

        # 5. Discounts
        usage_discount_aud = usage_cost_aud * (
            current_plan.usage_discount_pct / Decimal("100")
        )
        supply_discount_aud = supply_cost_aud * (
            current_plan.supply_discount_pct / Decimal("100")
        )
        discount_amount_aud = usage_discount_aud + supply_discount_aud

        # 6. Solar feed-in credit
        annual_solar_kwh = usage.solar_export_kwh * factor
        solar_fit_credit_aud = (
            annual_solar_kwh * current_plan.solar_fit_cents_kwh
        ) / CENTS_PER_DOLLAR

        # 7. Total recurring annual cost
        recurring_annual_cost_aud = (
            supply_cost_aud
            + usage_cost_aud
            + controlled_load_cost_aud
            + demand_cost_aud
            - discount_amount_aud
            - solar_fit_credit_aud
            + current_plan.annual_fees_aud
        )

        return CostBreakdown(
            supply_cost_aud=round_currency(supply_cost_aud),
            usage_cost_aud=round_currency(usage_cost_aud),
            controlled_load_cost_aud=round_currency(controlled_load_cost_aud),
            demand_cost_aud=round_currency(demand_cost_aud),
            discount_amount_aud=round_currency(discount_amount_aud),
            solar_fit_credit_aud=round_currency(solar_fit_credit_aud),
            annual_fees_aud=round_currency(current_plan.annual_fees_aud),
            recurring_credits_aud=Decimal("0.00"),
            recurring_annual_cost_aud=round_currency(recurring_annual_cost_aud),
            one_off_promotions_aud=Decimal("0.00"),
            first_year_net_cost_aud=round_currency(recurring_annual_cost_aud),
            assumptions=assumptions,
        )

    def calculate_baseline_gas_cost(
        self,
        usage: GasUsage,
        current_plan: CurrentPlanDetails,
    ) -> CostBreakdown:
        """Calculate annualized baseline natural gas cost from the household's current bill."""
        assumptions: List[str] = []
        if usage.billing_days < 300:
            assumptions.append(
                f"Gas usage annualized from {usage.billing_days}-day bill; "
                "winter heating bills can overstate full-year gas consumption."
            )

        supply_cost_aud = (
            DAYS_IN_YEAR * current_plan.daily_supply_charge_cents
        ) / CENTS_PER_DOLLAR

        daily_mj = usage.daily_average_mj
        block1_rate = (
            current_plan.gas_block1_rate_cents_mj
            or current_plan.single_rate_cents_kwh
            or Decimal("3.80")
        )
        if (
            current_plan.gas_block1_limit_mj_day is not None
            and current_plan.gas_remainder_rate_cents_mj is not None
        ):
            tier1_daily_mj = min(daily_mj, current_plan.gas_block1_limit_mj_day)
            tier2_daily_mj = max(
                Decimal("0"), daily_mj - current_plan.gas_block1_limit_mj_day
            )
            daily_usage_cents = (
                tier1_daily_mj * block1_rate
                + tier2_daily_mj * current_plan.gas_remainder_rate_cents_mj
            )
            usage_cost_aud = (DAYS_IN_YEAR * daily_usage_cents) / CENTS_PER_DOLLAR
        else:
            usage_cost_aud = (usage.annual_mj * block1_rate) / CENTS_PER_DOLLAR

        discount_amount_aud = usage_cost_aud * (
            current_plan.usage_discount_pct / Decimal("100")
        )
        recurring_annual_cost_aud = (
            supply_cost_aud
            + usage_cost_aud
            - discount_amount_aud
            + current_plan.annual_fees_aud
        )

        return CostBreakdown(
            supply_cost_aud=round_currency(supply_cost_aud),
            usage_cost_aud=round_currency(usage_cost_aud),
            controlled_load_cost_aud=Decimal("0.00"),
            demand_cost_aud=Decimal("0.00"),
            discount_amount_aud=round_currency(discount_amount_aud),
            solar_fit_credit_aud=Decimal("0.00"),
            annual_fees_aud=round_currency(current_plan.annual_fees_aud),
            recurring_credits_aud=Decimal("0.00"),
            recurring_annual_cost_aud=round_currency(recurring_annual_cost_aud),
            one_off_promotions_aud=Decimal("0.00"),
            first_year_net_cost_aud=round_currency(recurring_annual_cost_aud),
            assumptions=assumptions,
        )

    def check_eligibility(
        self,
        profile: HouseholdProfile,
        offer: EnergyOffer,
    ) -> Tuple[bool, List[str]]:
        """
        Evaluate explicit eligibility rules for an energy offer against the household profile (UC-02, UC-04).
        """
        notes: List[str] = []
        is_eligible = True

        # 1. Distribution network check
        expected_dist = (
            profile.distributor_electricity
            if offer.fuel_type == FuelType.ELECTRICITY
            else profile.distributor_gas
        )
        if (
            offer.distributor
            and expected_dist
            and offer.distributor.lower() != expected_dist.lower()
        ):
            is_eligible = False
            notes.append(
                f"Distributor mismatch: plan requires {offer.distributor}, household is in {expected_dist}."
            )

        rules = offer.eligibility
        # 2. EV ownership requirement
        if rules.requires_ev and not profile.has_ev:
            is_eligible = False
            notes.append("Plan requires an eligible Electric Vehicle (EV) at the premises.")

        # 3. Battery exclusion
        if rules.excluded_if_battery and profile.has_battery:
            is_eligible = False
            notes.append("Plan excludes households with a home battery system.")

        # 4. Solar inverter size cap
        if (
            rules.max_solar_inverter_kw is not None
            and profile.has_solar
            and profile.solar_capacity_kw > rules.max_solar_inverter_kw
        ):
            is_eligible = False
            notes.append(
                f"Solar inverter capacity ({profile.solar_capacity_kw} kW) exceeds plan limit ({rules.max_solar_inverter_kw} kW)."
            )

        # 5. Direct Debit & E-billing conditions
        if rules.requires_direct_debit:
            if not profile.accepts_direct_debit:
                is_eligible = False
                notes.append("Requires Direct Debit, which household has opted out of.")
            else:
                notes.append("Condition: Must pay via Direct Debit.")

        if rules.requires_ebilling:
            if not profile.accepts_ebilling:
                is_eligible = False
                notes.append("Requires E-billing, which household has opted out of.")
            else:
                notes.append("Condition: Must receive bills electronically (E-billing).")

        # 6. New customer condition for sign-up credits
        current_retailer = ""
        if (
            offer.fuel_type == FuelType.ELECTRICITY
            and profile.current_electricity_plan
        ):
            current_retailer = profile.current_electricity_plan.retailer
        elif offer.fuel_type == FuelType.GAS and profile.current_gas_plan:
            current_retailer = profile.current_gas_plan.retailer

        if (
            rules.requires_new_customer
            and current_retailer
            and current_retailer.lower() == offer.retailer.lower()
        ):
            is_eligible = False
            notes.append(
                f"New-customer-only plan, but household is already with {offer.retailer}."
            )

        if rules.notes:
            notes.append(rules.notes)

        return is_eligible, notes

    def calculate_electricity_offer(
        self,
        profile: HouseholdProfile,
        offer: EnergyOffer,
        baseline_annual_cost_aud: Decimal,
    ) -> OfferComparison:
        """
        Deterministically calculate annual cost and savings for an electricity offer (Section 8).
        """
        if not profile.electricity_usage:
            raise ValueError("HouseholdProfile has no electricity_usage data.")

        usage = profile.electricity_usage
        factor = usage.annualization_factor
        assumptions: List[str] = []
        warning_flags: List[str] = []
        definitive_ranking_eligible = True

        if usage.billing_days < 300:
            assumptions.append(
                f"Annualized from {usage.billing_days}-day billing period (365/{usage.billing_days})."
            )

        # 1. Supply charge
        supply_cost_aud = (
            DAYS_IN_YEAR * offer.daily_supply_charge_cents
        ) / CENTS_PER_DOLLAR

        # 2. Usage charge (Single-rate vs Time-of-Use)
        annual_total_kwh = usage.total_kwh * factor
        if offer.tariff_type in (TariffType.TIME_OF_USE, TariffType.DEMAND):
            peak_rate = (
                offer.peak_rate_cents_kwh
                or offer.single_rate_cents_kwh
                or Decimal("35.0")
            )
            shoulder_rate = offer.shoulder_rate_cents_kwh or peak_rate
            off_peak_rate = offer.off_peak_rate_cents_kwh or peak_rate

            if usage.has_tou_breakdown:
                annual_peak = (usage.peak_kwh or Decimal("0")) * factor
                annual_shoulder = (usage.shoulder_kwh or Decimal("0")) * factor
                annual_off_peak = (usage.off_peak_kwh or Decimal("0")) * factor
            else:
                # Household bill was single-rate; apply standard NSW TOU split and flag uncertainty
                annual_peak = annual_total_kwh * DEFAULT_NSW_PEAK_SHARE
                annual_shoulder = annual_total_kwh * DEFAULT_NSW_SHOULDER_SHARE
                annual_off_peak = annual_total_kwh * DEFAULT_NSW_OFF_PEAK_SHARE
                assumptions.append(
                    "Estimated TOU consumption split (30% Peak / 20% Shoulder / 50% Off-Peak) "
                    "because current bill only provided single-rate total kWh."
                )
                warning_flags.append(
                    "TOU band split estimated from flat total kWh; smart meter interval data needed for exact TOU cost."
                )
                if self.exclude_uncertain_tou_from_definitive:
                    definitive_ranking_eligible = False

            usage_cost_aud = (
                annual_peak * peak_rate
                + annual_shoulder * shoulder_rate
                + annual_off_peak * off_peak_rate
            ) / CENTS_PER_DOLLAR
        else:
            single_rate = (
                offer.single_rate_cents_kwh
                or offer.peak_rate_cents_kwh
                or Decimal("30.0")
            )
            usage_cost_aud = (annual_total_kwh * single_rate) / CENTS_PER_DOLLAR

        # 3. Controlled load
        annual_cl_kwh = usage.controlled_load_kwh * factor
        cl_rate = (
            offer.controlled_load_rate_cents_kwh
            if offer.controlled_load_rate_cents_kwh > Decimal("0")
            else (
                offer.off_peak_rate_cents_kwh
                or offer.single_rate_cents_kwh
                or Decimal("20.0")
            )
        )
        controlled_load_cost_aud = (annual_cl_kwh * cl_rate) / CENTS_PER_DOLLAR

        # 4. Demand tariff
        demand_cost_aud = Decimal("0.00")
        if offer.demand_rate_cents_kw_day > Decimal("0"):
            if usage.peak_demand_kw > Decimal("0"):
                demand_cost_aud = (
                    DAYS_IN_YEAR * usage.peak_demand_kw * offer.demand_rate_cents_kw_day
                ) / CENTS_PER_DOLLAR
            else:
                definitive_ranking_eligible = False
                warning_flags.append(
                    "Offer includes a demand tariff, but bill has no peak kW demand data."
                )

        # 5. Discounts (unconditional + eligible conditional)
        total_discount_pct = offer.unconditional_usage_discount_pct
        if offer.conditional_usage_discount_pct > Decimal("0"):
            if profile.accepts_direct_debit and profile.accepts_ebilling:
                total_discount_pct += offer.conditional_usage_discount_pct
                assumptions.append(
                    f"Includes {offer.conditional_usage_discount_pct}% conditional usage discount ({offer.conditional_discount_condition or 'Pay on time / Direct Debit'})."
                )
            else:
                warning_flags.append(
                    f"Excluded {offer.conditional_usage_discount_pct}% conditional discount because Direct Debit / E-billing is disabled."
                )

        discount_amount_aud = usage_cost_aud * (total_discount_pct / Decimal("100"))

        # 6. Solar feed-in credits (supports tiered daily cap FiT and flat FiT)
        solar_fit_credit_aud = Decimal("0.00")
        if profile.has_solar and usage.solar_export_kwh > Decimal("0"):
            daily_export_kwh = usage.solar_export_kwh / Decimal(str(usage.billing_days))
            if (
                offer.solar_fit_daily_cap_kwh is not None
                and offer.solar_fit_remainder_cents_kwh is not None
            ):
                tier1_daily = min(daily_export_kwh, offer.solar_fit_daily_cap_kwh)
                tier2_daily = max(
                    Decimal("0"), daily_export_kwh - offer.solar_fit_daily_cap_kwh
                )
                daily_fit_cents = (
                    tier1_daily * offer.solar_fit_cents_kwh
                    + tier2_daily * offer.solar_fit_remainder_cents_kwh
                )
                solar_fit_credit_aud = (
                    DAYS_IN_YEAR * daily_fit_cents
                ) / CENTS_PER_DOLLAR
                assumptions.append(
                    f"Tiered Solar FiT applied: {offer.solar_fit_cents_kwh}c/kWh for first "
                    f"{offer.solar_fit_daily_cap_kwh} kWh/day, {offer.solar_fit_remainder_cents_kwh}c/kWh remainder."
                )
            else:
                annual_export_kwh = usage.solar_export_kwh * factor
                solar_fit_credit_aud = (
                    annual_export_kwh * offer.solar_fit_cents_kwh
                ) / CENTS_PER_DOLLAR

        # 7. Separate recurring credits vs one-off first-year promotions
        current_retailer = (
            profile.current_electricity_plan.retailer
            if profile.current_electricity_plan
            else ""
        )
        is_switching_retailer = (
            not current_retailer or current_retailer.lower() != offer.retailer.lower()
        )

        recurring_credits_aud = Decimal("0.00")
        one_off_promotions_aud = Decimal("0.00")
        for promo in offer.promotions:
            if promo.is_recurring:
                recurring_credits_aud += promo.amount_aud
            else:
                if is_switching_retailer:
                    one_off_promotions_aud += promo.amount_aud
                    assumptions.append(
                        f"Includes one-off first-year credit: AUD ${promo.amount_aud} ({promo.title})."
                    )
                else:
                    warning_flags.append(
                        f"Excluded new-customer credit AUD ${promo.amount_aud} ({promo.title}) as household is already with {offer.retailer}."
                    )

        # 8. Recurring vs First-Year Net Cost
        recurring_annual_cost_aud = (
            supply_cost_aud
            + usage_cost_aud
            + controlled_load_cost_aud
            + demand_cost_aud
            - discount_amount_aud
            - solar_fit_credit_aud
            + offer.annual_fees_aud
            - recurring_credits_aud
        )
        first_year_net_cost_aud = recurring_annual_cost_aud - one_off_promotions_aud

        recurring_savings = baseline_annual_cost_aud - recurring_annual_cost_aud
        first_year_savings = baseline_annual_cost_aud - first_year_net_cost_aud

        if (
            one_off_promotions_aud > Decimal("0")
            and first_year_savings > Decimal("0")
            and recurring_savings <= Decimal("0")
        ):
            warning_flags.append(
                "Year-2 Loyalty Tax Warning: First-year savings rely entirely on a one-off sign-up credit; "
                f"ongoing tariff rates are AUD ${abs(round_currency(recurring_savings))}/yr more expensive than baseline from Year 2."
            )

        is_eligible, eligibility_notes = self.check_eligibility(profile, offer)

        breakdown = CostBreakdown(
            supply_cost_aud=round_currency(supply_cost_aud),
            usage_cost_aud=round_currency(usage_cost_aud),
            controlled_load_cost_aud=round_currency(controlled_load_cost_aud),
            demand_cost_aud=round_currency(demand_cost_aud),
            discount_amount_aud=round_currency(discount_amount_aud),
            solar_fit_credit_aud=round_currency(solar_fit_credit_aud),
            annual_fees_aud=round_currency(offer.annual_fees_aud),
            recurring_credits_aud=round_currency(recurring_credits_aud),
            recurring_annual_cost_aud=round_currency(recurring_annual_cost_aud),
            one_off_promotions_aud=round_currency(one_off_promotions_aud),
            first_year_net_cost_aud=round_currency(first_year_net_cost_aud),
            assumptions=assumptions,
        )

        return OfferComparison(
            offer=offer,
            breakdown=breakdown,
            baseline_annual_cost_aud=round_currency(baseline_annual_cost_aud),
            recurring_annual_savings_aud=round_currency(recurring_savings),
            first_year_savings_aud=round_currency(first_year_savings),
            is_eligible=is_eligible,
            eligibility_notes=eligibility_notes,
            warning_flags=warning_flags,
            definitive_ranking_eligible=definitive_ranking_eligible and is_eligible,
        )

    def calculate_gas_offer(
        self,
        profile: HouseholdProfile,
        offer: EnergyOffer,
        baseline_annual_cost_aud: Decimal,
    ) -> OfferComparison:
        """Deterministically calculate annual cost and savings for a natural gas offer (Section 8)."""
        if not profile.gas_usage:
            raise ValueError("HouseholdProfile has no gas_usage data.")

        usage = profile.gas_usage
        assumptions: List[str] = []
        warning_flags: List[str] = []

        if usage.billing_days < 300:
            assumptions.append(
                f"Annualized from {usage.billing_days}-day gas bill."
            )

        supply_cost_aud = (
            DAYS_IN_YEAR * offer.daily_supply_charge_cents
        ) / CENTS_PER_DOLLAR

        daily_mj = usage.daily_average_mj
        block1_rate = (
            offer.gas_block1_rate_cents_mj
            or offer.single_rate_cents_kwh
            or Decimal("3.50")
        )
        if (
            offer.gas_block1_limit_mj_day is not None
            and offer.gas_remainder_rate_cents_mj is not None
        ):
            tier1_daily_mj = min(daily_mj, offer.gas_block1_limit_mj_day)
            tier2_daily_mj = max(
                Decimal("0"), daily_mj - offer.gas_block1_limit_mj_day
            )
            daily_usage_cents = (
                tier1_daily_mj * block1_rate
                + tier2_daily_mj * offer.gas_remainder_rate_cents_mj
            )
            usage_cost_aud = (DAYS_IN_YEAR * daily_usage_cents) / CENTS_PER_DOLLAR
            assumptions.append(
                f"Stepped gas tariff: {block1_rate}c/MJ for first {offer.gas_block1_limit_mj_day} MJ/day, "
                f"{offer.gas_remainder_rate_cents_mj}c/MJ remainder."
            )
        else:
            usage_cost_aud = (usage.annual_mj * block1_rate) / CENTS_PER_DOLLAR

        total_discount_pct = offer.unconditional_usage_discount_pct
        if (
            offer.conditional_usage_discount_pct > Decimal("0")
            and profile.accepts_direct_debit
            and profile.accepts_ebilling
        ):
            total_discount_pct += offer.conditional_usage_discount_pct

        discount_amount_aud = usage_cost_aud * (total_discount_pct / Decimal("100"))

        current_retailer = (
            profile.current_gas_plan.retailer if profile.current_gas_plan else ""
        )
        is_switching_retailer = (
            not current_retailer or current_retailer.lower() != offer.retailer.lower()
        )

        recurring_credits_aud = Decimal("0.00")
        one_off_promotions_aud = Decimal("0.00")
        for promo in offer.promotions:
            if promo.is_recurring:
                recurring_credits_aud += promo.amount_aud
            elif is_switching_retailer:
                one_off_promotions_aud += promo.amount_aud
                assumptions.append(
                    f"Includes one-off gas sign-up credit: AUD ${promo.amount_aud} ({promo.title})."
                )

        recurring_annual_cost_aud = (
            supply_cost_aud
            + usage_cost_aud
            - discount_amount_aud
            + offer.annual_fees_aud
            - recurring_credits_aud
        )
        first_year_net_cost_aud = recurring_annual_cost_aud - one_off_promotions_aud

        recurring_savings = baseline_annual_cost_aud - recurring_annual_cost_aud
        first_year_savings = baseline_annual_cost_aud - first_year_net_cost_aud

        if (
            one_off_promotions_aud > Decimal("0")
            and first_year_savings > Decimal("0")
            and recurring_savings <= Decimal("0")
        ):
            warning_flags.append(
                "Year-2 Loyalty Tax Warning: Gas savings in Year 1 rely on a one-off credit."
            )

        is_eligible, eligibility_notes = self.check_eligibility(profile, offer)

        breakdown = CostBreakdown(
            supply_cost_aud=round_currency(supply_cost_aud),
            usage_cost_aud=round_currency(usage_cost_aud),
            controlled_load_cost_aud=Decimal("0.00"),
            demand_cost_aud=round_currency(Decimal("0.00")),
            discount_amount_aud=round_currency(discount_amount_aud),
            solar_fit_credit_aud=Decimal("0.00"),
            annual_fees_aud=round_currency(offer.annual_fees_aud),
            recurring_credits_aud=round_currency(recurring_credits_aud),
            recurring_annual_cost_aud=round_currency(recurring_annual_cost_aud),
            one_off_promotions_aud=round_currency(one_off_promotions_aud),
            first_year_net_cost_aud=round_currency(first_year_net_cost_aud),
            assumptions=assumptions,
        )

        return OfferComparison(
            offer=offer,
            breakdown=breakdown,
            baseline_annual_cost_aud=round_currency(baseline_annual_cost_aud),
            recurring_annual_savings_aud=round_currency(recurring_savings),
            first_year_savings_aud=round_currency(first_year_savings),
            is_eligible=is_eligible,
            eligibility_notes=eligibility_notes,
            warning_flags=warning_flags,
            definitive_ranking_eligible=is_eligible,
        )

    def rank_offers(
        self,
        profile: HouseholdProfile,
        offers: List[EnergyOffer],
        fuel_type: FuelType = FuelType.ELECTRICITY,
        rank_by_recurring: bool = False,
    ) -> List[OfferComparison]:
        """
        Compare and rank all offers for a given fuel type strictly by user cost/savings
        and eligibility, completely independent of referral commissions (Core Principle).
        """
        if fuel_type == FuelType.ELECTRICITY:
            if not profile.electricity_usage or not profile.current_electricity_plan:
                return []
            baseline_breakdown = self.calculate_baseline_electricity_cost(
                profile.electricity_usage, profile.current_electricity_plan
            )
            baseline_cost = (
                profile.baseline_electricity_annual_aud
                or baseline_breakdown.recurring_annual_cost_aud
            )
            profile.baseline_electricity_annual_aud = baseline_cost
        else:
            if not profile.gas_usage or not profile.current_gas_plan:
                return []
            baseline_breakdown = self.calculate_baseline_gas_cost(
                profile.gas_usage, profile.current_gas_plan
            )
            baseline_cost = (
                profile.baseline_gas_annual_aud
                or baseline_breakdown.recurring_annual_cost_aud
            )
            profile.baseline_gas_annual_aud = baseline_cost

        comparisons: List[OfferComparison] = []
        for offer in offers:
            if offer.fuel_type != fuel_type:
                continue
            if fuel_type == FuelType.ELECTRICITY:
                comp = self.calculate_electricity_offer(profile, offer, baseline_cost)
            else:
                comp = self.calculate_gas_offer(profile, offer, baseline_cost)
            comparisons.append(comp)

        # Assign recurring rank among eligible offers
        eligible_for_rec = [c for c in comparisons if c.definitive_ranking_eligible]
        eligible_for_rec.sort(
            key=lambda c: (
                c.breakdown.recurring_annual_cost_aud,
                c.breakdown.first_year_net_cost_aud,
                c.offer.offer_id,
            )
        )
        for idx, comp in enumerate(eligible_for_rec, start=1):
            comp.rank_recurring = idx

        # Assign first-year rank among eligible offers
        eligible_for_fy = [c for c in comparisons if c.definitive_ranking_eligible]
        eligible_for_fy.sort(
            key=lambda c: (
                c.breakdown.first_year_net_cost_aud,
                c.breakdown.recurring_annual_cost_aud,
                c.offer.offer_id,
            )
        )
        for idx, comp in enumerate(eligible_for_fy, start=1):
            comp.rank_first_year = idx

        # Final sort order: eligible offers first, then ordered by user-selected metric
        def sort_key(c: OfferComparison) -> Tuple[int, Decimal, Decimal, str]:
            elig_priority = 0 if c.definitive_ranking_eligible else 1
            if rank_by_recurring:
                return (
                    elig_priority,
                    c.breakdown.recurring_annual_cost_aud,
                    c.breakdown.first_year_net_cost_aud,
                    c.offer.offer_id,
                )
            return (
                elig_priority,
                c.breakdown.first_year_net_cost_aud,
                c.breakdown.recurring_annual_cost_aud,
                c.offer.offer_id,
            )

        comparisons.sort(key=sort_key)
        return comparisons

    def compare_dual_fuel(
        self,
        profile: HouseholdProfile,
        offers: List[EnergyOffer],
    ) -> Optional[DualFuelComparison]:
        """
        Compare electricity and gas plans together (FR-10) and return the optimal combined pairing.
        """
        elec_ranked = self.rank_offers(profile, offers, FuelType.ELECTRICITY)
        gas_ranked = self.rank_offers(profile, offers, FuelType.GAS)

        eligible_elec = [c for c in elec_ranked if c.definitive_ranking_eligible]
        eligible_gas = [c for c in gas_ranked if c.definitive_ranking_eligible]

        if not eligible_elec or not eligible_gas:
            return None

        best_elec = eligible_elec[0]
        best_gas = eligible_gas[0]

        combined_baseline = round_currency(
            best_elec.baseline_annual_cost_aud + best_gas.baseline_annual_cost_aud
        )
        combined_fy_cost = round_currency(
            best_elec.breakdown.first_year_net_cost_aud
            + best_gas.breakdown.first_year_net_cost_aud
        )
        combined_rec_cost = round_currency(
            best_elec.breakdown.recurring_annual_cost_aud
            + best_gas.breakdown.recurring_annual_cost_aud
        )

        return DualFuelComparison(
            electricity_comparison=best_elec,
            gas_comparison=best_gas,
            combined_baseline_aud=combined_baseline,
            combined_first_year_cost_aud=combined_fy_cost,
            combined_recurring_cost_aud=combined_rec_cost,
            combined_first_year_savings_aud=round_currency(
                combined_baseline - combined_fy_cost
            ),
            combined_recurring_savings_aud=round_currency(
                combined_baseline - combined_rec_cost
            ),
            same_retailer_bundle=(
                best_elec.offer.retailer.lower() == best_gas.offer.retailer.lower()
            ),
        )
