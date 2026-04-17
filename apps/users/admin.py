from django.contrib import admin
from django.contrib.auth.admin import UserAdmin

from apps.users.models import User


@admin.register(User)
class CustomUserAdmin(UserAdmin):
	model = User
	list_display = ("email", "name", "phone_number", "is_staff", "is_active")
	list_filter = ("is_staff", "is_active", "is_superuser")
	ordering = ("email",)
	search_fields = ("email", "name", "phone_number")

	fieldsets = (
		(None, {"fields": ("email", "password")}),
		("Personal info", {"fields": ("name", "phone_number")}),
		(
			"Permissions",
			{"fields": ("is_active", "is_staff", "is_superuser", "groups", "user_permissions")},
		),
		("Important dates", {"fields": ("last_login",)}),
	)

	add_fieldsets = (
		(
			None,
			{
				"classes": ("wide",),
				"fields": ("email", "name", "phone_number", "password1", "password2", "is_staff", "is_active"),
			},
		),
	)
