from django.contrib.auth.base_user import BaseUserManager
from django.contrib.auth.models import AbstractBaseUser, PermissionsMixin
from django.db import models


class CustomUserManager(BaseUserManager):
	def create_user(self, email, name, phone_number, password=None, **extra_fields):
		if not email:
			raise ValueError("Email is required")
		if not phone_number:
			raise ValueError("Phone number is required")

		email = self.normalize_email(email)
		user = self.model(
			email=email,
			name=name,
			phone_number=phone_number,
			**extra_fields,
		)
		user.set_password(password)
		user.save(using=self._db)
		return user

	def create_superuser(self, email, name, phone_number, password=None, **extra_fields):
		extra_fields.setdefault("is_staff", True)
		extra_fields.setdefault("is_superuser", True)
		extra_fields.setdefault("is_active", True)

		if extra_fields.get("is_staff") is not True:
			raise ValueError("Superuser must have is_staff=True")
		if extra_fields.get("is_superuser") is not True:
			raise ValueError("Superuser must have is_superuser=True")

		return self.create_user(email, name, phone_number, password, **extra_fields)


class User(AbstractBaseUser, PermissionsMixin):
	email = models.EmailField(primary_key=True, unique=True)
	name = models.CharField(max_length=150)
	phone_number = models.CharField(max_length=20, unique=True)
	is_active = models.BooleanField(default=True)
	is_staff = models.BooleanField(default=False)

	objects = CustomUserManager()

	USERNAME_FIELD = "email"
	REQUIRED_FIELDS = ["name", "phone_number"]

	def __str__(self):
		return self.email
