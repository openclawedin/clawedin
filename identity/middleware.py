from django.utils import timezone

from .auth import get_bearer_token, hash_token
from .models import ApiToken


class BearerTokenAuthMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        request.auth_token = None
        token = get_bearer_token(request)
        if token:
            token_hash = hash_token(token)
            api_token = (
                ApiToken.objects.select_related("user")
                .filter(token_hash=token_hash, revoked_at__isnull=True)
                .first()
            )
            if api_token:
                request.user = api_token.user
                request.auth_token = api_token
                ApiToken.objects.filter(id=api_token.id).update(last_used_at=timezone.now())
        return self.get_response(request)
