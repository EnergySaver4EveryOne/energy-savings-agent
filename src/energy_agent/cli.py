"""
Command-Line Prototype & Runner for the AI-Powered Energy Offer Comparison and Savings Agent.

Implements the Recommended First Milestone (Section 9):
- Accepts a PDF energy bill
- Outputs a validated JSON household profile
- Produces ranked CSV and HTML comparison reports
- Runs daily market monitoring and deduplicated threshold alerts (AUD $100+)
"""

from __future__ import annotations

import argparse
import sys
from decimal import Decimal
from pathlib import Path
from typing import List, Optional

from .models import OfferComparison
from .orchestrator import AnalysisResult, EnergySavingsOrchestrator
from .sample_bills import generate_all_sample_bills


def _print_comparison_table(title: str, comparisons: List[OfferComparison]) -> None:
    if not comparisons:
        return
    print(f"\n=== {title} ===")
    header = (
        f"{'Rank(Yr1/Rec)':<14} | {'Eligible':<8} | {'Retailer & Plan':<38} | "
        f"{'Baseline':>10} | {'Yr1 Cost':>10} | {'Yr1 Save':>10} | {'Rec Save':>10}"
    )
    print(header)
    print("-" * len(header))
    for c in comparisons:
        rank_str = (
            f"#{c.rank_first_year or '-'} / #{c.rank_recurring or '-'}"
            if c.is_eligible
            else "INELIGIBLE"
        )
        elig_str = "YES" if c.is_eligible else "NO"
        name_str = f"{c.offer.retailer} - {c.offer.plan_name}"[:38]
        print(
            f"{rank_str:<14} | {elig_str:<8} | {name_str:<38} | "
            f"${c.baseline_annual_cost_aud:>9} | ${c.breakdown.first_year_net_cost_aud:>9} | "
            f"${c.first_year_savings_aud:>9} | ${c.recurring_annual_savings_aud:>9}"
        )
        for w in c.warning_flags:
            print(f"   ⚠️  WARNING: {w}")


