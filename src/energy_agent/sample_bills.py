"""
Sample Australian (NSW) PDF Energy Bill Generator for testing and pilot validation.

Generates 3 realistic, redacted NSW residential bills in `data/sample_bills/`:
1. `nsw_ausgrid_tou_solar_bill.pdf`: Sydney 2000 (Ausgrid) Time-of-Use + Solar household with AGL
2. `nsw_endeavour_single_rate_bill.pdf`: Parramatta 2150 (Endeavour Energy) Single-Rate household with Origin
3. `nsw_dual_fuel_elec_gas_bill.pdf`: Chatswood 2067 (Ausgrid + Jemena Gas) Dual-Fuel household with EnergyAustralia
"""

from __future__ import annotations

from pathlib import Path
from typing import Dict, List, Optional, Union

from .pdf_utils import write_simple_pdf

DEFAULT_SAMPLE_BILLS_DIR = (
    Path(__file__).resolve().parents[2] / "data" / "sample_bills"
)


def generate_all_sample_bills(
    output_dir: Optional[Union[str, Path]] = None,
) -> Dict[str, Path]:
    """Generate the 3 sample NSW PDF energy bills and return their paths."""
    base_dir = Path(output_dir) if output_dir else DEFAULT_SAMPLE_BILLS_DIR
    base_dir.mkdir(parents=True, exist_ok=True)

    # Sample 1: NSW Ausgrid Time-of-Use + Solar + Controlled Load Bill
    bill1_lines: List[str] = [
        "TAX INVOICE - RESIDENTIAL ELECTRICITY STATEMENT",
        "Retailer: AGL",
        "Plan Name: AGL Essentials Variable TOU",
        "Customer Name: Alex Citizen",
        "Account Number: 492018472",
        "NMI: 4102938475",
        "Supply Address: 88 George St, Sydney NSW 2000",
        "Suburb: Sydney",
        "Postcode: 2000",
        "Distributor Network: Ausgrid",
        "Fuel Type: ELECTRICITY",
        "Tariff Type: TIME_OF_USE",
        "Billing Period: 90 days (01 Jul 2026 to 28 Sep 2026)",
        "------------------------------------------------------------",
        "METER USAGE SUMMARY (90 DAYS)",
        "Total Electricity Usage: 1350.0 kWh",
        "Peak Usage: 420.0 kWh",
        "Shoulder Usage: 310.0 kWh",
        "Off-Peak Usage: 620.0 kWh",
        "Controlled Load Usage: 220.0 kWh",
        "Solar Export: 680.0 kWh",
        "Solar System Capacity: 6.6 kW",
        "------------------------------------------------------------",
        "TARIFF RATES AND CHARGES (GST INCLUSIVE)",
        "Daily Supply Charge: 118.80 c/day",
        "Peak Rate: 44.50 c/kWh",
        "Shoulder Rate: 30.20 c/kWh",
        "Off-Peak Rate: 23.10 c/kWh",
        "Controlled Load Rate: 19.80 c/kWh",
        "Solar Feed-in Tariff: 5.00 c/kWh",
        "Usage Discount: 0.0 %",
        "Total Bill Amount Due: $542.65",
    ]
    p1 = write_simple_pdf(
        base_dir / "nsw_ausgrid_tou_solar_bill.pdf",
        bill1_lines,
        title="AGL NSW Ausgrid TOU Solar Bill",
    )

    # Sample 2: NSW Endeavour Energy Single-Rate Bill
    bill2_lines: List[str] = [
        "RESIDENTIAL ELECTRICITY BILL",
        "Retailer: Origin Energy",
        "Plan Name: Origin Everyday Standing",
        "Customer Name: Taylor Smith",
        "Account Number: 88392011",
        "NMI: 4310928374",
        "Supply Address: 15 Church St, Parramatta NSW 2150",
        "Suburb: Parramatta",
        "Postcode: 2150",
        "Distributor Network: Endeavour Energy",
        "Fuel Type: ELECTRICITY",
        "Tariff Type: SINGLE_RATE",
        "Billing Period: 92 days",
        "------------------------------------------------------------",
        "USAGE AND RATES (GST INCLUSIVE)",
        "Total Electricity Usage: 1480.0 kWh",
        "Controlled Load Usage: 180.0 kWh",
        "Daily Supply Charge: 112.50 c/day",
        "Single Rate: 33.40 c/kWh",
        "Controlled Load Rate: 19.50 c/kWh",
        "Usage Discount: 0.0 %",
        "Total Bill Amount Due: $632.90",
    ]
    p2 = write_simple_pdf(
        base_dir / "nsw_endeavour_single_rate_bill.pdf",
        bill2_lines,
        title="Origin NSW Endeavour Single Rate Bill",
    )

    # Sample 3: NSW Dual-Fuel (Ausgrid Electricity + Jemena Natural Gas)
    bill3_lines: List[str] = [
        "COMBINED DUAL-FUEL ENERGY STATEMENT (NSW)",
        "Retailer: EnergyAustralia",
        "Plan Name: Basic Home Dual Plan",
        "Customer Name: Jordan Lee",
        "Account Number: 77419283",
        "NMI: 4108877665",
        "MIRN: 5209988776",
        "Supply Address: 42 Victoria Ave, Chatswood NSW 2067",
        "Suburb: Chatswood",
        "Postcode: 2067",
        "Distributor Network: Ausgrid",
        "Fuel Type: DUAL",
        "Tariff Type: TIME_OF_USE",
        "Billing Period: 90 days",
        "------------------------------------------------------------",
        "ELECTRICITY SUMMARY (AUSGRID)",
        "Total Electricity Usage: 1200.0 kWh",
        "Peak Usage: 380.0 kWh",
        "Shoulder Usage: 260.0 kWh",
        "Off-Peak Usage: 560.0 kWh",
        "Daily Supply Charge: 116.00 c/day",
        "Peak Rate: 43.80 c/kWh",
        "Shoulder Rate: 29.50 c/kWh",
        "Off-Peak Rate: 22.80 c/kWh",
        "------------------------------------------------------------",
        "NATURAL GAS SUMMARY (JEMENA GAS NSW)",
        "Gas Retailer: EnergyAustralia",
        "Gas Plan: Basic Home Gas",
        "Total Gas Usage: 5800.0 MJ",
        "Gas Daily Supply Charge: 68.50 c/day",
        "Gas Block 1 Rate: 3.95 c/MJ",
        "Gas Block 1 Limit: 60.0 MJ/day",
        "Gas Remainder Rate: 2.45 c/MJ",
        "Total Bill Amount Due: $765.00",
    ]
    p3 = write_simple_pdf(
        base_dir / "nsw_dual_fuel_elec_gas_bill.pdf",
        bill3_lines,
        title="EnergyAustralia NSW Dual Fuel Bill",
    )

    return {
        "ausgrid_tou_solar": p1,
        "endeavour_single_rate": p2,
        "dual_fuel_elec_gas": p3,
    }
