"""
Offer Discovery and Market Data Normalisation Module (UC-02, FR-03, FR-04).

Responsibilities:
1. Discover relevant residential electricity and gas plans for a household's location and network.
2. Support official Australian Consumer Data Right (CDR) Energy Product Reference Data (PRD)
   public API endpoints (`GET /cds-au/v1/energy/plans`) as well as curated NSW datasets.
3. Enrich structured offers with retailer promotions, sign-up credits, and source provenance.
4. Detect stale or expired offer records so recommendations never rely on outdated rates.
"""

from __future__ import annotations

import json
import urllib.request
import urllib.parse
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any, Dict, List, Optional, Union

from .models import (
    EligibilityRule,
    EnergyOffer,
    FuelType,
    HouseholdProfile,
    PromotionCredit,
    TariffType,
    to_decimal,
)

# Official Australian CDR Energy Product Reference Data (AER / Energy Made Easy) base URL
CDR_PRD_BASE_URL = "https://cdr.energymadeeasy.gov.au"
EME_REFDATA_URL = "https://api.energymadeeasy.gov.au/refdata2?keys=organisations,thirdParties"

# Verified Australian CDR retailer slugs hosted on cdr.energymadeeasy.gov.au
AUSTRALIAN_CDR_RETAILER_SLUGS: List[str] = [
    "agl",
    "origin",
    "energyaustralia",
    "redenergy",
    "ovoenergy",
    "globird",
    "alinta",
    "amber",
    "engie",
    "powershop",
    "momentum",
    "dodo",
    "lumo",
]

AUSTRALIAN_CDR_RETAILER_ENDPOINTS: Dict[str, str] = {
    slug: f"{CDR_PRD_BASE_URL}/{slug}/cds-au/v1/energy/plans"
    for slug in AUSTRALIAN_CDR_RETAILER_SLUGS
}

DEFAULT_OFFERS_PATH = (
    Path(__file__).resolve().parents[2] / "data" / "market_offers" / "nsw_offers.json"
)


@dataclass
class MarketCoverageReport:
    """Evidence of collection; not a claim of complete market coverage."""
    sources_attempted: List[str] = field(default_factory=list)
    failures: Dict[str, str] = field(default_factory=dict)
    plans_seen: int = 0
    plans_accepted: int = 0
    plans_rejected: int = 0
    curated_count: int = 0
    live_requested: bool = False

    @property
    def market_complete(self) -> bool:
        return False  # Curated registry cannot establish complete market coverage.


