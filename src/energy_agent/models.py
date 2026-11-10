"""
Domain models for the AI-Powered Energy Offer Comparison and Savings Agent.

All monetary values, tariff rates, and energy quantities use Python's `Decimal`
to guarantee deterministic, reproducible arithmetic without floating-point drift.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from decimal import Decimal, ROUND_HALF_UP
from enum import Enum
from typing import Any, Dict, List, Optional


def to_decimal(value: Any, default: str = "0.00") -> Decimal:
    """Safely convert any numeric or string value to a Decimal."""
    if value is None or value == "":
        return Decimal(default)
    if isinstance(value, Decimal):
        return value
    return Decimal(str(value))


def round_currency(value: Decimal) -> Decimal:
    """Round a Decimal to 2 decimal places (AUD cents) using standard half-up rounding."""
    return value.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


class FuelType(str, Enum):
    ELECTRICITY = "ELECTRICITY"
    GAS = "GAS"


class TariffType(str, Enum):
    SINGLE_RATE = "SINGLE_RATE"
    TIME_OF_USE = "TIME_OF_USE"
    DEMAND = "DEMAND"
    STEPPED = "STEPPED"


class ValidationSeverity(str, Enum):
    INFO = "INFO"
    WARNING = "WARNING"
    CLARIFICATION_REQUIRED = "CLARIFICATION_REQUIRED"


@dataclass
class ValidationIssue:
    """Represents an uncertain or missing field detected during bill validation (UC-01)."""

    field_name: str
    severity: ValidationSeverity
    message: str
    suggested_action: str

    def to_dict(self) -> Dict[str, Any]:
        return {
            "field_name": self.field_name,
            "severity": self.severity.value,
            "message": self.message,
            "suggested_action": self.suggested_action,
        }


@dataclass
class ElectricityUsage:
    """Household electricity consumption profile extracted from a bill or interval data."""

    billing_days: int = 90
    total_kwh: Decimal = field(default_factory=lambda: Decimal("0"))
    peak_kwh: Optional[Decimal] = None
    shoulder_kwh: Optional[Decimal] = None
    off_peak_kwh: Optional[Decimal] = None
    controlled_load_kwh: Decimal = field(default_factory=lambda: Decimal("0"))
    solar_export_kwh: Decimal = field(default_factory=lambda: Decimal("0"))
    peak_demand_kw: Decimal = field(default_factory=lambda: Decimal("0"))

    def __post_init__(self) -> None:
        self.billing_days = int(self.billing_days)
        self.total_kwh = to_decimal(self.total_kwh)
        self.peak_kwh = to_decimal(self.peak_kwh) if self.peak_kwh is not None else None
        self.shoulder_kwh = (
            to_decimal(self.shoulder_kwh) if self.shoulder_kwh is not None else None
        )
        self.off_peak_kwh = (
            to_decimal(self.off_peak_kwh) if self.off_peak_kwh is not None else None
        )
        self.controlled_load_kwh = to_decimal(self.controlled_load_kwh)
        self.solar_export_kwh = to_decimal(self.solar_export_kwh)
        self.peak_demand_kw = to_decimal(self.peak_demand_kw)

    @property
    def has_tou_breakdown(self) -> bool:
        return (
            self.peak_kwh is not None
            and self.shoulder_kwh is not None
            and self.off_peak_kwh is not None
            and (self.peak_kwh + self.shoulder_kwh + self.off_peak_kwh) > Decimal("0")
        )

    @property
    def annualization_factor(self) -> Decimal:
        if self.billing_days <= 0:
            return Decimal("1")
        return Decimal("365") / Decimal(str(self.billing_days))


@dataclass
class GasUsage:
    """Household natural gas consumption profile in Megajoules (MJ)."""

    billing_days: int = 90
    total_mj: Decimal = field(default_factory=lambda: Decimal("0"))

    def __post_init__(self) -> None:
        self.billing_days = int(self.billing_days)
        self.total_mj = to_decimal(self.total_mj)

    @property
    def daily_average_mj(self) -> Decimal:
        if self.billing_days <= 0:
            return Decimal("0")
        return self.total_mj / Decimal(str(self.billing_days))

    @property
    def annual_mj(self) -> Decimal:
        if self.billing_days <= 0:
            return Decimal("0")
        return self.total_mj * (Decimal("365") / Decimal(str(self.billing_days)))


@dataclass
class CurrentPlanDetails:
    """Current tariff rates and charges extracted from the household's existing PDF bill."""

    retailer: str = "Unknown Retailer"
    plan_name: str = "Existing Plan"
    fuel_type: FuelType = FuelType.ELECTRICITY
    tariff_type: TariffType = TariffType.SINGLE_RATE
    distributor: str = "Ausgrid"
    daily_supply_charge_cents: Decimal = field(default_factory=lambda: Decimal("110.0"))
    single_rate_cents_kwh: Optional[Decimal] = None
    peak_rate_cents_kwh: Optional[Decimal] = None
    shoulder_rate_cents_kwh: Optional[Decimal] = None
    off_peak_rate_cents_kwh: Optional[Decimal] = None
    controlled_load_rate_cents_kwh: Decimal = field(default_factory=lambda: Decimal("0"))
    demand_rate_cents_kw_day: Decimal = field(default_factory=lambda: Decimal("0"))
    solar_fit_cents_kwh: Decimal = field(default_factory=lambda: Decimal("5.0"))
    gas_block1_rate_cents_mj: Optional[Decimal] = None
    gas_block1_limit_mj_day: Optional[Decimal] = None
    gas_remainder_rate_cents_mj: Optional[Decimal] = None
    usage_discount_pct: Decimal = field(default_factory=lambda: Decimal("0"))
    supply_discount_pct: Decimal = field(default_factory=lambda: Decimal("0"))
    annual_fees_aud: Decimal = field(default_factory=lambda: Decimal("0"))
    billed_total_aud: Optional[Decimal] = None

    def __post_init__(self) -> None:
        if isinstance(self.fuel_type, str):
            self.fuel_type = FuelType(self.fuel_type)
        if isinstance(self.tariff_type, str):
            self.tariff_type = TariffType(self.tariff_type)
        self.daily_supply_charge_cents = to_decimal(self.daily_supply_charge_cents)
        self.single_rate_cents_kwh = (
            to_decimal(self.single_rate_cents_kwh)
            if self.single_rate_cents_kwh is not None
            else None
        )
        self.peak_rate_cents_kwh = (
            to_decimal(self.peak_rate_cents_kwh)
            if self.peak_rate_cents_kwh is not None
            else None
        )
        self.shoulder_rate_cents_kwh = (
            to_decimal(self.shoulder_rate_cents_kwh)
            if self.shoulder_rate_cents_kwh is not None
            else None
        )
        self.off_peak_rate_cents_kwh = (
            to_decimal(self.off_peak_rate_cents_kwh)
            if self.off_peak_rate_cents_kwh is not None
            else None
        )
        self.controlled_load_rate_cents_kwh = to_decimal(
            self.controlled_load_rate_cents_kwh
        )
        self.demand_rate_cents_kw_day = to_decimal(self.demand_rate_cents_kw_day)
        self.solar_fit_cents_kwh = to_decimal(self.solar_fit_cents_kwh)
        self.gas_block1_rate_cents_mj = (
            to_decimal(self.gas_block1_rate_cents_mj)
            if self.gas_block1_rate_cents_mj is not None
            else None
        )
        self.gas_block1_limit_mj_day = (
            to_decimal(self.gas_block1_limit_mj_day)
            if self.gas_block1_limit_mj_day is not None
            else None
        )
        self.gas_remainder_rate_cents_mj = (
            to_decimal(self.gas_remainder_rate_cents_mj)
            if self.gas_remainder_rate_cents_mj is not None
            else None
        )
        self.usage_discount_pct = to_decimal(self.usage_discount_pct)
        self.supply_discount_pct = to_decimal(self.supply_discount_pct)
        self.annual_fees_aud = to_decimal(self.annual_fees_aud)
        self.billed_total_aud = (
            to_decimal(self.billed_total_aud)
            if self.billed_total_aud is not None
            else None
        )


