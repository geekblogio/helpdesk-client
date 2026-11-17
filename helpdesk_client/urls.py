"""URLs of the support pages.

Mount them in the website's urls.py with a prefix of your choice, e.g.
``path("support/", include("helpdesk_client.urls"))``.
"""

from django.urls import path

from . import views
from .webhooks import WebhookView

app_name = "helpdesk_client"

urlpatterns = [
    path("", views.TicketListView.as_view(), name="ticket_list"),
    path("new/", views.TicketCreateView.as_view(), name="ticket_create"),
    path("<int:number>/", views.TicketDetailView.as_view(), name="ticket_detail"),
    path("<int:number>/reply/", views.TicketReplyView.as_view(), name="ticket_reply"),
    path("<int:number>/close/", views.TicketCloseView.as_view(), name="ticket_close"),
    path(
        "<int:number>/attachments/<int:attachment_id>/",
        views.AttachmentDownloadView.as_view(),
        name="attachment_download",
    ),
    # No login: the helpdesk calls it, the signature authenticates it (404 without HELPDESK_WEBHOOK_SECRET).
    path("webhook/", WebhookView.as_view(), name="webhook"),
]
