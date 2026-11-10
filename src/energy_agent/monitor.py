"""
Daily Market Monitoring and Threshold Alerting Module (UC-05, UC-06, FR-07, FR-08, FR-09).

Responsibilities:
1. Refresh market offers daily and track new/changed offer versions in SQLite.
2. Recalculate potential savings for each stored household profile.
3. Trigger an alert ONLY when estimated first-year savings >= configured threshold (MVP default: AUD $100).
4. Deduplicate alerts by `(household_id, offer_id, offer_version_hash)` so unchanged offers never spam the user.
"""

from __future__ import annotations

import smtplib
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal
from email.message import EmailMessage
from typing import List, Optional

from .explainer import RecommendationExplainer
from .models import (
    AlertNotification,
    EnergyOffer,
    FuelType,
    HouseholdProfile,
    to_decimal,
)
from .offer_collector import OfferCollector
from .pricing_engine import PricingEngine
from .storage import StorageRepository


@dataclass
class MarketCheckSummary:
    """Result summary of a daily market monitoring run."""

    checked_at: str
    total_offers_scanned: int
    new_or_changed_offer_versions: int
    households_evaluated: int
    new_alerts_triggered: List[AlertNotification] = field(default_factory=list)
    duplicate_alerts_suppressed: int = 0


class MarketMonitor:
    """Executes daily market checks, version tracking, threshold filtering, and alert dispatch."""

    def __init__(
        self,
        storage: StorageRepository,
        collector: Optional[OfferCollector] = None,
        pricing_engine: Optional[PricingEngine] = None,
        explainer: Optional[RecommendationExplainer] = None,
        default_threshold_aud: Decimal = Decimal("100.00"),
    ) -> None:
        self.storage = storage
        self.collector = collector or OfferCollector()
        self.pricing_engine = pricing_engine or PricingEngine()
        self.explainer = explainer or RecommendationExplainer()
        self.default_threshold_aud = to_decimal(default_threshold_aud, "100.00")

    def run_daily_check(
        self,
        custom_offers: Optional[List[EnergyOffer]] = None,
        threshold_override_aud: Optional[Decimal] = None,
    ) -> MarketCheckSummary:
        """
        Refresh market offers, record offer versions, evaluate all stored households,
        and trigger deduplicated alerts when first-year savings reach the threshold (AUD $100).
        """
        offers = (
            custom_offers
            if custom_offers is not None
            else self.collector.load_curated_offers(exclude_stale=True)
        )

        changed_count = 0
        for offer in offers:
            is_new_version, _ = self.storage.record_offer_version(offer)
            if is_new_version:
                changed_count += 1

        households = self.storage.list_household_profiles()
        new_alerts: List[AlertNotification] = []
        suppressed_count = 0

        for profile in households:
            threshold = (
                to_decimal(threshold_override_aud)
                if threshold_override_aud is not None
                else (profile.alert_threshold_aud or self.default_threshold_aud)
            )
            matching_offers = [
                o
                for o in offers
                if o.state.upper() == profile.state.upper()
                and (
                    (
                        o.fuel_type == FuelType.ELECTRICITY
                        and o.distributor.lower()
                        == profile.distributor_electricity.lower()
                    )
                    or (
                        o.fuel_type == FuelType.GAS
                        and o.distributor.lower() == profile.distributor_gas.lower()
                    )
                )
            ]

            for fuel in (FuelType.ELECTRICITY, FuelType.GAS):
                if fuel == FuelType.ELECTRICITY and not profile.electricity_usage:
                    continue
                if fuel == FuelType.GAS and not profile.gas_usage:
                    continue

                ranked = self.pricing_engine.rank_offers(
                    profile, matching_offers, fuel_type=fuel
                )
                self.explainer.populate_explanations(profile, ranked)
                self.storage.save_comparison_run(
                    run_id=f"run-{uuid.uuid4().hex[:10]}",
                    household_id=profile.household_id,
                    fuel_type=fuel.value,
                    comparisons=ranked,
                )

                eligible_ranked = [
                    c for c in ranked if c.definitive_ranking_eligible
                ]
                if not eligible_ranked:
                    continue

                best = eligible_ranked[0]
                if best.first_year_savings_aud >= threshold:
                    v_hash = best.offer.version_hash
                    if self.storage.has_alert_been_sent(
                        profile.household_id, best.offer.offer_id, v_hash
                    ):
                        suppressed_count += 1
                        continue

                    alert = AlertNotification(
                        alert_id=f"alert-{uuid.uuid4().hex[:10]}",
                        household_id=profile.household_id,
                        offer_id=best.offer.offer_id,
                        offer_version_hash=v_hash,
                        retailer=best.offer.retailer,
                        plan_name=best.offer.plan_name,
                        fuel_type=fuel.value,
                        first_year_savings_aud=best.first_year_savings_aud,
                        recurring_annual_savings_aud=best.recurring_annual_savings_aud,
                        threshold_aud=threshold,
                        source_url=best.offer.source_url,
                        triggered_at=datetime.now(timezone.utc).isoformat(),
                        summary_message=(
                            f"[{fuel.value}] Qualifying saving found for {profile.household_id}: "
                            f"Switch to {best.offer.retailer} ({best.offer.plan_name}) to save an estimated "
                            f"AUD ${best.first_year_savings_aud} in Year 1 "
                            f"(AUD ${best.recurring_annual_savings_aud}/yr ongoing recurring savings). "
                            f"Threshold: AUD ${threshold}. Source: {best.offer.source_url}"
                        ),
                    )
                    if self.storage.record_alert(alert):
                        new_alerts.append(alert)

        return MarketCheckSummary(
            checked_at=datetime.now(timezone.utc).isoformat(),
            total_offers_scanned=len(offers),
            new_or_changed_offer_versions=changed_count,
            households_evaluated=len(households),
            new_alerts_triggered=new_alerts,
            duplicate_alerts_suppressed=suppressed_count,
        )

    @staticmethod
    def send_email_alert(
        alert: AlertNotification,
        recipient_email: str,
        smtp_host: str,
        smtp_port: int = 587,
        smtp_user: Optional[str] = None,
        smtp_password: Optional[str] = None,
    ) -> bool:
        """Send an email notification for a qualifying energy saving opportunity (UC-05)."""
        msg = EmailMessage()
        msg["Subject"] = (
            f"Energy Savings Alert: Save est. AUD ${alert.first_year_savings_aud}/yr "
            f"with {alert.retailer}"
        )
        msg["From"] = smtp_user or "alerts@energy-savings-agent.local"
        msg["To"] = recipient_email
        msg.set_content(
            f"{alert.summary_message}\n\n"
            f"Offer ID: {alert.offer_id} (Version: {alert.offer_version_hash})\n"
            f"First-Year Estimated Savings: AUD ${alert.first_year_savings_aud}\n"
            f"Ongoing Recurring Annual Savings: AUD ${alert.recurring_annual_savings_aud}\n"
            f"Official Offer Link: {alert.source_url}\n\n"
            "Note: Calculations are based on your latest uploaded energy bill profile. "
            "Ranking is 100% independent of referral commissions."
        )
        try:
            with smtplib.SMTP(smtp_host, smtp_port, timeout=10) as server:
                if smtp_user and smtp_password:
                    server.starttls()
                    server.login(smtp_user, smtp_password)
                server.send_message(msg)
            return True
        except Exception:
            return False