@dataclass
class HouseholdProfile:
    """Validated household energy profile and baseline for comparison (UC-01, FR-02)."""

    household_id: str
    postcode: str = "2000"
    suburb: str = "Sydney"
    state: str = "NSW"
    distributor_electricity: str = "Ausgrid"
    distributor_gas: str = "Jemena Gas"
    has_solar: bool = False
    solar_capacity_kw: Decimal = field(default_factory=lambda: Decimal("0"))
    has_battery: bool = False
    has_ev: bool = False
    concession_card: bool = False
    accepts_direct_debit: bool = True
    accepts_ebilling: bool = True
    alert_threshold_aud: Decimal = field(default_factory=lambda: Decimal("100.00"))
    notification_email: Optional[str] = None
    electricity_usage: Optional[ElectricityUsage] = None
    gas_usage: Optional[GasUsage] = None
    current_electricity_plan: Optional[CurrentPlanDetails] = None
    current_gas_plan: Optional[CurrentPlanDetails] = None
    baseline_electricity_annual_aud: Optional[Decimal] = None
    baseline_gas_annual_aud: Optional[Decimal] = None
    validation_issues: List[ValidationIssue] = field(default_factory=list)
    confidence_score: Decimal = field(default_factory=lambda: Decimal("0.90"))
    created_at: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )

    def __post_init__(self) -> None:
        self.solar_capacity_kw = to_decimal(self.solar_capacity_kw)
        self.alert_threshold_aud = to_decimal(self.alert_threshold_aud, "100.00")
        self.confidence_score = to_decimal(self.confidence_score, "0.90")
        if self.baseline_electricity_annual_aud is not None:
            self.baseline_electricity_annual_aud = to_decimal(
                self.baseline_electricity_annual_aud
            )
        if self.baseline_gas_annual_aud is not None:
            self.baseline_gas_annual_aud = to_decimal(self.baseline_gas_annual_aud)

    @property
    def needs_clarification(self) -> bool:
        return any(
            issue.severity == ValidationSeverity.CLARIFICATION_REQUIRED
            for issue in self.validation_issues
        )

    def to_json_dict(self) -> Dict[str, Any]:
        """Serialize profile to a JSON-friendly dictionary with stringified Decimals."""
        return json.loads(json.dumps(asdict(self), default=str))

    @classmethod
    def from_json_dict(cls, data: Dict[str, Any]) -> "HouseholdProfile":
        """Reconstruct a HouseholdProfile from a dictionary."""
        elec_usage = (
            ElectricityUsage(**data["electricity_usage"])
            if data.get("electricity_usage")
            else None
        )
        gas_usage = (
            GasUsage(**data["gas_usage"]) if data.get("gas_usage") else None
        )
        curr_elec = (
            CurrentPlanDetails(**data["current_electricity_plan"])
            if data.get("current_electricity_plan")
            else None
        )
        curr_gas = (
            CurrentPlanDetails(**data["current_gas_plan"])
            if data.get("current_gas_plan")
            else None
        )
        issues = [
            ValidationIssue(
                field_name=i["field_name"],
                severity=ValidationSeverity(i["severity"]),
                message=i["message"],
                suggested_action=i["suggested_action"],
            )
            for i in data.get("validation_issues", [])
        ]
        return cls(
            household_id=data["household_id"],
            postcode=str(data.get("postcode", "2000")),
            suburb=data.get("suburb", "Sydney"),
            state=data.get("state", "NSW"),
            distributor_electricity=data.get("distributor_electricity", "Ausgrid"),
            distributor_gas=data.get("distributor_gas", "Jemena Gas"),
            has_solar=bool(data.get("has_solar", False)),
            solar_capacity_kw=to_decimal(data.get("solar_capacity_kw", "0")),
            has_battery=bool(data.get("has_battery", False)),
            has_ev=bool(data.get("has_ev", False)),
            concession_card=bool(data.get("concession_card", False)),
            accepts_direct_debit=bool(data.get("accepts_direct_debit", True)),
            accepts_ebilling=bool(data.get("accepts_ebilling", True)),
            alert_threshold_aud=to_decimal(data.get("alert_threshold_aud", "100.00")),
            notification_email=data.get("notification_email"),
            electricity_usage=elec_usage,
            gas_usage=gas_usage,
            current_electricity_plan=curr_elec,
            current_gas_plan=curr_gas,
            baseline_electricity_annual_aud=(
                to_decimal(data["baseline_electricity_annual_aud"])
                if data.get("baseline_electricity_annual_aud") is not None
                else None
            ),
            baseline_gas_annual_aud=(
                to_decimal(data["baseline_gas_annual_aud"])
                if data.get("baseline_gas_annual_aud") is not None
                else None
            ),
            validation_issues=issues,
            confidence_score=to_decimal(data.get("confidence_score", "0.90")),
            created_at=data.get(
                "created_at", datetime.now(timezone.utc).isoformat()
            ),
        )


