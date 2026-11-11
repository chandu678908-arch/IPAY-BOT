import asyncio
import sqlite3
import json
import unittest
import uuid
from contextlib import closing
from datetime import date
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

from question_intelligence import QuestionStore, classify_message, classify_with_ai, report_csv, report_documents


class QuestionIntelligenceTests(unittest.TestCase):
    def setUp(self):
        self.db_file = Path(__file__).resolve().parent / f"test-intel-{uuid.uuid4().hex}.sqlite3"
        self.db_path = str(self.db_file)
        self.store = QuestionStore(self.db_path)
        self.store.ensure_sqlite_schema()

    def tearDown(self):
        if self.db_file.exists():
            self.db_file.unlink()

    def record(self, *, user_id, message_id, text, timestamp=1794355200, chat_id=123):
        return self.store.record(
            user_id=user_id,
            username=f"user{user_id}",
            first_name="Test",
            chat_id=chat_id,
            chat_type="private",
            chat_title=None,
            message_id=message_id,
            original_text=text,
            bot_response="Please check the app.",
            answer_status="needs_human_review",
            created_at=timestamp,
        )

    def test_classifier_separates_relevant_support_from_chat(self):
        self.assertFalse(classify_message("hello there")["eligible"])
        result = classify_message("Why is my withdrawal still pending?")
        self.assertTrue(result["eligible"])
        self.assertEqual(result["category"], "withdrawal")
        self.assertEqual(result["intent"], "question")

    def test_records_each_duplicate_and_groups_without_losing_events(self):
        first = self.record(user_id=11, message_id=1, text="Why is my withdrawal pending?")
        second = self.record(user_id=22, message_id=2, text="My withdrawal is still pending, when will it arrive?")
        self.assertNotEqual(first, second)
        rows = self.store.list_questions(limit=10)
        self.assertEqual(len(rows), 2)
        with closing(sqlite3.connect(self.db_path)) as conn:
            cluster_count, unique_user_count = conn.execute(
                "SELECT occurrence_count,unique_user_count FROM problem_clusters"
            ).fetchone()
            linked_questions = conn.execute("SELECT COUNT(*) FROM question_cluster_links").fetchone()[0]
        self.assertEqual(cluster_count, 2)
        self.assertEqual(unique_user_count, 2)
        self.assertEqual(linked_questions, 2)

    def test_duplicate_delivery_is_idempotent(self):
        first = self.record(user_id=11, message_id=1, text="Why is my withdrawal pending?")
        again = self.record(user_id=11, message_id=1, text="Why is my withdrawal pending?")
        self.assertEqual(first, again)
        self.assertEqual(len(self.store.list_questions()), 1)

    def test_sensitive_values_are_redacted_and_unanswered_reports_are_complete(self):
        self.record(user_id=11, message_id=1, text="Why is withdrawal 684446 pending?")
        with closing(sqlite3.connect(self.db_path)) as conn:
            saved = conn.execute("SELECT original_message FROM question_events").fetchone()[0]
        self.assertNotIn("684446", saved)
        report = self.store.daily_report(date(2026, 11, 11), "UTC")
        self.assertEqual(report["total_questions"], 1)
        self.assertEqual(len(report["unanswered"]), 1)
        self.assertIn("original_message", report_csv(report).decode("utf-8-sig"))

    def test_daily_report_paginates_and_keeps_every_question(self):
        created = "2026-11-11T00:00:00+00:00"
        with closing(sqlite3.connect(self.db_path)) as conn:
            conn.executemany(
                "INSERT INTO question_events(id,chat_id,chat_type,message_id,original_message,answer_status,classification_confidence,category,priority,created_at,updated_at) VALUES(?,?,?,?,?,'unanswered',0.8,'withdrawal','normal',?,?)",
                [(str(uuid.uuid4()), 333, "private", n, f"Question {n}", created, created) for n in range(501)],
            )
            conn.commit()
        report = self.store.daily_report(date(2026, 11, 11), "UTC")
        self.assertEqual(report["total_questions"], 501)
        self.assertEqual(len(report["unanswered"]), 501)
        self.assertEqual(len(report_documents(report)), 2)

    def test_admin_note_and_resolution_are_persisted_and_validated(self):
        question_id = self.record(user_id=11, message_id=1, text="I have a withdrawal problem")
        self.assertTrue(self.store.add_note(question_id, 99, "Reviewed the app error."))
        self.assertTrue(self.store.resolve(question_id, 99, "Explained the verified next step."))
        self.assertFalse(self.store.update_status(question_id, "made_up_status"))
        self.assertEqual(self.store.list_questions()[0]["answer_status"], "resolved")

    def test_ai_classifier_failure_falls_back_cleanly(self):
        client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=AsyncMock(side_effect=RuntimeError("offline")))))
        self.assertIsNone(asyncio.run(classify_with_ai("withdrawal pending?", client)))


if __name__ == "__main__":
    unittest.main()
