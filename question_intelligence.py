"""Question triage, duplicate clustering, and complete daily report helpers."""

from __future__ import annotations

import csv
import difflib
import hashlib
import io
import json
import logging
import os
import re
import sqlite3
import threading
import uuid
from collections import Counter
from datetime import date, datetime, time, timedelta, timezone
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

log = logging.getLogger("ipay-bot.intelligence")
_SQLITE_SCHEMA_LOCK = threading.Lock()
_SQLITE_INITIALIZED: set[str] = set()
_IST = timezone(timedelta(hours=5, minutes=30), name="IST")


def resolve_timezone(timezone_name: str):
    """Resolve an IANA timezone name, falling back for UTC/IST when tzdata is absent."""
    try:
        from zoneinfo import ZoneInfo

        return ZoneInfo(timezone_name)
    except Exception as exc:
        normalized = (timezone_name or "").strip().upper()
        if normalized in {"UTC", "ETC/UTC", "GMT"}:
            return timezone.utc
        if normalized in {"ASIA/KOLKATA", "ASIA/CALCUTTA", "IST"}:
            return _IST
        raise ValueError(f"Invalid report timezone: {timezone_name}") from exc

_TOPICS: Sequence[Tuple[str, Sequence[str]]] = (
    ("withdrawal", ("withdraw", "payout", "cashout", "cash out", "pending withdrawal", "withdrawal")),
    ("deposit", ("deposit", "add money", "recharge", "utr", "credited")),
    ("payment", ("payment", "upi", "transaction", "paid", "paying")),
    ("wallet", ("wallet", "phonepe", "paytm", "mobikwik", "freecharge")),
    ("account_verification", ("verify", "verification", "kyc", "verified")),
    ("login", ("login", "log in", "password", "otp", "sign in")),
    ("transaction_tracking", ("transaction id", "utr", "reference number", "tracking", "order id")),
    ("account_restriction", ("blocked", "restricted", "locked", "suspended", "freeze")),
    ("referral", ("referral", "invite", "refer")),
    ("technical_error", ("error", "crash", "bug", "not working", "doesn't work", "failed", "failure")),
    ("bot_functionality", ("bot", "command", "telegram")),
)
_QUESTION_MARKERS = (
    "what", "when", "where", "why", "how", "can i", "could you", "please help",
    "help me", "any update", "not working", "not received", "problem", "issue",
    "complaint", "unable", "failed", "stuck", "status", "?", "తెలియ", "ఎప్పుడు",
    "कब", "कैसे", "क्यों", "செய்ய", "எப்போது",
)
_COMPLAINT_MARKERS = ("complaint", "not working", "still not", "very bad", "worst", "unfair", "cheated", "not received", "failed again")
_FEATURE_MARKERS = ("feature request", "please add", "add a feature", "would like to", "suggestion", "it would be useful")
_HUMAN_MARKERS = ("agent", "human", "support person", "customer care", "talk to someone", "call me")
_SENSITIVE = re.compile(r"(?i)(?:\b(?:otp|password|passcode|pin|cvv|secret key|private key|access token)\b\s*[:= -]?\s*[A-Za-z0-9@#$%^&*!_-]{3,}|\b\d{12,19}\b)")
_URL = re.compile(r"https?://\S+", re.I)


def classify_message(text: str) -> Dict[str, Any]:
    """Cheap, deterministic triage. It never calls an external model."""
    original = (text or "").strip()
    clean = _URL.sub(" ", original)
    lower = clean.casefold()
    if not clean or len(clean) < 4:
        return {"eligible": False, "classification_confidence": 0.0}
    feature = any(marker in lower for marker in _FEATURE_MARKERS)
    complaint = any(marker in lower for marker in _COMPLAINT_MARKERS)
    human = any(marker in lower for marker in _HUMAN_MARKERS)
    matched = [topic for topic, phrases in _TOPICS if any(phrase in lower for phrase in phrases)]
    question = "?" in clean or any(marker in lower for marker in _QUESTION_MARKERS)
    eligible = bool(question or complaint or feature or human or matched)
    if not eligible:
        return {"eligible": False, "classification_confidence": 0.25}
    category = "feature_request" if feature else "general_support" if human else matched[0] if matched else "general_support"
    intent = "feature_request" if feature else "complaint" if complaint else "support_request" if human else "question" if question else "problem_report"
    priority = "high" if any(s in lower for s in ("urgent", "critical", "fraud", "hacked", "stolen", "money missing")) else "normal"
    sentiment = "negative" if complaint else "neutral"
    confidence = 0.82 if matched or complaint or feature or human else 0.62
    lang = "hi" if re.search(r"[\u0900-\u097f]", clean) else "te" if re.search(r"[\u0c00-\u0c7f]", clean) else "ta" if re.search(r"[\u0b80-\u0bff]", clean) else "unknown"
    normalized = re.sub(r"[^\w\s]", " ", lower)
    normalized = re.sub(r"\b(?:my|i|me|a|the|is|are|was|for|please|help|when|why|how|what)\b", " ", normalized)
    normalized = " ".join(normalized.split())[:500]
    summary_words = [word for word in normalized.split() if len(word) > 2][:9]
    return {
        "eligible": True,
        "category": category,
        "subcategory": matched[0] if matched else "other",
        "intent": intent,
        "summary": " ".join(summary_words)[:240] or category.replace("_", " "),
        "priority": priority,
        "sentiment": sentiment,
        "language": lang,
        "classification_confidence": confidence,
        "normalized_text": normalized,
        "is_complaint": complaint,
        "is_feature_request": feature,
        "needs_human": human or complaint,
    }