def cmd_analyze(args: argparse.Namespace) -> int:
    orchestrator = EnergySavingsOrchestrator(db_path=args.db_path)
    pdf_path = Path(args.bill_pdf)
    out_dir = Path(args.output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    result: AnalysisResult = orchestrator.analyze_pdf_bill(
        pdf_path=pdf_path,
        household_id=args.household_id,
        rank_by_recurring=args.rank_by_recurring,
    )
    p = result.profile

    print(f"\n[UC-01] Extracted & Validated Household Profile: {p.household_id}")
    print(
        f"  Location: {p.suburb}, {p.state} {p.postcode} | "
        f"Electricity Network: {p.distributor_electricity} | Confidence: {float(p.confidence_score)*100:.0f}%"
    )
    if p.baseline_electricity_annual_aud is not None:
        print(f"  Baseline Annual Electricity Cost: AUD ${p.baseline_electricity_annual_aud}/yr")
    if p.baseline_gas_annual_aud is not None:
        print(f"  Baseline Annual Natural Gas Cost: AUD ${p.baseline_gas_annual_aud}/yr")

    for issue in p.validation_issues:
        print(f"  [{issue.severity.value}] {issue.field_name}: {issue.message}")

    _print_comparison_table(
        "ELECTRICITY OFFER COMPARISON (UC-03, UC-04)",
        result.electricity_comparisons,
    )
    _print_comparison_table(
        "NATURAL GAS OFFER COMPARISON (UC-03, UC-04)",
        result.gas_comparisons,
    )

    if result.dual_fuel_comparison:
        df = result.dual_fuel_comparison
        print(
            f"\n[FR-10] Combined Dual-Fuel Savings Summary:\n"
            f"  Baseline: AUD ${df.combined_baseline_aud}/yr -> "
            f"Best Year-1 Cost: AUD ${df.combined_first_year_cost_aud}/yr "
            f"(Save AUD ${df.combined_first_year_savings_aud} in Year 1; "
            f"AUD ${df.combined_recurring_savings_aud}/yr ongoing)"
        )

    stem = pdf_path.stem
    json_out = orchestrator.export_profile_json(p, out_dir / f"{stem}_profile.json")
    all_comps = result.electricity_comparisons + result.gas_comparisons
    csv_out = orchestrator.export_comparison_csv(
        all_comps, out_dir / f"{stem}_comparison.csv"
    )
    html_out = orchestrator.export_comparison_html(
        result, out_dir / f"{stem}_report.html"
    )

    print(f"\nReports Exported:")
    print(f"  - JSON Household Profile : {json_out}")
    print(f"  - Ranked CSV Comparison  : {csv_out}")
    print(f"  - Interactive HTML Report: {html_out}")
    return 0


def cmd_monitor(args: argparse.Namespace) -> int:
    orchestrator = EnergySavingsOrchestrator(db_path=args.db_path)
    threshold = Decimal(str(args.threshold))
    summary = orchestrator.monitor.run_daily_check(threshold_override_aud=threshold)

    print("\n=== Daily Market Monitor Summary (UC-05, UC-06) ===")
    print(f"  Checked At                   : {summary.checked_at}")
    print(f"  Total Offers Scanned         : {summary.total_offers_scanned}")
    print(f"  New/Changed Offer Versions   : {summary.new_or_changed_offer_versions}")
    print(f"  Households Evaluated         : {summary.households_evaluated}")
    print(f"  New Alerts Triggered (>=${threshold}): {len(summary.new_alerts_triggered)}")
    print(f"  Duplicate Alerts Suppressed  : {summary.duplicate_alerts_suppressed}")

    for alert in summary.new_alerts_triggered:
        print(f"\n  🔔 ALERT [{alert.alert_id}]: {alert.summary_message}")
    return 0


def cmd_generate_samples(args: argparse.Namespace) -> int:
    paths = generate_all_sample_bills(args.output_dir)
    print("Generated sample NSW PDF energy bills:")
    for key, path in paths.items():
        print(f"  - {key}: {path}")
    return 0


def cmd_demo(args: argparse.Namespace) -> int:
    print("======================================================================")
    print(" AI-Powered Energy Offer Comparison & Savings Agent — End-to-End Demo")
    print("======================================================================")
    sample_paths = generate_all_sample_bills()
    out_dir = Path(args.output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    orchestrator = EnergySavingsOrchestrator(db_path=args.db_path)

    for key, pdf_path in sample_paths.items():
        print(f"\n>>> Analyzing Sample Bill [{key}]: {pdf_path.name}")
        ns = argparse.Namespace(
            bill_pdf=str(pdf_path),
            household_id=f"demo-{key}",
            output_dir=str(out_dir),
            db_path=args.db_path,
            rank_by_recurring=False,
        )
        cmd_analyze(ns)

    print("\n>>> Running Daily Market Monitor (Run #1 — triggers initial alerts >= AUD $100)...")
    mon_ns = argparse.Namespace(db_path=args.db_path, threshold="100.00")
    cmd_monitor(mon_ns)

    print("\n>>> Running Daily Market Monitor (Run #2 — verifies duplicate alert suppression UC-06)...")
    cmd_monitor(mon_ns)
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="energy-agent",
        description="AI-Powered Energy Offer Comparison and Savings Agent (NSW Residential MVP)",
    )
    parser.add_argument(
        "--db-path",
        default=None,
        help="Path to SQLite database file (default: data/energy_agent.db)",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p_analyze = sub.add_parser(
        "analyze", help="Parse a PDF energy bill and generate JSON, CSV, and HTML comparison reports"
    )
    p_analyze.add_argument("bill_pdf", help="Path to the household PDF energy bill")
    p_analyze.add_argument(
        "--household-id", default=None, help="Optional household identifier"
    )
    p_analyze.add_argument(
        "--output-dir",
        default="reports",
        help="Directory for JSON, CSV, and HTML output files",
    )
    p_analyze.add_argument(
        "--rank-by-recurring",
        action="store_true",
        help="Rank offers by ongoing recurring annual cost instead of Year-1 net cost",
    )

    p_monitor = sub.add_parser(
        "monitor", help="Run daily market monitoring and trigger threshold alerts"
    )
    p_monitor.add_argument(
        "--threshold",
        default="100.00",
        help="Minimum estimated first-year savings in AUD to trigger an alert (default: 100.00)",
    )

    p_samples = sub.add_parser(
        "generate-samples", help="Generate sample NSW residential PDF energy bills"
    )
    p_samples.add_argument(
        "--output-dir",
        default=None,
        help="Directory to write sample PDF bills (default: data/sample_bills)",
    )

    p_demo = sub.add_parser(
        "demo", help="Run full end-to-end demonstration across all sample NSW bills and market monitor"
    )
    p_demo.add_argument(
        "--output-dir",
        default="reports",
        help="Directory to write generated demo reports",
    )

    return parser


def main(argv: Optional[List[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.command == "analyze":
        return cmd_analyze(args)
    if args.command == "monitor":
        return cmd_monitor(args)
    if args.command == "generate-samples":
        return cmd_generate_samples(args)
    if args.command == "demo":
        return cmd_demo(args)
    return 1


if __name__ == "__main__":
    sys.exit(main())
