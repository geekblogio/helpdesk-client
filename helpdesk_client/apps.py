from django.apps import AppConfig
from django.utils.translation import gettext_lazy as _


class HelpdeskClientConfig(AppConfig):
    name = "helpdesk_client"
    label = "helpdesk_client"
    verbose_name = _("Support")

    def ready(self):
        from . import checks, hooks  # noqa: F401  (checks registers the system checks)

        hooks.connect()
