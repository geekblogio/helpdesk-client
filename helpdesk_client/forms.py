"""Forms of the support pages.

The attachment rules come from the API's site config (``GET /config``, see
:mod:`helpdesk_client.siteconfig`) so users see problems before the upload; the
API checks the content again and stays the authority (its errors are mapped
back onto the fields in the views).
"""

import copy
from pathlib import PurePath

from django import forms
from django.template.defaultfilters import filesizeformat
from django.utils.functional import lazy
from django.utils.translation import gettext_lazy as _
from django.utils.translation import ngettext

from .siteconfig import DEFAULT_ATTACHMENT_RULES, AttachmentRules

MAX_SUBJECT_LENGTH = 200
MAX_BODY_LENGTH = 50_000


def _attachments_help(rules: AttachmentRules) -> str:
    types = ", ".join(extension.lstrip(".").upper() for extension in rules.allowed_extensions)
    return ngettext(
        "Up to %(count)d file of up to %(size)s. Allowed types: %(types)s.",
        "Up to %(count)d files, each up to %(size)s. Allowed types: %(types)s.",
        rules.max_files,
    ) % {"count": rules.max_files, "size": filesizeformat(rules.max_file_size), "types": types}


class MultipleFileInput(forms.ClearableFileInput):
    allow_multiple_selected = True


class AttachmentsField(forms.FileField):
    """Zero to ``rules.max_files`` files from one ``<input type="file" multiple>``."""

    widget = MultipleFileInput

    def __init__(self, *, rules: AttachmentRules = DEFAULT_ATTACHMENT_RULES, **kwargs):
        kwargs.setdefault("required", False)
        super().__init__(**kwargs)
        self.set_rules(rules)

    def set_rules(self, rules: AttachmentRules) -> None:
        self.rules = rules
        self.widget.attrs["accept"] = ",".join(rules.allowed_extensions)
        # Lazy: the field is also built at import time, before a language is active.
        self.help_text = lazy(_attachments_help, str)(rules)

    def clean(self, data, initial=None):
        files = [f for f in (data if isinstance(data, list | tuple) else [data]) if f]
        if not files:
            if self.required:
                raise forms.ValidationError(self.error_messages["required"], code="required")
            return []
        rules = self.rules
        errors = []
        if len(files) > rules.max_files:
            errors.append(
                forms.ValidationError(
                    ngettext(
                        "You can attach at most %(max)d file.",
                        "You can attach at most %(max)d files.",
                        rules.max_files,
                    ),
                    code="too_many_attachments",
                    params={"max": rules.max_files},
                )
            )
        for file in files:
            errors.extend(_file_errors(file, rules))
        if errors:
            raise forms.ValidationError(errors)
        return files


def _file_errors(file, rules: AttachmentRules) -> list[forms.ValidationError]:
    errors = []
    if PurePath(file.name).suffix.lower() not in rules.allowed_extensions:
        errors.append(
            forms.ValidationError(
                _("%(name)s: this file type is not allowed. Allowed are %(types)s."),
                code="invalid_attachment",
                params={"name": file.name, "types": ", ".join(rules.allowed_extensions)},
            )
        )
    elif not file.size:
        errors.append(
            forms.ValidationError(_("%(name)s is empty."), code="invalid_attachment", params={"name": file.name})
        )
    elif file.size > rules.max_file_size:
        errors.append(
            forms.ValidationError(
                _("%(name)s is larger than %(max)s."),
                code="attachment_too_large",
                params={"name": file.name, "max": filesizeformat(rules.max_file_size)},
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

    def __init__(
        self,
        *args,
        categories: list[dict] | None = None,
        attachment_rules: AttachmentRules = DEFAULT_ATTACHMENT_RULES,
        **kwargs,
    ):
        super().__init__(*args, **kwargs)
        self.set_categories(categories)
        self.fields["attachments"].set_rules(attachment_rules)

    def set_categories(self, categories: list[dict] | None) -> None:
        """Offer these categories; without any the field is left out (they are there to be chosen)."""
        if not categories:
            self.fields.pop("category", None)
            return
        if "category" not in self.fields:
            self.fields["category"] = copy.deepcopy(self.base_fields["category"])
        choices = [(c["id"], c["name"]) for c in categories]
        self.fields["category"].choices = [("", "---------"), *choices]


class ReplyForm(forms.Form):
    body = forms.CharField(
        label=_("Your reply"),
        max_length=MAX_BODY_LENGTH,
        widget=forms.Textarea(attrs={"rows": 6}),
        help_text=_("You can use Markdown."),
    )
    attachments = AttachmentsField(label=_("Attachments"))

    def __init__(self, *args, attachment_rules: AttachmentRules = DEFAULT_ATTACHMENT_RULES, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["attachments"].set_rules(attachment_rules)
