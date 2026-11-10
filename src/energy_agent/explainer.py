"""
Recommendation Explanation Module (UC-04, FR-06).

Core Principle:
AI explains information grounded strictly in numbers produced by the deterministic pricing engine.
Recommendations are ranked by user benefit and eligibility, never by referral commission.
"""

from __future__ import annotations

import os
from decimal import Decimal
from typing import List

from .models import HouseholdProfile, OfferComparison


class RecommendationExplainer:
    """Generates transparent, verifiable explanations for ranked energy offers."""

    def __init__(self, use_llm: bool = False) -> None:
        self.use_llm = use_llm and bool(os.environ.get("GEMINI_API_KEY"))

    def populate_explanations(
        self,
        profile: HouseholdProfile,
        comparisons: List[OfferComparison],
    ) -> List[OfferComparison]:
        """Populate human-readable explanations for each OfferComparison."""
        for comp in comparisons:
            comp.explanation = self.explain_offer(profile, comp)
        return comparisons

    def explain_offer(
        self,
        profile: HouseholdProfile,
        comp: OfferComparison,
    ) -> str:
        """
        Generate a deterministic, verifiable explanation for a single offer comparison.
        Optionally enriches narrative with Gemini 3.8 Flash while preserving exact figures.
        """
        offer = comp.offer
        b = comp.breakdown
        parts: List[str] = []

        if not comp.is_eligible:
            reasons = "; ".join(comp.eligibility_notes) or "Eligibility requirements not met."
            return f"Not Eligible: {reasons}"

        # 1. Savings headline (First-Year vs Recurring)
        if comp.first_year_savings_aud > Decimal("0"):
            parts.append(
                f"Estimated Year-1 savings of AUD ${comp.first_year_savings_aud} "
                f"(net cost AUD ${b.first_year_net_cost_aud}/yr vs baseline AUD ${comp.baseline_annual_cost_aud}/yr)."
            )
        else:
            diff = abs(comp.first_year_savings_aud)
            parts.append(
                f"Estimated AUD ${diff}/yr more expensive than your current plan in Year 1 "
                f"(AUD ${b.first_year_net_cost_aud}/yr vs baseline AUD ${comp.baseline_annual_cost_aud}/yr)."
            )

        # 2. Separate recurring savings from one-off promotions
        if b.one_off_promotions_aud > Decimal("0"):
            if comp.recurring_annual_savings_aud > Decimal("0"):
                parts.append(
                    f"Includes AUD ${b.one_off_promotions_aud} in one-off Year-1 sign-up credits plus "
                    f"AUD ${comp.recurring_annual_savings_aud}/yr in ongoing recurring tariff savings."
                )
            else:
                parts.append(
                    f"CAUTION: Includes AUD ${b.one_off_promotions_aud} one-off credit, but ongoing tariff rates "
                    f"cost AUD ${abs(comp.recurring_annual_savings_aud)}/yr MORE than your current plan from Year 2 onwards."
                )
        else:
            parts.append(
                f"100% of savings (AUD ${comp.recurring_annual_savings_aud}/yr) come from ongoing recurring tariff rates "
                "with no reliance on temporary sign-up credits."
            )

        # 3. Key drivers (Supply, Usage, Solar FiT)
        drivers = [
            f"Supply: AUD ${b.supply_cost_aud}/yr ({offer.daily_supply_charge_cents}c/day)",
            f"Usage: AUD ${b.usage_cost_aud}/yr",
        ]
        if b.discount_amount_aud > Decimal("0"):
            drivers.append(f"Discounts: -AUD ${b.discount_amount_aud}/yr")
        if b.solar_fit_credit_aud > Decimal("0"):
            drivers.append(
                f"Solar FiT Credit: -AUD ${b.solar_fit_credit_aud}/yr ({offer.solar_fit_cents_kwh}c/kWh)"
            )
        parts.append("Breakdown: " + ", ".join(drivers) + ".")

        # 4. Eligibility & Contract conditions
        if comp.eligibility_notes:
            parts.append("Conditions: " + " ".join(comp.eligibility_notes))

        # 5. Commercial disclosure (Model A transparency)
        if offer.is_partner_referral:
            parts.append(
                f"Disclosure: Partner referral link available (commission AUD ${offer.referral_commission_aud} if switched). "
                "Ranking is 100% independent of commission."
            )
        else:
            parts.append(
                "Disclosure: Direct official link (no referral commission paid to this service)."
            )

        base_explanation = " ".join(parts)

        if self.use_llm and comp.rank_first_year == 1:
            llm_summary = self._enhance_top_recommendation_with_gemini(
                profile, comp, base_explanation
            )
            if llm_summary:
                return f"{base_explanation}\n\nAI Adviser Summary: {llm_summary}"

        return base_explanation

    def _enhance_top_recommendation_with_gemini(
        self,
        profile: HouseholdProfile,
        comp: OfferComparison,
        deterministic_text: str,
    ) -> str:
        """Use Gemini 3.8 Flash to provide a concise adviser note strictly grounded in deterministic numbers."""
        try:
            from google import genai  # type: ignore

            client = genai.Client()
            prompt = (
                "You are an independent Australian energy adviser. Summarize in 2 sentences why this plan "
                "is ranked #1 for the household based ONLY on the verified figures below. Do not invent or "
                "alter any numbers.\n\n"
                f"Household Postcode: {profile.postcode} ({profile.distributor_electricity})\n"
                f"Plan: {comp.offer.retailer} - {comp.offer.plan_name}\n"
                f"Verified Facts: {deterministic_text}"
            )
            interaction = client.interactions.create(
                model="gemini-3.8-flash",
                input=prompt,
                store=False,
            )
            return (interaction.output_text or "").strip()
        except Exception:
            return ""
