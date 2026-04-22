from datetime import datetime, timezone as datetime_timezone

from rest_framework_simplejwt.authentication import JWTAuthentication
from rest_framework_simplejwt.exceptions import InvalidToken

from apps.users.models import UserProfile
from config.myloggerconfig import get_master_logger


logger = get_master_logger().getChild(__name__)


class RevocationAwareJWTAuthentication(JWTAuthentication):
    def get_validated_token(self, raw_token):
        validated_token = super().get_validated_token(raw_token)

        user_id = validated_token.get("user_id")
        issued_at = validated_token.get("iat")
        if not user_id or issued_at is None:
            return validated_token

        profile = UserProfile.objects.filter(user_id=user_id).only("last_logout_at").first()
        if not profile or not profile.last_logout_at:
            return validated_token

        token_issued_at = datetime.fromtimestamp(int(issued_at), tz=datetime_timezone.utc)
        if token_issued_at <= profile.last_logout_at:
            logger.warning("Rejected revoked token for user_id=%s", user_id)
            raise InvalidToken("Token has been revoked.")

        logger.debug("Token validated for user_id=%s", user_id)
        return validated_token
