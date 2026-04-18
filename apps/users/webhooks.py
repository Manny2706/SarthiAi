from __future__ import annotations

from rest_framework import status
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.views import APIView

from django.conf import settings

from apps.users.tasks import process_vapi_webhook


class VapiWebhookView(APIView):
    permission_classes = [AllowAny]
    authentication_classes = []

    def post(self, request):
        secret = settings.VAPI_WEBHOOK_SECRET
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
                return Response({'detail': 'Invalid webhook secret'}, status=status.HTTP_403_FORBIDDEN)

        payload = request.data if isinstance(request.data, dict) else {'payload': request.data}
        process_vapi_webhook.delay(payload)
        return Response({'received': True}, status=status.HTTP_202_ACCEPTED)
