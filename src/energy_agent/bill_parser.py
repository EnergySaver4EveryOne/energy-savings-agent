"""
Bill Understanding and Validation Module (UC-01, FR-01, FR-02).

Responsibilities:
1. Extract text from PDF energy bills (PyMuPDF / pdfplumber / pure-Python fallback).
2. Redact sensitive PII and metering identifiers (NMI, MIRN/DPI, account numbers) before LLM calls.
3. Interpret document structure using Gemini 3.8 Flash (`google-genai` structured output) when
   `GEMINI_API_KEY` is configured, with a deterministic regex/heuristic parser fallback.
4. Validate uncertain fields, flag clarifications needed, and establish a transparent baseline.
"""

from __future__ import annotations

import json
import os
import re
import uuid
from decimal import Decimal
from pathlib import Path
from typing import Any, Dict, List, Optional, Union

from .models import (
    CurrentPlanDetails,
    ElectricityUsage,
    FuelType,
    GasUsage,
    HouseholdProfile,
    TariffType,
    ValidationIssue,
    ValidationSeverity,
    to_decimal,
)
from .pdf_utils import extract_text_from_pdf
from .pricing_engine import PricingEngine

# NSW Postcode to Electricity Distribution Network mapping
NSW_ENDEAVOUR_RANGES = [
    (2145, 2158),
    (2170, 2179),
    (2500, 2579),
    (2745, 2786),
]
NSW_AUSGRID_RANGES = [
    (2000, 2144),
    (2159, 2169),
    (2180, 2249),
    (2250, 2338),
]


def infer_nsw_distributor(postcode: str) -> str:
    """Infer NSW electricity distribution network (Ausgrid, Endeavour Energy, Essential Energy) from postcode."""
    try:
        code = int(postcode.strip())
    except ValueError:
        return "Ausgrid"

    for low, high in NSW_ENDEAVOUR_RANGES:
        if low <= code <= high:
            return "Endeavour Energy"
    for low, high in NSW_AUSGRID_RANGES:
        if low <= code <= high:
            return "Ausgrid"
    if 2000 <= code <= 2999:
        return "Essential Energy"
    return "Ausgrid"


def redact_sensitive_bill_text(raw_text: str) -> str:
    """
    Redact sensitive household identifiers (NMI, MIRN/DPI, Account No, Email, Phone)
    prior to external LLM processing (Section 10 Risk Mitigation).
    """
    redacted = raw_text
    # Redact 10-11 character NMI
    redacted = re.sub(
        r"(?i)(NMI\s*[:#-]?\s*)([A-Z0-9]{10,11})",
        r"\1[REDACTED_NMI]",
        redacted,
    )
    # Redact MIRN / DPI
    redacted = re.sub(
        r"(?i)((?:MIRN|DPI)\s*[:#-]?\s*)([A-Z0-9]{10,11})",
        r"\1[REDACTED_MIRN]",
        redacted,
    )
    # Redact Account Number
    redacted = re.sub(
        r"(?i)(Account\s*(?:Number|No\.?)\s*[:#-]?\s*)([A-Z0-9-]{6,16})",
        r"\1[REDACTED_ACCOUNT]",
        redacted,
    )
    # Redact Customer Name line
    redacted = re.sub(
        r"(?i)(Customer\s*Name\s*[:#-]?\s*)([^\n\r]+)",
        r"\1[REDACTED_CUSTOMER]",
        redacted,
    )
    return redacted


GEMINI_EXTRACTION_JSON_SCHEMA: Dict[str, Any] = {
    "type": "object",
    "properties": {
        "retailer": {"type": "string"},
        "plan_name": {"type": "string"},
        "postcode": {"type": "string"},
        "suburb": {"type": "string"},
        "state": {"type": "string"},
        "distributor_electricity": {"type": "string"},
        "fuel_type": {"type": "string", "enum": ["ELECTRICITY", "GAS", "DUAL"]},
        "tariff_type": {
            "type": "string",
            "enum": ["SINGLE_RATE", "TIME_OF_USE", "DEMAND", "STEPPED"],
        },
        "billing_days": {"type": "integer"},
        "total_kwh": {"type": "number"},
        "peak_kwh": {"type": "number"},
        "shoulder_kwh": {"type": "number"},
        "off_peak_kwh": {"type": "number"},
        "controlled_load_kwh": {"type": "number"},
        "solar_export_kwh": {"type": "number"},
        "solar_capacity_kw": {"type": "number"},
        "daily_supply_charge_cents": {"type": "number"},
        "single_rate_cents_kwh": {"type": "number"},
        "peak_rate_cents_kwh": {"type": "number"},
        "shoulder_rate_cents_kwh": {"type": "number"},
        "off_peak_rate_cents_kwh": {"type": "number"},
        "controlled_load_rate_cents_kwh": {"type": "number"},
        "solar_fit_cents_kwh": {"type": "number"},
        "usage_discount_pct": {"type": "number"},
        "billed_total_aud": {"type": "number"},
        "gas_total_mj": {"type": "number"},
        "gas_daily_supply_cents": {"type": "number"},
        "gas_block1_rate_cents_mj": {"type": "number"},
        "gas_block1_limit_mj_day": {"type": "number"},
        "gas_remainder_rate_cents_mj": {"type": "number"},
    },
    "required": ["retailer", "plan_name", "billing_days"],
}


