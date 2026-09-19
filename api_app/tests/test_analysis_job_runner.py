from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase

from api_app.analysis_job_runner import (
    claim_next_queued_job,
    process_next_queued_job,
)
from api_app.models import (
    APISpecification,
    AnalysisJob,
    Comparison,
    Project,
)


class AnalysisJobRunnerTests(TestCase):

    def setUp(self):
        User = get_user_model()

        self.user = User.objects.create_user(
            username="runner-test-user",
            password="testpass123",
        )

        self.project = Project.objects.create(
            owner=self.user,
            name="Runner Test Project",
        )

        self.base_spec = APISpecification.objects.create(
            project=self.project,
            name="Base",
            version="1.0.0",
            content={
                "openapi": "3.0.3",
                "info": {
                    "title": "Test API",
                    "version": "1.0.0",
                },
                "paths": {},
            },
            raw_text="",
            content_hash="a" * 64,
        )

        self.head_spec = APISpecification.objects.create(
            project=self.project,
            name="Head",
            version="1.1.0",
            content={
                "openapi": "3.0.3",
                "info": {
                    "title": "Test API",
                    "version": "1.1.0",
                },
                "paths": {},
            },
            raw_text="",
            content_hash="b" * 64,
        )

        self.comparison = Comparison.objects.create(
            project=self.project,
            old_specification=self.base_spec,
            new_specification=self.head_spec,
            status="queued",
        )

        self.job = AnalysisJob.objects.create(
            project=self.project,
            comparison=self.comparison,
            status="queued",
            progress=0,
        )

    def test_claim_next_queued_job(self):
        job = claim_next_queued_job(
            project_id=self.project.pk
        )

        self.assertIsNotNone(job)
        self.assertEqual(job.pk, self.job.pk)
        self.assertEqual(job.status, "running")
        self.assertEqual(job.progress, 5)
        self.assertIsNotNone(job.started_at)

    def test_claim_returns_none_when_no_queued_job(self):
        self.job.status = "running"
        self.job.save(
            update_fields=[
                "status",
                "updated_at",
            ]
        )

        job = claim_next_queued_job(
            project_id=self.project.pk
        )

        self.assertIsNone(job)

    @patch(
        "api_app.analysis_job_runner.run_analysis_job"
    )
    def test_process_next_queued_job_executes_pipeline(
        self,
        mock_run_analysis_job,
    ):
        mock_run_analysis_job.side_effect = (
            lambda job: job
        )

        result = process_next_queued_job(
            project_id=self.project.pk
        )

        self.assertIsNotNone(result)

        mock_run_analysis_job.assert_called_once()

        called_job = (
            mock_run_analysis_job.call_args.args[0]
        )

        self.assertEqual(
            called_job.pk,
            self.job.pk,
        )

    @patch(
        "api_app.analysis_job_runner.run_analysis_job"
    )
    def test_process_returns_none_when_queue_empty(
        self,
        mock_run_analysis_job,
    ):
        self.job.status = "completed"

        self.job.save(
            update_fields=[
                "status",
                "updated_at",
            ]
        )

        result = process_next_queued_job(
            project_id=self.project.pk
        )

        self.assertIsNone(result)

        mock_run_analysis_job.assert_not_called()

    def test_project_filter_prevents_other_project_job(self):
        User = get_user_model()

        another_user = User.objects.create_user(
            username="other-runner-user",
            password="testpass123",
        )

        another_project = Project.objects.create(
            owner=another_user,
            name="Other Runner Project",
        )

        job = claim_next_queued_job(
            project_id=another_project.pk
        )

        self.assertIsNone(job)