class OfferCollector:
    """Collects, normalises, and validates energy offers from CDR APIs and local datasets."""

    def __init__(
        self,
        offers_path: Optional[Union[str, Path]] = None,
        max_stale_days: int = 14,
    ) -> None:
        self.offers_path = Path(offers_path) if offers_path else DEFAULT_OFFERS_PATH
        self.max_stale_days = max_stale_days
        self.last_coverage_report = MarketCoverageReport()

    def load_curated_offers(
        self,
        exclude_stale: bool = True,
        reference_now: Optional[datetime] = None,
    ) -> List[EnergyOffer]:
        """Load normalised NSW market offers from the local JSON catalog."""
        if not self.offers_path.exists():
            return []

        raw_list = json.loads(self.offers_path.read_text(encoding="utf-8"))
        offers: List[EnergyOffer] = []
        now = reference_now or datetime.now(timezone.utc)

        for item in raw_list:
            offer = EnergyOffer.from_json_dict(item)
            if exclude_stale and self.is_offer_stale(offer, now=now):
                continue
            offers.append(offer)
        return offers

    def discover_offers_for_profile(
        self,
        profile: HouseholdProfile,
        include_live_cdr: bool = False,
        exclude_stale: bool = True,
    ) -> List[EnergyOffer]:
        """
        Discover all market offers matching the household's state and distribution networks (UC-02).
        """
        all_offers = self.load_curated_offers(exclude_stale=exclude_stale)
        report = MarketCoverageReport(
            curated_count=len(all_offers), live_requested=include_live_cdr
        )
        self.last_coverage_report = report
        if include_live_cdr:
            for retailer_key in AUSTRALIAN_CDR_RETAILER_SLUGS:
                for fuel in ("ELECTRICITY", "GAS"):
                    source = f"{retailer_key}:{fuel}"
                    report.sources_attempted.append(source)
                    try:
                        all_offers.extend(
                            self.fetch_cdr_plans_summary(retailer_key, fuel_type=fuel)
                        )
                    except Exception as exc:
                        report.failures[source] = str(exc)

        # Deduplicate records by plan, fuel, retailer and network.
        unique = {}
        for offer in all_offers:
            key = (offer.retailer.casefold(), offer.offer_id,
                   offer.fuel_type.value, offer.distributor.casefold())
            previous = unique.get(key)
            if previous is None or offer.retrieved_at > previous.retrieved_at:
                unique[key] = offer
        all_offers = list(unique.values())

        matching: List[EnergyOffer] = []
        for offer in all_offers:
            if offer.state.upper() != profile.state.upper():
                continue
            if offer.fuel_type == FuelType.ELECTRICITY:
                if (
                    offer.distributor.lower()
                    == profile.distributor_electricity.lower()
                ):
                    matching.append(offer)
            elif offer.fuel_type == FuelType.GAS:
                if offer.distributor.lower() == profile.distributor_gas.lower():
                    matching.append(offer)

        return matching

    def is_offer_stale(
        self,
        offer: EnergyOffer,
        now: Optional[datetime] = None,
    ) -> bool:
        """
        Verify that an offer's retrieval timestamp is within `max_stale_days`
        and that its promotions have not expired (Section 10 Risk Mitigation).
        """
        current_time = now or datetime.now(timezone.utc)
        try:
            retrieved = datetime.fromisoformat(
                offer.retrieved_at.replace("Z", "+00:00")
            )
            if retrieved.tzinfo is None:
                retrieved = retrieved.replace(tzinfo=timezone.utc)
            if (current_time - retrieved) > timedelta(days=self.max_stale_days):
                return True
        except Exception:
            return True

        # Filter out expired promotions in-place
        valid_promos: List[PromotionCredit] = []
        for promo in offer.promotions:
            if promo.expiry_date:
                try:
                    exp = datetime.fromisoformat(promo.expiry_date).replace(
                        tzinfo=timezone.utc
                    )
                    if exp < current_time:
                        continue
                except ValueError:
                    pass
            valid_promos.append(promo)
        offer.promotions = valid_promos
        return False

    def fetch_cdr_plans_summary(
        self,
        retailer_key: str,
        fuel_type: str = "ELECTRICITY",
        timeout_sec: float = 5.0,
    ) -> List[EnergyOffer]:
        """
        Query the official Australian Consumer Data Right (CDR) Energy Product Reference Data API
        Get Generic Plans (`GET /<retailer>/cds-au/v1/energy/plans`).
        Requires Consumer Data Standards headers `x-v: 1` and `x-min-v: 1`.
        Gracefully returns an empty list if offline.
        """
        slug = retailer_key.lower()
        base_url = AUSTRALIAN_CDR_RETAILER_ENDPOINTS.get(slug)
        if not base_url:
            return []

        if fuel_type not in ("ELECTRICITY", "GAS"):
            raise ValueError("Invalid fuel type")
        url = f"{base_url}?type=MARKET&fuelType={fuel_type}&page-size=100"
        visited = set()
        normalised = []
        report = self.last_coverage_report
        while url:
            if url in visited or len(visited) >= 100:
                raise RuntimeError("CDR pagination cycle or limit")
            visited.add(url)
            req = urllib.request.Request(url, headers={
                "x-v": "1", "x-min-v": "1", "Accept": "application/json",
                "User-Agent": "EnergySavingsAgent/0.1",
            })
            with urllib.request.urlopen(req, timeout=timeout_sec) as resp:
                payload = json.loads(resp.read().decode("utf-8"))
            plans = payload.get("data", {}).get("plans")
            if not isinstance(plans, list):
                raise ValueError("Invalid CDR plans response")
            for plan in plans:
                report.plans_seen += 1
                plan_id = plan.get("planId")
                detail = (self.fetch_cdr_plan_detail_v3(slug, str(plan_id), timeout_sec)
                          if plan_id else None)
                offer = (self._normalise_cdr_plan_metadata(detail, base_url)
                         if detail else None)
                if offer is None:
                    report.plans_rejected += 1
                else:
                    report.plans_accepted += 1
                    normalised.append(offer)
            next_url = payload.get("links", {}).get("next")
            if next_url:
                next_url = urllib.parse.urljoin(url, next_url)
                if urllib.parse.urlparse(next_url).netloc != urllib.parse.urlparse(base_url).netloc:
                    raise ValueError("Cross-host CDR pagination rejected")
            url = next_url
        return normalised

    def fetch_cdr_plan_detail_v3(
        self,
        retailer_slug: str,
        plan_id: str,
        timeout_sec: float = 5.0,
    ) -> Optional[Dict[str, Any]]:
        """
        Query the CDR Get Generic Plan Detail v3 endpoint:
        `GET https://cdr.energymadeeasy.gov.au/<retailer>/cds-au/v3/energy/plans/{planId}`
        Requires Consumer Data Standards headers `x-v: 3` and `x-min-v: 1`.
        """
        url = f"{CDR_PRD_BASE_URL}/{retailer_slug.lower()}/cds-au/v3/energy/plans/{plan_id}"
        req = urllib.request.Request(
            url,
            headers={
                "x-v": "3",
                "x-min-v": "1",
                "Accept": "application/json",
                "User-Agent": "EnergySavingsAgent/0.1 (NSW Residential Comparison)",
            },
        )
        try:
            with urllib.request.urlopen(req, timeout=timeout_sec) as resp:
                payload = json.loads(resp.read().decode("utf-8"))
            return payload.get("data")
        except Exception:
            return None

    def _normalise_cdr_plan_metadata(
        self,
        plan_json: Dict[str, Any],
        source_url: str,
    ) -> Optional[EnergyOffer]:
        """Normalise a CDR EnergyPlan / EnergyPlanDetail v3 record into our canonical EnergyOffer model (FR-04)."""
        if plan_json.get("customerType") not in (None, "RESIDENTIAL"):
            return None

        geography = plan_json.get("geography") or {}
        distributors = geography.get("distributors") or []
        states = geography.get("states") or []
        if len(distributors) != 1 or states != ["NSW"]:
            return None
        distributor = distributors[0]
        fuel_str = plan_json.get("fuelType")
        # The current pricing model cannot safely price live gas or complex tariffs.
        if fuel_str != "ELECTRICITY":
            return None
        contract = plan_json.get("electricityContract") or {}
        tariff_periods = contract.get("tariffPeriod") or []
        if len(tariff_periods) != 1:
            return None
        period = tariff_periods[0]
        if any(key in period for key in ("timeOfUseRates", "demandCharges",
                                        "controlledLoad", "blockRates")):
            return None
        rates = (period.get("singleRate") or {}).get("rates") or []
        supply = period.get("dailySupplyCharges")
        if supply is None or len(rates) != 1 or rates[0].get("unitPrice") is None:
            return None
        try:
            supply_cents = to_decimal(supply) * Decimal("100")
            single_rate_cents = to_decimal(rates[0]["unitPrice"]) * Decimal("100")
        except (ValueError, TypeError, ArithmeticError):
            return None
        if supply_cents < 0 or single_rate_cents < 0 or not plan_json.get("planId"):
            return None

        return EnergyOffer(
            offer_id=str(plan_json["planId"]),
            retailer=str(plan_json.get("brandName", "CDR Retailer")),
            plan_name=str(plan_json.get("displayName", "Market Plan")),
            fuel_type=FuelType(fuel_str),
            tariff_type=TariffType.SINGLE_RATE,
            distributor=distributor,
            state="NSW",
            daily_supply_charge_cents=supply_cents,
            single_rate_cents_kwh=single_rate_cents,
            source_url=source_url,
            retrieved_at=datetime.now(timezone.utc).isoformat(),
            eligibility=EligibilityRule(notes="Retrieved via live AER CDR API (v1/v3)"),
        )
