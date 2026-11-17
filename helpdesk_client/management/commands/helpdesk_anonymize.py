"""Retry pending anonymizations of deleted users, or anonymize given ids.

    manage.py helpdesk_anonymize                       # retry all pending (e.g. hourly by cron)
    manage.py helpdesk_anonymize --external-id 42 43   # anonymize these contacts now

Exits with status 1 when a call failed, so cron or a monitoring job notices.
"""

from django.core.management.base import BaseCommand, CommandError

from ...client import HelpdeskClient
from ...hooks import anonymize
from ...models import PendingAnonymization


class Command(BaseCommand):
    help = "Anonymize helpdesk contacts of deleted users: retry pending ones or the given external ids."

    def add_arguments(self, parser):
        parser.add_argument(
            "--external-id",
            nargs="+",
            dest="external_ids",
            metavar="ID",
            help="Anonymize the contacts with these external user ids instead of the pending ones.",
        )

    def handle(self, *args, external_ids=None, **options):
        client = HelpdeskClient()
        if external_ids:
            jobs = [(external_id, None) for external_id in external_ids]
        else:
            jobs = list(PendingAnonymization.objects.values_list("external_id", "pk"))
        failed = 0
        try:
            for external_id, pending_pk in jobs:
                if not anonymize(external_id, pending_pk=pending_pk, client=client):
                    failed += 1
        finally:
            client.session.close()
        self.stdout.write(f"{len(jobs) - failed} anonymized, {failed} failed.")
        if failed:
            raise CommandError(f"{failed} anonymization(s) failed; they stay pending.", returncode=1)
