from __future__ import annotations

from datetime import datetime
from datetime import timedelta
import re

from celery import shared_task
from celery import current_app
from django.conf import settings
from django.db import models
from django.utils import timezone
from twilio.base.exceptions import TwilioException
from twilio.rest import Client

from apps.users.vapi import VapiConfigurationError
from apps.users.vapi import place_vapi_call
from config.myloggerconfig import get_master_logger


logger = get_master_logger().getChild(__name__)


PATIENT_CALL_RETRY_STATUSES = {'no_answer', 'busy', 'failed', 'missed'}
ANSWERED_STATUSES = {'answered', 'completed', 'taken'}
WHATSAPP_POSITIVE_REPLIES = {'yes', 'y', 'taken', 'done'}
WHATSAPP_NEGATIVE_REPLIES = {'no', 'n', 'not', 'missed'}


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


def _get_twilio_client():
    account_sid = settings.TWILIO_ACCOUNT_SID
    auth_token = settings.TWILIO_AUTH_TOKEN
    if not account_sid or not auth_token:
        return None
    return Client(account_sid, auth_token)


def _build_whatsapp_message(schedule):
    medicine = schedule.medicine
    relative_name = medicine.relative_name or medicine.relative.name
    dosage = f" {medicine.dosage}" if medicine.dosage else ''
    return f"Hi {relative_name}, take {medicine.medicine_name}{dosage}. Reply YES or NO."


def _send_whatsapp_reminder(schedule):
    from_number = settings.TWILIO_WHATSAPP_FROM
    to_number = schedule.medicine.relative.phone_number
    if not from_number or not to_number:
        logger.warning('WhatsApp reminder skipped for schedule_id=%s due to missing phone number', schedule.id)
        return {'sent': False, 'reason': 'missing_phone_number'}

    if not str(from_number).lower().startswith('whatsapp:'):
        from_number = f"whatsapp:{from_number}"
    if not str(to_number).lower().startswith('whatsapp:'):
        to_number = f"whatsapp:{to_number}"

    client = _get_twilio_client()
    if not client:
        logger.warning('WhatsApp reminder skipped for schedule_id=%s because Twilio is not configured', schedule.id)
        return {'sent': False, 'reason': 'twilio_not_configured'}

    message = client.messages.create(
        from_=from_number,
        body=_build_whatsapp_message(schedule),
        to=to_number,
    )
    logger.info('WhatsApp reminder sent for schedule_id=%s message_sid=%s', schedule.id, message.sid)
    return {'sent': True, 'message_sid': message.sid}


def _extract_whatsapp_reply(payload):
    if not isinstance(payload, dict):
        return None

    body = str(payload.get('Body') or payload.get('body') or '').strip().lower()
    from_number = str(payload.get('From') or payload.get('from') or '').strip()
    if not body or not from_number:
        return None

    normalized = re.sub(r'\s+', ' ', body)
    token = normalized.split(' ')[0]
    if token in WHATSAPP_POSITIVE_REPLIES:
        return {'taken': True, 'from_number': from_number, 'raw_reply': body}
    if token in WHATSAPP_NEGATIVE_REPLIES:
        return {'taken': False, 'from_number': from_number, 'raw_reply': body}
    return None


def _cancel_pending_schedule_task(schedule):
    if not schedule.celery_task_id:
        return

    current_app.control.revoke(schedule.celery_task_id, terminate=False)
    schedule.celery_task_id = ''
    schedule.next_run_at = None
    schedule.save(update_fields=['celery_task_id', 'next_run_at', 'updated_at'])


