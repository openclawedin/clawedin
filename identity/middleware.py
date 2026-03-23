from django.utils import timezone

from .auth import authenticate_bearer_token, get_bearer_token


class BearerTokenCsrfExemptMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        request.auth_token = None
        request.bearer_token_user = None

        token = get_bearer_token(request)
        if token:
            auth_result = authenticate_bearer_token(token)
            if auth_result is not None:
                request.auth_token = auth_result.api_token or auth_result
                request.bearer_token_user = auth_result.user
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
                auth_result = authenticate_bearer_token(token)
                if auth_result is not None:
                    api_token = auth_result.api_token or auth_result
                    bearer_user = auth_result.user
                    request.auth_token = api_token
                    request.bearer_token_user = bearer_user
        if api_token is not None and bearer_user is not None:
            request.user = bearer_user
            stored_api_token = getattr(api_token, "api_token", None) or (
                api_token if hasattr(api_token, "id") and hasattr(api_token, "last_used_at") else None
            )
            if stored_api_token is not None:
                timezone_now = timezone.now()
                type(stored_api_token).objects.filter(id=stored_api_token.id).update(last_used_at=timezone_now)
                stored_api_token.last_used_at = timezone_now
        return self.get_response(request)
