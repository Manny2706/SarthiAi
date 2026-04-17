from django.contrib.auth.models import User
from django.db import models


class UserProfile(models.Model):
    user = models.OneToOneField(User, on_delete=models.CASCADE, primary_key=True, related_name="profile")
    name = models.CharField(max_length=150)
    phone_number = models.CharField(max_length=20, unique=True)

    def __str__(self):
        return self.user.email
