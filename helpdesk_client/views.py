"""Support pages for logged-in users: list, new request, detail, reply, close, attachment download.

Every view talks to the helpdesk API on behalf of the logged-in user
(``HELPDESK_CONTACT_RESOLVER``). Failures of the API become error pages
(``helpdesk_client/error.html``) instead of 500s: an unknown ticket is a 404,
a blocked contact a 403, a rate limit a 429, everything else a 503.
"""

import logging

from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin
from django.forms import Form
from django.http import Http404, HttpResponse, StreamingHttpResponse
from django.shortcuts import redirect
from django.template.response import TemplateResponse
from django.utils.translation import gettext as _
from django.views.generic import FormView, TemplateView, View

from . import conf
from .client import HelpdeskApiError, HelpdeskClient, HelpdeskError, HelpdeskUnavailable
from .contact import get_contact, get_metadata
from .forms import ReplyForm, TicketForm

logger = logging.getLogger(__name__)

# Headers of the API's download answer that the proxy sets again for the browser.
DOWNLOAD_SECURITY_HEADERS = {
    "X-Content-Type-Options": "nosniff",
    "Content-Security-Policy": "default-src 'none'; sandbox",
    "Cache-Control": "private, no-store",
}

ATTACHMENT_ERROR_CODES = ("invalid_attachment", "attachment_too_large", "too_many_attachments", "request_too_large")


class HelpdeskMixin(LoginRequiredMixin):
    """Login required; provides ``self.api`` and ``self.contact`` and turns API failures into pages."""

    error_template_name = "helpdesk_client/error.html"

    def dispatch(self, request, *args, **kwargs):
        if not request.user.is_authenticated:
            return self.handle_no_permission()
        self.api = HelpdeskClient()
        self.contact = get_contact(request)
        response = None
        try:
            response = super().dispatch(request, *args, **kwargs)
        except HelpdeskApiError as exc:
            response = self.api_error_response(exc)
        except HelpdeskUnavailable:
            response = self.unavailable_response()
        finally:
            # A streamed download still needs the connection; it is closed with the response.
            if not getattr(response, "streaming", False):
                self.api.session.close()
        return response

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["helpdesk_base_template"] = conf.get("HELPDESK_BASE_TEMPLATE")
        return context

    # --- Error pages ----------------------------------------------------------

    def api_error_response(self, exc: HelpdeskApiError) -> HttpResponse:
        if exc.code == "not_found":
            raise Http404
        if exc.code == "contact_blocked":
            return self.error_response(
                403,
                _("Support is not available for your account"),
                _("Your account cannot use the support pages at the moment. Please contact us another way."),
            )
        if exc.code == "rate_limited":
            return self.error_response(
                429,
                _("Too many requests"),
                _("You have sent many requests in a short time. Please wait a moment and try again."),
            )
        logger.error("Unexpected answer of the helpdesk API: %s %s", exc.status, exc.code)
        return self.unavailable_response()

    def unavailable_response(self) -> HttpResponse:
        return self.error_response(
            503,
            _("Support is temporarily unavailable"),
            _("We cannot reach our support system right now. Please try again in a few minutes."),
        )

    def error_response(self, status: int, title: str, message: str) -> HttpResponse:
        context = {
            "helpdesk_base_template": conf.get("HELPDESK_BASE_TEMPLATE"),
            "error_title": title,
            "error_message": message,
        }
        return TemplateResponse(self.request, self.error_template_name, context, status=status)


def apply_api_errors(form: Form, exc: HelpdeskApiError, fields: dict[str, str]) -> bool:
    """Show a 422/413 answer of the API on ``form``; ``fields`` maps payload keys to form fields.

    Returns False when the error is not about the input, so the caller re-raises it.
    """
    if exc.code in ATTACHMENT_ERROR_CODES:
        form.add_error("attachments", _attachment_error_message(form, exc))
        return True
    if exc.code == "validation_error":
        targets = set()
        for detail in exc.details:
            loc = [str(part) for part in detail.get("loc", [])]
            key = loc[2] if loc[:2] == ["body", "payload"] and len(loc) > 2 else None
            target = fields.get(key)
            targets.add(target if target in form.fields else None)
        for target in sorted(targets, key=lambda t: t or ""):
            form.add_error(target, _("This value was not accepted.") if target else _invalid_input_message())
        if not targets:
            form.add_error(None, _invalid_input_message())
        return True
    if exc.code == "invalid_input":
        form.add_error(None, _invalid_input_message())
        return True
    return False


def _invalid_input_message() -> str:
    return _("Your entries could not be saved. Please check them and try again.")


def _attachment_error_message(form: Form, exc: HelpdeskApiError) -> str:
    name = _file_name_from_details(form, exc)
    if exc.code == "too_many_attachments":
        return _("Too many files attached.")
    if exc.code == "request_too_large":
        return _("The attachments are too large together.")
    if exc.code == "attachment_too_large":
        return _("%(name)s is too large.") % {"name": name} if name else _("A file is too large.")
    if name:
        return _("%(name)s was not accepted: its content does not match an allowed file type.") % {"name": name}
    return _("A file was not accepted: its content does not match an allowed file type.")


