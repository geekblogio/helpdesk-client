from django.db import models
from django.utils.translation import gettext_lazy as _


class PendingAnonymization(models.Model):
    """A deleted user whose helpdesk contact still has to be anonymized.

    The delete hook writes the row in the same transaction as the deletion and
    removes it once the API confirmed. A row that stays has failed at least once;
    ``manage.py helpdesk_anonymize`` retries them. The row holds the external id
    only as long as needed to delete the data in the helpdesk.
    """

    external_id = models.CharField(_("external user id"), max_length=255, unique=True)
    created_at = models.DateTimeField(_("created"), auto_now_add=True)
    attempts = models.PositiveIntegerField(_("attempts"), default=0)
    last_attempt_at = models.DateTimeField(_("last attempt"), null=True, blank=True)
    last_error = models.CharField(_("last error"), max_length=100, blank=True)

    class Meta:
        ordering = ["created_at", "pk"]
        verbose_name = _("pending anonymization")
        verbose_name_plural = _("pending anonymizations")

    def __str__(self):
        return f"#{self.pk}"