def _handle_twilio_whatsapp_reply(payload):
    from apps.users.models import MedicationLog
    from apps.users.models import MedicineSchedule

    reply = _extract_whatsapp_reply(payload)
    if not reply:
        return None

    logger.debug('Processing Twilio WhatsApp reply payload')

    from_number = reply['from_number']
    normalized_number = ''.join(ch for ch in from_number if ch.isdigit())

    schedule = None
    candidates = (
        MedicineSchedule.objects.select_related('medicine', 'medicine__relative')
        .filter(is_active=True)
        .order_by('next_run_at', '-updated_at')
    )
    for candidate in candidates:
        candidate_number = ''.join(ch for ch in (candidate.medicine.relative.phone_number or '') if ch.isdigit())
        if candidate_number and candidate_number == normalized_number:
            schedule = candidate
            break
    if not schedule:
        logger.warning('Twilio WhatsApp reply skipped: schedule not found for incoming number')
        return {'status': 'skipped', 'reason': 'schedule_not_found_for_phone'}

    current_attempt = max(schedule.patient_call_attempts, 1)
    status_value = 'taken' if reply['taken'] else 'missed'
    MedicationLog.objects.create(
        schedule=schedule,
        attempt_number=current_attempt,
        call_kind='patient',
        status=status_value,
        event_type='whatsapp.reply',
        call_id='',
        raw_payload=payload,
    )

    if reply['taken']:
        logger.info('Medication marked as taken from WhatsApp reply for schedule_id=%s', schedule.id)
        _cancel_pending_schedule_task(schedule)
        schedule.patient_call_status = 'taken'
        schedule.save(update_fields=['patient_call_status', 'updated_at'])
        _schedule_next_cycle(schedule)
        return {'status': 'handled', 'action': 'taken_confirmed_whatsapp'}

    schedule.patient_call_status = 'missed'
    schedule.save(update_fields=['patient_call_status', 'updated_at'])

    logger.info('Medication marked missed from WhatsApp reply; triggering call for schedule_id=%s', schedule.id)

    _cancel_pending_schedule_task(schedule)
    async_result = trigger_medicine_call.apply_async(
        args=[schedule.id, current_attempt, False],
    )
    schedule.celery_task_id = async_result.id
    schedule.save(update_fields=['celery_task_id', 'updated_at'])
    return {'status': 'handled', 'action': 'call_started_after_no_reply'}


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
def trigger_medicine_call(self, schedule_id, attempt_number=1, send_whatsapp_first=True):
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
        logger.warning('Medicine call skipped: schedule_id=%s not found', schedule_id)
        return {'status': 'skipped', 'reason': 'schedule_not_found'}

    if not schedule.is_active:
        logger.info('Medicine call skipped: schedule_id=%s is inactive', schedule.id)
        return {'status': 'skipped', 'reason': 'schedule_inactive'}

    now = timezone.now()
    if schedule.end_date and now.date() > schedule.end_date:
        schedule.is_active = False
        schedule.next_run_at = None
        schedule.celery_task_id = ''
        schedule.save(update_fields=['is_active', 'next_run_at', 'celery_task_id', 'updated_at'])
        logger.info('Medicine call skipped: schedule_id=%s ended by date', schedule.id)
        return {'status': 'skipped', 'reason': 'schedule_ended'}

    attempt_number = int(attempt_number or 1)
    schedule.patient_call_attempts = attempt_number
    schedule.patient_call_status = 'whatsapp_sent' if send_whatsapp_first else 'ringing'
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

    if send_whatsapp_first:
        try:
            whatsapp_result = _send_whatsapp_reminder(schedule)
        except TwilioException:
            logger.exception('Twilio error while sending WhatsApp reminder for schedule_id=%s', schedule.id)
            whatsapp_result = {'sent': False, 'reason': 'twilio_error'}

        MedicationLog.objects.create(
            schedule=schedule,
            attempt_number=attempt_number,
            call_kind='patient',
            status='queued',
            event_type='whatsapp.sent' if whatsapp_result.get('sent') else 'whatsapp.skipped',
            call_id=str(whatsapp_result.get('message_sid') or ''),
            raw_payload=whatsapp_result,
        )

        if whatsapp_result.get('sent'):
            wait_minutes = max(0, int(settings.TWILIO_WHATSAPP_REPLY_WAIT_MINUTES))
            async_result = trigger_medicine_call.apply_async(
                args=[schedule.id, attempt_number, False],
                countdown=wait_minutes * 60,
            )
            schedule.celery_task_id = async_result.id
            schedule.next_run_at = timezone.now() + timedelta(minutes=wait_minutes)
            schedule.save(update_fields=['celery_task_id', 'next_run_at', 'updated_at'])
            logger.info('Scheduled post-WhatsApp call check for schedule_id=%s', schedule.id)
            return {
                'status': 'reminder_sent',
                'schedule_id': schedule.id,
                'message_sid': whatsapp_result.get('message_sid'),
            }

    if schedule.patient_call_status != 'ringing':
        schedule.patient_call_status = 'ringing'
        schedule.save(update_fields=['patient_call_status', 'updated_at'])

    if not settings.VAPI_CALLS_ENABLED:
        schedule.patient_call_status = 'vapi_disabled'
        schedule.celery_task_id = ''
        schedule.next_run_at = None
        schedule.save(update_fields=['patient_call_status', 'celery_task_id', 'next_run_at', 'updated_at'])
        MedicationLog.objects.create(
            schedule=schedule,
            attempt_number=attempt_number,
            call_kind='patient',
            status='failed',
            event_type='call.skipped',
            call_id='',
            raw_payload={'reason': 'vapi_calls_disabled', 'metadata': metadata},
        )
        _schedule_next_cycle(schedule)
        logger.warning('VAPI disabled; skipped patient call for schedule_id=%s', schedule.id)
        return {'status': 'skipped', 'reason': 'vapi_calls_disabled'}

    try:
        logger.info('Placing patient VAPI call for schedule_id=%s attempt=%s', schedule.id, attempt_number)
        vapi_response = place_vapi_call(
            customer_number=schedule.medicine.relative.phone_number,
            assistant_overrides=assistant_overrides,
            metadata={**metadata, 'log_id': log.id},
            webhook_url=settings.VAPI_WEBHOOK_URL or None,
        )
    except VapiConfigurationError as exc:
        schedule.patient_call_status = 'failed'
        schedule.celery_task_id = ''
        schedule.next_run_at = None
        schedule.save(update_fields=['patient_call_status', 'celery_task_id', 'next_run_at', 'updated_at'])
        MedicationLog.objects.create(
            schedule=schedule,
            attempt_number=attempt_number,
            call_kind='patient',
            status='failed',
            event_type='call.config_error',
            call_id='',
            raw_payload={'reason': str(exc), 'metadata': metadata},
        )
        logger.error('VAPI configuration error for schedule_id=%s: %s', schedule.id, str(exc))
        return {'status': 'skipped', 'reason': 'vapi_configuration_error'}
    except Exception as exc:
        logger.exception('Unexpected VAPI error for schedule_id=%s; retrying task', schedule.id)
        raise self.retry(exc=exc)

    call_id = str(vapi_response.get('id') or vapi_response.get('callId') or vapi_response.get('call_id') or '')
    schedule.patient_last_call_id = call_id
    schedule.celery_task_id = ''
    schedule.next_run_at = None
    schedule.save(update_fields=['patient_last_call_id', 'celery_task_id', 'next_run_at', 'updated_at'])

    if call_id:
        MedicationLog.objects.filter(id=log.id).update(call_id=call_id, raw_payload=vapi_response)

    logger.info('Patient VAPI call created for schedule_id=%s call_id=%s', schedule.id, call_id)

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
        logger.warning('Escalation skipped: schedule_id=%s not found', schedule_id)
        return {'status': 'skipped', 'reason': 'schedule_not_found'}

    user_profile = getattr(schedule.medicine.relative.user, 'profile', None)
    if not user_profile or not user_profile.phone_number:
        logger.warning('Escalation skipped for schedule_id=%s: user phone missing', schedule.id)
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

    if not settings.VAPI_CALLS_ENABLED:
        schedule.patient_call_status = 'escalation_skipped'
        schedule.escalation_sent_at = timezone.now()
        schedule.save(update_fields=['patient_call_status', 'escalation_sent_at', 'updated_at'])
        MedicationLog.objects.create(
            schedule=schedule,
            attempt_number=schedule.patient_call_attempts,
            call_kind='escalation',
            status='failed',
            event_type='escalation.skipped',
            call_id='',
            raw_payload={'reason': 'vapi_calls_disabled', 'metadata': metadata},
        )
        _schedule_next_cycle(schedule)
        logger.warning('Escalation skipped for schedule_id=%s because VAPI is disabled', schedule.id)
        return {'status': 'skipped', 'reason': 'vapi_calls_disabled'}

    try:
        logger.info('Placing escalation VAPI call for schedule_id=%s', schedule.id)
        vapi_response = place_vapi_call(
            customer_number=user_profile.phone_number,
            assistant_overrides=assistant_overrides,
            metadata={**metadata, 'log_id': log.id},
            webhook_url=settings.VAPI_WEBHOOK_URL or None,
        )
    except VapiConfigurationError as exc:
        schedule.patient_call_status = 'escalation_failed'
        schedule.escalation_sent_at = timezone.now()
        schedule.save(update_fields=['patient_call_status', 'escalation_sent_at', 'updated_at'])
        MedicationLog.objects.create(
            schedule=schedule,
            attempt_number=schedule.patient_call_attempts,
            call_kind='escalation',
            status='failed',
            event_type='escalation.config_error',
            call_id='',
            raw_payload={'reason': str(exc), 'metadata': metadata},
        )
        _schedule_next_cycle(schedule)
        logger.error('Escalation VAPI configuration error for schedule_id=%s: %s', schedule.id, str(exc))
        return {'status': 'skipped', 'reason': 'vapi_configuration_error'}
    except Exception as exc:
        logger.exception('Unexpected escalation VAPI error for schedule_id=%s; retrying task', schedule.id)
        raise self.retry(exc=exc)

    call_id = str(vapi_response.get('id') or vapi_response.get('callId') or vapi_response.get('call_id') or '')
    schedule.escalation_call_id = call_id
    schedule.escalation_sent_at = timezone.now()
    schedule.patient_call_status = 'escalated'
    schedule.save(update_fields=['escalation_call_id', 'escalation_sent_at', 'patient_call_status', 'updated_at'])

    if call_id:
        MedicationLog.objects.filter(id=log.id).update(call_id=call_id, raw_payload=vapi_response)

    logger.info('Escalation VAPI call created for schedule_id=%s call_id=%s', schedule.id, call_id)

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

    twilio_result = _handle_twilio_whatsapp_reply(payload)
    if twilio_result is not None:
        logger.info('Webhook processed as Twilio WhatsApp event: %s', twilio_result.get('action') or twilio_result.get('reason'))
        return twilio_result

    # Compact webhook contract example: {"id": "med_123", "taken": true}
    if isinstance(payload, dict) and 'id' in payload and 'taken' in payload and 'metadata' not in payload:
        schedule = _resolve_schedule_from_compact_id(payload.get('id'))
        if not schedule:
            logger.warning('Compact webhook skipped: schedule not found for id=%s', payload.get('id'))
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
            logger.info('Compact webhook marked taken for schedule_id=%s', schedule.id)
            return {'status': 'handled', 'action': 'taken_confirmed'}

        next_attempt = current_attempt + 1
        schedule.patient_call_status = 'retrying' if next_attempt <= 3 else 'escalate'
        schedule.save(update_fields=['patient_call_status', 'updated_at'])

        if next_attempt <= 3:
            trigger_medicine_call.apply_async(
                args=[schedule.id, next_attempt],
                countdown=int(settings.VAPI_RETRY_DELAY_MINUTES) * 60,
            )
            logger.info('Compact webhook scheduled retry for schedule_id=%s attempt=%s', schedule.id, next_attempt)
            return {'status': 'handled', 'action': 'retry_scheduled', 'attempts': next_attempt}

        trigger_escalation_call.delay(schedule.id)
        logger.info('Compact webhook scheduled escalation for schedule_id=%s', schedule.id)
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
        logger.warning('VAPI webhook skipped: schedule not found for call_id=%s', call_id)
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
            logger.info('Patient call answered for schedule_id=%s', schedule.id)
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
                logger.info('Patient call retry scheduled for schedule_id=%s attempt=%s', schedule.id, next_attempt)
                return {'status': 'handled', 'action': 'retry_scheduled', 'attempts': next_attempt}

            trigger_escalation_call.delay(schedule.id)
            logger.info('Patient call escalation scheduled for schedule_id=%s', schedule.id)
            return {'status': 'handled', 'action': 'escalation_scheduled', 'attempts': next_attempt}

    if call_kind == 'escalation' and normalized_status == 'answered':
        schedule.patient_call_status = 'escalated'
        schedule.save(update_fields=['patient_call_status', 'updated_at'])
        logger.info('Escalation call answered for schedule_id=%s', schedule.id)
        return {'status': 'handled', 'action': 'escalation_answered'}

    return {'status': 'handled', 'action': 'logged'}
