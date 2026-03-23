from dataclasses import dataclass

from django.conf import settings
from django.contrib.auth import get_user_model
from django.contrib.auth.hashers import check_password, make_password
from django.core import signing
from django.core.exceptions import ImproperlyConfigured
from django.utils.crypto import constant_time_compare, get_random_string, salted_hmac


API_TOKEN_ALPHABET = "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789"
API_TOKEN_LENGTH = 48
API_TOKEN_PREFIX_LENGTH = 12
BEARER_TOKEN_SIGNING_SALT = "users.cli_job_apply_token"
BEARER_TOKEN_PURPOSE = "job_apply_cli"


@dataclass
class BearerTokenMatch:
    user: object
    api_token: object | None = None


def generate_api_token() -> str:
    return get_random_string(API_TOKEN_LENGTH, allowed_chars=API_TOKEN_ALPHABET)


def hash_token(token: str) -> str:
    return make_password(token)


def check_token(token: str, token_hash: str) -> bool:
    return check_password(token, token_hash)


def token_prefix(token: str) -> str:
    return token[:API_TOKEN_PREFIX_LENGTH]


def _shared_bearer_secret() -> str:
    secret = settings.BEARER_TOKEN_SHARED_SECRET
    if not secret:
        raise ImproperlyConfigured("BEARER_TOKEN_SHARED_SECRET must be configured.")
    return secret


def _password_hash_marker(user) -> str:
    return salted_hmac(
        "users.cli_job_apply_token.password_marker",
        user.password,
        secret=_shared_bearer_secret(),
    ).hexdigest()


def mint_bearer_token(user, *, issuer: str | None = None) -> str:
    resolved_issuer = issuer if issuer is not None else settings.BEARER_TOKEN_ISSUER
    if not resolved_issuer:
        raise ImproperlyConfigured("BEARER_TOKEN_ISSUER must be configured.")
    payload = {
        "user_id": str(user.pk),
        "iss": resolved_issuer,
        "purpose": BEARER_TOKEN_PURPOSE,
        "pwd": _password_hash_marker(user),
        "nonce": get_random_string(12, allowed_chars=API_TOKEN_ALPHABET),
    }
    return signing.dumps(
        payload,
        salt=BEARER_TOKEN_SIGNING_SALT,
        key=_shared_bearer_secret(),
        compress=True,
    )


def _find_stored_api_token(token: str):
    from .models import ApiToken

    candidates = ApiToken.objects.select_related("user").filter(
        prefix=token_prefix(token),
        revoked_at__isnull=True,
    )
    return next(
        (candidate for candidate in candidates if check_token(token, candidate.token_hash)),
        None,
    )


def _get_shared_user(user_id):
    user_model = get_user_model()
    return (
        user_model._default_manager.db_manager(settings.USER_DOMAIN_DB_ALIAS)
        .filter(pk=user_id)
        .first()
    )


def _validate_shared_bearer_token(token: str):
    try:
        payload = signing.loads(
            token,
            salt=BEARER_TOKEN_SIGNING_SALT,
            key=_shared_bearer_secret(),
            max_age=getattr(settings, "BEARER_TOKEN_MAX_AGE_SECONDS", None),
        )
    except (ImproperlyConfigured, signing.BadSignature):
        return None

    if payload.get("purpose", payload.get("pur")) != BEARER_TOKEN_PURPOSE:
        return None

    issuer = (payload.get("iss") or "").strip()
    if issuer not in set(settings.BEARER_TOKEN_ACCEPTED_ISSUERS):
        return None

    user = _get_shared_user(payload.get("user_id", payload.get("uid")))
    if user is None or not getattr(user, "is_active", True):
        return None

    if not constant_time_compare(payload.get("pwd", ""), _password_hash_marker(user)):
        return None

    return BearerTokenMatch(user=user)


def authenticate_bearer_token(token: str):
    stored_token = _find_stored_api_token(token)
    if stored_token is not None:
        return BearerTokenMatch(user=stored_token.user, api_token=stored_token)
    return _validate_shared_bearer_token(token)


def get_bearer_token(request):
    auth = (
        request.META.get("HTTP_AUTHORIZATION")
        or request.META.get("Authorization")
        or request.META.get("REDIRECT_HTTP_AUTHORIZATION")
        or request.headers.get("Authorization", "")
    )
    if not auth:
        return None
    parts = auth.split(None, 1)
    if len(parts) != 2:
        return None
    scheme, value = parts
    if scheme.lower() != "bearer":
        return None
    return value.strip() or None