async def classify_with_ai(text: str, client: Any) -> Optional[Dict[str, Any]]:
    """Optional multilingual classification; failures leave the local classifier in place."""
    categories = [key for key, _ in _TOPICS] + ["feature_request", "general_support"]
    prompt = (
        "Classify this Telegram support message. Return only JSON with keys: eligible (boolean), "
        "category, subcategory, intent, summary, priority (low|normal|high|critical), "
        "sentiment (positive|neutral|negative), language (short language code), "
        "confidence (0..1), is_complaint (boolean), is_feature_request (boolean), needs_human (boolean). "
        f"Allowed categories: {', '.join(categories)}. Mark greetings, spam, and unrelated chat as not eligible. "
        "Do not repeat credentials or personal numbers in the summary. Message: " + text[:1500]
    )
    try:
        result = await client.chat.completions.create(
            model=os.getenv("AI_MODEL", "gpt-4o-mini"),
            messages=[{"role": "user", "content": prompt}],
            response_format={"type": "json_object"},
            temperature=0,
            max_tokens=180,
            timeout=8.0,
        )
        raw = result.choices[0].message.content or "{}"
        data = json.loads(raw)
        if not isinstance(data, dict) or not data.get("eligible"):
            return {"eligible": False}
        if data.get("category") not in categories:
            return None
        priority = data.get("priority")
        if priority not in {"low", "normal", "high", "critical"}:
            priority = "normal"
        sentiment = data.get("sentiment")
        if sentiment not in {"positive", "neutral", "negative"}:
            sentiment = "neutral"
        try:
            confidence = min(1.0, max(0.0, float(data.get("confidence", 0))))
        except (TypeError, ValueError):
            confidence = 0.0
        return {
            "eligible": True,
            "category": data["category"],
            "subcategory": str(data.get("subcategory", "other"))[:80],
            "intent": str(data.get("intent", "support_request"))[:80],
            "summary": _safe_text(str(data.get("summary", data["category"])), 240),
            "priority": priority,
            "sentiment": sentiment,
            "language": str(data.get("language", "unknown"))[:16],
            "classification_confidence": confidence,
            "is_complaint": bool(data.get("is_complaint")),
            "is_feature_request": bool(data.get("is_feature_request")),
            "needs_human": bool(data.get("needs_human")),
        }
    except Exception as exc:
        log.info("AI question classification unavailable; using local rules (%s)", type(exc).__name__)
        return None


def _safe_text(value: str, limit: int = 2000) -> str:
    value = (value or "").strip()
    if _SENSITIVE.search(value):
        return "[redacted: potentially sensitive content]"
    value = re.sub(r"(?<!\d)\d{6}(?!\d)", "[redacted UID]", value)
    return value[:limit]


def sanitize_for_classification(value: str) -> str:
    return _safe_text(value, 1500)


class _ClosingConnection(sqlite3.Connection):
    def __exit__(self, *args: Any) -> Any:
        try:
            return super().__exit__(*args)
        finally:
            self.close()


