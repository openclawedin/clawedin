from django.utils import timezone

from .auth import check_token, get_bearer_token, token_prefix
from .models import ApiToken


class BearerTokenAuthMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        request.auth_token = None
        token = get_bearer_token(request)
        if token:
            candidates = ApiToken.objects.select_related("user").filter(
                prefix=token_prefix(token),
                revoked_at__isnull=True,
            )
            api_token = next(
                (candidate for candidate in candidates if check_token(token, candidate.token_hash)),
                None,
            )
            if api_token is not None:
                request.user = api_token.user
                request.auth_token = api_token
                ApiToken.objects.filter(id=api_token.id).update(last_used_at=timezone.now())
        return self.get_response(request)
