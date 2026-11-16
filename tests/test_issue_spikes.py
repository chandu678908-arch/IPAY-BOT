import concurrent.futures
import shutil
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
import uuid

from issue_spikes import IssueSpikeMonitor, classify_spike_category


class IssueSpikeTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(__file__).resolve().parent / f".spike-test-{uuid.uuid4().hex}"
        self.root.mkdir()
        self.db_path = str(self.root / "spikes.sqlite3")
        self.monitor = IssueSpikeMonitor(self.db_path)
        self.now = datetime(2026, 11, 16, 12, 0, tzinfo=timezone.utc)

    def tearDown(self):
        shutil.rmtree(self.root)

    def record(self, message_id, user_id, *, category="ORDER_LOCKED", now=None, **kwargs):
        return self.monitor.record_and_check(
            category=category,
            chat_id=-100,
            message_id=message_id,
            user_id=user_id,
            threshold=kwargs.get("threshold", 3),
            window_minutes=kwargs.get("window_minutes", 15),
            cooldown_seconds=kwargs.get("cooldown_seconds", 900),
            now=now or self.now,
        )

    def test_category_classifier_requires_transaction_status_evidence(self):
        self.assertEqual(
            classify_spike_category("My order is locked after processing"),
            "ORDER_LOCKED",
        )
        self.assertEqual(
            classify_spike_category("Withdrawal pending since yesterday"),
            "WITHDRAWAL_PENDING",
        )
        self.assertEqual(classify_spike_category("The wallet dashboard shows balance"), None)
        self.assertEqual(classify_spike_category("Order and withdrawal menu"), None)

    def test_alert_threshold_counts_reports_and_distinct_users(self):
        self.assertIsNone(self.record(1, 10))
        self.assertIsNone(self.record(2, 10))
        alert = self.record(3, 11)
        self.assertEqual(alert, {
            "category": "ORDER_LOCKED",
            "report_count": 3,
            "unique_users": 2,
            "window_minutes": 15,
        })

    def test_one_alert_per_incident_and_reset_below_threshold(self):
        for message_id in (1, 2, 3):
            self.record(message_id, message_id)
        self.assertIsNone(self.record(4, 4))
        later = self.now + timedelta(minutes=16)
        self.assertIsNone(self.record(5, 5, now=later))
        self.assertIsNone(self.record(6, 6, now=later))
        alert = self.record(7, 7, now=later)
        self.assertIsNotNone(alert)

    def test_same_incident_does_not_repeat_after_alert_cooldown(self):
        alerts = [self.record(index, index) for index in range(1, 7)]
        self.assertEqual(sum(alert is not None for alert in alerts), 1)
        self.assertIsNone(
            self.record(7, 7, now=self.now + timedelta(minutes=20))
        )

    def test_concurrent_reports_claim_exactly_one_alert(self):
        def submit(index):
            return self.record(index, index, threshold=10)

        with concurrent.futures.ThreadPoolExecutor(max_workers=12) as executor:
            results = list(executor.map(submit, range(1, 31)))
        alerts = [result for result in results if result is not None]
        self.assertEqual(len(alerts), 1)
        self.assertGreaterEqual(alerts[0]["report_count"], 10)

    def test_repeated_telegram_update_is_idempotent(self):
        first = self.record(9, 91, threshold=2)
        repeated = self.record(9, 91, threshold=2)
        self.assertIsNone(first)
        self.assertIsNone(repeated)
        self.assertIsNotNone(self.record(10, 92, threshold=2))
        self.assertIsNone(self.record(10, 92, threshold=2))


if __name__ == "__main__":
    unittest.main()
