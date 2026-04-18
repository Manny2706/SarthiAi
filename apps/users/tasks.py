from __future__ import annotations

from datetime import datetime
from datetime import timedelta
import re

from celery import shared_task
from django.conf import settings
from django.db import models
from django.utils import timezone

from apps.users.vapi import place_vapi_call


PATIENT_CALL_RETRY_STATUSES = {'no_answer', 'busy', 'failed', 'missed'}
ANSWERED_STATUSES = {'answered', 'completed', 'taken'}


def _to_aware(date_value, time_value):
    naive_dt = datetime.combine(date_value, time_value)
    if timezone.is_naive(naive_dt):
        return timezone.make_aware(naive_dt, timezone.get_current_timezone())
    return naive_dt


def calculate_next_run(schedule, from_dt=None):
    if not schedule.is_active:
        return None

    if schedule.frequency == 'as_needed':
        return None

    now = from_dt or timezone.now()
    if schedule.end_date and now.date() > schedule.end_date:
        return None

    start_date = schedule.start_date

    if schedule.frequency == 'daily':
        candidate_date = max(start_date, now.date())
        candidate = _to_aware(candidate_date, schedule.time)
        if candidate <= now:
            candidate = _to_aware(candidate_date + timedelta(days=1), schedule.time)

    elif schedule.frequency == 'weekly':
        if now.date() <= start_date:
            candidate_date = start_date
        else:
            days_since_start = (now.date() - start_date).days
            remainder = days_since_start % 7
            days_to_add = 0 if remainder == 0 else 7 - remainder
            candidate_date = now.date() + timedelta(days=days_to_add)

        candidate = _to_aware(candidate_date, schedule.time)
        if candidate <= now:
            candidate = _to_aware(candidate_date + timedelta(days=7), schedule.time)
    else:
        return None

    if schedule.end_date and candidate.date() > schedule.end_date:
        return None

    return candidate


def _build_assistant_overrides(schedule):
    medicine = schedule.medicine
    relative = medicine.relative
    return {
        'variableValues': {
            'patient_name': medicine.relative_name or relative.name,
            'relationship': medicine.relative_relationship or relative.relationship,
            'medicine_name': medicine.medicine_name,
            'dosage': medicine.dosage,
            'notes': medicine.notes,
            'id': f'med_{medicine.id}',
        }
    }


def _resolve_schedule_from_compact_id(compact_id):
    from apps.users.models import MedicineSchedule

    if compact_id is None:
        return None

    compact_id_str = str(compact_id).strip()
    if not compact_id_str:
        return None

    # Supports "123", "med_123", and "schedule_123" forms.
    numeric_match = re.search(r'(\d+)$', compact_id_str)
    if not numeric_match:
        return None

    numeric_id = int(numeric_match.group(1))
    if compact_id_str.lower().startswith('schedule_'):
        return MedicineSchedule.objects.filter(id=numeric_id).first()

    if compact_id_str.isdigit():
        return MedicineSchedule.objects.filter(id=numeric_id).first()

    if compact_id_str.lower().startswith('med_'):
        return (
            MedicineSchedule.objects.filter(
                medicine_id=numeric_id,
                is_active=True,
            )
            .order_by('-updated_at')
            .first()
        )

    return MedicineSchedule.objects.filter(id=numeric_id).first()


def _schedule_next_cycle(schedule):
    next_run = calculate_next_run(schedule)
    if not next_run:
        schedule.is_active = False
        schedule.next_run_at = None
        schedule.celery_task_id = ''
        schedule.patient_call_attempts = 0
        schedule.patient_call_status = 'completed'
        schedule.save(
            update_fields=[
                'is_active',
                'next_run_at',
                'celery_task_id',
                'patient_call_attempts',
                'patient_call_status',
                'updated_at',
            ]
        )
        return None

    async_result = trigger_medicine_call.apply_async(args=[schedule.id], eta=next_run)
    schedule.next_run_at = next_run
    schedule.celery_task_id = async_result.id
    schedule.patient_call_attempts = 0
    schedule.patient_call_status = 'pending'
    schedule.patient_last_call_id = ''
    schedule.escalation_call_id = ''
    schedule.escalation_sent_at = None
    schedule.save(
        update_fields=[
            'next_run_at',
            'celery_task_id',
            'patient_call_attempts',
            'patient_call_status',
            'patient_last_call_id',
            'escalation_call_id',
            'escalation_sent_at',
            'updated_at',
        ]
    )
    return next_run


