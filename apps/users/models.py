from django.contrib.auth.models import User
from django.db import models


class UserProfile(models.Model):
    user = models.OneToOneField(User, on_delete=models.CASCADE, primary_key=True, related_name="profile")
    name = models.CharField(max_length=150)
    phone_number = models.CharField(max_length=20, unique=True)
    last_logout_at = models.DateTimeField(null=True, blank=True)

    def __str__(self):
        return self.user.email

class Relative(models.Model):
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='relatives')
    name = models.CharField(max_length=100)
    relationship = models.CharField(max_length=50)  # e.g., "Father", "Mother"
    age = models.IntegerField(null=True)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"{self.name} ({self.relationship})"

class RelativeMedicine(models.Model):
    relative = models.ForeignKey(Relative, on_delete=models.CASCADE, related_name='medicines')
    relative_name = models.CharField(max_length=100)  # Redundant but useful for quick access
    relative_relationship = models.CharField(max_length=50)  # Redundant but useful for quick access
    relative_phone_number = models.CharField(max_length=20)  # Redundant but useful for quick access
    medicine_name = models.CharField(max_length=100)
    dosage = models.CharField(max_length=50)  # e.g., "500mg"
    notes = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"{self.relative_name}: {self.medicine_name}"

class MedicineSchedule(models.Model):
    medicine = models.ForeignKey(RelativeMedicine, on_delete=models.CASCADE, related_name='schedules')
    time = models.TimeField()  # What time to take
    frequency = models.CharField(max_length=20, choices=[
        ('daily', 'Daily'),
        ('weekly', 'Weekly'),
        ('as_needed', 'As Needed')
    ])
    start_date = models.DateField()
    end_date = models.DateField(null=True, blank=True)
    is_active = models.BooleanField(default=True)
    patient_call_attempts = models.PositiveSmallIntegerField(default=0)
    patient_call_status = models.CharField(max_length=20, default='pending')
    patient_last_call_id = models.CharField(max_length=255, blank=True)
    escalation_call_id = models.CharField(max_length=255, blank=True)
    escalation_sent_at = models.DateTimeField(null=True, blank=True)
    next_run_at = models.DateTimeField(null=True, blank=True)
    last_called_at = models.DateTimeField(null=True, blank=True)
    celery_task_id = models.CharField(max_length=255, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"{self.medicine.medicine_name} ({self.frequency})"


class MedicationLog(models.Model):
    CALL_KIND_CHOICES = [
        ('patient', 'Patient'),
        ('escalation', 'Escalation'),
    ]

    STATUS_CHOICES = [
        ('queued', 'Queued'),
        ('ringing', 'Ringing'),
        ('answered', 'Answered'),
        ('no_answer', 'No Answer'),
        ('busy', 'Busy'),
        ('failed', 'Failed'),
        ('completed', 'Completed'),
        ('taken', 'Taken'),
        ('missed', 'Missed'),
        ('escalated', 'Escalated'),
    ]

    schedule = models.ForeignKey(MedicineSchedule, on_delete=models.CASCADE, related_name='logs')
    log_date = models.DateField(auto_now_add=True)
    attempt_number = models.PositiveSmallIntegerField(default=1)
    call_kind = models.CharField(max_length=20, choices=CALL_KIND_CHOICES)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='queued')
    event_type = models.CharField(max_length=100, blank=True)
    call_id = models.CharField(max_length=255, blank=True, db_index=True)
    raw_payload = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"{self.schedule_id} - {self.call_kind} - {self.status}"