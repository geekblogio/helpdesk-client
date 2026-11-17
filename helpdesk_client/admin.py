from django.contrib import admin

from .models import PendingAnonymization


@admin.register(PendingAnonymization)
class PendingAnonymizationAdmin(admin.ModelAdmin):
    """Read-only list of deleted users whose helpdesk contact is not anonymized yet."""

    list_display = ["__str__", "created_at", "attempts", "last_attempt_at", "last_error"]
    readonly_fields = ["external_id", "created_at", "attempts", "last_attempt_at", "last_error"]

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False
