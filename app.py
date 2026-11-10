"""
Streamlit Web Interface for the AI-Powered Energy Offer Comparison and Savings Agent.

Run with:
    streamlit run app.py
"""

from __future__ import annotations

import sys
import tempfile
from decimal import Decimal
from pathlib import Path

# Ensure `src` is on sys.path when running `streamlit run app.py` directly
ROOT_DIR = Path(__file__).resolve().parent
SRC_DIR = ROOT_DIR / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

import streamlit as st  # type: ignore

from energy_agent.models import FuelType
from energy_agent.orchestrator import EnergySavingsOrchestrator
from energy_agent.sample_bills import generate_all_sample_bills

st.set_page_config(
    page_title="NSW Energy Offer Comparison & Savings Agent",
    page_icon="⚡",
    layout="wide",
)

st.title("⚡ AI-Powered Energy Offer Comparison & Savings Agent")
st.caption(
    "Australian Residential Energy Adviser (Initial Market: NSW) • "
    "**Core Principle:** AI extracts & explains; a deterministic Decimal engine calculates costs; "
    "rankings are 100% by user benefit & eligibility, never by referral commission."
)

orchestrator = EnergySavingsOrchestrator()
sample_bills = generate_all_sample_bills()

tab_upload, tab_monitor, tab_history = st.tabs(
    [
        "📄 1. Bill Upload & Offer Comparison (UC-01 to UC-04)",
        "🔔 2. Daily Market Monitor & Alerts (UC-05, UC-06)",
        "🗄️ 3. Stored Profiles & Offer Version Audit",
    ]
)

with tab_upload:
    col_left, col_right = st.columns([1, 2])
    with col_left:
        st.subheader("Input Household Energy Bill")
        input_mode = st.radio(
            "Select Bill Source:",
            [
                "Use Sample NSW PDF Bill",
                "Upload PDF Energy Bill",
            ],
        )

        selected_pdf_path = None
        if input_mode == "Use Sample NSW PDF Bill":
            sample_choice = st.selectbox(
                "Choose a Representative NSW Bill:",
                options=list(sample_bills.keys()),
                format_func=lambda k: {
                    "ausgrid_tou_solar": "Sydney 2000 (Ausgrid) — Time-of-Use + 6.6kW Solar (AGL)",
                    "endeavour_single_rate": "Parramatta 2150 (Endeavour) — Single-Rate + Controlled Load (Origin)",
                    "dual_fuel_elec_gas": "Chatswood 2067 (Ausgrid + Jemena) — Dual-Fuel Elec & Gas (EnergyAustralia)",
                }.get(k, k),
            )
            selected_pdf_path = sample_bills[sample_choice]
        else:
            uploaded = st.file_uploader("Upload PDF Energy Bill", type=["pdf"])
            if uploaded is not None:
                tmp_dir = Path(tempfile.mkdtemp())
                selected_pdf_path = tmp_dir / uploaded.name
                selected_pdf_path.write_bytes(uploaded.read())

        rank_mode = st.checkbox(
            "Rank strictly by Ongoing Recurring Cost (exclude one-off Year-1 sign-up credits from rank order)",
            value=False,
        )
        include_live_cdr = st.checkbox(
            "Experimental live CDR offers (slow; incomplete coverage)",
            value=False,
            help="Queries public plan APIs. Rates and eligibility require verification before switching.",
        )
        alert_threshold = st.number_input(
            "Savings Alert Threshold (AUD $/yr)",
            min_value=10.0,
            max_value=1000.0,
            value=100.0,
            step=10.0,
        )

    with col_right:
        if selected_pdf_path and selected_pdf_path.exists():
            result = orchestrator.analyze_pdf_bill(
                pdf_path=selected_pdf_path,
                rank_by_recurring=rank_mode,
                persist=True,
                include_live_cdr=include_live_cdr,
            )
            coverage = result.market_coverage
            if coverage:
                st.info(
                    f"Market coverage is not complete. Live requested: {coverage.live_requested}; "
                    f"registry: {coverage.registry_source}; "
                    f"sources attempted: {len(coverage.sources_attempted)}; "
                    f"plans seen: {coverage.plans_seen}; accepted: {coverage.plans_accepted}; "
                    f"rejected: {coverage.plans_rejected}; failed sources: {len(coverage.failures)}. "
                    "Verify prices, meter configuration and postcode eligibility before switching."
                )
                if coverage.failures:
                    with st.expander("CDR source failures"):
                        st.json(coverage.failures)
            profile = result.profile
            profile.alert_threshold_aud = Decimal(str(alert_threshold))
            orchestrator.storage.save_household_profile(profile)

            st.subheader(f"Validated Household Profile (`{profile.household_id}`)")
            m1, m2, m3, m4 = st.columns(4)
            m1.metric("Location", f"{profile.suburb} ({profile.postcode})")
            m2.metric("Electricity Network", profile.distributor_electricity)
            m3.metric(
                "Baseline Electricity",
                f"AUD ${profile.baseline_electricity_annual_aud}/yr"
                if profile.baseline_electricity_annual_aud
                else "N/A",
            )
            m4.metric(
                "Validation Confidence",
                f"{float(profile.confidence_score) * 100:.0f}%",
            )

            for issue in profile.validation_issues:
                st.warning(
                    f"**[{issue.severity.value}] {issue.field_name}:** {issue.message} — *{issue.suggested_action}*"
                )

            if result.dual_fuel_comparison:
                df = result.dual_fuel_comparison
                st.success(
                    f"🔥⚡ **Dual-Fuel Combined Summary (FR-10):** Combined Baseline **AUD ${df.combined_baseline_aud}/yr** → "
                    f"Best Year-1 Combined Cost **AUD ${df.combined_first_year_cost_aud}/yr** "
                    f"(**Save AUD ${df.combined_first_year_savings_aud}** in Year 1; "
                    f"**AUD ${df.combined_recurring_savings_aud}/yr** ongoing)."
                )

            for label, comps in [
                ("⚡ Ranked Electricity Offers", result.electricity_comparisons),
                ("🔥 Ranked Natural Gas Offers", result.gas_comparisons),
            ]:
                if not comps:
                    continue
                st.markdown(f"### {label}")
                table_rows = []
                for c in comps:
                    b = c.breakdown
                    table_rows.append(
                        {
                            "Yr1 Rank": f"#{c.rank_first_year}" if c.is_eligible else "Ineligible",
                            "Ongoing Rank": f"#{c.rank_recurring}" if c.is_eligible else "Ineligible",
                            "Retailer": c.offer.retailer,
                            "Plan": c.offer.plan_name,
                            "Tariff": c.offer.tariff_type.value,
                            "Yr1 Net Cost ($)": f"${b.first_year_net_cost_aud}",
                            "Yr1 Savings ($)": f"${c.first_year_savings_aud}",
                            "Ongoing Cost ($)": f"${b.recurring_annual_cost_aud}",
                            "Ongoing Savings ($)": f"${c.recurring_annual_savings_aud}",
                            "1-Off Credit ($)": f"${b.one_off_promotions_aud}",
                            "Referral Disclosed": "Partner Link" if c.offer.is_partner_referral else "Direct (No Comm.)",
                        }
                    )
                st.dataframe(table_rows, use_container_width=True)

                eligible_comps = [c for c in comps if c.is_eligible]
                if eligible_comps:
                    best = eligible_comps[0]
                    st.info(
                        f"🏆 **Top Recommendation ({best.offer.retailer} — {best.offer.plan_name}):**\n\n"
                        f"{best.explanation}\n\n"
                        f"🔗 [Official Offer & Plan Document]({best.offer.source_url})"
                    )
                for c in comps:
                    for w in c.warning_flags:
                        st.warning(f"**{c.offer.retailer} ({c.offer.plan_name}):** {w}")

