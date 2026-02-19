from django.utils.text import slugify


def normalize_namespace(username: str, user_id: int) -> str:
    namespace = slugify(username)
    if not namespace:
        namespace = f"user-{user_id}"
    namespace = namespace[:63].strip("-")
    if not namespace:
        namespace = f"user-{user_id}"
    return namespace


def load_kube_config() -> None:
    from kubernetes import config

    try:
        config.load_incluster_config()
    except Exception:  # pragma: no cover - falls back to local kube config
        config.load_kube_config()