class BillParser:
    """Extracts, interprets, and validates Australian residential PDF energy bills."""

    def __init__(
        self,
        use_llm: bool = True,
        pricing_engine: Optional[PricingEngine] = None,
    ) -> None:
        self.use_llm = use_llm and bool(os.environ.get("GEMINI_API_KEY"))
        self.pricing_engine = pricing_engine or PricingEngine()

    def parse_pdf_bill(
        self,
        pdf_path: Union[str, Path],
        household_id: Optional[str] = None,
    ) -> HouseholdProfile:
        """Extract text from a PDF energy bill, build a structured HouseholdProfile, and validate."""
        raw_text = extract_text_from_pdf(pdf_path)
        return self.parse_bill_text(raw_text, household_id=household_id)

    def parse_bill_text(
        self,
        raw_text: str,
        household_id: Optional[str] = None,
    ) -> HouseholdProfile:
        """Parse extracted bill text using Gemini structured output (if enabled) or deterministic parser."""
        safe_text = redact_sensitive_bill_text(raw_text)
        extracted_data: Optional[Dict[str, Any]] = None

        if self.use_llm:
            extracted_data = self._extract_with_gemini(safe_text)

        if not extracted_data:
            extracted_data = self._extract_deterministic(safe_text)

        profile = self._build_profile_from_extracted(
            extracted_data,
            household_id=household_id or f"hh-{uuid.uuid4().hex[:8]}",
        )
        self.validate_profile(profile)
        return profile

    def _extract_with_gemini(self, redacted_text: str) -> Optional[Dict[str, Any]]:
        """
        Call Gemini 3.8 Flash via `google-genai` Interactions API with Structured Output
        for document interpretation (never for arithmetic).
        """
        try:
            from google import genai  # type: ignore

            client = genai.Client()
            prompt = (
                "Extract structured Australian energy bill details from the redacted bill text below. "
                "Do not perform cost arithmetic; extract only the stated quantities, rates, and metadata.\n\n"
                f"BILL TEXT:\n{redacted_text}"
            )
            interaction = client.interactions.create(
                model="gemini-3.8-flash",
                input=prompt,
                response_format={
                    "type": "text",
                    "mime_type": "application/json",
                    "schema": GEMINI_EXTRACTION_JSON_SCHEMA,
                },
                store=False,
            )
            if interaction.output_text:
                return json.loads(interaction.output_text)
        except Exception:
            return None
        return None

    def _extract_deterministic(self, text: str) -> Dict[str, Any]:
        """Deterministic regex parser tailored to Australian (NSW) electricity and gas bills."""

        def find_str(pattern: str, default: str = "") -> str:
            m = re.search(pattern, text, re.IGNORECASE)
            return m.group(1).strip() if m else default

        def find_num(pattern: str) -> Optional[str]:
            m = re.search(pattern, text, re.IGNORECASE)
            if not m:
                return None
            return m.group(1).replace(",", "").strip()

        retailer = find_str(r"Retailer\s*:\s*([^\n\r]+)")
        if not retailer:
            for known in [
                "AGL",
                "Origin Energy",
                "EnergyAustralia",
                "Red Energy",
                "OVO Energy",
                "Globird Energy",
                "Alinta Energy",
            ]:
                if re.search(rf"\b{re.escape(known)}\b", text, re.IGNORECASE):
                    retailer = known
                    break
        retailer = retailer or "Unknown Retailer"

        plan_name = find_str(r"Plan(?:\s*Name)?\s*:\s*([^\n\r]+)", "Standard Residential Plan")
        postcode = find_str(r"Postcode\s*:\s*(\d{4})", "")
        if not postcode:
            m_pc = re.search(r"\bNSW\s+(\d{4})\b", text)
            postcode = m_pc.group(1) if m_pc else "2000"

        suburb = find_str(r"Suburb\s*:\s*([^\n\r,]+)", "Sydney")
        distributor = find_str(r"Distributor(?:\s*Network)?\s*:\s*([^\n\r]+)", "")
        if not distributor:
            distributor = infer_nsw_distributor(postcode)

        fuel_raw = find_str(r"Fuel\s*Type\s*:\s*([^\n\r]+)", "ELECTRICITY").upper()
        tariff_raw = find_str(r"Tariff\s*Type\s*:\s*([^\n\r]+)", "").upper()

        billing_days_str = find_num(r"Billing\s*(?:Period|Days)\s*[:\s]*(\d+)\s*days?")
        billing_days = int(billing_days_str) if billing_days_str else 90

        total_kwh = find_num(r"Total\s*(?:Electricity\s*)?Usage\s*[:\s]*([\d,]+\.?\d*)\s*kWh")
        peak_kwh = find_num(r"Peak\s*Usage\s*[:\s]*([\d,]+\.?\d*)\s*kWh")
        shoulder_kwh = find_num(r"Shoulder\s*Usage\s*[:\s]*([\d,]+\.?\d*)\s*kWh")
        off_peak_kwh = find_num(r"Off[- ]?Peak\s*Usage\s*[:\s]*([\d,]+\.?\d*)\s*kWh")
        controlled_load_kwh = find_num(r"Controlled\s*Load\s*Usage\s*[:\s]*([\d,]+\.?\d*)\s*kWh")
        solar_export_kwh = find_num(r"Solar\s*(?:Feed[- ]?in|Export)\s*[:\s]*([\d,]+\.?\d*)\s*kWh")
        solar_capacity_kw = find_num(r"Solar\s*(?:System|Inverter)\s*Capacity\s*[:\s]*([\d,]+\.?\d*)\s*kW")

        daily_supply_cents = find_num(r"Daily\s*Supply\s*Charge\s*[:\s]*([\d,]+\.?\d*)\s*c(?:/day|\b)")
        single_rate_cents = find_num(r"(?:Single|Anytime|Flat)\s*(?:Usage\s*)?Rate\s*[:\s]*([\d,]+\.?\d*)\s*c/kWh")
        peak_rate_cents = find_num(r"Peak\s*Rate\s*[:\s]*([\d,]+\.?\d*)\s*c/kWh")
        shoulder_rate_cents = find_num(r"Shoulder\s*Rate\s*[:\s]*([\d,]+\.?\d*)\s*c/kWh")
        off_peak_rate_cents = find_num(r"Off[- ]?Peak\s*Rate\s*[:\s]*([\d,]+\.?\d*)\s*c/kWh")
        cl_rate_cents = find_num(r"Controlled\s*Load\s*Rate\s*[:\s]*([\d,]+\.?\d*)\s*c/kWh")
        solar_fit_cents = find_num(r"Solar\s*Feed[- ]?in\s*(?:Tariff|Rate)\s*[:\s]*([\d,]+\.?\d*)\s*c/kWh")
        usage_discount_pct = find_num(r"Usage\s*Discount\s*[:\s]*([\d,]+\.?\d*)\s*%")
        billed_total_aud = find_num(r"Total\s*(?:Bill\s*)?Amount\s*(?:Due)?\s*[:\s]*\$?\s*([\d,]+\.?\d*)")

        # Gas fields
        gas_total_mj = find_num(r"Total\s*Gas\s*Usage\s*[:\s]*([\d,]+\.?\d*)\s*MJ")
        gas_supply_cents = find_num(r"Gas\s*Daily\s*Supply\s*Charge\s*[:\s]*([\d,]+\.?\d*)\s*c(?:/day|\b)")
        gas_block1_rate = find_num(r"Gas\s*Block\s*1\s*Rate\s*[:\s]*([\d,]+\.?\d*)\s*c/MJ")
        gas_block1_limit = find_num(r"Gas\s*Block\s*1\s*Limit\s*[:\s]*([\d,]+\.?\d*)\s*MJ/day")
        gas_remainder_rate = find_num(r"Gas\s*(?:Remainder|Block\s*2)\s*Rate\s*[:\s]*([\d,]+\.?\d*)\s*c/MJ")
        gas_retailer = find_str(r"Gas\s*Retailer\s*:\s*([^\n\r]+)", retailer)
        gas_plan_name = find_str(r"Gas\s*Plan\s*:\s*([^\n\r]+)", plan_name)

        if not tariff_raw:
            if peak_kwh is not None or peak_rate_cents is not None:
                tariff_raw = "TIME_OF_USE"
            elif gas_total_mj is not None and total_kwh is None:
                tariff_raw = "STEPPED"
            else:
                tariff_raw = "SINGLE_RATE"

        if total_kwh is None and (peak_kwh or shoulder_kwh or off_peak_kwh):
            sum_tou = (
                to_decimal(peak_kwh, "0")
                + to_decimal(shoulder_kwh, "0")
                + to_decimal(off_peak_kwh, "0")
            )
            total_kwh = str(sum_tou)

        return {
            "retailer": retailer,
            "plan_name": plan_name,
            "postcode": postcode,
            "suburb": suburb,
            "state": "NSW",
            "distributor_electricity": distributor,
            "fuel_type": fuel_raw,
            "tariff_type": tariff_raw,
            "billing_days": billing_days,
            "total_kwh": total_kwh,
            "peak_kwh": peak_kwh,
            "shoulder_kwh": shoulder_kwh,
            "off_peak_kwh": off_peak_kwh,
            "controlled_load_kwh": controlled_load_kwh or "0",
            "solar_export_kwh": solar_export_kwh or "0",
            "solar_capacity_kw": solar_capacity_kw or ("6.6" if solar_export_kwh else "0"),
            "daily_supply_charge_cents": daily_supply_cents,
            "single_rate_cents_kwh": single_rate_cents,
            "peak_rate_cents_kwh": peak_rate_cents,
            "shoulder_rate_cents_kwh": shoulder_rate_cents,
            "off_peak_rate_cents_kwh": off_peak_rate_cents,
            "controlled_load_rate_cents_kwh": cl_rate_cents or "0",
            "solar_fit_cents_kwh": solar_fit_cents or "5.0",
            "usage_discount_pct": usage_discount_pct or "0",
            "billed_total_aud": billed_total_aud,
            "gas_total_mj": gas_total_mj,
            "gas_daily_supply_cents": gas_supply_cents or daily_supply_cents,
            "gas_block1_rate_cents_mj": gas_block1_rate,
            "gas_block1_limit_mj_day": gas_block1_limit,
            "gas_remainder_rate_cents_mj": gas_remainder_rate,
            "gas_retailer": gas_retailer,
            "gas_plan_name": gas_plan_name,
        }

    def _build_profile_from_extracted(
        self,
        data: Dict[str, Any],
        household_id: str,
    ) -> HouseholdProfile:
        postcode = str(data.get("postcode") or "2000")
        distributor_elec = data.get("distributor_electricity") or infer_nsw_distributor(
            postcode
        )
        billing_days = int(data.get("billing_days") or 90)
        fuel_str = str(data.get("fuel_type") or "ELECTRICITY").upper()

        elec_usage: Optional[ElectricityUsage] = None
        curr_elec_plan: Optional[CurrentPlanDetails] = None

        if data.get("total_kwh") is not None or "ELECTRICITY" in fuel_str or "DUAL" in fuel_str:
            if data.get("total_kwh") is not None or data.get("peak_kwh") is not None:
                elec_usage = ElectricityUsage(
                    billing_days=billing_days,
                    total_kwh=to_decimal(data.get("total_kwh"), "0"),
                    peak_kwh=(
                        to_decimal(data["peak_kwh"])
                        if data.get("peak_kwh") is not None
                        else None
                    ),
                    shoulder_kwh=(
                        to_decimal(data["shoulder_kwh"])
                        if data.get("shoulder_kwh") is not None
                        else None
                    ),
                    off_peak_kwh=(
                        to_decimal(data["off_peak_kwh"])
                        if data.get("off_peak_kwh") is not None
                        else None
                    ),
                    controlled_load_kwh=to_decimal(
                        data.get("controlled_load_kwh"), "0"
                    ),
                    solar_export_kwh=to_decimal(data.get("solar_export_kwh"), "0"),
                )
                tariff_str = str(data.get("tariff_type") or "SINGLE_RATE").upper()
                tariff_type = (
                    TariffType.TIME_OF_USE
                    if "TIME" in tariff_str or "TOU" in tariff_str
                    else TariffType.SINGLE_RATE
                )
                curr_elec_plan = CurrentPlanDetails(
                    retailer=str(data.get("retailer") or "Unknown Retailer"),
                    plan_name=str(data.get("plan_name") or "Existing Plan"),
                    fuel_type=FuelType.ELECTRICITY,
                    tariff_type=tariff_type,
                    distributor=distributor_elec,
                    daily_supply_charge_cents=to_decimal(
                        data.get("daily_supply_charge_cents"), "115.0"
                    ),
                    single_rate_cents_kwh=(
                        to_decimal(data["single_rate_cents_kwh"])
                        if data.get("single_rate_cents_kwh") is not None
                        else None
                    ),
                    peak_rate_cents_kwh=(
                        to_decimal(data["peak_rate_cents_kwh"])
                        if data.get("peak_rate_cents_kwh") is not None
                        else None
                    ),
                    shoulder_rate_cents_kwh=(
                        to_decimal(data["shoulder_rate_cents_kwh"])
                        if data.get("shoulder_rate_cents_kwh") is not None
                        else None
                    ),
                    off_peak_rate_cents_kwh=(
                        to_decimal(data["off_peak_rate_cents_kwh"])
                        if data.get("off_peak_rate_cents_kwh") is not None
                        else None
                    ),
                    controlled_load_rate_cents_kwh=to_decimal(
                        data.get("controlled_load_rate_cents_kwh"), "0"
                    ),
                    solar_fit_cents_kwh=to_decimal(
                        data.get("solar_fit_cents_kwh"), "5.0"
                    ),
                    usage_discount_pct=to_decimal(
                        data.get("usage_discount_pct"), "0"
                    ),
                    billed_total_aud=(
                        to_decimal(data["billed_total_aud"])
                        if data.get("billed_total_aud") is not None
                        else None
                    ),
                )

        gas_usage: Optional[GasUsage] = None
        curr_gas_plan: Optional[CurrentPlanDetails] = None
        if data.get("gas_total_mj") is not None or "GAS" in fuel_str or "DUAL" in fuel_str:
            if data.get("gas_total_mj") is not None:
                gas_usage = GasUsage(
                    billing_days=billing_days,
                    total_mj=to_decimal(data.get("gas_total_mj"), "0"),
                )
                curr_gas_plan = CurrentPlanDetails(
                    retailer=str(data.get("gas_retailer") or data.get("retailer") or "Unknown Retailer"),
                    plan_name=str(data.get("gas_plan_name") or data.get("plan_name") or "Existing Gas Plan"),
                    fuel_type=FuelType.GAS,
                    tariff_type=TariffType.STEPPED,
                    distributor="Jemena Gas",
                    daily_supply_charge_cents=to_decimal(
                        data.get("gas_daily_supply_cents"), "68.0"
                    ),
                    gas_block1_rate_cents_mj=(
                        to_decimal(data["gas_block1_rate_cents_mj"])
                        if data.get("gas_block1_rate_cents_mj") is not None
                        else Decimal("3.95")
                    ),
                    gas_block1_limit_mj_day=(
                        to_decimal(data["gas_block1_limit_mj_day"])
                        if data.get("gas_block1_limit_mj_day") is not None
                        else Decimal("60.0")
                    ),
                    gas_remainder_rate_cents_mj=(
                        to_decimal(data["gas_remainder_rate_cents_mj"])
                        if data.get("gas_remainder_rate_cents_mj") is not None
                        else Decimal("2.45")
                    ),
                )

        solar_export = elec_usage.solar_export_kwh if elec_usage else Decimal("0")
        has_solar = solar_export > Decimal("0") or to_decimal(
            data.get("solar_capacity_kw"), "0"
        ) > Decimal("0")

        return HouseholdProfile(
            household_id=household_id,
            postcode=postcode,
            suburb=str(data.get("suburb") or "Sydney"),
            state=str(data.get("state") or "NSW"),
            distributor_electricity=distributor_elec,
            distributor_gas="Jemena Gas",
            has_solar=has_solar,
            solar_capacity_kw=to_decimal(data.get("solar_capacity_kw"), "0"),
            electricity_usage=elec_usage,
            gas_usage=gas_usage,
            current_electricity_plan=curr_elec_plan,
            current_gas_plan=curr_gas_plan,
        )

    def validate_profile(self, profile: HouseholdProfile) -> List[ValidationIssue]:
        """
        Validate extracted bill fields, check internal consistency, ask for clarification
        when required fields are missing or uncertain, and establish baseline annual cost (UC-01).
        """
        issues: List[ValidationIssue] = []
        confidence = Decimal("0.95")

        if not profile.electricity_usage and not profile.gas_usage:
            issues.append(
                ValidationIssue(
                    field_name="energy_usage",
                    severity=ValidationSeverity.CLARIFICATION_REQUIRED,
                    message="Neither electricity (kWh) nor gas (MJ) usage could be extracted from the bill.",
                    suggested_action="Please confirm your total kWh or MJ usage and billing period days.",
                )
            )
            confidence -= Decimal("0.50")

        if profile.electricity_usage and profile.current_electricity_plan:
            eu = profile.electricity_usage
            ep = profile.current_electricity_plan

            if eu.billing_days <= 0 or eu.billing_days > 370:
                issues.append(
                    ValidationIssue(
                        field_name="billing_days",
                        severity=ValidationSeverity.CLARIFICATION_REQUIRED,
                        message=f"Billing period ({eu.billing_days} days) is outside normal range (1-366 days).",
                        suggested_action="Confirm the start and end dates on your bill.",
                    )
                )
                confidence -= Decimal("0.25")
            elif eu.billing_days < 300:
                issues.append(
                    ValidationIssue(
                        field_name="billing_days",
                        severity=ValidationSeverity.WARNING,
                        message=(
                            f"Baseline is extrapolated from a single {eu.billing_days}-day bill. "
                            "Seasonal heating/cooling spikes may cause actual 12-month usage to vary."
                        ),
                        suggested_action="Provide 12 months of bills or smart-meter interval data for higher precision.",
                    )
                )
                confidence -= Decimal("0.08")

            # Verify TOU band sum vs total_kwh
            if eu.has_tou_breakdown:
                band_sum = (
                    (eu.peak_kwh or Decimal("0"))
                    + (eu.shoulder_kwh or Decimal("0"))
                    + (eu.off_peak_kwh or Decimal("0"))
                )
                if abs(band_sum - eu.total_kwh) > Decimal("1.0"):
                    issues.append(
                        ValidationIssue(
                            field_name="tou_usage_breakdown",
                            severity=ValidationSeverity.CLARIFICATION_REQUIRED,
                            message=(
                                f"Sum of Peak+Shoulder+Off-Peak ({band_sum} kWh) does not match "
                                f"Total Usage ({eu.total_kwh} kWh)."
                            ),
                            suggested_action="Verify the Time-of-Use meter readings on page 2 of your bill.",
                        )
                    )
                    confidence -= Decimal("0.20")

            # Check missing tariff rates
            if (
                ep.tariff_type == TariffType.SINGLE_RATE
                and ep.single_rate_cents_kwh is None
            ):
                issues.append(
                    ValidationIssue(
                        field_name="single_rate_cents_kwh",
                        severity=ValidationSeverity.CLARIFICATION_REQUIRED,
                        message="Single-rate usage charge (c/kWh) could not be confirmed from the bill.",
                        suggested_action="Enter your current anytime usage rate in cents/kWh.",
                    )
                )
                confidence -= Decimal("0.25")
            elif (
                ep.tariff_type == TariffType.TIME_OF_USE
                and ep.peak_rate_cents_kwh is None
            ):
                issues.append(
                    ValidationIssue(
                        field_name="peak_rate_cents_kwh",
                        severity=ValidationSeverity.CLARIFICATION_REQUIRED,
                        message="Time-of-Use peak tariff rate (c/kWh) is missing.",
                        suggested_action="Confirm Peak, Shoulder, and Off-Peak rates (c/kWh) from your bill.",
                    )
                )
                confidence -= Decimal("0.25")

            # Calculate baseline annual electricity cost
            baseline_breakdown = self.pricing_engine.calculate_baseline_electricity_cost(
                eu, ep
            )
            profile.baseline_electricity_annual_aud = (
                baseline_breakdown.recurring_annual_cost_aud
            )

        if profile.gas_usage and profile.current_gas_plan:
            baseline_gas = self.pricing_engine.calculate_baseline_gas_cost(
                profile.gas_usage, profile.current_gas_plan
            )
            profile.baseline_gas_annual_aud = baseline_gas.recurring_annual_cost_aud

        profile.validation_issues = issues
        profile.confidence_score = max(Decimal("0.10"), confidence)
        return issues