def _sqlite_connect(path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(path, timeout=15, factory=_ClosingConnection)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


class QuestionStore:
    """Store questions in the configured Supabase client or local SQLite file."""

    def __init__(self, db_path: str, supabase: Any = None):
        self.db_path = db_path
        self.supabase = supabase

    def ensure_sqlite_schema(self) -> None:
        if self.supabase is not None:
            return
        key = str(self.db_path)
        if key in _SQLITE_INITIALIZED:
            return
        with _SQLITE_SCHEMA_LOCK:
            if key not in _SQLITE_INITIALIZED:
                with _sqlite_connect(self.db_path) as conn:
                    conn.executescript(SQLITE_SCHEMA)
                _SQLITE_INITIALIZED.add(key)

    def _similar_cluster(self, category: str, normalized: str) -> Optional[Dict[str, Any]]:
        if self.supabase is not None:
            rows = (self.supabase.table("problem_clusters").select("id,title,category,normalized_text,occurrence_count,unique_user_count,first_reported_at,last_reported_at,status,priority").eq("category", category).order("last_reported_at", desc=True).limit(300).execute().data or [])
        else:
            with _sqlite_connect(self.db_path) as conn:
                rows = [dict(row) for row in conn.execute("SELECT id,title,category,normalized_text,occurrence_count,unique_user_count,first_reported_at,last_reported_at,status,priority FROM problem_clusters WHERE category=? ORDER BY last_reported_at DESC LIMIT 300", (category,))]
        tokens = set(normalized.split())
        best, best_score = None, 0.0
        for row in rows:
            prior = row.get("normalized_text") or ""
            if not prior:
                continue
            prior_tokens = set(prior.split())
            overlap = len(tokens & prior_tokens) / max(1, len(tokens | prior_tokens))
            ratio = difflib.SequenceMatcher(None, normalized, prior).ratio()
            score = max(overlap, ratio)
            if score > best_score:
                best, best_score = row, score
        return best if best_score >= 0.62 else None

    def _audit_admin(self, event_type: str, admin_id: int, question_id: str, detail: Dict[str, Any]) -> None:
        payload = {"event_type": event_type, "severity": "info", "user_id": admin_id, "detail": {"question_id": question_id, **detail}}
        try:
            if self.supabase is not None:
                self.supabase.table("bot_activity_logs").insert(payload).execute()
            else:
                with _sqlite_connect(self.db_path) as conn:
                    conn.execute("INSERT INTO bot_activity_logs(event_type,severity,user_id,detail,created_at) VALUES(?, 'info', ?, ?, ?)", (event_type,admin_id,json.dumps(payload["detail"],ensure_ascii=False),datetime.now(timezone.utc).isoformat()))
        except Exception as exc:
            log.warning("Could not write admin audit event (%s)", type(exc).__name__)

    def record(self, *, user_id: Optional[int], username: Optional[str], first_name: Optional[str], chat_id: int, chat_type: str, chat_title: Optional[str], message_id: int, original_text: str, bot_response: Optional[str], answer_status: str, created_at: int, classification: Optional[Dict[str, Any]] = None) -> Optional[str]:
        triage = classify_message(original_text)
        if classification:
            if classification.get("eligible") is False:
                return None
            allowed = {"category", "subcategory", "intent", "summary", "priority", "sentiment", "language", "classification_confidence", "is_complaint", "is_feature_request", "needs_human"}
            triage.update({key: value for key, value in classification.items() if key in allowed})
        if not triage.get("eligible"):
            return None
        if self.supabase is not None:
            existing = self.supabase.table("question_events").select("id").eq("chat_id", chat_id).eq("message_id", message_id).limit(1).execute().data or []
        else:
            with _sqlite_connect(self.db_path) as conn:
                existing = conn.execute("SELECT id FROM question_events WHERE chat_id=? AND message_id=?", (chat_id,message_id)).fetchone()
        if existing:
            return str(existing[0] if not isinstance(existing, list) else existing[0]["id"])
        question_id = str(uuid.uuid4())
        safe_original = _safe_text(original_text)
        safe_response = _safe_text(bot_response or "", 4000) if bot_response else None
        duplicate = self._similar_cluster(triage["category"], triage["normalized_text"])
        cluster_id = duplicate["id"] if duplicate else str(uuid.uuid5(uuid.NAMESPACE_URL, "ipay-problem:" + triage["category"] + ":" + triage["normalized_text"]))
        now_iso = datetime.fromtimestamp(created_at, timezone.utc).isoformat()
        user_key = str(user_id) if user_id is not None else None
        if self.supabase is not None:
            question_payload = {
                "id": question_id, "user_id": user_id, "username": username,
                "display_name": first_name, "chat_id": chat_id, "chat_type": chat_type,
                "chat_title": chat_title, "message_id": message_id,
                "original_message": safe_original, "language": triage["language"],
                "bot_response": safe_response, "answer_status": answer_status,
                "classification_confidence": triage["classification_confidence"],
                "category": triage["category"], "subcategory": triage["subcategory"],
                "intent": triage["intent"], "problem_summary": triage["summary"],
                "priority": triage["priority"], "sentiment": triage["sentiment"],
                "is_complaint": triage["is_complaint"],
                "is_feature_request": triage["is_feature_request"],
                "related_question_id": None, "created_at": now_iso,
            }
            result = self.supabase.table("question_events").upsert(
                question_payload, on_conflict="chat_id,message_id", ignore_duplicates=True
            ).execute()
            actual_id = (result.data or [{}])[0].get("id") if result.data else None
            if not actual_id:
                found = self.supabase.table("question_events").select("id").eq("chat_id", chat_id).eq("message_id", message_id).limit(1).execute().data or []
                actual_id = found[0]["id"] if found else question_id
            if duplicate is None:
                self.supabase.table("problem_clusters").upsert({
                    "id": cluster_id, "title": triage["summary"][:180], "category": triage["category"],
                    "normalized_text": triage["normalized_text"], "occurrence_count": 0,
                    "unique_user_count": 0, "first_reported_at": now_iso, "last_reported_at": now_iso,
                    "status": "open", "priority": triage["priority"],
                }, on_conflict="id", ignore_duplicates=True).execute()
            self.supabase.table("question_cluster_links").upsert(
                {"question_id": actual_id, "cluster_id": cluster_id},
                on_conflict="question_id", ignore_duplicates=True,
            ).execute()
            if user_id is not None:
                self.supabase.table("user_question_history").upsert({"user_id": user_id, "question_id": actual_id, "created_at": now_iso}, on_conflict="user_id,question_id", ignore_duplicates=True).execute()
            self.supabase.rpc("refresh_problem_cluster", {"p_cluster_id": cluster_id}).execute()
            if answer_status in {"unanswered", "partially_answered", "needs_human_review"}:
                gap_summary = triage["summary"][:240]
                known_gap = self.supabase.table("bot_knowledge_gaps").select("id,occurrence_count").eq("category", triage["category"]).eq("summary", gap_summary).limit(1).execute().data or []
                if known_gap:
                    self.supabase.table("bot_knowledge_gaps").update({"occurrence_count": int(known_gap[0]["occurrence_count"]) + 1, "last_seen_at": now_iso}).eq("id", known_gap[0]["id"]).execute()
                else:
                    self.supabase.table("bot_knowledge_gaps").insert({"category": triage["category"], "summary": gap_summary, "occurrence_count": 1, "first_seen_at": now_iso, "last_seen_at": now_iso}).execute()
            return str(actual_id)

        with _sqlite_connect(self.db_path) as conn:
            exists = conn.execute("SELECT id FROM question_events WHERE chat_id=? AND message_id=?", (chat_id, message_id)).fetchone()
            if exists:
                return str(exists["id"])
            if duplicate is None:
                conn.execute("INSERT INTO problem_clusters(id,title,category,normalized_text,occurrence_count,unique_user_count,first_reported_at,last_reported_at,status,priority) VALUES(?,?,?,?,0,0,?,?,?,?)", (cluster_id, triage["summary"][:180], triage["category"], triage["normalized_text"], now_iso, now_iso, "open", triage["priority"]))
            conn.execute("INSERT INTO question_events(id,user_id,username,display_name,chat_id,chat_type,chat_title,message_id,original_message,language,bot_response,answer_status,classification_confidence,category,subcategory,intent,problem_summary,priority,sentiment,is_complaint,is_feature_request,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", (question_id,user_key,username,first_name,chat_id,chat_type,chat_title,message_id,safe_original,triage["language"],safe_response,answer_status,triage["classification_confidence"],triage["category"],triage["subcategory"],triage["intent"],triage["summary"],triage["priority"],triage["sentiment"],int(triage["is_complaint"]),int(triage["is_feature_request"]),now_iso,now_iso))
            conn.execute("INSERT INTO question_cluster_links(question_id,cluster_id) VALUES(?,?)", (question_id,cluster_id))
            if user_id is not None:
                conn.execute("INSERT OR IGNORE INTO user_question_history(user_id,question_id,created_at) VALUES(?,?,?)", (user_key,question_id,now_iso))
            users = {str(r[0]) for r in conn.execute("SELECT DISTINCT q.user_id FROM question_events q JOIN question_cluster_links l ON l.question_id=q.id WHERE l.cluster_id=? AND q.user_id IS NOT NULL", (cluster_id,))}
            if user_id is not None:
                users.add(str(user_id))
            conn.execute("UPDATE problem_clusters SET occurrence_count=(SELECT COUNT(*) FROM question_cluster_links WHERE cluster_id=?),unique_user_count=?,last_reported_at=?,priority=CASE WHEN priority='high' OR ?='high' THEN 'high' ELSE priority END WHERE id=?", (cluster_id, len(users), now_iso, triage["priority"], cluster_id))
            if answer_status in {"unanswered", "partially_answered", "needs_human_review"}:
                conn.execute("INSERT INTO bot_knowledge_gaps(id,category,summary,occurrence_count,first_seen_at,last_seen_at,status) VALUES(?,?,?,1,?,?,'open') ON CONFLICT(category,summary) DO UPDATE SET occurrence_count=occurrence_count+1,last_seen_at=excluded.last_seen_at", (str(uuid.uuid4()),triage["category"],triage["summary"][:240],now_iso,now_iso))
        return question_id

    def record_activity(self, *, chat_id: int, user_id: Optional[int], message_id: int, chat_type: str, created_at: int) -> None:
        created = datetime.fromtimestamp(created_at, timezone.utc).isoformat()
        if self.supabase is not None:
            self.supabase.table("bot_activity_logs").upsert({"event_type": "incoming_message", "severity": "info", "chat_id": chat_id, "user_id": user_id, "message_id": message_id, "detail": {"chat_type": chat_type}, "created_at": created}, on_conflict="chat_id,message_id", ignore_duplicates=True).execute()
        else:
            with _sqlite_connect(self.db_path) as conn:
                conn.execute("INSERT OR IGNORE INTO bot_activity_logs(event_type,severity,chat_id,user_id,message_id,detail,created_at) VALUES('incoming_message','info',?,?,?,?,?)", (chat_id,user_id,message_id,json.dumps({"chat_type":chat_type}),created))

    def list_questions(self, *, status: Optional[str] = None, category: Optional[str] = None, priority: Optional[str] = None, chat_id: Optional[int] = None, user_id: Optional[int] = None, search: Optional[str] = None, limit: int = 10) -> List[Dict[str, Any]]:
        limit = max(1, min(limit, 50))
        if self.supabase is not None:
            query = self.supabase.table("question_events").select("id,user_id,username,display_name,chat_id,chat_type,chat_title,original_message,bot_response,answer_status,category,priority,assigned_admin_id,created_at").order("created_at", desc=True).limit(limit)
            if status:
                query = query.eq("answer_status", status)
            if category:
                query = query.eq("category", category)
            if priority:
                query = query.eq("priority", priority)
            if chat_id is not None:
                query = query.eq("chat_id", chat_id)
            if user_id is not None:
                query = query.eq("user_id", user_id)
            if search:
                query = query.ilike("original_message", f"%{search[:100]}%")
            return query.execute().data or []
        with _sqlite_connect(self.db_path) as conn:
            sql = "SELECT id,user_id,username,display_name,chat_id,chat_type,chat_title,original_message,bot_response,answer_status,category,priority,assigned_admin_id,created_at FROM question_events"
            clauses: List[str] = []
            args: List[Any] = []
            for column, value in (("answer_status", status), ("category", category), ("priority", priority), ("chat_id", chat_id), ("user_id", user_id)):
                if value is not None:
                    clauses.append(f"{column}=?"); args.append(value)
            if search:
                clauses.append("original_message LIKE ?"); args.append(f"%{search[:100]}%")
            if clauses:
                sql += " WHERE " + " AND ".join(clauses)
            sql += " ORDER BY created_at DESC LIMIT ?"; args.append(limit)
            return [dict(row) for row in conn.execute(sql, args)]

    def update_status(self, question_id: str, status: str, admin_id: int = 0) -> bool:
        if status not in {"unanswered", "partially_answered", "answered", "needs_human_review", "resolved", "ignored"}:
            return False
        now = datetime.now(timezone.utc).isoformat()
        if self.supabase is not None:
            res = self.supabase.table("question_events").update({"answer_status": status, "updated_at": now}).eq("id", question_id).select("id").execute()
            changed = bool(res.data)
            if changed:
                self._audit_admin("question_status_changed", admin_id, question_id, {"status": status})
            return changed
        with _sqlite_connect(self.db_path) as conn:
            cur = conn.execute("UPDATE question_events SET answer_status=?,updated_at=? WHERE id=?", (status,now,question_id))
        changed = cur.rowcount > 0
        if changed:
            self._audit_admin("question_status_changed", admin_id, question_id, {"status": status})
        return changed

    def assign_question(self, question_id: str, assigned_admin_id: int) -> bool:
        now = datetime.now(timezone.utc).isoformat()
        if self.supabase is not None:
            response = self.supabase.table("question_events").update({"assigned_admin_id": assigned_admin_id, "updated_at": now}).eq("id", question_id).select("id").execute()
            changed = bool(response.data)
            if changed:
                self._audit_admin("question_assigned", assigned_admin_id, question_id, {"assigned_admin_id": assigned_admin_id})
            return changed
        with _sqlite_connect(self.db_path) as conn:
            cur = conn.execute("UPDATE question_events SET assigned_admin_id=?,updated_at=? WHERE id=?", (assigned_admin_id,now,question_id))
            changed = cur.rowcount > 0
        if changed:
            self._audit_admin("question_assigned", assigned_admin_id, question_id, {"assigned_admin_id": assigned_admin_id})
        return changed

    def question_details(self, question_id: str) -> Optional[Dict[str, Any]]:
        if self.supabase is not None:
            rows = self.supabase.table("question_events").select("*").eq("id", question_id).limit(1).execute().data or []
            if not rows:
                return None
            question = rows[0]
            question["admin_notes"] = self.supabase.table("admin_notes").select("admin_user_id,note,created_at").eq("question_id", question_id).order("created_at").execute().data or []
            question["resolutions"] = self.supabase.table("question_resolutions").select("resolution,resolved_by,resolved_at").eq("question_id", question_id).order("resolved_at").execute().data or []
            links = self.supabase.table("question_cluster_links").select("cluster_id").eq("question_id", question_id).limit(1).execute().data or []
            question["related_examples"] = []
            if links:
                related = self.supabase.table("question_cluster_links").select("question_id").eq("cluster_id", links[0]["cluster_id"]).limit(100).execute().data or []
                related_ids = [item["question_id"] for item in related]
                if related_ids:
                    question["related_examples"] = self.supabase.table("question_events").select("id,user_id,original_message,answer_status,created_at").in_("id", related_ids).order("created_at", desc=True).limit(20).execute().data or []
            return question
        with _sqlite_connect(self.db_path) as conn:
            row = conn.execute("SELECT * FROM question_events WHERE id=?", (question_id,)).fetchone()
            if not row:
                return None
            result = dict(row)
            result["admin_notes"] = [dict(note) for note in conn.execute("SELECT admin_user_id,note,created_at FROM admin_notes WHERE question_id=? ORDER BY created_at", (question_id,))]
            result["resolutions"] = [dict(item) for item in conn.execute("SELECT resolution,resolved_by,resolved_at FROM question_resolutions WHERE question_id=? ORDER BY resolved_at", (question_id,))]
            cluster = conn.execute("SELECT cluster_id FROM question_cluster_links WHERE question_id=?", (question_id,)).fetchone()
            result["related_examples"] = [dict(item) for item in conn.execute("SELECT q.id,q.user_id,q.original_message,q.answer_status,q.created_at FROM question_events q JOIN question_cluster_links l ON l.question_id=q.id WHERE l.cluster_id=? ORDER BY q.created_at DESC LIMIT 20", (cluster[0],))] if cluster else []
            return result

    def user_history(self, user_id: int, limit: int = 20) -> List[Dict[str, Any]]:
        if self.supabase is not None:
            return self.supabase.table("question_events").select("id,chat_id,chat_type,original_message,answer_status,category,priority,created_at").eq("user_id", user_id).order("created_at", desc=True).limit(max(1,min(limit,50))).execute().data or []
        with _sqlite_connect(self.db_path) as conn:
            rows = conn.execute("SELECT id,chat_id,chat_type,original_message,answer_status,category,priority,created_at FROM question_events WHERE user_id=? ORDER BY created_at DESC LIMIT ?", (str(user_id),max(1,min(limit,50)))).fetchall()
            return [dict(row) for row in rows]

    def add_note(self, question_id: str, admin_id: int, note: str) -> bool:
        safe_note = _safe_text(note, 2000)
        if not safe_note:
            return False
        if self.supabase is not None:
            self.supabase.table("admin_notes").insert({"question_id": question_id, "admin_user_id": admin_id, "note": safe_note}).execute()
            self._audit_admin("question_note_added", admin_id, question_id, {})
            return True
        with _sqlite_connect(self.db_path) as conn:
            try:
                conn.execute("INSERT INTO admin_notes(question_id,admin_user_id,note,created_at) VALUES(?,?,?,?)", (question_id,admin_id,safe_note,datetime.now(timezone.utc).isoformat()))
                # Audit separately after the note transaction has committed.
                conn.commit()
                self._audit_admin("question_note_added", admin_id, question_id, {})
                return True
            except sqlite3.IntegrityError:
                return False

    def resolve(self, question_id: str, admin_id: int, resolution: str) -> bool:
        resolution = _safe_text(resolution, 2000)
        if not resolution:
            return False
        now = datetime.now(timezone.utc).isoformat()
        if self.supabase is not None:
            updated = self.supabase.table("question_events").update({"answer_status": "resolved", "updated_at": now}).eq("id", question_id).select("id").execute().data or []
            if not updated:
                return False
            self.supabase.table("question_resolutions").insert({"question_id": question_id, "resolution": resolution, "resolved_by": admin_id, "resolved_at": now}).execute()
            self._audit_admin("question_resolved", admin_id, question_id, {})
            return True
        with _sqlite_connect(self.db_path) as conn:
            updated = conn.execute("UPDATE question_events SET answer_status='resolved',updated_at=? WHERE id=?", (now,question_id))
            if not updated.rowcount:
                return False
            conn.execute("INSERT INTO question_resolutions(question_id,resolution,resolved_by,resolved_at) VALUES(?,?,?,?)", (question_id,resolution,admin_id,now))
        self._audit_admin("question_resolved", admin_id, question_id, {})
        return True

    def daily_report(self, day: date, timezone_name: str = "UTC") -> Dict[str, Any]:
        """Fetch every event for a local calendar day using stable pagination."""
        tz = resolve_timezone(timezone_name)
        start = datetime.combine(day, time.min, tzinfo=tz).astimezone(timezone.utc)
        end = (datetime.combine(day + timedelta(days=1), time.min, tzinfo=tz)).astimezone(timezone.utc)
        rows: List[Dict[str, Any]] = []
        offset, page_size = 0, 500
        if self.supabase is not None:
            while True:
                page = (self.supabase.table("question_events").select("*").gte("created_at", start.isoformat()).lt("created_at", end.isoformat()).order("created_at").order("id").range(offset, offset + page_size - 1).execute().data or [])
                rows.extend(page)
                if len(page) < page_size:
                    break
                offset += page_size
        else:
            with _sqlite_connect(self.db_path) as conn:
                while True:
                    page = [dict(row) for row in conn.execute("SELECT * FROM question_events WHERE created_at>=? AND created_at<? ORDER BY created_at,id LIMIT ? OFFSET ?", (start.isoformat(),end.isoformat(),page_size,offset))]
                    rows.extend(page)
                    if len(page) < page_size:
                        break
                    offset += page_size
        if rows:
            question_ids = [row["id"] for row in rows]
            cluster_by_id: Dict[str, Dict[str, Any]] = {}
            if self.supabase is not None:
                links: List[Dict[str, Any]] = []
                for index in range(0, len(question_ids), 100):
                    links.extend(self.supabase.table("question_cluster_links").select("question_id,cluster_id").in_("question_id", question_ids[index:index + 100]).execute().data or [])
                cluster_ids = list({link["cluster_id"] for link in links})
                clusters_by_id: Dict[str, Dict[str, Any]] = {}
                for index in range(0, len(cluster_ids), 100):
                    for cluster in self.supabase.table("problem_clusters").select("id,title,occurrence_count,unique_user_count,status").in_("id", cluster_ids[index:index + 100]).execute().data or []:
                        clusters_by_id[cluster["id"]] = cluster
                cluster_by_id = {link["question_id"]: clusters_by_id.get(link["cluster_id"], {"id": link["cluster_id"]}) for link in links}
            else:
                with _sqlite_connect(self.db_path) as conn:
                    links = [dict(link) for link in conn.execute("SELECT l.question_id,l.cluster_id,c.title,c.occurrence_count,c.unique_user_count,c.status FROM question_cluster_links l JOIN problem_clusters c ON c.id=l.cluster_id WHERE l.question_id IN (%s)" % ",".join("?" for _ in question_ids), question_ids)]
                cluster_by_id = {link["question_id"]: {"id": link["cluster_id"], "title": link["title"], "occurrence_count": link["occurrence_count"], "unique_user_count": link["unique_user_count"], "status": link["status"]} for link in links}
            for row in rows:
                cluster = cluster_by_id.get(row["id"])
                row["problem_cluster_id"] = cluster.get("id") if cluster else None
                row["problem_cluster"] = cluster.get("title") if cluster else None
        by_category = Counter(row.get("category", "other") for row in rows)
        by_status = Counter(row.get("answer_status", "unanswered") for row in rows)
        groups = Counter((row.get("chat_title") or str(row.get("chat_id"))) for row in rows if row.get("chat_type") in {"group", "supergroup"})
        cluster_stats: Dict[str, Dict[str, Any]] = {}
        for row in rows:
            cluster_id = row.get("problem_cluster_id") or row.get("problem_summary", "other")
            item = cluster_stats.setdefault(cluster_id, {"problem": row.get("problem_cluster") or row.get("problem_summary", "other"), "occurrences": 0, "unique_users": set()})
            item["occurrences"] += 1
            if row.get("user_id") is not None:
                item["unique_users"].add(str(row["user_id"]))
        grouped: Dict[int, List[Dict[str, Any]]] = {}
        for row in rows:
            if row.get("chat_type") in {"group", "supergroup"}:
                grouped.setdefault(int(row["chat_id"]), []).append(row)
        for group_id, group_rows in grouped.items():
            group_title = group_rows[0].get("chat_title")
            common = Counter(row.get("problem_summary", "other") for row in group_rows).most_common(10)
            if self.supabase is not None:
                self.supabase.table("group_question_analytics").upsert({"chat_id": group_id, "report_day": day.isoformat(), "group_title": group_title, "question_count": len(group_rows), "unique_users": len({row.get("user_id") for row in group_rows if row.get("user_id") is not None}), "common_problems": [{"problem": item[0], "count": item[1]} for item in common]}, on_conflict="chat_id,report_day").execute()
            else:
                with _sqlite_connect(self.db_path) as conn:
                    conn.execute("INSERT INTO group_question_analytics(chat_id,report_day,group_title,question_count,unique_users,common_problems,updated_at) VALUES(?,?,?,?,?,?,?) ON CONFLICT(chat_id,report_day) DO UPDATE SET group_title=excluded.group_title,question_count=excluded.question_count,unique_users=excluded.unique_users,common_problems=excluded.common_problems,updated_at=excluded.updated_at", (group_id,day.isoformat(),group_title,len(group_rows),len({row.get("user_id") for row in group_rows if row.get("user_id") is not None}),json.dumps([{"problem": item[0], "count": item[1]} for item in common]),datetime.now(timezone.utc).isoformat()))
        if self.supabase is not None:
            total_incoming = 0
            offset = 0
            while True:
                page = (self.supabase.table("bot_activity_logs").select("id").eq("event_type", "incoming_message").gte("created_at", start.isoformat()).lt("created_at", end.isoformat()).order("created_at").order("id").range(offset, offset + page_size - 1).execute().data or [])
                total_incoming += len(page)
                if len(page) < page_size: break
                offset += page_size
        else:
            with _sqlite_connect(self.db_path) as conn:
                total_incoming = conn.execute("SELECT COUNT(*) FROM bot_activity_logs WHERE event_type='incoming_message' AND created_at>=? AND created_at<?", (start.isoformat(),end.isoformat())).fetchone()[0]
        unanswered = [row for row in rows if row.get("answer_status") in {"unanswered", "partially_answered", "needs_human_review"}]
        gaps = Counter((row.get("category", "other"), row.get("problem_summary", "unknown")) for row in unanswered)
        top_problems = sorted(({"problem": value["problem"], "occurrences": value["occurrences"], "unique_users": len(value["unique_users"])} for value in cluster_stats.values()), key=lambda item: item["occurrences"], reverse=True)[:20]
        return {"date": day.isoformat(), "timezone": timezone_name, "total_incoming_messages": total_incoming, "total_questions": len(rows), "unanswered_count": sum(v for k,v in by_status.items() if k == "unanswered"), "partially_answered_count": by_status.get("partially_answered",0), "resolved_count": by_status.get("resolved",0), "unique_users": len({row.get("user_id") for row in rows if row.get("user_id") is not None}), "groups_involved": len(groups), "complaints": sum(bool(row.get("is_complaint")) for row in rows), "feature_requests": sum(bool(row.get("is_feature_request")) for row in rows), "by_category": dict(by_category), "by_status": dict(by_status), "top_problems": top_problems, "top_groups": groups.most_common(20), "knowledge_gaps": [{"category": key[0], "summary": key[1], "occurrences": value} for key,value in gaps.most_common(30)], "unanswered": unanswered, "all_questions": rows}

    def store_report(self, report: Dict[str, Any]) -> None:
        payload = {"report_date": report["date"], "timezone": report["timezone"], "report_json": report, "created_at": datetime.now(timezone.utc).isoformat()}
        if self.supabase is not None:
            self.supabase.table("daily_reports").upsert(payload, on_conflict="report_date,timezone").execute()
        else:
            with _sqlite_connect(self.db_path) as conn:
                conn.execute("INSERT INTO daily_reports(report_date,timezone,report_json,created_at) VALUES(?,?,?,?) ON CONFLICT(report_date,timezone) DO UPDATE SET report_json=excluded.report_json,created_at=excluded.created_at", (report["date"],report["timezone"],json.dumps(report,ensure_ascii=False),payload["created_at"]))


def report_csv(report: Dict[str, Any]) -> bytes:
    output = io.StringIO(newline="")
    fields = ["id", "created_at", "user_id", "username", "chat_type", "chat_title", "category", "priority", "answer_status", "intent", "problem_summary", "problem_cluster_id", "problem_cluster", "original_message", "bot_response"]
    writer = csv.DictWriter(output, fieldnames=fields, extrasaction="ignore")
    writer.writeheader()
    writer.writerows(report["all_questions"])
    return output.getvalue().encode("utf-8-sig")


def report_documents(report: Dict[str, Any], max_bytes: int = 20_000_000) -> List[Tuple[str, bytes]]:
    """Create bounded downloadable report files without omitting individual questions."""
    questions = report["all_questions"]
    full_json = json.dumps(report, ensure_ascii=False, indent=2).encode("utf-8")
    full_csv = report_csv(report)
    if len(full_json) <= max_bytes and len(full_csv) <= max_bytes:
        suffix = report["date"]
        return [(f"ipay-report-{suffix}.json", full_json), (f"ipay-report-{suffix}.csv", full_csv)]
    output: List[Tuple[str, bytes]] = []
    chunk_size = 1000
    chunks = [questions[i:i + chunk_size] for i in range(0, len(questions), chunk_size)] or [[]]
    for index, chunk in enumerate(chunks, start=1):
        part = {key: value for key, value in report.items() if key != "all_questions"}
        part.update({"part": index, "parts": len(chunks), "all_questions": chunk})
        output.append((f"ipay-report-{report['date']}-part-{index}.json", json.dumps(part, ensure_ascii=False, indent=2).encode("utf-8")))
        output.append((f"ipay-report-{report['date']}-part-{index}.csv", report_csv({"all_questions": chunk})))
    if any(len(payload) > max_bytes for _, payload in output):
        raise ValueError("A report part exceeds the configured file size; reduce the report period or export size")
    return output


SQLITE_SCHEMA = """
CREATE TABLE IF NOT EXISTS problem_categories (
 id TEXT PRIMARY KEY, key TEXT NOT NULL UNIQUE, title TEXT NOT NULL, description TEXT,
 created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS problem_clusters (
 id TEXT PRIMARY KEY, title TEXT NOT NULL, category TEXT NOT NULL, normalized_text TEXT NOT NULL,
 occurrence_count INTEGER NOT NULL DEFAULT 0, unique_user_count INTEGER NOT NULL DEFAULT 0,
 first_reported_at TEXT NOT NULL, last_reported_at TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'open',
 priority TEXT NOT NULL DEFAULT 'normal', notes TEXT
);
CREATE INDEX IF NOT EXISTS idx_problem_clusters_category_time ON problem_clusters(category,last_reported_at);
CREATE TABLE IF NOT EXISTS question_events (
 id TEXT PRIMARY KEY, user_id TEXT, username TEXT, display_name TEXT, chat_id INTEGER NOT NULL,
 chat_type TEXT NOT NULL, chat_title TEXT, message_id INTEGER NOT NULL, original_message TEXT NOT NULL,
 language TEXT NOT NULL DEFAULT 'unknown', bot_response TEXT, answer_status TEXT NOT NULL,
 classification_confidence REAL NOT NULL DEFAULT 0, category TEXT NOT NULL, subcategory TEXT,
 intent TEXT, problem_summary TEXT, priority TEXT NOT NULL DEFAULT 'normal', sentiment TEXT,
 is_complaint INTEGER NOT NULL DEFAULT 0, is_feature_request INTEGER NOT NULL DEFAULT 0,
    related_question_id TEXT, assigned_admin_id INTEGER, created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
 UNIQUE(chat_id,message_id)
);
CREATE INDEX IF NOT EXISTS idx_question_events_created ON question_events(created_at,id);
CREATE INDEX IF NOT EXISTS idx_question_events_user ON question_events(user_id,created_at);
CREATE INDEX IF NOT EXISTS idx_question_events_chat ON question_events(chat_id,created_at);
CREATE INDEX IF NOT EXISTS idx_question_events_status ON question_events(answer_status,priority,created_at);
CREATE INDEX IF NOT EXISTS idx_question_events_category ON question_events(category,created_at);
CREATE TABLE IF NOT EXISTS question_cluster_links (
 question_id TEXT PRIMARY KEY REFERENCES question_events(id) ON DELETE CASCADE,
 cluster_id TEXT NOT NULL REFERENCES problem_clusters(id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_question_cluster_links_cluster ON question_cluster_links(cluster_id);
CREATE TABLE IF NOT EXISTS user_question_history (
 user_id TEXT NOT NULL, question_id TEXT NOT NULL REFERENCES question_events(id) ON DELETE CASCADE,
 created_at TEXT NOT NULL, PRIMARY KEY(user_id,question_id)
);
CREATE TABLE IF NOT EXISTS group_question_analytics (
 chat_id INTEGER NOT NULL, report_day TEXT NOT NULL, group_title TEXT, question_count INTEGER NOT NULL DEFAULT 0,
 unique_users INTEGER NOT NULL DEFAULT 0, common_problems TEXT NOT NULL DEFAULT '[]', updated_at TEXT NOT NULL,
 PRIMARY KEY(chat_id,report_day)
);
CREATE TABLE IF NOT EXISTS bot_knowledge_gaps (
 id TEXT PRIMARY KEY, category TEXT NOT NULL, summary TEXT NOT NULL, occurrence_count INTEGER NOT NULL DEFAULT 1,
 first_seen_at TEXT NOT NULL, last_seen_at TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'open', UNIQUE(category,summary)
);
CREATE TABLE IF NOT EXISTS admin_notes (
 id INTEGER PRIMARY KEY AUTOINCREMENT, question_id TEXT NOT NULL REFERENCES question_events(id) ON DELETE CASCADE,
 admin_user_id INTEGER NOT NULL, note TEXT NOT NULL, created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS question_resolutions (
 id INTEGER PRIMARY KEY AUTOINCREMENT, question_id TEXT NOT NULL REFERENCES question_events(id) ON DELETE CASCADE,
 resolution TEXT NOT NULL, answer_key TEXT, resolved_by INTEGER, resolved_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS daily_reports (
 report_date TEXT NOT NULL, timezone TEXT NOT NULL, report_json TEXT NOT NULL, created_at TEXT NOT NULL,
 PRIMARY KEY(report_date,timezone)
);
CREATE TABLE IF NOT EXISTS bot_activity_logs (
 id INTEGER PRIMARY KEY AUTOINCREMENT, event_type TEXT NOT NULL, severity TEXT NOT NULL DEFAULT 'info',
 chat_id INTEGER, user_id INTEGER, message_id INTEGER, detail TEXT NOT NULL DEFAULT '{}', created_at TEXT NOT NULL,
 UNIQUE(chat_id,message_id)
);
CREATE INDEX IF NOT EXISTS idx_bot_activity_type_time ON bot_activity_logs(event_type,created_at);
INSERT OR IGNORE INTO problem_categories(id,key,title,description,created_at) VALUES
 ('withdrawal','withdrawal','Withdrawal problems','Payout delays, pending withdrawals, and withdrawal errors',datetime('now')),
 ('deposit','deposit','Deposit problems','Missing or delayed deposits',datetime('now')),
 ('payment','payment','Payment issues','Payment processing and UPI problems',datetime('now')),
 ('wallet','wallet','Wallet issues','Wallet eligibility and connection problems',datetime('now')),
 ('account_verification','account_verification','Account verification','KYC and verification issues',datetime('now')),
 ('login','login','Login problems','Sign-in and access issues',datetime('now')),
 ('transaction_tracking','transaction_tracking','Transaction tracking','Order and transaction status queries',datetime('now')),
 ('account_restriction','account_restriction','Account restrictions','Locked or restricted accounts',datetime('now')),
 ('referral','referral','Referral problems','Referral and invite questions',datetime('now')),
 ('technical_error','technical_error','Technical errors','App failures and technical errors',datetime('now')),
 ('bot_functionality','bot_functionality','Bot functionality','Telegram bot features and commands',datetime('now')),
 ('feature_request','feature_request','Feature requests','Requested product or bot improvements',datetime('now')),
 ('general_support','general_support','General support','Other support requests',datetime('now'));
"""
