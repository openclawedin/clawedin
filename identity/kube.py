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


def normalize_k8s_name(value: str, fallback: str) -> str:
    name = slugify(value)
    if not name:
        name = fallback
    name = name[:63].strip("-")
    if not name:
        name = fallback
    return name


def gui_service_name(pod_name: str) -> str:
    return normalize_k8s_name(f"agent-gui-{pod_name}", "agent-gui")


def gui_ingress_name(pod_name: str) -> str:
    return normalize_k8s_name(f"agent-gui-ingress-{pod_name}", "agent-gui-ingress")
