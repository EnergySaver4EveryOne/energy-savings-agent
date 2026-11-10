"""
SQLite Persistence Layer (UC-06, FR-02, FR-09).

Stores:
1. Household energy profiles (`household_profiles`)
2. Market offer versions and rate change history (`offer_versions`)
3. Historical comparison runs (`comparison_results`)
4. Alert history with deduplication keys (`alert_history`) so unchanged offers never trigger duplicate alerts.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple, Union

from .models import (
    AlertNotification,
    EnergyOffer,
    HouseholdProfile,
    OfferComparison,
    to_decimal,
)

DEFAULT_DB_PATH = (
    Path(__file__).resolve().parents[2] / "data" / "energy_agent.db"
)


class StorageRepository:
    """SQLite storage manager for household profiles, offer versions, comparisons, and alerts."""

    def __init__(self, db_path: Optional[Union[str, Path]] = None) -> None:
        self.db_path = Path(db_path) if db_path else DEFAULT_DB_PATH
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_schema()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self.db_path))
        conn.row_factory = sqlite3.Row
        return conn

    def _init_schema(self) -> None:
        with self._connect() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS household_profiles (
                    household_id TEXT PRIMARY KEY,
                    postcode TEXT NOT NULL,
                    distributor_electricity TEXT NOT NULL,
                    distributor_gas TEXT NOT NULL,
                    baseline_electricity_aud TEXT,
                    baseline_gas_aud TEXT,
                    alert_threshold_aud TEXT NOT NULL,
                    profile_json TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS offer_versions (
                    offer_id TEXT NOT NULL,
                    version_hash TEXT NOT NULL,
                    retailer TEXT NOT NULL,
                    plan_name TEXT NOT NULL,
                    fuel_type TEXT NOT NULL,
                    distributor TEXT NOT NULL,
                    daily_supply_cents TEXT NOT NULL,
                    offer_json TEXT NOT NULL,
                    first_seen_at TEXT NOT NULL,
                    last_seen_at TEXT NOT NULL,
                    PRIMARY KEY (offer_id, version_hash)
                );

                CREATE TABLE IF NOT EXISTS comparison_results (
                    run_id TEXT PRIMARY KEY,
                    household_id TEXT NOT NULL,
                    fuel_type TEXT NOT NULL,
                    top_offer_id TEXT,
                    top_offer_version_hash TEXT,
                    baseline_annual_aud TEXT NOT NULL,
                    best_first_year_cost_aud TEXT,
                    best_first_year_savings_aud TEXT,
                    best_recurring_savings_aud TEXT,
                    results_json TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS alert_history (
                    alert_id TEXT PRIMARY KEY,
                    household_id TEXT NOT NULL,
                    offer_id TEXT NOT NULL,
                    offer_version_hash TEXT NOT NULL,
                    retailer TEXT NOT NULL,
                    plan_name TEXT NOT NULL,
                    fuel_type TEXT NOT NULL,
                    first_year_savings_aud TEXT NOT NULL,
                    recurring_annual_savings_aud TEXT NOT NULL,
                    threshold_aud TEXT NOT NULL,
                    source_url TEXT NOT NULL,
                    triggered_at TEXT NOT NULL,
                    summary_message TEXT NOT NULL,
                    UNIQUE (household_id, offer_id, offer_version_hash)
                );
                """
            )

    def save_household_profile(self, profile: HouseholdProfile) -> None:
        """Insert or update a validated HouseholdProfile (FR-02)."""
        now_iso = datetime.now(timezone.utc).isoformat()
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO household_profiles (
                    household_id, postcode, distributor_electricity, distributor_gas,
                    baseline_electricity_aud, baseline_gas_aud, alert_threshold_aud,
                    profile_json, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(household_id) DO UPDATE SET
                    postcode = excluded.postcode,
                    distributor_electricity = excluded.distributor_electricity,
                    distributor_gas = excluded.distributor_gas,
                    baseline_electricity_aud = excluded.baseline_electricity_aud,
                    baseline_gas_aud = excluded.baseline_gas_aud,
                    alert_threshold_aud = excluded.alert_threshold_aud,
                    profile_json = excluded.profile_json,
                    updated_at = excluded.updated_at
                """,
                (
                    profile.household_id,
                    profile.postcode,
                    profile.distributor_electricity,
                    profile.distributor_gas,
                    str(profile.baseline_electricity_annual_aud or ""),
                    str(profile.baseline_gas_annual_aud or ""),
                    str(profile.alert_threshold_aud),
                    json.dumps(profile.to_json_dict()),
                    now_iso,
                ),
            )

    def get_household_profile(self, household_id: str) -> Optional[HouseholdProfile]:
        """Retrieve a HouseholdProfile by ID."""
        with self._connect() as conn:
            row = conn.execute(
                "SELECT profile_json FROM household_profiles WHERE household_id = ?",
                (household_id,),
            ).fetchone()
        if not row:
            return None
        return HouseholdProfile.from_json_dict(json.loads(row["profile_json"]))

    def list_household_profiles(self) -> List[HouseholdProfile]:
        """List all stored HouseholdProfiles for daily monitoring."""
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT profile_json FROM household_profiles ORDER BY updated_at DESC"
            ).fetchall()
        return [
            HouseholdProfile.from_json_dict(json.loads(r["profile_json"]))
            for r in rows
        ]

    def record_offer_version(self, offer: EnergyOffer) -> Tuple[bool, str]:
        """
        Store an EnergyOffer version by its deterministic `version_hash` (UC-06, FR-09).
        Returns `(is_new_or_changed_version, version_hash)`.
        """
        v_hash = offer.version_hash
        now_iso = datetime.now(timezone.utc).isoformat()
        with self._connect() as conn:
            existing = conn.execute(
                "SELECT version_hash FROM offer_versions WHERE offer_id = ? AND version_hash = ?",
                (offer.offer_id, v_hash),
            ).fetchone()
            if existing:
                conn.execute(
                    "UPDATE offer_versions SET last_seen_at = ? WHERE offer_id = ? AND version_hash = ?",
                    (now_iso, offer.offer_id, v_hash),
                )
                return False, v_hash

            conn.execute(
                """
                INSERT INTO offer_versions (
                    offer_id, version_hash, retailer, plan_name, fuel_type,
                    distributor, daily_supply_cents, offer_json, first_seen_at, last_seen_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    offer.offer_id,
                    v_hash,
                    offer.retailer,
                    offer.plan_name,
                    offer.fuel_type.value,
                    offer.distributor,
                    str(offer.daily_supply_charge_cents),
                    json.dumps(offer.to_json_dict()),
                    now_iso,
                    now_iso,
                ),
            )
            return True, v_hash

    def get_offer_version_history(self, offer_id: Optional[str] = None) -> List[Dict[str, Any]]:
        """Return version history of offers stored in the database."""
        with self._connect() as conn:
            if offer_id:
                rows = conn.execute(
                    "SELECT * FROM offer_versions WHERE offer_id = ? ORDER BY first_seen_at DESC",
                    (offer_id,),
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT * FROM offer_versions ORDER BY first_seen_at DESC"
                ).fetchall()
        return [dict(r) for r in rows]

    def save_comparison_run(
        self,
        run_id: str,
        household_id: str,
        fuel_type: str,
        comparisons: List[OfferComparison],
    ) -> None:
        """Persist a ranked comparison run for auditability and tracking (UC-06)."""
        if not comparisons:
            return
        eligible = [c for c in comparisons if c.definitive_ranking_eligible]
        best = eligible[0] if eligible else comparisons[0]
        now_iso = datetime.now(timezone.utc).isoformat()
        summary_payload = [
            {
                "offer_id": c.offer.offer_id,
                "version_hash": c.offer.version_hash,
                "retailer": c.offer.retailer,
                "plan_name": c.offer.plan_name,
                "first_year_net_cost_aud": str(c.breakdown.first_year_net_cost_aud),
                "recurring_annual_cost_aud": str(c.breakdown.recurring_annual_cost_aud),
                "first_year_savings_aud": str(c.first_year_savings_aud),
                "recurring_annual_savings_aud": str(c.recurring_annual_savings_aud),
                "is_eligible": c.is_eligible,
                "rank_first_year": c.rank_first_year,
                "rank_recurring": c.rank_recurring,
            }
            for c in comparisons
        ]
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO comparison_results (
                    run_id, household_id, fuel_type, top_offer_id, top_offer_version_hash,
                    baseline_annual_aud, best_first_year_cost_aud, best_first_year_savings_aud,
                    best_recurring_savings_aud, results_json, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    run_id,
                    household_id,
                    fuel_type,
                    best.offer.offer_id,
                    best.offer.version_hash,
                    str(best.baseline_annual_cost_aud),
                    str(best.breakdown.first_year_net_cost_aud),
                    str(best.first_year_savings_aud),
                    str(best.recurring_annual_savings_aud),
                    json.dumps(summary_payload),
                    now_iso,
                ),
            )

    def has_alert_been_sent(
        self,
        household_id: str,
        offer_id: str,
        offer_version_hash: str,
    ) -> bool:
        """Check whether an alert has already been sent for this exact (household, offer, version) tuple (FR-09)."""
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT 1 FROM alert_history
                WHERE household_id = ? AND offer_id = ? AND offer_version_hash = ?
                """,
                (household_id, offer_id, offer_version_hash),
            ).fetchone()
        return row is not None

    def record_alert(self, alert: AlertNotification) -> bool:
        """
        Persist an AlertNotification if not already recorded.
        Returns True if inserted, False if deduplicated (already existed).
        """
        if self.has_alert_been_sent(
            alert.household_id, alert.offer_id, alert.offer_version_hash
        ):
            return False

        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO alert_history (
                    alert_id, household_id, offer_id, offer_version_hash,
                    retailer, plan_name, fuel_type, first_year_savings_aud,
                    recurring_annual_savings_aud, threshold_aud, source_url,
                    triggered_at, summary_message
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    alert.alert_id,
                    alert.household_id,
                    alert.offer_id,
                    alert.offer_version_hash,
                    alert.retailer,
                    alert.plan_name,
                    alert.fuel_type,
                    str(alert.first_year_savings_aud),
                    str(alert.recurring_annual_savings_aud),
                    str(alert.threshold_aud),
                    alert.source_url,
                    alert.triggered_at,
                    alert.summary_message,
                ),
            )
        return True

    def list_alerts(self, household_id: Optional[str] = None) -> List[AlertNotification]:
        """List historical alerts, optionally filtered by household_id."""
        with self._connect() as conn:
            if household_id:
                rows = conn.execute(
                    "SELECT * FROM alert_history WHERE household_id = ? ORDER BY triggered_at DESC",
                    (household_id,),
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT * FROM alert_history ORDER BY triggered_at DESC"
                ).fetchall()

        return [
            AlertNotification(
                alert_id=r["alert_id"],
                household_id=r["household_id"],
                offer_id=r["offer_id"],
                offer_version_hash=r["offer_version_hash"],
                retailer=r["retailer"],
                plan_name=r["plan_name"],
                fuel_type=r["fuel_type"],
                first_year_savings_aud=to_decimal(r["first_year_savings_aud"]),
                recurring_annual_savings_aud=to_decimal(r["recurring_annual_savings_aud"]),
                threshold_aud=to_decimal(r["threshold_aud"]),
                source_url=r["source_url"],
                triggered_at=r["triggered_at"],
                summary_message=r["summary_message"],
            )
            for r in rows
        ]
