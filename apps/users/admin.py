from django.contrib import admin

from apps.users.models import MedicationLog
from apps.users.models import UserProfile


@admin.register(UserProfile)
class UserProfileAdmin(admin.ModelAdmin):
    list_display = ("user", "name", "phone_number")
    search_fields = ("user__email", "name", "phone_number")


@admin.register(MedicationLog)
class MedicationLogAdmin(admin.ModelAdmin):
    list_display = ("schedule", "call_kind", "status", "attempt_number", "call_id", "created_at")
    search_fields = (
        "schedule__medicine__medicine_name",
        "schedule__medicine__relative_name",
        "call_id",
        "event_type",
    )
    list_filter = ("call_kind", "status", "log_date")
