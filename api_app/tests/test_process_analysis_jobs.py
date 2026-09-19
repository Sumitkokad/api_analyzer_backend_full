from unittest.mock import patch

from django.core.management import call_command
from django.test import SimpleTestCase


class ProcessAnalysisJobsCommandTests(SimpleTestCase):

    @patch(
        "api_app.management.commands.process_analysis_jobs.process_next_queued_job",
        return_value=None,
    )
    def test_once_exits_when_no_job(self, mock_process):
        call_command(
            "process_analysis_jobs",
            "--once",
        )

        mock_process.assert_called_once_with(project_id=None)

    @patch(
        "api_app.management.commands.process_analysis_jobs.process_next_queued_job"
    )
    def test_once_processes_one_job(self, mock_process):
        job = type(
            "FakeJob",
            (),
            {
                "id": 123,
                "status": "completed",
            },
        )()

        mock_process.return_value = job

        call_command(
            "process_analysis_jobs",
            "--once",
        )

        mock_process.assert_called_once_with(project_id=None)

    @patch(
        "api_app.management.commands.process_analysis_jobs.process_next_queued_job",
        return_value=None,
    )
    def test_project_id_is_forwarded(self, mock_process):
        call_command(
            "process_analysis_jobs",
            "--once",
            "--project-id",
            "5",
        )

        mock_process.assert_called_once_with(project_id=5)