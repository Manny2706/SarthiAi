from __future__ import annotations

import requests
from django.conf import settings


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
        raise VapiConfigurationError('VAPI calls are disabled by configuration.')

    if not settings.VAPI_API_KEY:
        raise VapiConfigurationError('VAPI_API_KEY is missing in environment configuration.')
    if not settings.VAPI_ASSISTANT_ID:
        raise VapiConfigurationError('VAPI_ASSISTANT_ID is missing in environment configuration.')

    has_twilio_phone = bool(settings.VAPI_TWILIO_PHONE_NUMBER and settings.VAPI_TWILIO_ACCOUNT_SID)
    has_phone_number_id = bool(settings.VAPI_PHONE_NUMBER_ID)
    if not has_twilio_phone and not has_phone_number_id:
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

    response = requests.post(
        settings.VAPI_API_URL,
        json=payload,
        headers=headers,
        timeout=20,
    )
    response.raise_for_status()
    return response.json()
