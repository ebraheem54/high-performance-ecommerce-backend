from __future__ import annotations

from django.conf import settings
from django.core.management.base import BaseCommand


class Command(BaseCommand):
    help = "Truncate all project .log files under the configured logs directory."

    def handle(self, *args, **options) -> None:
        log_dir = settings.BASE_DIR / "logs"
        if not log_dir.exists():
            self.stdout.write(self.style.WARNING(f"Log directory does not exist: {log_dir}"))
            return

        cleared = 0
        for log_file in log_dir.rglob("*.log"):
            log_file.write_text("")
            cleared += 1

        self.stdout.write(self.style.SUCCESS(f"Cleared {cleared} log files from {log_dir}"))