@shared_task(bind=True, max_retries=3, default_retry_delay=60)
def trigger_medicine_call(self, schedule_id, attempt_number=1):
    from apps.users.models import MedicationLog
    from apps.users.models import MedicineSchedule

    try:
        schedule = MedicineSchedule.objects.select_related(
            'medicine',
            'medicine__relative',
            'medicine__relative__user',
            'medicine__relative__user__profile',
        ).get(id=schedule_id)
    except MedicineSchedule.DoesNotExist:
        return {'status': 'skipped', 'reason': 'schedule_not_found'}

    if not schedule.is_active:
        return {'status': 'skipped', 'reason': 'schedule_inactive'}

    now = timezone.now()
    if schedule.end_date and now.date() > schedule.end_date:
        schedule.is_active = False
        schedule.next_run_at = None
        schedule.celery_task_id = ''
        schedule.save(update_fields=['is_active', 'next_run_at', 'celery_task_id', 'updated_at'])
        return {'status': 'skipped', 'reason': 'schedule_ended'}

    attempt_number = int(attempt_number or 1)
    schedule.patient_call_attempts = attempt_number
    schedule.patient_call_status = 'ringing'
    schedule.last_called_at = now
    schedule.save(
        update_fields=[
            'patient_call_attempts',
            'patient_call_status',
            'last_called_at',
            'updated_at',
        ]
    )

    assistant_overrides = _build_assistant_overrides(schedule)
    metadata = {
        'schedule_id': schedule.id,
        'medicine_id': schedule.medicine_id,
        'relative_id': schedule.medicine.relative_id,
        'attempt_number': attempt_number,
        'call_kind': 'patient',
    }

    log = MedicationLog.objects.create(
        schedule=schedule,
        attempt_number=attempt_number,
        call_kind='patient',
        status='queued',
        event_type='call.created',
        raw_payload=metadata,
    )

    try:
        vapi_response = place_vapi_call(
            customer_number=schedule.medicine.relative_phone_number,
            assistant_overrides=assistant_overrides,
            metadata={**metadata, 'log_id': log.id},
            webhook_url=settings.VAPI_WEBHOOK_URL or None,
        )
    except Exception as exc:
        raise self.retry(exc=exc)

    call_id = str(vapi_response.get('id') or vapi_response.get('callId') or vapi_response.get('call_id') or '')
    schedule.patient_last_call_id = call_id
    schedule.celery_task_id = ''
    schedule.next_run_at = None
    schedule.save(update_fields=['patient_last_call_id', 'celery_task_id', 'next_run_at', 'updated_at'])

    if call_id:
        MedicationLog.objects.filter(id=log.id).update(call_id=call_id, raw_payload=vapi_response)

    return {
        'status': 'called',
        'schedule_id': schedule.id,
        'call_id': call_id,
        'vapi_response': vapi_response,
    }


@shared_task(bind=True, max_retries=3, default_retry_delay=60)
def trigger_escalation_call(self, schedule_id):
    from apps.users.models import MedicationLog
    from apps.users.models import MedicineSchedule

    try:
        schedule = MedicineSchedule.objects.select_related(
            'medicine',
            'medicine__relative',
            'medicine__relative__user',
            'medicine__relative__user__profile',
        ).get(id=schedule_id)
    except MedicineSchedule.DoesNotExist:
        return {'status': 'skipped', 'reason': 'schedule_not_found'}

    user_profile = getattr(schedule.medicine.relative.user, 'profile', None)
    if not user_profile or not user_profile.phone_number:
        return {'status': 'skipped', 'reason': 'user_phone_missing'}

    assistant_overrides = {
        'variableValues': {
            'patient_name': schedule.medicine.relative_name or schedule.medicine.relative.name,
            'relationship': schedule.medicine.relative_relationship or schedule.medicine.relative.relationship,
            'medicine_name': schedule.medicine.medicine_name,
            'dosage': schedule.medicine.dosage,
            'notes': schedule.medicine.notes,
            'alertType': 'escalation',
            'id': f'med_{schedule.medicine_id}',
        }
    }
    metadata = {
        'schedule_id': schedule.id,
        'medicine_id': schedule.medicine_id,
        'relative_id': schedule.medicine.relative_id,
        'call_kind': 'escalation',
    }

    log = MedicationLog.objects.create(
        schedule=schedule,
        attempt_number=schedule.patient_call_attempts,
        call_kind='escalation',
        status='queued',
        event_type='call.created',
        raw_payload=metadata,
    )

    try:
        vapi_response = place_vapi_call(
            customer_number=user_profile.phone_number,
            assistant_overrides=assistant_overrides,
            metadata={**metadata, 'log_id': log.id},
            webhook_url=settings.VAPI_WEBHOOK_URL or None,
        )
    except Exception as exc:
        raise self.retry(exc=exc)

    call_id = str(vapi_response.get('id') or vapi_response.get('callId') or vapi_response.get('call_id') or '')
    schedule.escalation_call_id = call_id
    schedule.escalation_sent_at = timezone.now()
    schedule.patient_call_status = 'escalated'
    schedule.save(update_fields=['escalation_call_id', 'escalation_sent_at', 'patient_call_status', 'updated_at'])

    if call_id:
        MedicationLog.objects.filter(id=log.id).update(call_id=call_id, raw_payload=vapi_response)

    _schedule_next_cycle(schedule)

    return {
        'status': 'escalation_called',
        'schedule_id': schedule.id,
        'call_id': call_id,
        'vapi_response': vapi_response,
    }


