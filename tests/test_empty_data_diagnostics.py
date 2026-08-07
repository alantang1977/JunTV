import os
import unittest
from time import time
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from desktop_ui.pages.dashboard import DashboardPage
from main import UpdateSource
from utils.i18n import t
from utils.reporting import Reporter


class EmptyDataDiagnosticsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def _source(self, **metrics):
        events = []
        reporter = Reporter(
            event_callback=events.append,
            enable_console=False,
            enable_runtime_file=False,
        )
        source = UpdateSource(reporter=reporter)
        source.source_metrics = {
            "template_channels": 10,
            "prepared_items": 0,
            "subscription_urls": 0,
            "subscription_channels": 0,
            "subscription_items": 0,
            "aggregated_items": 0,
            "output_items": 0,
            **metrics,
        }
        source.channel_data = {}
        return source, reporter, events

    def test_no_configured_sources_has_actionable_warning(self):
        source, reporter, events = self._source()
        try:
            source._diagnose_aggregated_data()
        finally:
            reporter.close()

        self.assertEqual(source.run_outcome["reason"], "no_source_configured")
        event = next(item for item in events if item["event"] == "run.empty_data")
        self.assertEqual(event["level"], "WARNING")
        self.assertEqual(event["data"]["template_channels"], 10)
        self.assertIn("config/subscribe.txt", event["message"])
        self.assertIn("/iptv-api/config/subscribe.txt", event["message"])

    def test_configured_but_empty_subscriptions_are_distinguished(self):
        source, reporter, _ = self._source(subscription_urls=2)
        try:
            source._diagnose_aggregated_data()
        finally:
            reporter.close()

        self.assertEqual(source.run_outcome["reason"], "sources_unavailable")

    def test_unmatched_subscription_results_are_distinguished(self):
        source, reporter, _ = self._source(
            subscription_urls=1,
            subscription_channels=3,
            subscription_items=8,
        )
        try:
            source._diagnose_aggregated_data()
        finally:
            reporter.close()

        self.assertEqual(source.run_outcome["reason"], "no_matching_channels")

    def test_ui_completion_metadata_marks_empty_run(self):
        source, reporter, _ = self._source()
        updates = []
        source.run_ui = True
        source.update_progress = lambda *args, **kwargs: updates.append((args, kwargs))
        source._diagnose_aggregated_data()
        try:
            source._notify_ui_finished(time())
        finally:
            reporter.close()

        args, kwargs = updates[-1]
        self.assertTrue(kwargs["finished"])
        self.assertEqual(kwargs["url"]["status"], "empty")
        self.assertEqual(kwargs["url"]["reason"], "no_source_configured")

    def test_dashboard_shows_empty_result_guidance_and_source_action(self):
        with (
            patch.object(DashboardPage, "refresh_metrics"),
            patch.object(DashboardPage, "refresh_schedule"),
        ):
            page = DashboardPage()
        destinations = []
        page.destination_requested.connect(destinations.append)
        page._runtime_rows = []

        page.set_progress(
            "finished",
            100,
            finished=True,
            metadata={"status": "empty", "reason": "no_source_configured"},
        )
        page.configure_sources_button.click()

        self.assertEqual(page.progress_title.text(), t("desktop.update_empty_gui"))
        self.assertEqual(page.empty_title.text(), t("desktop.channel_results_empty_after_run"))
        self.assertEqual(destinations, ["sources"])
        page.deleteLater()

    def test_dashboard_channel_sorting_survives_runtime_refresh(self):
        with (
            patch.object(DashboardPage, "refresh_metrics"),
            patch.object(DashboardPage, "refresh_schedule"),
        ):
            page = DashboardPage()
        self.addCleanup(page.deleteLater)
        page._runtime_rows = [
            {"name": "Zulu", "category": "News"},
            {"name": "Alpha", "category": "News"},
        ]
        page._apply_runtime_rows()
        page.show()
        self.app.processEvents()

        header = page.channel_table.horizontalHeader()
        click_position = header.viewport().rect().center()
        click_position.setX(header.sectionViewportPosition(0) + 20)
        QTest.mouseClick(
            header.viewport(),
            Qt.MouseButton.LeftButton,
            pos=click_position,
        )
        self.app.processEvents()
        self.assertEqual([row["name"] for row in page.channel_model.rows], ["Alpha", "Zulu"])

        page._runtime_rows = list(reversed(page._runtime_rows))
        page._apply_runtime_rows()
        self.assertEqual([row["name"] for row in page.channel_model.rows], ["Alpha", "Zulu"])

    def test_dashboard_overview_table_only_searches_channel_names(self):
        with (
            patch.object(DashboardPage, "refresh_metrics"),
            patch.object(DashboardPage, "refresh_schedule"),
        ):
            page = DashboardPage()
        self.addCleanup(page.deleteLater)

        self.assertEqual(
            [column[0] for column in page.channel_model.columns],
            [
                "name",
                "display_status",
                "valid_results",
                "selected_results",
                "best_speed",
                "max_resolution",
                "total_results",
                "updated_at",
            ],
        )
        self.assertEqual(page.channels_title.text(), t("desktop.channel_result_status"))

        page._runtime_rows = [
            {"name": "News One", "category": "Sports"},
            {"name": "Sports One", "category": "News"},
        ]
        page.channel_search.setText("sports")
        self.assertEqual([row["name"] for row in page.channel_model.rows], ["Sports One"])


if __name__ == "__main__":
    unittest.main()
