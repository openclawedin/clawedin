from django.utils import timezone

from .auth import find_api_token, get_bearer_token


class BearerTokenCsrfExemptMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        request.auth_token = None
        request.bearer_token_user = None

        token = get_bearer_token(request)
        if token:
            api_token = find_api_token(token)
            if api_token is not None:
                request.auth_token = api_token
                request.bearer_token_user = api_token.user
                request._dont_enforce_csrf_checks = True
        return self.get_response(request)


class BearerTokenAuthMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        api_token = getattr(request, "auth_token", None)
        bearer_user = getattr(request, "bearer_token_user", None)
        if api_token is None:
            token = get_bearer_token(request)
            if token:
                api_token = find_api_token(token)
                if api_token is not None:
                    bearer_user = api_token.user
                    request.auth_token = api_token
                    request.bearer_token_user = bearer_user
        if api_token is not None and bearer_user is not None:
            request.user = bearer_user
            timezone_now = timezone.now()
            type(api_token).objects.filter(id=api_token.id).update(last_used_at=timezone_now)
            api_token.last_used_at = timezone_now
        return self.get_response(request)