def _file_name_from_details(form: Form, exc: HelpdeskApiError) -> str:
    files = form.cleaned_data.get("attachments") or []
    for detail in exc.details:
        loc = [str(part) for part in detail.get("loc", [])]
        if loc[:2] == ["file", "attachments"] and len(loc) > 2 and loc[2].isdigit() and int(loc[2]) < len(files):
            return files[int(loc[2])].name
    return ""


class TicketListView(HelpdeskMixin, TemplateView):
    """The page "My requests": the user's tickets, most recently updated first."""

    template_name = "helpdesk_client/ticket_list.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        try:
            page = max(1, int(self.request.GET.get("page", 1)))
        except ValueError:
            page = 1
        data = self.api.tickets(self.contact, page=page, page_size=conf.get("HELPDESK_PAGE_SIZE"))
        context.update(
            tickets=data["items"],
            count=data["count"],
            page=data["page"],
            pages=data["pages"],
            previous_page=page - 1 if page > 1 else None,
            next_page=page + 1 if page < data["pages"] else None,
        )
        return context


class TicketCreateView(HelpdeskMixin, FormView):
    """New request with category and attachments."""

    template_name = "helpdesk_client/ticket_form.html"
    form_class = TicketForm

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs["categories"] = self.api.categories(language=self.contact.language or None)
        return kwargs

    def form_valid(self, form):
        data = form.cleaned_data
        try:
            ticket = self.api.create_ticket(
                self.contact,
                subject=data["subject"],
                body=data["body"],
                category_id=data.get("category"),
                metadata=get_metadata(self.request),
                files=data["attachments"],
            )
        except HelpdeskApiError as exc:
            if apply_api_errors(form, exc, {"subject": "subject", "body": "body", "category_id": "category"}):
                return self.form_invalid(form)
            raise
        messages.success(
            self.request,
            _("Your request %(number)s has been sent.") % {"number": ticket["display_number"]},
            fail_silently=True,
        )
        return redirect("helpdesk_client:ticket_detail", number=ticket["number"])


class TicketDetailView(HelpdeskMixin, TemplateView):
    """The ticket with its history and the reply form. Opening it marks it read."""

    template_name = "helpdesk_client/ticket_detail.html"

    def get(self, request, *args, number: int, **kwargs):
        ticket = self.api.ticket(self.contact, number)
        if ticket.get("unread"):
            try:
                self.api.mark_read(self.contact, number)
            except HelpdeskError:
                # Not worth an error page: the ticket stays unread and is marked next time.
                logger.warning("Could not mark ticket %s as read", number)
        return self.render_detail(ticket)

    def render_detail(self, ticket: dict, *, reply_form: ReplyForm | None = None, **extra) -> HttpResponse:
        if reply_form is None and ticket.get("can_reply"):
            reply_form = ReplyForm()
        context = self.get_context_data(ticket=ticket, reply_form=reply_form, **extra)
        return self.render_to_response(context)


class TicketReplyView(TicketDetailView):
    """Reply of the user (POST). Invalid input shows the detail page again with the errors."""

    http_method_names = ["post"]

    def post(self, request, *args, number: int, **kwargs):
        form = ReplyForm(request.POST, request.FILES)
        if form.is_valid():
            try:
                self.api.reply(
                    self.contact, number, body=form.cleaned_data["body"], files=form.cleaned_data["attachments"]
                )
            except HelpdeskApiError as exc:
                if exc.code == "reopen_window_expired":
                    return self.render_detail(self.api.ticket(self.contact, number), reopen_window_expired=True)
                if not apply_api_errors(form, exc, {"body": "body"}):
                    raise
            else:
                messages.success(request, _("Your reply has been sent."), fail_silently=True)
                return redirect("helpdesk_client:ticket_detail", number=number)
        return self.render_detail(self.api.ticket(self.contact, number), reply_form=form)


class TicketCloseView(HelpdeskMixin, View):
    """Close the ticket on behalf of the user (POST)."""

    http_method_names = ["post"]

    def post(self, request, *args, number: int, **kwargs):
        try:
            self.api.close_ticket(self.contact, number)
        except HelpdeskApiError as exc:
            if exc.code != "invalid_status_change":
                raise
        else:
            messages.success(request, _("The request has been closed."), fail_silently=True)
        return redirect("helpdesk_client:ticket_detail", number=number)


class AttachmentDownloadView(HelpdeskMixin, View):
    """Stream an attachment from the API to the user, with the API's safe download headers."""

    http_method_names = ["get"]

    def get(self, request, *args, number: int, attachment_id: int, **kwargs):
        download = self.api.download_attachment(self.contact, number, attachment_id)
        response = StreamingHttpResponse(download, content_type=download.content_type)
        response["Content-Disposition"] = download.content_disposition
        if download.content_length:
            response["Content-Length"] = download.content_length
        for name, value in DOWNLOAD_SECURITY_HEADERS.items():
            response[name] = value
        return response
