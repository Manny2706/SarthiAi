from django.contrib import admin

from apps.users.models import UserProfile


@admin.register(UserProfile)
class UserProfileAdmin(admin.ModelAdmin):
    list_display = ("user", "name", "phone_number")
    search_fields = ("user__email", "name", "phone_number")
