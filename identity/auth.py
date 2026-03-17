from django.contrib.auth.hashers import check_password, make_password
from django.utils.crypto import get_random_string


API_TOKEN_ALPHABET = "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789"
API_TOKEN_LENGTH = 48
API_TOKEN_PREFIX_LENGTH = 12


def generate_api_token() -> str:
    return get_random_string(API_TOKEN_LENGTH, allowed_chars=API_TOKEN_ALPHABET)


def hash_token(token: str) -> str:
    return make_password(token)


def check_token(token: str, token_hash: str) -> bool:
    return check_password(token, token_hash)


def token_prefix(token: str) -> str:
    return token[:API_TOKEN_PREFIX_LENGTH]


def find_api_token(token: str):
    from .models import ApiToken

    candidates = ApiToken.objects.select_related("user").filter(
        prefix=token_prefix(token),
        revoked_at__isnull=True,
    )
    return next(
        (candidate for candidate in candidates if check_token(token, candidate.token_hash)),
        None,
    )


def get_bearer_token(request):
    auth = request.META.get("HTTP_AUTHORIZATION", "")
    if not auth:
        return None
    parts = auth.split(None, 1)
    if len(parts) != 2:
        return None
    scheme, value = parts
    if scheme.lower() != "bearer":
        return None
    return value.strip() or None
