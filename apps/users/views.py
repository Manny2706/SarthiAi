from rest_framework import status
from django.shortcuts import get_object_or_404
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework import viewsets
from rest_framework.permissions import IsAuthenticated
from apps.users.serializers import LoginSerializer
from apps.users.serializers import SignupSerializer
from apps.users.serializers import UserSerializer
from apps.users.serializers import build_token_response
from .serializers import RelativeSerializer
from .serializers import RelativeDetailSerializer
from .serializers import RelativeMedicineSerializer
from .serializers import MedicineScheduleSerializer
from .models import Relative
from .models import RelativeMedicine
from .models import MedicineSchedule

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