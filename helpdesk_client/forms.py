"""Forms of the support pages.

The attachment rules mirror the helpdesk API (``docs/api.md#anhänge``) so users
see problems before the upload; the API checks the content again and stays
the authority (its errors are mapped back onto the fields in the views).
"""

from pathlib import PurePath

from django import forms
from django.template.defaultfilters import filesizeformat
from django.utils.translation import gettext_lazy as _
from django.utils.translation import ngettext

MAX_ATTACHMENTS = 5
MAX_ATTACHMENT_SIZE = 10 * 1024 * 1024
ALLOWED_EXTENSIONS = (".png", ".jpg", ".jpeg", ".gif", ".webp", ".pdf", ".txt", ".log")
MAX_SUBJECT_LENGTH = 200
MAX_BODY_LENGTH = 50_000


class MultipleFileInput(forms.ClearableFileInput):
    allow_multiple_selected = True


class AttachmentsField(forms.FileField):
    """Zero to ``MAX_ATTACHMENTS`` files from one ``<input type="file" multiple>``."""

    widget = MultipleFileInput

    def __init__(self, **kwargs):
        kwargs.setdefault("required", False)
        super().__init__(**kwargs)
        self.widget.attrs.setdefault("accept", ",".join(ALLOWED_EXTENSIONS))

    def clean(self, data, initial=None):
        files = [f for f in (data if isinstance(data, list | tuple) else [data]) if f]
        if not files:
            if self.required:
                raise forms.ValidationError(self.error_messages["required"], code="required")
            return []
        errors = []
        if len(files) > MAX_ATTACHMENTS:
            errors.append(
                forms.ValidationError(
                    ngettext(
                        "You can attach at most %(max)d file.",
                        "You can attach at most %(max)d files.",
                        MAX_ATTACHMENTS,
                    ),
                    code="too_many_attachments",
                    params={"max": MAX_ATTACHMENTS},
                )
            )
        for file in files:
            errors.extend(_file_errors(file))
        if errors:
            raise forms.ValidationError(errors)
        return files


def _file_errors(file) -> list[forms.ValidationError]:
    errors = []
    if PurePath(file.name).suffix.lower() not in ALLOWED_EXTENSIONS:
        errors.append(
            forms.ValidationError(
                _("%(name)s: this file type is not allowed. Allowed are %(types)s."),
                code="invalid_attachment",
                params={"name": file.name, "types": ", ".join(ALLOWED_EXTENSIONS)},
            )
        )
    elif not file.size:
        errors.append(
            forms.ValidationError(_("%(name)s is empty."), code="invalid_attachment", params={"name": file.name})
        )
    elif file.size > MAX_ATTACHMENT_SIZE:
        errors.append(
            forms.ValidationError(
                _("%(name)s is larger than %(max)s."),
                code="attachment_too_large",
                params={"name": file.name, "max": filesizeformat(MAX_ATTACHMENT_SIZE)},
            )
        )
    return errors


class TicketForm(forms.Form):
    subject = forms.CharField(label=_("Subject"), max_length=MAX_SUBJECT_LENGTH)
    category = forms.TypedChoiceField(label=_("Category"), coerce=int, empty_value=None)
    body = forms.CharField(
        label=_("Message"),
        max_length=MAX_BODY_LENGTH,
        widget=forms.Textarea(attrs={"rows": 10}),
        help_text=_("You can use Markdown."),
    )
    attachments = AttachmentsField(label=_("Attachments"))

    def __init__(self, *args, categories: list[dict] | None = None, **kwargs):
        super().__init__(*args, **kwargs)
        categories = categories or []
        if categories:
            # Categories are there to be chosen; without any the field is left out.
            self.fields["category"].choices = [("", "---------")] + [(c["id"], c["name"]) for c in categories]
        else:
            del self.fields["category"]
        self.fields["attachments"].help_text = _attachments_help()


class ReplyForm(forms.Form):
    body = forms.CharField(
        label=_("Your reply"),
        max_length=MAX_BODY_LENGTH,
        widget=forms.Textarea(attrs={"rows": 6}),
        help_text=_("You can use Markdown."),
    )
    attachments = AttachmentsField(label=_("Attachments"))

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["attachments"].help_text = _attachments_help()


def _attachments_help() -> str:
    return _("Up to %(count)d files, each up to %(size)s: images (PNG, JPEG, GIF, WebP), PDF, TXT, LOG.") % {
        "count": MAX_ATTACHMENTS,
        "size": filesizeformat(MAX_ATTACHMENT_SIZE),
    }