@dataclass
class PromotionCredit:
    """One-off or recurring promotional credit discovered on a retailer offer (UC-02, FR-03)."""

    title: str
    amount_aud: Decimal
    is_recurring: bool = False
    conditions: str = "New customers switching online"
    expiry_date: Optional[str] = None
    source_url: Optional[str] = None

    def __post_init__(self) -> None:
        self.amount_aud = to_decimal(self.amount_aud)


@dataclass
class EligibilityRule:
    """Explicit eligibility conditions attached to an energy plan (UC-02, UC-04)."""

    requires_new_customer: bool = False
    requires_direct_debit: bool = False
    requires_ebilling: bool = False
    requires_ev: bool = False
    max_solar_inverter_kw: Optional[Decimal] = None
    excluded_if_battery: bool = False
    notes: str = ""

    def __post_init__(self) -> None:
        if self.max_solar_inverter_kw is not None:
            self.max_solar_inverter_kw = to_decimal(self.max_solar_inverter_kw)


@dataclass
class EnergyOffer:
    """Structured market energy plan with rates, promotions, eligibility and provenance (UC-02, FR-04)."""

    offer_id: str
    retailer: str
    plan_name: str
    fuel_type: FuelType
    tariff_type: TariffType
    distributor: str
    state: str = "NSW"
    daily_supply_charge_cents: Decimal = field(default_factory=lambda: Decimal("100.0"))
    single_rate_cents_kwh: Optional[Decimal] = None
    peak_rate_cents_kwh: Optional[Decimal] = None
    shoulder_rate_cents_kwh: Optional[Decimal] = None
    off_peak_rate_cents_kwh: Optional[Decimal] = None
    controlled_load_rate_cents_kwh: Decimal = field(default_factory=lambda: Decimal("0"))
    demand_rate_cents_kw_day: Decimal = field(default_factory=lambda: Decimal("0"))
    solar_fit_cents_kwh: Decimal = field(default_factory=lambda: Decimal("5.0"))
    solar_fit_daily_cap_kwh: Optional[Decimal] = None
    solar_fit_remainder_cents_kwh: Optional[Decimal] = None
    gas_block1_rate_cents_mj: Optional[Decimal] = None
    gas_block1_limit_mj_day: Optional[Decimal] = None
    gas_remainder_rate_cents_mj: Optional[Decimal] = None
    unconditional_usage_discount_pct: Decimal = field(
        default_factory=lambda: Decimal("0")
    )
    conditional_usage_discount_pct: Decimal = field(
        default_factory=lambda: Decimal("0")
    )
    conditional_discount_condition: str = ""
    annual_fees_aud: Decimal = field(default_factory=lambda: Decimal("0"))
    promotions: List[PromotionCredit] = field(default_factory=list)
    eligibility: EligibilityRule = field(default_factory=EligibilityRule)
    contract_term: str = "No lock-in contract"
    green_power_note: str = ""
    source_url: str = "https://www.energymadeeasy.gov.au/"
    retrieved_at: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )
    is_partner_referral: bool = False
    referral_commission_aud: Decimal = field(default_factory=lambda: Decimal("0"))
    referral_url: Optional[str] = None

    def __post_init__(self) -> None:
        if isinstance(self.fuel_type, str):
            self.fuel_type = FuelType(self.fuel_type)
        if isinstance(self.tariff_type, str):
            self.tariff_type = TariffType(self.tariff_type)
        self.daily_supply_charge_cents = to_decimal(self.daily_supply_charge_cents)
        self.single_rate_cents_kwh = (
            to_decimal(self.single_rate_cents_kwh)
            if self.single_rate_cents_kwh is not None
            else None
        )
        self.peak_rate_cents_kwh = (
            to_decimal(self.peak_rate_cents_kwh)
            if self.peak_rate_cents_kwh is not None
            else None
        )
        self.shoulder_rate_cents_kwh = (
            to_decimal(self.shoulder_rate_cents_kwh)
            if self.shoulder_rate_cents_kwh is not None
            else None
        )
        self.off_peak_rate_cents_kwh = (
            to_decimal(self.off_peak_rate_cents_kwh)
            if self.off_peak_rate_cents_kwh is not None
            else None
        )
        self.controlled_load_rate_cents_kwh = to_decimal(
            self.controlled_load_rate_cents_kwh
        )
        self.demand_rate_cents_kw_day = to_decimal(self.demand_rate_cents_kw_day)
        self.solar_fit_cents_kwh = to_decimal(self.solar_fit_cents_kwh)
        self.solar_fit_daily_cap_kwh = (
            to_decimal(self.solar_fit_daily_cap_kwh)
            if self.solar_fit_daily_cap_kwh is not None
            else None
        )
        self.solar_fit_remainder_cents_kwh = (
            to_decimal(self.solar_fit_remainder_cents_kwh)
            if self.solar_fit_remainder_cents_kwh is not None
            else None
        )
        self.gas_block1_rate_cents_mj = (
            to_decimal(self.gas_block1_rate_cents_mj)
            if self.gas_block1_rate_cents_mj is not None
            else None
        )
        self.gas_block1_limit_mj_day = (
            to_decimal(self.gas_block1_limit_mj_day)
            if self.gas_block1_limit_mj_day is not None
            else None
        )
        self.gas_remainder_rate_cents_mj = (
            to_decimal(self.gas_remainder_rate_cents_mj)
            if self.gas_remainder_rate_cents_mj is not None
            else None
        )
        self.unconditional_usage_discount_pct = to_decimal(
            self.unconditional_usage_discount_pct
        )
        self.conditional_usage_discount_pct = to_decimal(
            self.conditional_usage_discount_pct
        )
        self.annual_fees_aud = to_decimal(self.annual_fees_aud)
        self.referral_commission_aud = to_decimal(self.referral_commission_aud)

    @property
    def version_hash(self) -> str:
        """Compute a deterministic content hash of rates & promotions for change tracking (UC-06)."""
        payload = {
            "offer_id": self.offer_id,
            "retailer": self.retailer,
            "plan_name": self.plan_name,
            "fuel_type": self.fuel_type.value,
            "tariff_type": self.tariff_type.value,
            "distributor": self.distributor,
            "daily_supply_charge_cents": str(self.daily_supply_charge_cents),
            "single_rate_cents_kwh": str(self.single_rate_cents_kwh),
            "peak_rate_cents_kwh": str(self.peak_rate_cents_kwh),
            "shoulder_rate_cents_kwh": str(self.shoulder_rate_cents_kwh),
            "off_peak_rate_cents_kwh": str(self.off_peak_rate_cents_kwh),
            "controlled_load_rate_cents_kwh": str(self.controlled_load_rate_cents_kwh),
            "demand_rate_cents_kw_day": str(self.demand_rate_cents_kw_day),
            "solar_fit_cents_kwh": str(self.solar_fit_cents_kwh),
            "solar_fit_daily_cap_kwh": str(self.solar_fit_daily_cap_kwh),
            "solar_fit_remainder_cents_kwh": str(self.solar_fit_remainder_cents_kwh),
            "gas_block1_rate_cents_mj": str(self.gas_block1_rate_cents_mj),
            "gas_block1_limit_mj_day": str(self.gas_block1_limit_mj_day),
            "gas_remainder_rate_cents_mj": str(self.gas_remainder_rate_cents_mj),
            "unconditional_usage_discount_pct": str(
                self.unconditional_usage_discount_pct
            ),
            "conditional_usage_discount_pct": str(self.conditional_usage_discount_pct),
            "annual_fees_aud": str(self.annual_fees_aud),
            "promotions": [
                {
                    "title": p.title,
                    "amount_aud": str(p.amount_aud),
                    "is_recurring": p.is_recurring,
                }
                for p in self.promotions
            ],
        }
        canonical = json.dumps(payload, sort_keys=True)
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:16]

    def to_json_dict(self) -> Dict[str, Any]:
        return json.loads(json.dumps(asdict(self), default=str))

    @classmethod
    def from_json_dict(cls, data: Dict[str, Any]) -> "EnergyOffer":
        promos = [PromotionCredit(**p) for p in data.get("promotions", [])]
        elig_data = data.get("eligibility", {})
        elig = (
            EligibilityRule(**elig_data)
            if isinstance(elig_data, dict)
            else EligibilityRule()
        )
        copy_data = dict(data)
        copy_data["promotions"] = promos
        copy_data["eligibility"] = elig
        return cls(**copy_data)