@shared_task(bind=True)
def process_vapi_webhook(self, payload):
    from apps.users.models import MedicationLog
    from apps.users.models import MedicineSchedule

    # Compact webhook contract example: {"id": "med_123", "taken": true}
    if isinstance(payload, dict) and 'id' in payload and 'taken' in payload and 'metadata' not in payload:
        schedule = _resolve_schedule_from_compact_id(payload.get('id'))
        if not schedule:
            return {'status': 'skipped', 'reason': 'schedule_not_found'}

        taken = bool(payload.get('taken'))
        current_attempt = max(schedule.patient_call_attempts, 1)
        MedicationLog.objects.create(
            schedule=schedule,
            attempt_number=current_attempt,
            call_kind='patient',
            status='taken' if taken else 'missed',
            event_type='medication.confirmation',
            call_id=schedule.patient_last_call_id,
            raw_payload=payload,
        )

        if taken:
            schedule.patient_call_status = 'answered'
            schedule.save(update_fields=['patient_call_status', 'updated_at'])
            _schedule_next_cycle(schedule)
            return {'status': 'handled', 'action': 'taken_confirmed'}

        next_attempt = current_attempt + 1
        schedule.patient_call_status = 'retrying' if next_attempt <= 3 else 'escalate'
        schedule.save(update_fields=['patient_call_status', 'updated_at'])

        if next_attempt <= 3:
            trigger_medicine_call.apply_async(
                args=[schedule.id, next_attempt],
                countdown=int(settings.VAPI_RETRY_DELAY_MINUTES) * 60,
            )
            return {'status': 'handled', 'action': 'retry_scheduled', 'attempts': next_attempt}

        trigger_escalation_call.delay(schedule.id)
        return {'status': 'handled', 'action': 'escalation_scheduled', 'attempts': next_attempt}

    metadata = payload.get('metadata') or payload.get('call', {}).get('metadata') or {}
    call_id = str(
        payload.get('callId')
        or payload.get('call_id')
        or payload.get('id')
        or payload.get('call', {}).get('id')
        or ''
    )
    event_type = str(payload.get('type') or payload.get('event') or payload.get('status') or payload.get('call', {}).get('status') or '').lower()
    call_kind = str(metadata.get('call_kind') or metadata.get('callKind') or 'patient').lower()
    schedule_id = metadata.get('schedule_id') or payload.get('schedule_id')

    if not schedule_id and call_id:
        schedule = MedicineSchedule.objects.filter(
            models.Q(patient_last_call_id=call_id) | models.Q(escalation_call_id=call_id)
        ).first()
    else:
        schedule = MedicineSchedule.objects.filter(id=schedule_id).first()

    if not schedule:
        return {'status': 'skipped', 'reason': 'schedule_not_found'}

    normalized_status = 'queued'
    if event_type in ANSWERED_STATUSES:
        normalized_status = 'answered'
    elif event_type in PATIENT_CALL_RETRY_STATUSES:
        normalized_status = 'no_answer'
    elif event_type in {'ringing', 'in_progress', 'started'}:
        normalized_status = 'ringing'
    elif event_type in {'failed', 'error'}:
        normalized_status = 'failed'
    elif event_type in {'completed', 'ended'}:
        normalized_status = 'completed'

    MedicationLog.objects.create(
        schedule=schedule,
        attempt_number=int(metadata.get('attempt_number') or schedule.patient_call_attempts or 1),
        call_kind=call_kind,
        status=normalized_status,
        event_type=event_type,
        call_id=call_id,
        raw_payload=payload,
    )

    if call_kind == 'patient':
        if normalized_status == 'answered':
            schedule.patient_call_status = 'answered'
            schedule.patient_call_attempts = max(schedule.patient_call_attempts, int(metadata.get('attempt_number') or 1))
            schedule.save(update_fields=['patient_call_status', 'patient_call_attempts', 'updated_at'])
            _schedule_next_cycle(schedule)
            return {'status': 'handled', 'action': 'answered'}

        if normalized_status in {'no_answer', 'busy', 'failed'}:
            attempts = max(schedule.patient_call_attempts, int(metadata.get('attempt_number') or 1))
            next_attempt = attempts + 1
            schedule.patient_call_attempts = attempts
            schedule.patient_call_status = 'retrying' if next_attempt <= 3 else 'escalate'
            schedule.save(update_fields=['patient_call_attempts', 'patient_call_status', 'updated_at'])

            if next_attempt <= 3:
                trigger_medicine_call.apply_async(
                    args=[schedule.id, next_attempt],
                    countdown=int(settings.VAPI_RETRY_DELAY_MINUTES) * 60,
                )
                return {'status': 'handled', 'action': 'retry_scheduled', 'attempts': next_attempt}

            trigger_escalation_call.delay(schedule.id)
            return {'status': 'handled', 'action': 'escalation_scheduled', 'attempts': next_attempt}

    if call_kind == 'escalation' and normalized_status == 'answered':
        schedule.patient_call_status = 'escalated'
        schedule.save(update_fields=['patient_call_status', 'updated_at'])
        return {'status': 'handled', 'action': 'escalation_answered'}

    return {'status': 'handled', 'action': 'logged'}
