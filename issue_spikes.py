"""Privacy-preserving issue spike tracking with SQLite and Supabase backends."""

from __future__ import annotations

import re
import sqlite3
from contextlib import closing
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Optional


SPIKE_CATEGORIES = {
    "ORDER_LOCKED",
    "WITHDRAWAL_LOCKED",
    "WITHDRAWAL_PENDING",
    "DEPOSIT_PENDING",
    "PAYMENT_FAILED",
}

_ORDER_LOCK = re.compile(r"\border\b.{0,40}\block(?:ed|ing)?\b|\block(?:ed|ing)?\b.{0,40}\border\b", re.I)
_WITHDRAWAL_LOCK = re.compile(r"\bwithdraw(?:al)?\b.{0,40}\block(?:ed|ing)?\b|\block(?:ed|ing)?\b.{0,40}\bwithdraw(?:al)?\b", re.I)
_WITHDRAWAL_PENDING = re.compile(r"\bwithdraw(?:al)?\b.{0,60}\bpending\b|\bpending\b.{0,60}\bwithdraw(?:al)?\b", re.I)
_DEPOSIT_PENDING = re.compile(r"\bdeposit\b.{0,60}\bpending\b|\bpending\b.{0,60}\bdeposit\b", re.I)
_PAYMENT_FAILED = re.compile(r"\bpayment\b.{0,60}\b(?:failed|failure)\b|\b(?:failed|failure)\b.{0,60}\bpayment\b", re.I)


def classify_spike_category(text: str) -> Optional[str]:
    """Return a report category only when the wording has explicit status evidence."""
    text = (text or "")[:2000]
    if _WITHDRAWAL_LOCK.search(text):
        return "WITHDRAWAL_LOCKED"
    if _ORDER_LOCK.search(text):
        return "ORDER_LOCKED"
    if _WITHDRAWAL_PENDING.search(text):
        return "WITHDRAWAL_PENDING"
    if _DEPOSIT_PENDING.search(text):
        return "DEPOSIT_PENDING"
    if _PAYMENT_FAILED.search(text):
        return "PAYMENT_FAILED"
    return None


class IssueSpikeMonitor:
    """Record minimal issue metadata and atomically claim one alert per incident."""

    def __init__(self, db_path: str, supabase: Any = None):
        self.db_path = str(db_path)
        self.supabase = supabase

    def record_and_check(
        self,
        *,
        category: str,
        chat_id: int,
        message_id: int,
        user_id: Optional[int],
        threshold: int = 10,
        window_minutes: int = 15,
        cooldown_seconds: int = 900,
        now: Optional[datetime] = None,
    ) -> Optional[Dict[str, Any]]:
        category = category.upper()
        if category not in SPIKE_CATEGORIES:
            raise ValueError("Unsupported issue spike category")
        if threshold < 1 or window_minutes < 1 or cooldown_seconds < 1:
            raise ValueError("Spike threshold, window, and cooldown must be positive")
        current = now or datetime.now(timezone.utc)
        if current.tzinfo is None:
            current = current.replace(tzinfo=timezone.utc)
        current = current.astimezone(timezone.utc)
        window_start = current - timedelta(minutes=window_minutes)
        current_iso, start_iso = current.isoformat(), window_start.isoformat()

        if self.supabase is not None:
            self.supabase.table("issue_spike_reports").upsert(
                {
                    "category": category,
                    "chat_id": chat_id,
                    "message_id": message_id,
                    "user_id": user_id,
                    "created_at": current_iso,
                },
                on_conflict="category,chat_id,message_id",
                ignore_duplicates=True,
            ).execute()
            result = self.supabase.rpc(
                "claim_issue_spike_alert",
                {
                    "p_category": category,
                    "p_window_start": start_iso,
                    "p_now": current_iso,
                    "p_threshold": threshold,
                    "p_cooldown_seconds": cooldown_seconds,
                },
            ).execute().data
            if isinstance(result, list) and result:
                result = result[0]
            if not isinstance(result, dict):
                raise RuntimeError("Supabase returned an invalid issue spike result")
            should_alert = bool(result.get("should_alert"))
            if not should_alert:
                return None
            return {
                "category": category,
                "report_count": int(result.get("report_count", 0)),
                "unique_users": int(result.get("unique_user_count", 0)),
                "window_minutes": window_minutes,
            }

        with closing(sqlite3.connect(self.db_path, timeout=15)) as conn:
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA busy_timeout = 15000")
            conn.execute("BEGIN IMMEDIATE")
            conn.execute(
                """CREATE TABLE IF NOT EXISTS issue_spike_reports (
                    category TEXT NOT NULL,
                    chat_id INTEGER NOT NULL,
                    message_id INTEGER NOT NULL,
                    user_id INTEGER,
                    created_at TEXT NOT NULL,
                    PRIMARY KEY(category, chat_id, message_id)
                )"""
            )
            conn.execute(
                """CREATE TABLE IF NOT EXISTS issue_spike_alert_state (
                    category TEXT PRIMARY KEY,
                    last_alert_at TEXT,
                    incident_active INTEGER NOT NULL DEFAULT 0
                )"""
            )
            conn.execute(
                """INSERT OR IGNORE INTO issue_spike_reports(
                    category, chat_id, message_id, user_id, created_at
                ) VALUES(?,?,?,?,?)""",
                (category, chat_id, message_id, user_id, current_iso),
            )
            conn.execute(
                "DELETE FROM issue_spike_reports WHERE created_at<?",
                ((current - timedelta(days=30)).isoformat(),),
            )
            count_row = conn.execute(
                """SELECT COUNT(*) AS reports, COUNT(DISTINCT user_id) AS users
                   FROM issue_spike_reports
                   WHERE category=? AND created_at>=? AND created_at<=?""",
                (category, start_iso, current_iso),
            ).fetchone()
            report_count, unique_users = int(count_row["reports"]), int(count_row["users"])
            state = conn.execute(
                "SELECT last_alert_at,incident_active FROM issue_spike_alert_state WHERE category=?",
                (category,),
            ).fetchone()

            if report_count < threshold:
                conn.execute(
                    """INSERT INTO issue_spike_alert_state(category,last_alert_at,incident_active)
                       VALUES(?,NULL,0) ON CONFLICT(category) DO UPDATE SET incident_active=0""",
                    (category,),
                )
                conn.commit()
                return None

            cooldown_passed = not state or not state["last_alert_at"]
            if state and state["last_alert_at"]:
                last_alert = datetime.fromisoformat(state["last_alert_at"])
                cooldown_passed = (current - last_alert).total_seconds() >= cooldown_seconds
            should_alert = (not state or not state["incident_active"]) and cooldown_passed
            if should_alert:
                conn.execute(
                    """INSERT INTO issue_spike_alert_state(category,last_alert_at,incident_active)
                       VALUES(?,?,1) ON CONFLICT(category) DO UPDATE SET
                       last_alert_at=excluded.last_alert_at,incident_active=1""",
                    (category, current_iso),
                )
            conn.commit()
            if not should_alert:
                return None
            return {
                "category": category,
                "report_count": report_count,
                "unique_users": unique_users,
                "window_minutes": window_minutes,
            }
