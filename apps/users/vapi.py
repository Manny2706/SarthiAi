from __future__ import annotations

import requests
from django.conf import settings

from config.myloggerconfig import get_master_logger


logger = get_master_logger().getChild(__name__)


class VapiConfigurationError(Exception):
    """Raised when required Vapi configuration is missing."""


def place_vapi_call(
    *,
    customer_number: str,
    assistant_overrides: dict | None = None,
    metadata: dict | None = None,
    webhook_url: str | None = None,
) -> dict:
    if not settings.VAPI_CALLS_ENABLED:
        logger.warning('VAPI call blocked because VAPI_CALLS_ENABLED is false')
        raise VapiConfigurationError('VAPI calls are disabled by configuration.')

    if not settings.VAPI_API_KEY:
        logger.error('VAPI_API_KEY missing while trying to place call')
        raise VapiConfigurationError('VAPI_API_KEY is missing in environment configuration.')
    if not settings.VAPI_ASSISTANT_ID:
        logger.error('VAPI_ASSISTANT_ID missing while trying to place call')
        raise VapiConfigurationError('VAPI_ASSISTANT_ID is missing in environment configuration.')

    has_twilio_phone = bool(settings.VAPI_TWILIO_PHONE_NUMBER and settings.VAPI_TWILIO_ACCOUNT_SID)
    has_phone_number_id = bool(settings.VAPI_PHONE_NUMBER_ID)
    if not has_twilio_phone and not has_phone_number_id:
        logger.error('VAPI phone configuration missing for outbound call')
        raise VapiConfigurationError(
            'Either VAPI_PHONE_NUMBER_ID or both VAPI_TWILIO_PHONE_NUMBER and VAPI_TWILIO_ACCOUNT_SID are required.'
        )

    headers = {
        'Authorization': f'Bearer {settings.VAPI_API_KEY}',
        'Content-Type': 'application/json',
    }
    payload = {
        'assistantId': settings.VAPI_ASSISTANT_ID,
        'customer': {
            'number': customer_number,
        },
    }

    if has_twilio_phone:
        payload['phoneNumber'] = {
            'twilioPhoneNumber': settings.VAPI_TWILIO_PHONE_NUMBER,
            'twilioAccountSid': settings.VAPI_TWILIO_ACCOUNT_SID,
        }
    else:
        payload['phoneNumberId'] = settings.VAPI_PHONE_NUMBER_ID
    if metadata:
        payload['metadata'] = metadata
    if webhook_url:
        payload['webhookUrl'] = webhook_url
    if assistant_overrides:
        payload['assistantOverrides'] = assistant_overrides

    logger.info('Placing VAPI call to customer_number=%s', customer_number)

    response = requests.post(
        settings.VAPI_API_URL,
        json=payload,
        headers=headers,
        timeout=20,
    )
    response.raise_for_status()
    logger.info('VAPI call created successfully for customer_number=%s', customer_number)
    return response.json()
