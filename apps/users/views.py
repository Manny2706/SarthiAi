from rest_framework import status
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.users.serializers import LoginSerializer
from apps.users.serializers import SignupSerializer
from apps.users.serializers import UserSerializer
from apps.users.serializers import build_token_response


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
