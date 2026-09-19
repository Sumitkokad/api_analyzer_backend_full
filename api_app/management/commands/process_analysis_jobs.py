from __future__ import annotations

import time

from django.core.management.base import BaseCommand

from api_app.analysis_job_runner import process_next_queued_job


class Command(BaseCommand):
    help = "Process queued API analysis jobs in the background."

    def add_arguments(self, parser):
        parser.add_argument(
            "--once",
            action="store_true",
            help="Process at most one queued job and exit.",
        )
        parser.add_argument(
            "--poll-seconds",
            type=float,
            default=2.0,
            help="Seconds to wait when no queued job is available.",
        )
        parser.add_argument(
            "--project-id",
            type=int,
            default=None,
            help="Process queued jobs only for this project.",
        )

    def handle(self, *args, **options):
        once = options["once"]
        poll_seconds = max(options["poll_seconds"], 0.1)
        project_id = options["project_id"]

        self.stdout.write(
            self.style.SUCCESS(
                "Analysis job worker started."
            )
        )

        try:
            while True:
                job = process_next_queued_job(project_id=project_id)

                if job is not None:
                    self.stdout.write(
                        self.style.SUCCESS(
                            f"Processed analysis job {job.id} "
                            f"with status '{job.status}'."
                        )
                    )

                    if once:
                        break

                    continue

                if once:
                    self.stdout.write(
                        "No queued analysis jobs found."
                    )
                    break

                time.sleep(poll_seconds)

        except KeyboardInterrupt:
            self.stdout.write(
                self.style.WARNING(
                    "Analysis job worker stopped."
                )
            )