with tab_monitor:
    st.subheader("Daily Market Monitoring & Threshold Alerts (UC-05, UC-06)")
    st.write(
        "The agent refreshes market offers daily, tracks offer versions via SHA-256 content hashes, "
        "and triggers alerts only when first-year savings meet or exceed the household's threshold "
        "(default **AUD $100**), while suppressing duplicate alerts for unchanged offers."
    )
    if st.button("🔄 Run Daily Market Check Now"):
        summary = orchestrator.run_daily_monitoring()
        c1, c2, c3, c4 = st.columns(4)
        c1.metric("Offers Scanned", summary.total_offers_scanned)
        c2.metric("New/Changed Offer Versions", summary.new_or_changed_offer_versions)
        c3.metric("New Alerts Triggered", len(summary.new_alerts_triggered))
        c4.metric("Duplicate Alerts Suppressed", summary.duplicate_alerts_suppressed)

    alerts = orchestrator.storage.list_alerts()
    st.markdown("#### Historical Alert Log (`alert_history`)")
    if alerts:
        st.dataframe(
            [
                {
                    "Alert ID": a.alert_id,
                    "Household": a.household_id,
                    "Fuel": a.fuel_type,
                    "Retailer & Plan": f"{a.retailer} - {a.plan_name}",
                    "Yr1 Savings": f"AUD ${a.first_year_savings_aud}",
                    "Ongoing Savings": f"AUD ${a.recurring_annual_savings_aud}/yr",
                    "Offer Version": a.offer_version_hash,
                    "Triggered At": a.triggered_at,
                }
                for a in alerts
            ],
            use_container_width=True,
        )
    else:
        st.caption("No alerts recorded yet. Click 'Run Daily Market Check Now' above.")

with tab_history:
    st.subheader("Stored Household Profiles & Offer Version History")
    profiles = orchestrator.storage.list_household_profiles()
    st.markdown(f"**Stored Household Profiles ({len(profiles)}):**")
    if profiles:
        st.dataframe(
            [
                {
                    "Household ID": p.household_id,
                    "Postcode": p.postcode,
                    "Electricity Network": p.distributor_electricity,
                    "Elec Baseline": f"AUD ${p.baseline_electricity_annual_aud}" if p.baseline_electricity_annual_aud else "N/A",
                    "Gas Baseline": f"AUD ${p.baseline_gas_annual_aud}" if p.baseline_gas_annual_aud else "N/A",
                    "Alert Threshold": f"AUD ${p.alert_threshold_aud}",
                }
                for p in profiles
            ],
            use_container_width=True,
        )

    versions = orchestrator.storage.get_offer_version_history()
    st.markdown(f"**Tracked Market Offer Versions ({len(versions)}):**")
    if versions:
        st.dataframe(
            [
                {
                    "Offer ID": v["offer_id"],
                    "Version Hash": v["version_hash"],
                    "Retailer": v["retailer"],
                    "Plan Name": v["plan_name"],
                    "Fuel": v["fuel_type"],
                    "Distributor": v["distributor"],
                    "First Seen": v["first_seen_at"],
                    "Last Seen": v["last_seen_at"],
                }
                for v in versions
            ],
            use_container_width=True,
        )
