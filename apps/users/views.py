import os

import requests
from django.shortcuts import get_object_or_404
from django.utils import timezone
from rest_framework import status
from rest_framework import viewsets
from rest_framework.permissions import AllowAny
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.throttling import ScopedRateThrottle
from rest_framework.views import APIView
from rest_framework_simplejwt.exceptions import TokenError
from rest_framework_simplejwt.token_blacklist.models import BlacklistedToken
from rest_framework_simplejwt.token_blacklist.models import OutstandingToken
from rest_framework_simplejwt.tokens import AccessToken
from rest_framework_simplejwt.tokens import RefreshToken
from apps.users.serializers import LoginSerializer
from apps.users.serializers import LogoutSerializer
from apps.users.serializers import SignupSerializer
from apps.users.serializers import UserSerializer
from apps.users.serializers import build_token_response
from .serializers import RelativeSerializer
from .serializers import RelativeDetailSerializer
from .serializers import RelativeMedicineSerializer
from .serializers import MedicineScheduleSerializer
from .serializers import DoctorAgentMessageSerializer
from .models import Relative
from .models import RelativeMedicine
from .models import MedicineSchedule
from .models import UserProfile

class SignupView(APIView):
	permission_classes = [AllowAny]

	def post(self, request):
		serializer = SignupSerializer(data=request.data)
		serializer.is_valid(raise_exception=True)
		user = serializer.save()
		tokens = build_token_response(user)

		return Response(
			{
				"message": "Signup successful",
				"user": UserSerializer(user).data,
				"tokens": tokens,
			},
			status=status.HTTP_201_CREATED,
		)


class LoginView(APIView):
	permission_classes = [AllowAny]

	def post(self, request):
		serializer = LoginSerializer(data=request.data)
		serializer.is_valid(raise_exception=True)
		user = serializer.validated_data["user"]
		tokens = build_token_response(user)

		return Response(
			{
				"message": "Login successful",
				"user": UserSerializer(user).data,
				"tokens": tokens,
			},
			status=status.HTTP_200_OK,
		)


class LogoutView(APIView):
	permission_classes = [AllowAny]
	authentication_classes = []

	def post(self, request):
		serializer = LogoutSerializer(data=request.data)
		serializer.is_valid(raise_exception=True)
		token_value = serializer.validated_data["token"]

		payload = None
		token_type = None
		token_object = None

		try:
			token_object = RefreshToken(token_value)
			payload = token_object.payload
			token_type = "refresh"
		except TokenError:
			try:
				token_object = AccessToken(token_value)
				payload = token_object.payload
				token_type = "access"
			except TokenError:
				return Response(
					{"detail": "Invalid token."},
					status=status.HTTP_400_BAD_REQUEST,
				)

		user_id = payload.get("user_id")
		if not user_id:
			return Response(
				{"detail": "Token is missing a user identifier."},
				status=status.HTTP_400_BAD_REQUEST,
			)

		now = timezone.now()
		UserProfile.objects.filter(user_id=user_id).update(last_logout_at=now)

		for outstanding_token in OutstandingToken.objects.filter(user_id=user_id):
			BlacklistedToken.objects.get_or_create(token=outstanding_token)

		if token_type == "refresh" and token_object is not None:
			try:
				token_object.blacklist()
			except AttributeError:
				pass

		return Response(
			{"message": "Logout successful"},
			status=status.HTTP_200_OK,
		)

class RelativeViewSet(viewsets.ModelViewSet):
    """
    API endpoint for managing relatives
    GET /relatives/ - List all relatives of logged-in user
    POST /relatives/ - Create new relative
    GET /relatives/{id}/ - Get relative details with medicines
    PATCH /relatives/{id}/ - Update relative
    DELETE /relatives/{id}/ - Delete relative
    """
    permission_classes = [IsAuthenticated]
    
    def get_queryset(self):
        return Relative.objects.filter(user=self.request.user)
    
    def get_serializer_class(self):
        if self.action == 'list' or self.action == 'retrieve':
            return RelativeSerializer
        return RelativeDetailSerializer
    
    def perform_create(self, serializer):
        serializer.save(user=self.request.user)

class RelativeMedicineViewSet(viewsets.ModelViewSet):
    """
    API endpoint for managing relative medicines
    POST /relatives/{relative_id}/medicines/ - Add medicine
    GET /relatives/{relative_id}/medicines/ - List medicines
    """
    permission_classes = [IsAuthenticated]
    serializer_class = RelativeMedicineSerializer
    
    def get_queryset(self):
        relative_id = self.kwargs.get('relative_id')
        return RelativeMedicine.objects.filter(
            relative_id=relative_id,
            relative__user=self.request.user
        )
    
    def perform_create(self, serializer):
        relative_id = self.kwargs.get('relative_id')
        relative = get_object_or_404(Relative, id=relative_id, user=self.request.user)
        serializer.save(relative=relative)

