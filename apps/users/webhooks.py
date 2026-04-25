from __future__ import annotations

from rest_framework import status
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.throttling import ScopedRateThrottle
from rest_framework.views import APIView

from django.conf import settings

from apps.users.tasks import process_vapi_webhook
from config.myloggerconfig import get_master_logger


logger = get_master_logger().getChild(__name__)


def _normalize_payload(data):
    if isinstance(data, dict):
        return data

    try:
        items = data.lists()
    except AttributeError:
        return {'payload': data}

    normalized = {}
    for key, values in items:
        if len(values) == 1:
            normalized[key] = values[0]
        else:
            normalized[key] = values
    return normalized


class VapiWebhookView(APIView):
    permission_classes = [AllowAny]
    authentication_classes = []
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = 'vapi_webhook'

    def post(self, request):
        secret = settings.VAPI_WEBHOOK_SECRET
        logger.debug('Received Vapi webhook request')
        if secret:
            authorization = request.headers.get('Authorization', '')
            bearer_prefix = 'Bearer '
            provided_secret = ''
            if authorization.startswith(bearer_prefix):
                provided_secret = authorization[len(bearer_prefix):].strip()
            if not provided_secret:
                provided_secret = (
                    request.headers.get('X-Vapi-Webhook-Secret')
                    or request.headers.get('X-Webhook-Secret')
                    or request.query_params.get('secret')
                )
            if provided_secret != secret:
                logger.warning('Rejected Vapi webhook due to invalid secret')
                return Response({'detail': 'Invalid webhook secret'}, status=status.HTTP_403_FORBIDDEN)

        payload = _normalize_payload(request.data)
        logger.info('Accepted Vapi webhook and queued async processing')
        process_vapi_webhook.delay(payload)
        return Response({'received': True}, status=status.HTTP_202_ACCEPTED)
