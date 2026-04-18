from django.contrib.auth import authenticate
from django.contrib.auth.models import User
from django.db import transaction
from rest_framework import serializers
from rest_framework_simplejwt.tokens import RefreshToken

from apps.users.models import MedicineSchedule
from apps.users.models import MedicationLog
from apps.users.models import Relative
from apps.users.models import RelativeMedicine
from apps.users.models import UserProfile
from apps.users.tasks import calculate_next_run
from apps.users.tasks import trigger_medicine_call


class SignupSerializer(serializers.Serializer):
    email = serializers.EmailField()
    name = serializers.CharField(max_length=150)
    phone_number = serializers.CharField(max_length=20)
    password = serializers.CharField(write_only=True, min_length=8)

    def validate_email(self, value):
        if User.objects.filter(email__iexact=value).exists():
            raise serializers.ValidationError("Email already exists")
        return value

    def validate_phone_number(self, value):
        if UserProfile.objects.filter(phone_number=value).exists():
            raise serializers.ValidationError("Phone number already exists")
        return value

    def create(self, validated_data):
        email = validated_data["email"]
        name = validated_data["name"]
        phone_number = validated_data["phone_number"]
        password = validated_data["password"]

        user = User.objects.create_user(
            username=email,
            email=email,
            password=password,
        )
        UserProfile.objects.create(user=user, name=name, phone_number=phone_number)
        return user


class LoginSerializer(serializers.Serializer):
    email = serializers.EmailField()
    password = serializers.CharField(write_only=True)

    def validate(self, attrs):
        email = attrs.get("email")
        password = attrs.get("password")

        user = authenticate(username=email, password=password)
        if not user:
            raise serializers.ValidationError("Invalid email or password")
        if not user.is_active:
            raise serializers.ValidationError("User is inactive")

        attrs["user"] = user
        return attrs


class UserSerializer(serializers.ModelSerializer):
    name = serializers.SerializerMethodField()
    phone_number = serializers.SerializerMethodField()

    class Meta:
        model = User
        fields = ("id", "email", "name", "phone_number")

    def get_name(self, obj):
        profile = getattr(obj, "profile", None)
        return getattr(profile, "name", "")

    def get_phone_number(self, obj):
        profile = getattr(obj, "profile", None)
        return getattr(profile, "phone_number", "")


def build_token_response(user):
    refresh = RefreshToken.for_user(user)
    return {
        "access": str(refresh.access_token),
        "refresh": str(refresh),
    }


class MedicineScheduleSerializer(serializers.ModelSerializer):
    def validate(self, attrs):
        start_date = attrs.get('start_date')
        end_date = attrs.get('end_date')
        if end_date and start_date and end_date < start_date:
            raise serializers.ValidationError('end_date must be greater than or equal to start_date.')
        return attrs

    class Meta:
        model = MedicineSchedule
        fields = (
            'id',
            'time',
            'frequency',
            'start_date',
            'end_date',
            'is_active',
            'patient_call_attempts',
            'patient_call_status',
            'patient_last_call_id',
            'escalation_call_id',
            'escalation_sent_at',
            'next_run_at',
            'last_called_at',
            'created_at',
            'updated_at',
        )
        read_only_fields = ('next_run_at', 'last_called_at', 'created_at', 'updated_at')

    def create(self, validated_data):
        schedule = super().create(validated_data)

        def enqueue_schedule_task():
            next_run = calculate_next_run(schedule)
            if not next_run:
                return

            async_result = trigger_medicine_call.apply_async(args=[schedule.id], eta=next_run)
            MedicineSchedule.objects.filter(id=schedule.id).update(
                next_run_at=next_run,
                celery_task_id=async_result.id,
            )

        transaction.on_commit(enqueue_schedule_task)
        return schedule

    def update(self, instance, validated_data):
        old_task_id = instance.celery_task_id
        instance = super().update(instance, validated_data)

        def reschedule_task():
            from celery import current_app

            if old_task_id:
                current_app.control.revoke(old_task_id, terminate=False)

            next_run = calculate_next_run(instance)
            if not next_run:
                MedicineSchedule.objects.filter(id=instance.id).update(
                    next_run_at=None,
                    celery_task_id='',
                )
                return

            async_result = trigger_medicine_call.apply_async(args=[instance.id], eta=next_run)
            MedicineSchedule.objects.filter(id=instance.id).update(
                next_run_at=next_run,
                celery_task_id=async_result.id,
            )

        transaction.on_commit(reschedule_task)
        return instance

class RelativeMedicineSerializer(serializers.ModelSerializer):
    schedules = MedicineScheduleSerializer(many=True, read_only=True)

    def create(self, validated_data):
        relative = validated_data['relative']
        validated_data['relative_name'] = relative.name
        validated_data['relative_relationship'] = relative.relationship
        validated_data['relative_phone_number'] = relative.user.profile.phone_number
        return super().create(validated_data)
    
    class Meta:
        model = RelativeMedicine
        fields = ('id', 'medicine_name', 'dosage', 'notes', 'schedules', 'created_at')


class MedicationLogSerializer(serializers.ModelSerializer):
    class Meta:
        model = MedicationLog
        fields = (
            'id',
            'schedule',
            'log_date',
            'attempt_number',
            'call_kind',
            'status',
            'event_type',
            'call_id',
            'raw_payload',
            'created_at',
            'updated_at',
        )
        read_only_fields = (
            'id',
            'schedule',
            'log_date',
            'attempt_number',
            'call_kind',
            'status',
            'event_type',
            'call_id',
            'raw_payload',
            'created_at',
            'updated_at',
        )

class RelativeSerializer(serializers.ModelSerializer):
    medicines = RelativeMedicineSerializer(many=True, read_only=True)
    
    class Meta:
        model = Relative
        fields = ('id', 'name', 'relationship', 'age', 'medicines', 'created_at')

class RelativeDetailSerializer(serializers.ModelSerializer):
    """For creating/updating relatives"""
    class Meta:
        model = Relative
        fields = ('id', 'name', 'relationship', 'age')