class MedicineScheduleViewSet(viewsets.ModelViewSet):
    """
    API endpoint for medicine schedules
    POST /relatives/{relative_id}/medicines/{medicine_id}/schedules/
    GET /relatives/{relative_id}/medicines/{medicine_id}/schedules/
    """
    permission_classes = [IsAuthenticated]
    serializer_class = MedicineScheduleSerializer
    
    def get_queryset(self):
        medicine_id = self.kwargs.get('medicine_id')
        relative_id = self.kwargs.get('relative_id')
        return MedicineSchedule.objects.filter(
            medicine_id=medicine_id,
            medicine__relative_id=relative_id,
            medicine__relative__user=self.request.user
        )
    
    def perform_create(self, serializer):
        medicine_id = self.kwargs.get('medicine_id')
        relative_id = self.kwargs.get('relative_id')
        medicine = get_object_or_404(
            RelativeMedicine,
            id=medicine_id,
            relative_id=relative_id,
            relative__user=self.request.user,
        )
        serializer.save(medicine=medicine)


class DoctorAgentChatView(APIView):
	permission_classes = [IsAuthenticated]
	throttle_classes = [ScopedRateThrottle]
	throttle_scope = "doctor_agent"

	SYSTEM_PROMPT = (
		"You are a medical emergency guidance assistant. "
		"Give short, clear, practical steps only. "
		"Do not diagnose. "
		"If risk is high, tell the user to call emergency services immediately."
		"Respond in the same language style as the user. If user writes Hinglish, reply in natural Hinglish (simple Roman Hindi + English mix)."
	)

	EMERGENCY_KEYWORDS = [
		"chest pain",
		"stroke",
		"unconscious",
		"seizure",
		"severe bleeding",
		"not breathing",
		"suicide",
		"heart attack",
	]
	HINGLISH_HINTS = [
		"kya",
		"hai",
		"kaise",
		"karu",
		"karo",
		"madad",
		"dard",
		"saans",
		"khun",
		"vomit",
		"bukhar",
	]

	def _is_hinglish(self, message):
		lowered_message = message.lower()
		return any(word in lowered_message for word in self.HINGLISH_HINTS)

	def _fallback_reply(self, message, emergency=False):
		if emergency:
			if self._is_hinglish(message):
				return (
					"Possible emergency lag rahi hai. Abhi local emergency services call karo. "
					"Person ko safe rakho, saans check karo, aur delay mat karo."
				)
			return (
				"Possible emergency detected. Call local emergency services now. "
				"Keep the person safe, monitor breathing, and do not delay professional care."
			)

		if self._is_hinglish(message):
			return (
				"Mujhe exact symptoms batao. Agar chest pain, saans phoolna, behoshi, "
				"ya heavy bleeding ho rahi hai to turant emergency help lo. "
				"Tab tak patient ko safe aur calm rakho."
			)
		return (
			"Tell me the exact symptoms. If there is chest pain, trouble breathing, unconsciousness, "
			"or heavy bleeding, get emergency help now. Keep the person safe and calm meanwhile."
		)

	def post(self, request):
		serializer = DoctorAgentMessageSerializer(data=request.data)
		serializer.is_valid(raise_exception=True)
		message = serializer.validated_data["message"]

		lowered_message = message.lower()
		if any(keyword in lowered_message for keyword in self.EMERGENCY_KEYWORDS):
			return Response(
				{
					"reply": self._fallback_reply(message, emergency=True)
				},
				status=status.HTTP_200_OK,
			)

		api_key = os.getenv("AI_API_KEY", "")
		model = os.getenv("AI_MODEL", "gpt-4.1-mini")
		base_url = os.getenv("AI_BASE_URL", "https://api.openai.com/v1")

		if not api_key:
			return Response({"reply": self._fallback_reply(message)}, status=status.HTTP_200_OK)

		try:
			response = requests.post(
				f"{base_url}/chat/completions",
				headers={
					"Authorization": f"Bearer {api_key}",
					"Content-Type": "application/json",
				},
				json={
					"model": model,
					"temperature": 0.2,
					"max_tokens": 220,
					"messages": [
						{"role": "system", "content": self.SYSTEM_PROMPT},
						{"role": "user", "content": message},
					],
				},
				timeout=12,
			)
			response.raise_for_status()
			data = response.json()
			reply = data["choices"][0]["message"]["content"].strip()
			return Response({"reply": reply}, status=status.HTTP_200_OK)
		except requests.RequestException:
			return Response({"reply": self._fallback_reply(message)}, status=status.HTTP_200_OK)