@dataclass
class CostBreakdown:
    """Transparent itemized annual cost breakdown from the deterministic pricing engine (Section 8)."""

    supply_cost_aud: Decimal
    usage_cost_aud: Decimal
    controlled_load_cost_aud: Decimal
    demand_cost_aud: Decimal
    discount_amount_aud: Decimal
    solar_fit_credit_aud: Decimal
    annual_fees_aud: Decimal
    recurring_credits_aud: Decimal
    recurring_annual_cost_aud: Decimal
    one_off_promotions_aud: Decimal
    first_year_net_cost_aud: Decimal
    assumptions: List[str] = field(default_factory=list)


@dataclass
class OfferComparison:
    """Comparison result for a single EnergyOffer against the household's baseline (UC-03, UC-04)."""

    offer: EnergyOffer
    breakdown: CostBreakdown
    baseline_annual_cost_aud: Decimal
    recurring_annual_savings_aud: Decimal
    first_year_savings_aud: Decimal
    is_eligible: bool
    eligibility_notes: List[str] = field(default_factory=list)
    warning_flags: List[str] = field(default_factory=list)
    definitive_ranking_eligible: bool = True
    rank_first_year: Optional[int] = None
    rank_recurring: Optional[int] = None
    explanation: str = ""


@dataclass
class DualFuelComparison:
    """Combined Electricity + Gas annual cost and savings comparison (FR-10)."""

    electricity_comparison: OfferComparison
    gas_comparison: OfferComparison
    combined_baseline_aud: Decimal
    combined_first_year_cost_aud: Decimal
    combined_recurring_cost_aud: Decimal
    combined_first_year_savings_aud: Decimal
    combined_recurring_savings_aud: Decimal
    same_retailer_bundle: bool


@dataclass
class AlertNotification:
    """Threshold-based market monitoring notification record (UC-05, UC-06, FR-08, FR-09)."""

    alert_id: str
    household_id: str
    offer_id: str
    offer_version_hash: str
    retailer: str
    plan_name: str
    fuel_type: str
    first_year_savings_aud: Decimal
    recurring_annual_savings_aud: Decimal
    threshold_aud: Decimal
    source_url: str
    triggered_at: str
    summary_message: str
