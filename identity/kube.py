import os

from django.utils.text import slugify


def normalize_namespace(username: str, user_id: int) -> str:
    namespace = slugify(username)
    if not namespace:
        namespace = f"user-{user_id}"
    namespace = namespace[:63].strip("-")
    if not namespace:
        namespace = f"user-{user_id}"
    return namespace


def resolve_agent_namespace(username: str, user_id: int):
    forced = os.environ.get("AGENT_NAMESPACE", "").strip()
    if forced:
        return forced, True
    return normalize_namespace(username, user_id), False


def load_kube_config() -> None:
    from kubernetes import config

    try:
        config.load_incluster_config()
    except Exception:  # pragma: no cover - falls back to local kube config
        kubeconfig = os.environ.get("KUBECONFIG", "")
        if kubeconfig:
            config.load_kube_config(config_file=kubeconfig)
        else:
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


def gui_middleware_name(pod_name: str) -> str:
    return normalize_k8s_name(f"agent-gui-mw-{pod_name}", "agent-gui-mw")


def gateway_secret_name(username: str, user_id: int) -> str:
    return normalize_k8s_name(f"openclaw-gateway-{username}", f"openclaw-gateway-{user_id}")


def gateway_secret_name_for_deployment(deployment_name: str, user_id: int) -> str:
    return normalize_k8s_name(
        f"openclaw-gateway-{deployment_name}",
        f"openclaw-gateway-{user_id}",
    )
