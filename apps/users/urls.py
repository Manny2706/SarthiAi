from django.urls import path, include
from rest_framework.routers import DefaultRouter
from rest_framework_simplejwt.views import TokenRefreshView, TokenVerifyView
from apps.users.views import (
    LoginView, LogoutView, SignupView, RelativeViewSet,
    RelativeMedicineViewSet, MedicineScheduleViewSet
)
from apps.users.webhooks import VapiWebhookView

router = DefaultRouter()
router.register(r'relatives', RelativeViewSet, basename='relative')

urlpatterns = [
    path("signup/", SignupView.as_view(), name="signup"),
    path("login/", LoginView.as_view(), name="login"),
    path("logout/", LogoutView.as_view(), name="logout"),
    path("token/refresh/", TokenRefreshView.as_view(), name="token-refresh"),
    path("token/verify/", TokenVerifyView.as_view(), name="token-verify"),
    path("webhooks/vapi/", VapiWebhookView.as_view(), name="vapi-webhook"),
    path("", include(router.urls)),
    
    # Nested routes for medicines and schedules
    path(
        "relatives/<int:relative_id>/medicines/",
        RelativeMedicineViewSet.as_view({
            'get': 'list',
            'post': 'create'
        }),
        name='relative-medicines'
    ),
    path(
        "relatives/<int:relative_id>/medicines/<int:medicine_id>/schedules/",
        MedicineScheduleViewSet.as_view({
            'get': 'list',
            'post': 'create'
        }),
        name='medicine-schedules'
    ),
    path(
        "relatives/<int:relative_id>/medicines/<int:medicine_id>/schedules/<int:pk>/",
        MedicineScheduleViewSet.as_view({
            'get': 'retrieve',
            'patch': 'partial_update',
            'put': 'update',
            'delete': 'destroy',
        }),
        name='medicine-schedule-detail'
    ),
]