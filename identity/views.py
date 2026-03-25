import base64
import json
import logging
import mimetypes
import os
import secrets
import shlex
import socket
import ssl
import threading
import time
import uuid
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen
from urllib.parse import urlsplit, urlunsplit, parse_qsl, urlencode
from decimal import Decimal, ROUND_DOWN
from datetime import datetime, timedelta, timezone as dt_timezone

from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import login_required, user_passes_test
from django.contrib.auth.views import LoginView, LogoutView
from django.core.mail import send_mail
from django.core.signing import BadSignature, SignatureExpired, TimestampSigner
from django.http import HttpResponse, StreamingHttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.db import close_old_connections, transaction
from django.db.models import Count, Q, Sum
from django.utils import timezone
from django.utils.text import get_valid_filename
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_GET, require_POST

try:
    import stripe

    STRIPE_SDK_AVAILABLE = True
except ImportError:  # pragma: no cover - exercised in environments without stripe installed
    stripe = None
    STRIPE_SDK_AVAILABLE = False

from .forms import (
    AgentChannelCreateForm,
    LoginForm,
    AgentLaunchForm,
    ProfileUpdateForm,
    RegisterForm,
    ResumeCertificationForm,
    ResumeEducationForm,
    ResumeExperienceForm,
    ResumeForm,
    ResumeProjectForm,
    ResumeSkillForm,
    SolanaTransferForm,
    UserSkillForm,
)
from .auth import generate_api_token, hash_token, mint_bearer_token, token_prefix
from .kube import (
    agent_user_bearer_secret_name_for_deployment,
    agent_web_auth_secret_name_for_deployment,
    gui_ingress_name,
    gui_middleware_name,
    gui_service_name,
    gateway_secret_name,
    gateway_secret_name_for_deployment,
    load_kube_config,
    normalize_k8s_name,
    normalize_namespace,
    openai_secret_name_for_deployment,
    resolve_agent_namespace,
)
from .solana_wallet import generate_solana_wallet, load_keypair
from .solana_wallet import generate_solana_wallet
from .models import (
    ApiToken,
    Resume,
    ResumeCertification,
    ResumeEducation,
    ResumeExperience,
    ResumeProject,
    ResumeSkill,
    AgentDeployment,
    AgentDashboardAttachment,
    AgentDashboardTurn,
    User,
    UserSkill,
    agent_deployment_has_dashboard_bootstrap_field,
)
from analytics.models import SkillPageRequestMetric

logger = logging.getLogger(__name__)

AGENT_CLAWEDIN_SKILL_URL = "https://openclawedin.com/static/skill.md"
AGENT_JOBS_SKILL_URL = "https://jobs.openclawedin.com/api/static/skill.md"

try:
    from solana.rpc.api import Client
    from solana.rpc.types import TxOpts
    from solana.transaction import Transaction
    from solders.pubkey import Pubkey
    from spl.token._layouts import MINT_LAYOUT
    from spl.token.constants import TOKEN_PROGRAM_ID
    from spl.token.instructions import (
        CreateAssociatedTokenAccountParams,
        TransferCheckedParams,
        create_associated_token_account,
        get_associated_token_address,
        transfer_checked,
    )

    SOLANA_SDK_AVAILABLE = True
except ImportError:  # pragma: no cover - optional in environments without solana installed
    SOLANA_SDK_AVAILABLE = False

BIRDEYE_PRICE_URL = "https://public-api.birdeye.so/defi/price"
CLAWEDIN_CHANNEL_DEFAULT_PORT = 31890
CLAWEDIN_CHANNEL_DEFAULT_PATH = "/v1/messages"
AGENT_MODEL_PROVIDER_OPENAI = AgentLaunchForm.MODEL_PROVIDER_OPENAI
AGENT_MODEL_PROVIDER_ANTHROPIC = AgentLaunchForm.MODEL_PROVIDER_ANTHROPIC
AGENT_DEFAULT_OPENAI_MODEL = "openai/gpt-5.2"
AGENT_DEFAULT_ANTHROPIC_MODEL = "anthropic/claude-opus-4-6"
DEFAULT_AGENT_DASHBOARD_ITEM_KEYS = [
    "top_route_1",
    "top_route_2",
    "top_route_3",
    "top_route_4",
]
MAX_AGENT_DASHBOARD_ITEMS = 6
AGENT_DASHBOARD_ITEM_OPTIONS = [
    {
        "key": "top_route_1",
        "label": "Busiest API route",
        "description": "The documented API route with the highest call volume in the current reporting window.",
    },
    {
        "key": "top_route_2",
        "label": "2nd busiest API route",
        "description": "The documented API route with the second-highest call volume in the current reporting window.",
    },
    {
        "key": "top_route_3",
        "label": "3rd busiest API route",
        "description": "The documented API route with the third-highest call volume in the current reporting window.",
    },
    {
        "key": "top_route_4",
        "label": "4th busiest API route",
        "description": "The documented API route with the fourth-highest call volume in the current reporting window.",
    },
    {
        "key": "tracked_api_calls",
        "label": "Total API requests",
        "description": "Every request recorded against documented agent-facing API routes in the current reporting window.",
    },
    {
        "key": "successful_api_calls",
        "label": "Successful API requests",
        "description": "Tracked API requests that finished with a successful HTTP status code.",
    },
    {
        "key": "failed_api_calls",
        "label": "Failed API requests",
        "description": "Tracked API requests that returned an HTTP error status code.",
    },
    {
        "key": "get_calls",
        "label": "GET requests",
        "description": "Read-only API requests recorded in the current reporting window.",
    },
    {
        "key": "post_calls",
        "label": "POST requests",
        "description": "Write or action API requests recorded in the current reporting window.",
    },
    {
        "key": "tracked_endpoints",
        "label": "Active API routes",
        "description": "The number of unique method-and-path combinations that received traffic in the current reporting window.",
    },
    {
        "key": "prompt_turns",
        "label": "Prompts sent",
        "description": "Messages submitted from this dashboard to the live agent gateway.",
    },
    {
        "key": "agent_replies",
        "label": "Replies received",
        "description": "Successful responses returned from the live agent gateway to this dashboard.",
    },
    {
        "key": "linked_channels",
        "label": "Connected channels",
        "description": "The number of OpenClaw channels currently configured in the running agent.",
    },
]


def _default_agent_model_provider_for_user(user: User) -> str:
    if user.anthropic_api_key and not user.openai_api_key:
        return AGENT_MODEL_PROVIDER_ANTHROPIC
    return AGENT_MODEL_PROVIDER_OPENAI


def _agent_model_for_provider(provider: str) -> str:
    if provider == AGENT_MODEL_PROVIDER_ANTHROPIC:
        return AGENT_DEFAULT_ANTHROPIC_MODEL
    return AGENT_DEFAULT_OPENAI_MODEL


def _agent_models_config(provider: str) -> dict:
    model_name = _agent_model_for_provider(provider)
    return {
        "defaults": {
            "model": {"primary": model_name},
            "models": {model_name: {}},
        }
    }


def _agent_secret_string_data(openai_api_key: str, anthropic_api_key: str) -> dict:
    payload = {}
    if openai_api_key:
        payload["OPENAI_API_KEY"] = openai_api_key
    if anthropic_api_key:
        payload["ANTHROPIC_API_KEY"] = anthropic_api_key
    return payload


def _resolve_agent_launch_credentials(user: User, cleaned_data: dict) -> dict:
    provider = (cleaned_data.get("model_provider") or "").strip().lower()
    if provider not in {AGENT_MODEL_PROVIDER_OPENAI, AGENT_MODEL_PROVIDER_ANTHROPIC}:
        provider = AGENT_MODEL_PROVIDER_OPENAI

    # Keep saved credentials reusable across launches so users only need to submit
    # the provider key they want to update, while still enforcing the active provider.
    openai_input = (cleaned_data.get("openai_api_key") or "").strip()
    anthropic_input = (cleaned_data.get("anthropic_api_key") or "").strip()
    openai_api_key = openai_input or (user.openai_api_key or "").strip()
    anthropic_api_key = anthropic_input or (user.anthropic_api_key or "").strip()

    errors = {}
    if provider == AGENT_MODEL_PROVIDER_OPENAI and not openai_api_key:
        errors["openai_api_key"] = "Enter an OpenAI API key or choose Claude as the provider."
    if provider == AGENT_MODEL_PROVIDER_ANTHROPIC and not anthropic_api_key:
        errors["anthropic_api_key"] = "Enter a Claude API key or choose OpenAI as the provider."

    updates = {}
    if openai_input:
        updates["openai_api_key"] = openai_input
    if anthropic_input:
        updates["anthropic_api_key"] = anthropic_input

    return {
        "provider": provider,
        "default_model": _agent_model_for_provider(provider),
        "openai_api_key": openai_api_key,
        "anthropic_api_key": anthropic_api_key,
        "secret_string_data": _agent_secret_string_data(openai_api_key, anthropic_api_key),
        "errors": errors,
        "updates": updates,
    }


def _agent_shared_vfs_storage_root() -> Path:
    return Path(getattr(settings, "AGENT_SHARED_VFS_STORAGE_PATH", "/mnt/vfs") or "/mnt/vfs")


def _agent_shared_vfs_mount_root() -> str:
    return (getattr(settings, "AGENT_SHARED_VFS_MOUNT_PATH", "/mnt/clawedin-shared") or "/mnt/clawedin-shared").rstrip("/")


def _safe_agent_attachment_name(filename: str) -> str:
    cleaned = get_valid_filename(Path(filename or "attachment").name)
    return cleaned or "attachment"


def _agent_attachment_relative_path(user, deployment_name: str, filename: str) -> str:
    safe_name = _safe_agent_attachment_name(filename)
    normalized_deployment = normalize_k8s_name(
        deployment_name or user.username or "agent",
        "agent",
    )
    return os.path.join(
        "agent-dashboard-uploads",
        str(user.id),
        normalized_deployment[:63] or "agent",
        timezone.now().strftime("%Y/%m/%d"),
        f"{uuid.uuid4().hex}-{safe_name}",
    )


def _serialize_dashboard_attachment(attachment: AgentDashboardAttachment) -> dict:
    return {
        "id": str(attachment.id),
        "name": attachment.original_name,
        "contentType": attachment.content_type or "",
        "sizeBytes": attachment.size_bytes,
        "agentPath": attachment.agent_path,
        "createdAt": timezone.localtime(attachment.created_at).strftime("%b %d, %I:%M %p"),
    }


def _build_attachment_notice_text(attachment: AgentDashboardAttachment) -> str:
    return (
        "I just saved a file for you in the shared Clawedin workspace.\n"
        f"Filename: {attachment.original_name}\n"
        f"Location: {attachment.agent_path}\n"
        "Please use this exact path if you need to open, read, or process the file."
    )


def _dashboard_conversation_id(user, deployment_name: str, pod_name: str) -> str:
    base = (deployment_name or pod_name or "clawedin-dashboard").strip()
    return f"{base}-{user.id}"


def _sanitize_agent_dashboard_item_keys(item_keys):
    allowed_keys = {item["key"] for item in AGENT_DASHBOARD_ITEM_OPTIONS}
    cleaned = []
    for item_key in item_keys or []:
        if item_key in allowed_keys and item_key not in cleaned:
            cleaned.append(item_key)
        if len(cleaned) >= MAX_AGENT_DASHBOARD_ITEMS:
            break
    return cleaned or list(DEFAULT_AGENT_DASHBOARD_ITEM_KEYS)


def _dashboard_card(label, value, delta, description, key):
    return {
        "key": key,
        "label": label,
        "value": str(value),
        "delta": delta,
        "description": description,
    }


def _dashboard_top_route_copy(rank: int, route: dict | None = None) -> tuple[str, str]:
    labels = {
        1: "Busiest API route",
        2: "2nd busiest API route",
        3: "3rd busiest API route",
        4: "4th busiest API route",
    }
    label = labels.get(rank, f"Top API route #{rank}")
    if not route:
        return label, "No tracked API traffic has been recorded for this slot yet."
    method = (route.get("method") or "HTTP").upper()
    path = route.get("normalized_path") or "/"
    return label, f"Currently {method} {path}. This route is ranked #{rank} by request volume."


def _build_agent_navigation(active_key: str, pod_name: str = ""):
    has_agent = bool(pod_name)
    items = [
        {
            "key": "manager",
            "label": "Agent Manager",
            "url": reverse("identity:agent_manager"),
            "disabled": False,
        },
        {
            "key": "details",
            "label": "Details",
            "url": reverse("identity:agent_detail", args=[pod_name]) if has_agent else "",
            "disabled": not has_agent,
        },
        {
            "key": "dashboard",
            "label": "Dashboard",
            "url": reverse("identity:agent_dashboard", args=[pod_name]) if has_agent else "",
            "disabled": not has_agent,
        },
        {
            "key": "configure",
            "label": "Configure",
            "url": reverse("identity:agent_dashboard_config_page", args=[pod_name]) if has_agent else "",
            "disabled": not has_agent,
        },
        {
            "key": "terminal",
            "label": "Terminal",
            "url": reverse("identity:agent_terminal", args=[pod_name]) if has_agent else "",
            "disabled": not has_agent,
        },
    ]
    for item in items:
        item["active"] = item["key"] == active_key
    return items

def _ensure_dockerhub_secret(client_module, v1, namespace: str, source_namespace: str = "default") -> None:
    secret_name = "dockerhub-secret"
    try:
        v1.read_namespaced_secret(secret_name, namespace)
        return
    except Exception:
        pass

    try:
        source_secret = v1.read_namespaced_secret(secret_name, source_namespace)
    except Exception:
        return

    secret_body = client_module.V1Secret(
        metadata=client_module.V1ObjectMeta(name=secret_name),
        type=source_secret.type,
        data=source_secret.data,
    )
    v1.create_namespaced_secret(namespace, secret_body)


def _upsert_namespaced_secret(v1, namespace: str, secret_name: str, secret_body, api_exception_cls) -> None:
    try:
        v1.read_namespaced_secret(secret_name, namespace)
        v1.patch_namespaced_secret(secret_name, namespace, secret_body)
    except api_exception_cls as exc:
        if exc.status == 404:
            v1.create_namespaced_secret(namespace, secret_body)
        else:
            raise


def _delete_namespaced_secret_if_present(v1, namespace: str, secret_name: str) -> None:
    if not secret_name:
        return
    try:
        v1.delete_namespaced_secret(secret_name, namespace)
    except Exception:
        pass


def _parse_request_json(request):
    if not request.body:
        return {}
    try:
        return json.loads(request.body.decode("utf-8"))
    except (UnicodeDecodeError, ValueError):
        return None


def _agent_openclaw_pvc_mode() -> str:
    mode = (getattr(settings, "AGENT_OPENCLAW_PVC_MODE", "per_agent") or "per_agent").strip().lower()
    if mode in {"per-agent", "per_agent", "dedicated"}:
        return "per_agent"
    return "shared"


def _shared_agent_openclaw_claim_name() -> str:
    return (getattr(settings, "AGENT_OPENCLAW_PVC_NAME", "clawedin-vfs-pvc") or "").strip()


def _agent_openclaw_claim_name_for_deployment(deployment_name: str, user_id: int) -> str:
    return normalize_k8s_name(
        f"openclaw-home-{deployment_name}",
        f"openclaw-home-{user_id}",
    )


def _openclaw_claim_from_deployment_obj(deployment) -> str | None:
    if not deployment or not deployment.spec or not deployment.spec.template or not deployment.spec.template.spec:
        return None
    for volume in deployment.spec.template.spec.volumes or []:
        pvc = getattr(volume, "persistent_volume_claim", None)
        if volume.name == "openclaw-home" and pvc and pvc.claim_name:
            return pvc.claim_name
    return None


def _is_managed_agent_claim_name(claim_name: str) -> bool:
    if not claim_name:
        return False
    shared = _shared_agent_openclaw_claim_name()
    if shared and claim_name == shared:
        return False
    return claim_name.startswith("openclaw-home-")


def _gui_path_prefix() -> str:
    prefix = getattr(settings, "AGENT_GUI_PATH_PREFIX", "/agents/gui") or "/agents/gui"
    if not prefix.startswith("/"):
        prefix = f"/{prefix}"
    return prefix.rstrip("/")


def _gui_path_for_pod(pod_name: str) -> str:
    return f"{_gui_path_prefix()}/{pod_name}/"


def _gui_host_for_pod(pod_name: str) -> str | None:
    suffix = getattr(settings, "AGENT_GUI_HOST_SUFFIX", "").strip().lower()
    if suffix:
        return f"{pod_name}.{suffix}"
    host = getattr(settings, "AGENT_GUI_INGRESS_HOST", "").strip().lower()
    return host or None


def _gui_url_for_pod(request, pod_name: str) -> str:
    host = _gui_host_for_pod(pod_name)
    if not host:
        return _gui_path_for_pod(pod_name)
    forwarded = request.META.get("HTTP_X_FORWARDED_PROTO", "").split(",")[0].strip().lower()
    scheme = forwarded if forwarded in {"http", "https"} else None
    if not scheme:
        force_https = bool(getattr(settings, "AGENT_GUI_FORCE_HTTPS", False))
        scheme = "https" if (force_https or host) else ("https" if request.is_secure() else "http")
    default_path = getattr(settings, "AGENT_GUI_DEFAULT_PATH", "/overview") or "/overview"
    if not default_path.startswith("/"):
        default_path = f"/{default_path}"
    return f"{scheme}://{host}{default_path}"


def _gui_resource_name_for_pod(pod) -> str:
    if pod and pod.metadata and pod.metadata.labels:
        deployment_name = (pod.metadata.labels.get("deployment") or "").strip()
        if deployment_name and getattr(settings, "AGENT_GUI_HOST_SUFFIX", "").strip():
            return deployment_name
    return getattr(getattr(pod, "metadata", None), "name", "") or ""


def _gui_allowed_origins_for_pod(pod_name: str) -> list[str]:
    origins = []
    host = _gui_host_for_pod(pod_name)
    if not host:
        return origins
    schemes = {"https"}
    if bool(getattr(settings, "DEBUG", False)):
        schemes.add("http")
    for scheme in sorted(schemes):
        origins.append(f"{scheme}://{host}")
    return origins


def _append_query_param(url: str, key: str, value: str) -> str:
    if not value:
        return url
    parsed = urlsplit(url)
    query = dict(parse_qsl(parsed.query, keep_blank_values=True))
    query[key] = value
    return urlunsplit((parsed.scheme, parsed.netloc, parsed.path, urlencode(query), parsed.fragment))


def _append_fragment_param(url: str, key: str, value: str) -> str:
    if not value:
        return url
    parsed = urlsplit(url)
    fragment = dict(parse_qsl(parsed.fragment, keep_blank_values=True))
    fragment[key] = value
    return urlunsplit((parsed.scheme, parsed.netloc, parsed.path, parsed.query, urlencode(fragment)))


def _pod_container_ready(pod) -> bool:
    statuses = getattr(getattr(pod, "status", None), "container_statuses", None) or []
    if not statuses:
        return False
    return any(getattr(status, "ready", False) for status in statuses)


def _verify_agent_gui_tls(gui_url: str) -> tuple[bool, str]:
    parsed = urlsplit(gui_url)
    if parsed.scheme != "https" or not parsed.hostname:
        return True, "Web GUI is ready."

    port = parsed.port or 443
    context = ssl.create_default_context()
    try:
        with socket.create_connection((parsed.hostname, port), timeout=5) as sock:
            with context.wrap_socket(sock, server_hostname=parsed.hostname):
                return True, "HTTPS certificate is valid."
    except ssl.SSLCertVerificationError:
        return False, "HTTPS certificate is not trusted yet."
    except ssl.SSLError:
        return False, "HTTPS certificate is not ready yet."
    except socket.timeout:
        return False, "Container is not ready yet."
    except OSError:
        return False, "Preparing secure Web GUI access."


def _warm_and_probe_agent_gui(gui_url: str) -> tuple[bool, str]:
    parsed = urlsplit(gui_url)
    if not parsed.scheme or not parsed.netloc:
        return True, "Web GUI is ready."

    tls_ready, tls_message = _verify_agent_gui_tls(gui_url)
    if not tls_ready:
        return False, tls_message

    headers = {"User-Agent": "clawedin/1.0", "Accept": "text/html,application/xhtml+xml"}
    req = Request(gui_url, headers=headers)
    try:
        with urlopen(req, timeout=5) as resp:
            status_code = getattr(resp, "status", 200)
            if status_code < 500:
                return True, "Web GUI is ready."
            return False, "Container is not ready yet."
    except HTTPError as exc:
        if exc.code in {401, 403}:
            return True, "Web GUI is ready."
        if exc.code in {404, 425, 429, 500, 502, 503, 504}:
            return False, "Container is not ready yet."
        return False, "Preparing secure Web GUI access."
    except URLError as exc:
        reason = getattr(exc, "reason", exc)
        if isinstance(reason, ssl.SSLCertVerificationError):
            return False, "HTTPS certificate is not trusted yet."
        if isinstance(reason, ssl.SSLError):
            return False, "HTTPS certificate is not ready yet."
        if isinstance(reason, socket.timeout):
            return False, "Container is not ready yet."
        return False, "Preparing secure Web GUI access."
    except (TimeoutError, ValueError):
        return False, "Container is not ready yet."


def _prepare_agent_gui_context(request, pod_name: str) -> dict:
    namespace, _ = resolve_agent_namespace(request.user.username, request.user.id)
    error_message = None
    pod = None
    gui_path = None
    resolved_pod_name = pod_name

    try:
        from kubernetes import client
    except ImportError:
        error_message = "Kubernetes client not installed."
    else:
        try:
            load_kube_config()
            v1 = client.CoreV1Api()
            networking = client.NetworkingV1Api()
            allow_cross_namespace = request.user.is_staff or request.user.is_superuser
            try:
                pod, namespace = _resolve_pod(v1, pod_name, namespace, allow_cross_namespace)
            except client.exceptions.ApiException as exc:
                if exc.status != 404:
                    raise
                label_selector = f"app=openclaw-agent,owner={request.user.username}"
                pods = v1.list_namespaced_pod(
                    namespace=namespace,
                    label_selector=label_selector,
                )
                if not pods.items:
                    error_message = (
                        "Pod not found.\n"
                        f"Requested pod: {pod_name}\n"
                        f"Namespace: {namespace}\n"
                        f"Selector: {label_selector}\n"
                        f"{_pod_debug_snapshot(v1, namespace, label_selector)}\n"
                        f"{_kube_context_snapshot()}"
                    )
                    logger.warning(
                        "Agent GUI pod not found: pod=%s namespace=%s selector=%s",
                        pod_name,
                        namespace,
                        label_selector,
                    )
                    return {
                        "namespace": namespace,
                        "pod": pod,
                        "gui_path": gui_path,
                        "resolved_pod_name": resolved_pod_name,
                        "error_message": error_message,
                    }
                pods_sorted = sorted(
                    pods.items,
                    key=lambda item: item.status.start_time or datetime.min.replace(tzinfo=dt_timezone.utc),
                    reverse=True,
                )
                pod = pods_sorted[0]
                pod_name = pod.metadata.name
                resolved_pod_name = pod_name
            if (
                pod
                and pod.metadata
                and pod.metadata.labels
                and (
                    pod.metadata.labels.get("app") != "openclaw-agent"
                    or (
                        not _is_admin_user(request.user)
                        and pod.metadata.labels.get("owner") != request.user.username
                    )
                )
            ):
                return {
                    "namespace": namespace,
                    "pod": pod,
                    "gui_path": gui_path,
                    "resolved_pod_name": resolved_pod_name,
                    "error_message": "Agent GUI is only available for your agent pods.",
                }

            _ensure_agent_gui_resources(client, v1, networking, namespace, pod, request.user.username)

            gui_resource_name = _gui_resource_name_for_pod(pod) or pod_name
            gui_path = _gui_url_for_pod(request, gui_resource_name)
            if getattr(settings, "AGENT_GUI_HOST_SUFFIX", "").strip():
                deployment_name = None
                if pod and pod.metadata and pod.metadata.labels:
                    deployment_name = pod.metadata.labels.get("deployment")
                deployment_record = AgentDeployment.objects.filter(
                    user=request.user,
                    deployment_name=deployment_name or "",
                    namespace=namespace,
                ).first()
                if deployment_record:
                    gui_path = _append_fragment_param(gui_path, "token", deployment_record.gateway_token)
                else:
                    try:
                        token_applied = False
                        secret_name = None
                        if pod and pod.spec and pod.spec.containers:
                            for container in pod.spec.containers:
                                for env_var in container.env or []:
                                    if (
                                        env_var.name == "OPENCLAW_GATEWAY_TOKEN"
                                        and env_var.value_from
                                        and env_var.value_from.secret_key_ref
                                        and env_var.value_from.secret_key_ref.name
                                    ):
                                        secret_name = env_var.value_from.secret_key_ref.name
                                        break
                                if secret_name:
                                    break
                        if secret_name:
                            secret = v1.read_namespaced_secret(secret_name, namespace)
                            encoded = (secret.data or {}).get("OPENCLAW_GATEWAY_TOKEN")
                            if encoded:
                                token = base64.b64decode(encoded).decode("utf-8")
                                gui_path = _append_fragment_param(gui_path, "token", token)
                                token_applied = True
                        if not token_applied:
                            legacy_secret = gateway_secret_name(request.user.username, request.user.id)
                            legacy = v1.read_namespaced_secret(legacy_secret, namespace)
                            encoded = (legacy.data or {}).get("OPENCLAW_GATEWAY_TOKEN")
                            if encoded:
                                token = base64.b64decode(encoded).decode("utf-8")
                                gui_path = _append_fragment_param(gui_path, "token", token)
                    except Exception:
                        pass
        except client.exceptions.ApiException as exc:
            if exc.status == 404:
                label_selector = f"app=openclaw-agent,owner={request.user.username}"
                error_message = (
                    "Pod not found.\n"
                    f"Requested pod: {pod_name}\n"
                    f"Namespace: {namespace}\n"
                    f"Selector: {label_selector}\n"
                    f"{_pod_debug_snapshot(v1, namespace, label_selector)}\n"
                    f"{_kube_context_snapshot()}"
                )
                logger.warning(
                    "Agent GUI pod not found (404): pod=%s namespace=%s selector=%s",
                    pod_name,
                    namespace,
                    label_selector,
                )
            else:
                error_message = (
                    "Cluster API error while preparing agent GUI.\n"
                    f"Requested pod: {pod_name}\n"
                    f"Namespace: {namespace}\n"
                    f"Details: {_format_api_exception(exc)}\n"
                    f"{_kube_context_snapshot()}"
                )
        except Exception as exc:  # pragma: no cover - depends on kube setup
            error_message = (
                "Cluster error while preparing agent GUI.\n"
                f"Requested pod: {pod_name}\n"
                f"Namespace: {namespace}\n"
                f"Details: {exc}\n"
                f"{_kube_context_snapshot()}"
            )
            logger.exception(
                "Agent GUI error: pod=%s namespace=%s",
                pod_name,
                namespace,
            )

    return {
        "namespace": namespace,
        "pod": pod,
        "gui_path": gui_path,
        "resolved_pod_name": resolved_pod_name,
        "error_message": error_message,
    }


def _fetch_birdeye_price(mint_address: str) -> tuple[Decimal | None, str | None]:
    if not settings.BIRDEYE_API_KEY:
        return None, "Birdeye API key is not configured."

    url = f"{BIRDEYE_PRICE_URL}?{urlencode({'address': mint_address})}"
    headers = {
        "X-API-KEY": settings.BIRDEYE_API_KEY,
        "x-chain": "solana",
        "Accept": "application/json",
        "User-Agent": "clawedin/1.0",
    }
    req = Request(url, headers=headers)
    try:
        with urlopen(req, timeout=10) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
    except HTTPError as exc:
        return None, f"Could not fetch token price: HTTP {exc.code} {exc.reason}"
    except (URLError, ValueError) as exc:
        return None, f"Could not fetch token price: {exc}"

    price = None
    if isinstance(payload, dict):
        data = payload.get("data")
        if isinstance(data, dict):
            for key in ("value", "price", "usd", "usdPrice", "valueUsd"):
                if data.get(key) is not None:
                    price = data.get(key)
                    break
        if price is None:
            for key in ("price", "value", "usd", "usdPrice"):
                if payload.get(key) is not None:
                    price = payload.get(key)
                    break

    if price is None:
        return None, "Token price unavailable from Birdeye."

    try:
        return Decimal(str(price)), None
    except Exception:
        return None, "Token price is invalid."


@login_required
@require_GET
def solana_token_price(request):
    mint_address = request.GET.get("mint", "").strip()
    if not mint_address:
        return JsonResponse({"ok": False, "error": "Token mint address is required."}, status=400)

    if SOLANA_SDK_AVAILABLE:
        try:
            Pubkey.from_string(mint_address)
        except Exception:
            return JsonResponse({"ok": False, "error": "Token mint address is invalid."}, status=400)

    price, price_error = _fetch_birdeye_price(mint_address)
    if price_error:
        return JsonResponse({"ok": False, "error": price_error}, status=400)

    return JsonResponse({"ok": True, "price": str(price)})


def _upsert_service(v1, namespace: str, service_body):
    try:
        v1.read_namespaced_service(service_body.metadata.name, namespace)
        v1.patch_namespaced_service(service_body.metadata.name, namespace, service_body)
    except Exception as exc:
        if getattr(exc, "status", None) == 404:
            v1.create_namespaced_service(namespace, service_body)
        else:
            raise


def _upsert_endpoints(v1, namespace: str, endpoints_body):
    try:
        v1.read_namespaced_endpoints(endpoints_body.metadata.name, namespace)
        v1.patch_namespaced_endpoints(endpoints_body.metadata.name, namespace, endpoints_body)
    except Exception as exc:
        if getattr(exc, "status", None) == 404:
            v1.create_namespaced_endpoints(namespace, endpoints_body)
        else:
            raise


def _upsert_ingress(networking, namespace: str, ingress_body):
    try:
        networking.read_namespaced_ingress(ingress_body.metadata.name, namespace)
        networking.patch_namespaced_ingress(ingress_body.metadata.name, namespace, ingress_body)
    except Exception as exc:
        if getattr(exc, "status", None) == 404:
            networking.create_namespaced_ingress(namespace, ingress_body)
        else:
            raise


def _upsert_custom_object(api, group: str, version: str, plural: str, namespace: str, body):
    name = body["metadata"]["name"]
    try:
        api.get_namespaced_custom_object(group, version, namespace, plural, name)
        api.patch_namespaced_custom_object(group, version, namespace, plural, name, body)
    except Exception as exc:
        if getattr(exc, "status", None) == 404:
            api.create_namespaced_custom_object(group, version, namespace, plural, body)
        else:
            raise


def _delete_custom_object(api, group: str, version: str, plural: str, namespace: str, name: str):
    try:
        api.delete_namespaced_custom_object(group, version, namespace, plural, name)
    except Exception as exc:
        if getattr(exc, "status", None) != 404:
            raise


def _model_class(client_module, name: str):
    if hasattr(client_module, name):
        return getattr(client_module, name)
    models = getattr(client_module, "models", None)
    if models and hasattr(models, name):
        return getattr(models, name)
    return None


def _proxy_request_headers(request):
    hop_by_hop = {
        "connection",
        "keep-alive",
        "proxy-authenticate",
        "proxy-authorization",
        "te",
        "trailers",
        "transfer-encoding",
        "upgrade",
    }
    headers = {}
    for key, value in request.headers.items():
        lower = key.lower()
        if lower in hop_by_hop or lower == "host":
            continue
        headers[key] = value
    ingress_host = getattr(settings, "AGENT_GUI_INGRESS_HOST", "").strip()
    if ingress_host:
        headers["Host"] = ingress_host
    headers.setdefault("X-Forwarded-Proto", "https" if request.is_secure() else "http")
    headers.setdefault("X-Forwarded-Host", request.get_host())
    remote_addr = request.META.get("REMOTE_ADDR")
    if remote_addr:
        headers.setdefault("X-Forwarded-For", remote_addr)
    return headers


def _stream_response(response):
    while True:
        chunk = response.read(64 * 1024)
        if not chunk:
            break
        yield chunk


def _resolve_pod(v1, pod_name: str, namespace: str, allow_cross_namespace: bool = False):
    try:
        return v1.read_namespaced_pod(name=pod_name, namespace=namespace), namespace
    except Exception as exc:
        if getattr(exc, "status", None) != 404 or not allow_cross_namespace:
            raise
        original_exc = exc

    try:
        pods = v1.list_pod_for_all_namespaces(field_selector=f"metadata.name={pod_name}")
    except Exception as exc:
        if getattr(exc, "status", None) in {401, 403}:
            raise original_exc
        raise
    if pods.items:
        pod = pods.items[0]
        return pod, pod.metadata.namespace
    raise original_exc


def _format_pod_line(pod) -> str:
    name = getattr(pod.metadata, "name", "unknown")
    phase = getattr(pod.status, "phase", None) or "unknown"
    pod_ip = getattr(pod.status, "pod_ip", None) or "none"
    node = getattr(pod.spec, "node_name", None) or "unknown"
    start_time = getattr(pod.status, "start_time", None)
    start_str = start_time.isoformat() if start_time else "unknown"
    return f"- {name} | phase={phase} | ip={pod_ip} | node={node} | start={start_str}"


def _pod_debug_snapshot(v1, namespace: str, label_selector: str, limit: int = 5) -> str:
    try:
        pods = v1.list_namespaced_pod(namespace=namespace, label_selector=label_selector).items
    except Exception as exc:
        return f"Failed to list pods in namespace {namespace} with selector {label_selector}: {exc}"
    if not pods:
        return f"No pods matched selector {label_selector} in namespace {namespace}."
    pods_sorted = sorted(
        pods,
        key=lambda item: item.status.start_time or datetime.min.replace(tzinfo=dt_timezone.utc),
        reverse=True,
    )
    lines = [_format_pod_line(pod) for pod in pods_sorted[:limit]]
    return "Recent pods:\n" + "\n".join(lines)


def _kube_context_snapshot() -> str:
    try:
        from kubernetes import client as k8s_client
    except Exception as exc:  # pragma: no cover - import guarded elsewhere
        return f"Kube client unavailable: {exc}"
    try:
        config = k8s_client.Configuration.get_default_copy()
    except Exception as exc:
        return f"Kube config unavailable: {exc}"
    kubeconfig = os.environ.get("KUBECONFIG", "").strip() or "not set"
    host = getattr(config, "host", "") or "unknown"
    verify_ssl = getattr(config, "verify_ssl", None)
    ssl_ca = getattr(config, "ssl_ca_cert", None) or "none"
    proxy = getattr(config, "proxy", None) or "none"
    return (
        "Kube context:\n"
        f"- KUBECONFIG: {kubeconfig}\n"
        f"- Host: {host}\n"
        f"- Verify SSL: {verify_ssl}\n"
        f"- CA cert: {ssl_ca}\n"
        f"- Proxy: {proxy}"
    )


def _format_api_exception(exc) -> str:
    status = getattr(exc, "status", "unknown")
    reason = getattr(exc, "reason", "unknown")
    body = getattr(exc, "body", "")
    if body:
        body = str(body).strip()
    return f"status={status} reason={reason} body={body}"


def _wait_for_agent_pod(v1, namespace: str, deployment_name: str, owner_username: str, timeout_seconds: int = 15):
    label_selector = f"app=openclaw-agent,owner={owner_username},deployment={deployment_name}"
    deadline = time.monotonic() + timeout_seconds
    last_pod = None
    while time.monotonic() < deadline:
        pods = v1.list_namespaced_pod(namespace=namespace, label_selector=label_selector)
        if pods.items:
            for pod in pods.items:
                last_pod = pod
                if pod.status and pod.status.pod_ip:
                    return pod
        time.sleep(2)
    return last_pod


def _coerce_openclaw_json(payload: str):
    payload = (payload or "").strip()
    if not payload:
        return None
    try:
        return json.loads(payload)
    except ValueError:
        return None


def _find_first_list(payload):
    if isinstance(payload, list):
        return payload
    if isinstance(payload, dict):
        for key in ("channels", "items", "data", "results", "accounts"):
            value = payload.get(key)
            if isinstance(value, list):
                return value
        for value in payload.values():
            if isinstance(value, list):
                return value
    return []


def _normalize_channel_choices(capabilities_payload):
    items = _find_first_list(capabilities_payload)
    normalized = []
    seen = set()
    for item in items:
        if isinstance(item, dict):
            raw_value = (
                item.get("type")
                or item.get("channel")
                or item.get("name")
                or item.get("id")
            )
            label = item.get("label") or item.get("title") or raw_value
        else:
            raw_value = item
            label = item
        if not raw_value:
            continue
        value = str(raw_value).strip()
        if not value or value in seen:
            continue
        seen.add(value)
        normalized.append((value, str(label or value)))
    return normalized


def _normalize_channels(list_payload, status_payload):
    items = _find_first_list(list_payload)
    status_map = status_payload if isinstance(status_payload, dict) else {}
    rows = []

    for item in items:
        if isinstance(item, dict):
            channel_type = item.get("type") or item.get("channel") or item.get("provider") or "unknown"
            channel_id = item.get("id") or item.get("name") or item.get("slug") or item.get("accountId") or channel_type
            display_name = item.get("name") or item.get("displayName") or item.get("label") or channel_id
            account_id = item.get("accountId") or item.get("account") or item.get("identifier") or ""
            metadata = {key: value for key, value in item.items() if key not in {"id", "name", "displayName", "label", "type", "channel", "provider", "accountId", "account", "identifier"}}
        else:
            channel_type = "unknown"
            channel_id = str(item)
            display_name = str(item)
            account_id = ""
            metadata = {}

        status_value = None
        if isinstance(status_map, dict):
            status_value = (
                status_map.get(channel_id)
                or status_map.get(display_name)
                or status_map.get(channel_type)
            )
        if isinstance(status_value, dict):
            status_label = (
                status_value.get("status")
                or status_value.get("state")
                or status_value.get("health")
                or json.dumps(status_value)
            )
        else:
            status_label = status_value

        rows.append(
            {
                "id": channel_id,
                "display_name": display_name,
                "type": channel_type,
                "account_id": account_id,
                "status": status_label or "Unknown",
                "metadata": metadata,
            }
        )

    return rows


def _agent_clawedin_base_url(pod) -> str:
    pod_ip = getattr(getattr(pod, "status", None), "pod_ip", "") or ""
    if not pod_ip:
        raise RuntimeError("Agent pod does not have an IP address yet.")
    port = int(getattr(settings, "AGENT_CLAWEDIN_CHANNEL_PORT", CLAWEDIN_CHANNEL_DEFAULT_PORT))
    return f"http://{pod_ip}:{port}"


def _agent_clawedin_request(
    pod,
    *,
    path: str,
    method: str = "GET",
    token: str = "",
    payload: dict | None = None,
    timeout: int | float | None = None,
):
    base_url = _agent_clawedin_base_url(pod)
    request_path = path if path.startswith("/") else f"/{path}"
    headers = {
        "Accept": "application/json",
        "User-Agent": "clawedin/1.0",
    }
    if token:
        headers["Authorization"] = f"Bearer {token}"
    data = None
    if payload is not None:
        headers["Content-Type"] = "application/json"
        data = json.dumps(payload).encode("utf-8")
    request = Request(
        f"{base_url}{request_path}",
        data=data,
        headers=headers,
        method=method.upper(),
    )
    request_timeout = timeout
    if request_timeout is None:
        request_timeout = int(getattr(settings, "AGENT_CLAWEDIN_CHANNEL_TIMEOUT", 120))
    try:
        with urlopen(request, timeout=request_timeout) as response:
            body = response.read().decode("utf-8")
            if not body:
                return response.status, {}
            try:
                return response.status, json.loads(body)
            except ValueError:
                return response.status, {
                    "error": "Agent returned a non-JSON response.",
                    "raw_body": body[:1000],
                }
    except HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        try:
            payload = json.loads(body) if body else {}
        except ValueError:
            payload = {"error": body or exc.reason}
        return exc.code, payload
    except (TimeoutError, socket.timeout) as exc:
        raise RuntimeError(
            f"Clawedin channel timed out after {request_timeout} seconds."
        ) from exc
    except URLError as exc:
        raise RuntimeError(f"Could not reach Clawedin channel: {exc}") from exc


def _agent_clawedin_health(pod, token: str = "") -> dict:
    try:
        status_code, payload = _agent_clawedin_request(
            pod,
            path="/healthz",
            method="GET",
            token=token,
        )
    except RuntimeError as exc:
        return {
            "ok": False,
            "label": "Gateway unreachable",
            "detail": str(exc),
        }
    detail = payload.get("accountId") or payload.get("channel") or "Waiting for channel startup."
    return {
        "ok": status_code == 200 and bool(payload.get("ok")),
        "label": "Currently working" if status_code == 200 and bool(payload.get("ok")) else "Gateway offline",
        "detail": detail,
    }


def _should_query_agent_channels(gateway_health: dict) -> bool:
    """Avoid expensive OpenClaw channel CLI calls while the gateway is still booting."""
    return bool(gateway_health.get("ok"))


def _agent_dashboard_metrics(user, channel_rows, status_window_start, status_window_end):
    skill_metrics = SkillPageRequestMetric.objects.filter(
        user=user,
        date__range=(status_window_start, status_window_end),
    )
    skill_totals = skill_metrics.aggregate(
        skill_calls=Sum(
            "total_calls",
            filter=Q(source=SkillPageRequestMetric.SOURCE_SKILL_MD),
        ),
        skill_successes=Sum(
            "success_calls",
            filter=Q(source=SkillPageRequestMetric.SOURCE_SKILL_MD),
        ),
        skill_failures=Sum(
            "error_calls",
            filter=Q(source=SkillPageRequestMetric.SOURCE_SKILL_MD),
        ),
        get_calls=Sum(
            "total_calls",
            filter=Q(source=SkillPageRequestMetric.SOURCE_SKILL_MD, method="GET"),
        ),
        post_calls=Sum(
            "total_calls",
            filter=Q(source=SkillPageRequestMetric.SOURCE_SKILL_MD, method="POST"),
        ),
        prompt_turns=Sum(
            "total_calls",
            filter=Q(source=SkillPageRequestMetric.SOURCE_AGENT_DASHBOARD),
        ),
        prompt_replies=Sum(
            "success_calls",
            filter=Q(source=SkillPageRequestMetric.SOURCE_AGENT_DASHBOARD),
        ),
    )
    prompt_turns_total = skill_totals["prompt_turns"] or 0
    prompt_replies_total = skill_totals["prompt_replies"] or 0
    prompt_success_rate = (
        round((prompt_replies_total / prompt_turns_total) * 100)
        if prompt_turns_total
        else 0
    )
    tracked_endpoints_total = (
        skill_metrics.filter(source=SkillPageRequestMetric.SOURCE_SKILL_MD)
        .values("normalized_path", "method")
        .distinct()
        .count()
    )
    metrics = [
        {
            "key": "prompt_turns",
            "label": "Prompts sent",
            "value": str(prompt_turns_total),
            "delta": "Past 7 days",
            "description": "Messages sent from this dashboard into the live agent gateway during the last 7 days.",
        },
        {
            "key": "agent_replies",
            "label": "Replies received",
            "value": str(prompt_replies_total),
            "delta": f"{prompt_success_rate}% reply rate",
            "description": "Successful replies returned by the live agent gateway during the last 7 days.",
        },
        {
            "key": "linked_channels",
            "label": "Connected channels",
            "value": str(len(channel_rows)),
            "delta": "Live runtime count",
            "description": "OpenClaw channels currently configured in the running agent.",
        },
        {
            "key": "tracked_api_calls",
            "label": "Total API requests",
            "value": str(skill_totals["skill_calls"] or 0),
            "delta": "Past 7 days",
            "description": "Requests made to documented agent-facing API routes during the last 7 days.",
        },
        {
            "key": "skill_failures",
            "label": "API errors",
            "value": str(skill_totals["skill_failures"] or 0),
            "delta": "HTTP 4xx/5xx",
            "description": "Tracked API requests that returned an HTTP error during the last 7 days.",
        },
    ]
    top_skill_routes = list(
        skill_metrics.filter(source=SkillPageRequestMetric.SOURCE_SKILL_MD)
        .values("normalized_path", "method")
        .annotate(total_calls=Sum("total_calls"))
        .order_by("-total_calls", "normalized_path")[:4]
    )
    metric_lookup = {
        "tracked_api_calls": _dashboard_card(
            "Total API requests",
            skill_totals["skill_calls"] or 0,
            "Current window",
            "All requests made to documented agent-facing API routes in the current reporting window.",
            "tracked_api_calls",
        ),
        "successful_api_calls": _dashboard_card(
            "Successful API requests",
            skill_totals["skill_successes"] or 0,
            "HTTP 2xx/3xx",
            "Tracked API requests that finished with a successful HTTP status code.",
            "successful_api_calls",
        ),
        "failed_api_calls": _dashboard_card(
            "Failed API requests",
            skill_totals["skill_failures"] or 0,
            "HTTP 4xx/5xx",
            "Tracked API requests that returned an HTTP error status code.",
            "failed_api_calls",
        ),
        "get_calls": _dashboard_card(
            "GET requests",
            skill_totals["get_calls"] or 0,
            "Current window",
            "Read-only API requests recorded in the current reporting window.",
            "get_calls",
        ),
        "post_calls": _dashboard_card(
            "POST requests",
            skill_totals["post_calls"] or 0,
            "Current window",
            "Write or action API requests recorded in the current reporting window.",
            "post_calls",
        ),
        "tracked_endpoints": _dashboard_card(
            "Active API routes",
            tracked_endpoints_total,
            "Current window",
            "Unique method-and-path combinations that received traffic in the current reporting window.",
            "tracked_endpoints",
        ),
        "prompt_turns": _dashboard_card(
            "Prompts sent",
            prompt_turns_total,
            "Past 7 days",
            "Messages sent from this dashboard to the live agent gateway during the last 7 days.",
            "prompt_turns",
        ),
        "agent_replies": _dashboard_card(
            "Replies received",
            prompt_replies_total,
            f"{prompt_success_rate}% reply rate",
            "Successful replies returned to this dashboard during the last 7 days.",
            "agent_replies",
        ),
        "linked_channels": _dashboard_card(
            "Connected channels",
            len(channel_rows),
            "Live runtime count",
            "OpenClaw channels currently configured in the running agent.",
            "linked_channels",
        ),
    }
    selected_item_keys = _sanitize_agent_dashboard_item_keys(getattr(user, "agent_dashboard_items", []))
    dashboard_cards = []
    for item_key in selected_item_keys:
        if item_key.startswith("top_route_"):
            try:
                route_index = int(item_key.rsplit("_", 1)[-1]) - 1
            except (TypeError, ValueError):
                route_index = -1
            route = top_skill_routes[route_index] if 0 <= route_index < len(top_skill_routes) else None
            route_label, route_description = _dashboard_top_route_copy(route_index + 1, route)
            if route:
                dashboard_cards.append(
                    _dashboard_card(
                        route_label,
                        route["total_calls"],
                        f'{route["method"]} · current window',
                        route_description,
                        item_key,
                    )
                )
            else:
                dashboard_cards.append(
                    _dashboard_card(
                        route_label,
                        0,
                        "No traffic yet",
                        route_description,
                        item_key,
                    )
                )
            continue
        card = metric_lookup.get(item_key)
        if card:
            dashboard_cards.append(card)
    return metrics, top_skill_routes, dashboard_cards, AGENT_DASHBOARD_ITEM_OPTIONS, selected_item_keys


def _agent_dashboard_runtime_snapshot(request, pod_name: str, namespace: str, deployment_record):
    channel_rows = []
    channels_raw = ""
    status_raw = ""
    capabilities_raw = ""
    channel_choices = None
    gateway_health = {
        "ok": False,
        "label": "Gateway offline",
        "detail": "Waiting for channel startup.",
    }
    runtime_error = ""

    try:
        from kubernetes import client

        load_kube_config()
        v1 = client.CoreV1Api()
        allow_cross_namespace = request.user.is_staff or request.user.is_superuser
        pod, namespace = _resolve_pod(v1, pod_name, namespace, allow_cross_namespace)

        gateway_health = _agent_clawedin_health(
            pod,
            token=getattr(deployment_record, "web_auth_token", "") or "",
        )
        if _should_query_agent_channels(gateway_health):
            capabilities_result = _run_openclaw_cli(v1, namespace, pod_name, ["channels", "capabilities", "--json"])
            capabilities_raw = capabilities_result["output"]
            capabilities_payload = (
                _coerce_openclaw_json(capabilities_result["output"]) if capabilities_result["ok"] else None
            )
            channel_choices = _normalize_channel_choices(capabilities_payload)

            channels_result = _run_openclaw_cli(v1, namespace, pod_name, ["channels", "list", "--json"])
            status_result = _run_openclaw_cli(v1, namespace, pod_name, ["channels", "status", "--json"])
            channels_raw = channels_result["output"]
            status_raw = status_result["output"]
            channels_payload = _coerce_openclaw_json(channels_result["output"]) if channels_result["ok"] else None
            status_payload = _coerce_openclaw_json(status_result["output"]) if status_result["ok"] else None
            channel_rows = _normalize_channels(channels_payload, status_payload)

            if not channels_result["ok"]:
                runtime_error = channels_result["output"] or "Could not fetch channels from the agent."
            elif not status_result["ok"]:
                runtime_error = status_result["output"] or "Could not fetch channel status from the agent."
    except Exception as exc:  # pragma: no cover - depends on kube setup
        logger.exception("Failed to load OpenClaw runtime snapshot for pod %s", pod_name)
        runtime_error = str(exc)

    return {
        "channel_rows": channel_rows,
        "channels_raw": channels_raw,
        "status_raw": status_raw,
        "capabilities_raw": capabilities_raw,
        "channel_choices": channel_choices or [],
        "gateway_health": gateway_health,
        "runtime_error": runtime_error,
    }


def _dashboard_bootstrap_prompt(user, deployment_name: str, bearer_secret_name: str) -> str:
    display_name = user.display_name or user.username
    return (
        f"Clawedin bootstrap for user {display_name}.\n\n"
        "A Clawedin user bearer token is available inside this container for authenticated Clawedin requests. "
        "Use it to pull the signed-in user's profile so you can identify the user before taking personalized actions.\n\n"
        f"Bearer token secret name: {bearer_secret_name or 'agent-user-bearer'}\n"
        "Env var: CLAWEDIN_USER_BEARER_TOKEN\n"
        "File var: CLAWEDIN_USER_BEARER_TOKEN_FILE\n"
        "File: /var/run/secrets/clawedin-user-bearer/token\n\n"
        "Use this token only for Clawedin-authenticated user/profile requests. "
        "Start by fetching the user's Clawedin profile to identify who they are."
    )


def _maybe_queue_dashboard_bootstrap_turn(user, deployment_record, pod_name: str, namespace: str, gateway_health: dict) -> bool:
    if not deployment_record or not getattr(deployment_record, "web_auth_token", ""):
        return False
    if not gateway_health.get("ok"):
        return False
    if not agent_deployment_has_dashboard_bootstrap_field():
        return False

    db_alias = getattr(getattr(deployment_record, "_state", None), "db", None) or "default"
    with transaction.atomic(using=db_alias):
        deployment = (
            AgentDeployment.objects.using(db_alias).select_for_update().filter(pk=deployment_record.pk).first()
        )
        if not deployment or deployment.dashboard_bootstrap_sent_at:
            return False

        sender_name = "Clawedin bootstrap"
        bearer_secret_name = agent_user_bearer_secret_name_for_deployment(
            deployment.deployment_name,
            user.id,
        )
        conversation_id = _dashboard_conversation_id(user, deployment.deployment_name, pod_name)
        prompt = _dashboard_bootstrap_prompt(user, deployment.deployment_name, bearer_secret_name)
        turn = AgentDashboardTurn.objects.create(
            user=user,
            deployment=deployment,
            pod_name=pod_name,
            namespace=namespace,
            conversation_id=conversation_id,
            prompt_text=prompt,
            prompt_author=sender_name,
            status=AgentDashboardTurn.STATUS_QUEUED,
            status_detail="Queued initial Clawedin bootstrap prompt.",
        )
        deployment.dashboard_bootstrap_sent_at = timezone.now()
        deployment.save(update_fields=["dashboard_bootstrap_sent_at", "updated_at"])

    worker = threading.Thread(
        target=_run_agent_dashboard_turn,
        kwargs={
            "turn_id": turn.id,
            "user_id": user.id,
            "username": user.username,
            "pod_name": pod_name,
            "namespace": namespace,
            "deployment_record_id": deployment.id,
            "conversation_id": conversation_id,
            "prompt": prompt,
            "sender_name": sender_name,
        },
        daemon=True,
    )
    worker.start()
    return True


def _serialize_dashboard_turn(turn: AgentDashboardTurn) -> dict:
    return {
        "id": str(turn.id),
        "conversationId": turn.conversation_id,
        "promptText": turn.prompt_text,
        "promptAuthor": turn.prompt_author or turn.user.display_name or turn.user.username,
        "status": turn.status,
        "statusDetail": turn.status_detail or "",
        "responseText": turn.response_text or "",
        "responseError": turn.response_error or "",
        "sessionKey": turn.session_key or "",
        "agentId": turn.agent_id or "",
        "createdAt": timezone.localtime(turn.created_at).strftime("%b %d, %I:%M %p"),
        "updatedAt": turn.updated_at.isoformat(),
        "attachments": [_serialize_dashboard_attachment(item) for item in turn.attachments.all()],
    }


def _recent_dashboard_turns(user, pod_name: str, limit: int = 25):
    turns = list(
        AgentDashboardTurn.objects.filter(user=user, pod_name=pod_name)
        .select_related("user")
        .prefetch_related("attachments")
        .order_by("-created_at")[:limit]
    )
    turns.reverse()
    return [_serialize_dashboard_turn(turn) for turn in turns]


def _pending_dashboard_attachments(user, pod_name: str):
    attachments = AgentDashboardAttachment.objects.filter(
        user=user,
        pod_name=pod_name,
        turn__isnull=True,
    ).order_by("created_at")
    return [_serialize_dashboard_attachment(item) for item in attachments]


def _run_agent_dashboard_turn(
    *,
    turn_id,
    user_id: int,
    username: str,
    pod_name: str,
    namespace: str,
    deployment_record_id: int | None,
    conversation_id: str,
    prompt: str,
    sender_name: str,
):
    close_old_connections()
    try:
        turn = (
            AgentDashboardTurn.objects.select_related("user")
            .prefetch_related("attachments")
            .get(pk=turn_id, user_id=user_id)
        )
    except AgentDashboardTurn.DoesNotExist:
        close_old_connections()
        return

    try:
        turn.status = AgentDashboardTurn.STATUS_RUNNING
        turn.status_detail = "Connecting to the agent gateway..."
        turn.save(update_fields=["status", "status_detail", "updated_at"])

        from kubernetes import client

        load_kube_config()
        v1 = client.CoreV1Api()
        pod, resolved_namespace = _resolve_pod(v1, pod_name, namespace, False)
        if (
            pod
            and pod.metadata
            and pod.metadata.labels
            and (
                pod.metadata.labels.get("app") != "openclaw-agent"
                or pod.metadata.labels.get("owner") != username
            )
        ):
            raise RuntimeError("You do not have permission to use this agent.")

        deployment_record = (
            AgentDeployment.objects.filter(pk=deployment_record_id, user_id=user_id).first()
            if deployment_record_id
            else None
        )
        if not deployment_record or not deployment_record.web_auth_token:
            raise RuntimeError("Agent web auth token is not configured.")

        turn.namespace = resolved_namespace
        turn.status_detail = "Waiting for the agent reply..."
        turn.save(update_fields=["namespace", "status_detail", "updated_at"])

        prompt_payload = prompt
        attachments = list(turn.attachments.all())
        if attachments:
            attachment_lines = [
                "Attached files are already uploaded into the shared Clawedin workspace mount.",
                "Use these exact file paths when you need to inspect, reference, or process them:",
            ]
            for attachment in attachments:
                attachment_lines.append(f"- {attachment.original_name} -> {attachment.agent_path}")
            prompt_payload = f"{prompt}\n\n" + "\n".join(attachment_lines)

        status_code, response_payload = _agent_clawedin_request(
            pod,
            path=getattr(settings, "AGENT_CLAWEDIN_CHANNEL_REQUEST_PATH", CLAWEDIN_CHANNEL_DEFAULT_PATH),
            method="POST",
            token=deployment_record.web_auth_token,
            payload={
                "text": prompt_payload,
                "conversationId": conversation_id,
                "chatType": "direct",
                "senderId": str(user_id),
                "senderName": sender_name,
                "skills": [],
            },
        )
        if status_code >= 400:
            raise RuntimeError(response_payload.get("error") or "The agent gateway rejected the request.")

        messages_payload = response_payload.get("messages")
        if not isinstance(messages_payload, list):
            messages_payload = []
        reply_text = response_payload.get("text") or "\n\n".join(
            message.get("text") or ""
            for message in messages_payload
            if isinstance(message, dict)
        ).strip()
        if not reply_text and response_payload.get("error"):
            raise RuntimeError(response_payload.get("error"))

        turn.status = AgentDashboardTurn.STATUS_COMPLETED
        turn.status_detail = "Reply received."
        turn.response_text = reply_text or "The gateway accepted the turn, but no text reply was returned."
        turn.response_error = ""
        turn.session_key = response_payload.get("sessionKey") or ""
        turn.agent_id = response_payload.get("agentId") or ""
        turn.completed_at = timezone.now()
        turn.save(
            update_fields=[
                "status",
                "status_detail",
                "response_text",
                "response_error",
                "session_key",
                "agent_id",
                "completed_at",
                "updated_at",
            ]
        )
    except Exception as exc:
        turn.status = AgentDashboardTurn.STATUS_FAILED
        turn.status_detail = "Turn failed."
        turn.response_error = str(exc)
        turn.completed_at = timezone.now()
        turn.save(
            update_fields=[
                "status",
                "status_detail",
                "response_error",
                "completed_at",
                "updated_at",
            ]
        )
    finally:
        close_old_connections()


def _exec_agent_shell_command(v1, namespace: str, pod_name: str, shell_command: str) -> dict:
    from kubernetes import stream

    wrapped_command = (
        f"{shell_command} 2>&1\n"
        "status=$?\n"
        "printf '\\n__CLAWEDIN_EXIT_CODE__=%s' \"$status\"\n"
    )
    output = stream.stream(
        v1.connect_get_namespaced_pod_exec,
        pod_name,
        namespace,
        container="openclaw-agent",
        command=["/bin/sh", "-lc", wrapped_command],
        stderr=True,
        stdin=False,
        stdout=True,
        tty=False,
    )
    marker = "__CLAWEDIN_EXIT_CODE__="
    stdout = output or ""
    exit_code = 1
    if marker in stdout:
        stdout, _, tail = stdout.rpartition(marker)
        try:
            exit_code = int(tail.strip().splitlines()[0])
        except (TypeError, ValueError, IndexError):
            exit_code = 1
    return {
        "ok": exit_code == 0,
        "exit_code": exit_code,
        "output": stdout.strip(),
    }


def _run_openclaw_cli(v1, namespace: str, pod_name: str, args: list[str]) -> dict:
    command = shlex.join(["node", "/app/openclaw.mjs", *args])
    return _exec_agent_shell_command(v1, namespace, pod_name, command)


def _ensure_agent_gui_resources(client_module, v1, networking, namespace: str, pod, owner_username: str):
    agent_port = int(getattr(settings, "AGENT_GUI_PORT", 18789))
    proxy_port = int(getattr(settings, "AGENT_GUI_PROXY_PORT", 18790))
    resource_name = _gui_resource_name_for_pod(pod) or pod.metadata.name
    service_name = gui_service_name(resource_name)
    ingress_name = gui_ingress_name(resource_name)
    ingress_class = getattr(settings, "AGENT_GUI_INGRESS_CLASS", "") or None
    labels = {
        "app": "openclaw-agent",
        "owner": owner_username,
        "pod": pod.metadata.name,
        "gui-resource": resource_name,
    }

    service_body = client_module.V1Service(
        metadata=client_module.V1ObjectMeta(name=service_name, labels=labels),
        spec=client_module.V1ServiceSpec(
            ports=[
                client_module.V1ServicePort(
                    name="gui",
                    port=proxy_port,
                    target_port=proxy_port,
                    protocol="TCP",
                )
            ],
        ),
    )
    try:
        _upsert_service(v1, namespace, service_body)
    except Exception as exc:
        raise RuntimeError(f"Failed to upsert service {service_name}: {exc}") from exc

    pod_ip = pod.status.pod_ip
    if not pod_ip:
        logger.info(
            "Agent GUI pod has no IP yet; skipping endpoints for %s in %s",
            pod.metadata.name,
            namespace,
        )
    else:
        endpoint_port_cls = _model_class(client_module, "V1EndpointPort")
        endpoint_port = (
            endpoint_port_cls(name="gui", port=proxy_port, protocol="TCP")
            if endpoint_port_cls
            else {"name": "gui", "port": proxy_port, "protocol": "TCP"}
        )

        endpoints_body = client_module.V1Endpoints(
            metadata=client_module.V1ObjectMeta(name=service_name, labels=labels),
            subsets=[
                client_module.V1EndpointSubset(
                    addresses=[
                        client_module.V1EndpointAddress(
                            ip=pod_ip,
                            target_ref=client_module.V1ObjectReference(
                                kind="Pod",
                                name=pod.metadata.name,
                                namespace=namespace,
                            ),
                        )
                    ],
                    ports=[
                        endpoint_port
                    ],
                )
            ],
        )
        try:
            _upsert_endpoints(v1, namespace, endpoints_body)
        except Exception as exc:
            raise RuntimeError(f"Failed to upsert endpoints for {service_name}: {exc}") from exc

    host = _gui_host_for_pod(resource_name)
    path_prefix = _gui_path_prefix()
    annotations = {}
    path = f"{path_prefix}/{resource_name}(/|$)(.*)"
    path_type = "ImplementationSpecific"
    subdomain_mode = bool(getattr(settings, "AGENT_GUI_HOST_SUFFIX", "").strip())
    if ingress_class == "traefik":
        middleware_name = gui_middleware_name(resource_name)
        if subdomain_mode:
            middleware_body = {
                "apiVersion": "traefik.io/v1alpha1",
                "kind": "Middleware",
                "metadata": {
                    "name": middleware_name,
                    "namespace": namespace,
                    "labels": labels,
                },
                "spec": {
                    "headers": {
                        "customResponseHeaders": {
                            "Content-Security-Policy": (
                                "default-src 'self'; "
                                "base-uri 'none'; "
                                "object-src 'none'; "
                                "frame-ancestors 'none'; "
                                "script-src 'self'; "
                                "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com; "
                                "img-src 'self' data: https:; "
                                "font-src 'self' https://fonts.gstatic.com; "
                                "connect-src 'self' ws: wss:"
                            ),
                        }
                    }
                },
            }
        else:
            middleware_body = {
                "apiVersion": "traefik.io/v1alpha1",
                "kind": "Middleware",
                "metadata": {
                    "name": middleware_name,
                    "namespace": namespace,
                    "labels": labels,
                },
                "spec": {
                    "stripPrefix": {
                        "prefixes": [f"{path_prefix}/{resource_name}"],
                    }
                },
            }
        custom_api = client_module.CustomObjectsApi()
        middleware_groups = (
            ("traefik.io", "v1alpha1"),
            ("traefik.containo.us", "v1alpha1"),
        )
        last_error = None
        for group, version in middleware_groups:
            middleware_body["apiVersion"] = f"{group}/{version}"
            try:
                _upsert_custom_object(
                    custom_api,
                    group,
                    version,
                    "middlewares",
                    namespace,
                    middleware_body,
                )
                last_error = None
                break
            except Exception as exc:
                last_error = exc
        if last_error:
            raise RuntimeError(f"Failed to upsert traefik middleware {middleware_name}: {last_error}") from last_error
        annotations["traefik.ingress.kubernetes.io/router.middlewares"] = f"{namespace}-{middleware_name}@kubernetescrd"
        annotations["traefik.ingress.kubernetes.io/router.entrypoints"] = "websecure"
        annotations["traefik.ingress.kubernetes.io/router.tls"] = "true"
        cert_resolver = getattr(settings, "AGENT_GUI_TLS_RESOLVER", "").strip()
        if cert_resolver:
            annotations["traefik.ingress.kubernetes.io/router.tls.certresolver"] = cert_resolver
        if subdomain_mode:
            path = "/"
            path_type = "Prefix"
        else:
            path = f"{path_prefix}/{resource_name}"
            path_type = "Prefix"
    else:
        annotations.update(
            {
                "nginx.ingress.kubernetes.io/use-regex": "true",
                "nginx.ingress.kubernetes.io/rewrite-target": "/$2",
                "nginx.ingress.kubernetes.io/proxy-read-timeout": "3600",
                "nginx.ingress.kubernetes.io/proxy-send-timeout": "3600",
                "nginx.ingress.kubernetes.io/configuration-snippet": (
                    "more_set_headers \"X-Frame-Options: SAMEORIGIN\";\n"
                    "more_set_headers \"Content-Security-Policy: frame-ancestors 'self'\";\n"
                ),
            }
        )
    ingress_body = client_module.V1Ingress(
        metadata=client_module.V1ObjectMeta(
            name=ingress_name,
            labels=labels,
            annotations=annotations,
        ),
        spec=client_module.V1IngressSpec(
            ingress_class_name=ingress_class,
            rules=[
                client_module.V1IngressRule(
                    host=host,
                    http=client_module.V1HTTPIngressRuleValue(
                        paths=[
                            client_module.V1HTTPIngressPath(
                                path=path,
                                path_type=path_type,
                                backend=client_module.V1IngressBackend(
                                    service=client_module.V1IngressServiceBackend(
                                        name=service_name,
                                        port=client_module.V1ServiceBackendPort(number=proxy_port),
                                    )
                                ),
                            )
                        ]
                    ),
                )
            ],
        ),
    )
    try:
        _upsert_ingress(networking, namespace, ingress_body)
    except Exception as exc:
        raise RuntimeError(f"Failed to upsert ingress {ingress_name}: {exc}") from exc


def _delete_agent_gui_resources(client_module, v1, networking, namespace: str, pod_name: str, resource_name: str | None = None):
    target_name = resource_name or pod_name
    service_name = gui_service_name(target_name)
    ingress_name = gui_ingress_name(target_name)
    ingress_class = getattr(settings, "AGENT_GUI_INGRESS_CLASS", "") or None
    middleware_name = gui_middleware_name(target_name)

    for delete_fn, name in (
        (networking.delete_namespaced_ingress, ingress_name),
        (v1.delete_namespaced_endpoints, service_name),
        (v1.delete_namespaced_service, service_name),
    ):
        try:
            delete_fn(name, namespace)
        except Exception as exc:
            if getattr(exc, "status", None) != 404:
                raise

    if ingress_class == "traefik":
        custom_api = client_module.CustomObjectsApi()
        for group in ("traefik.io", "traefik.containo.us"):
            _delete_custom_object(
                custom_api,
                group,
                "v1alpha1",
                "middlewares",
                namespace,
                middleware_name,
            )


def _is_admin_user(user):
    return user.is_authenticated and (user.is_staff or user.is_superuser)


def _get_rpc_value(response):
    if hasattr(response, "value"):
        return response.value
    return response.get("result", {}).get("value")


def _get_account_data_bytes(account_value):
    if account_value is None:
        return None
    data = getattr(account_value, "data", account_value.get("data") if isinstance(account_value, dict) else None)
    if hasattr(data, "data"):
        data = data.data
    if isinstance(data, (list, tuple)):
        data = data[0]
    if isinstance(data, bytes):
        return data
    if isinstance(data, str):
        return base64.b64decode(data)
    return None


def _solana_mint_decimals(client, mint_pubkey):
    mint_resp = client.get_account_info(mint_pubkey)
    mint_value = _get_rpc_value(mint_resp)
    mint_data = _get_account_data_bytes(mint_value)
    if not mint_data:
        raise ValueError("Mint account not found.")
    mint_info = MINT_LAYOUT.parse(mint_data)
    return mint_info.decimals

SERVICE_PLANS = {
    User.SERVICE_FREE: {
        "name": "Clawedin Basic",
        "headline": "Expose your agent. Let it work.",
        "price_label": "$12.00 / mo",
        "features": [
            "AI & human service profiles",
            "Basic agent runtime (shared)",
            "Public agent actions & posts",
            "Messaging between humans & agents",
            "Community support",
        ],
        "settings_price_key": "STRIPE_PRICE_ID_FREE",
    },
    User.SERVICE_PRO: {
        "name": "Clawedin Pro",
        "headline": "More power. More reach. More work done.",
        "price_label": "$19.00 / mo",
        "badge": "Premium+",
        "features": [
            "Everything in Free",
            "Priority agent execution",
            "Expanded agent action limits",
            "Smart inbox & task summaries",
            "Service analytics (usage, reach, impact)",
            "Priority support",
        ],
        "settings_price_key": "STRIPE_PRICE_ID_PRO",
    },
    User.SERVICE_BUSINESS: {
        "name": "Clawedin Business",
        "headline": "Business-grade automation for teams.",
        "price_label": "$40.00 / mo",
        "features": [
            "Everything in Pro",
            "Multiple active AI agents",
            "Persistent agent services (24/7)",
            "Agent workflows (email, calendar, ops, automation)",
            "API & webhook access",
            "Business-grade support",
        ],
        "settings_price_key": "STRIPE_PRICE_ID_BUSINESS",
    },
}

AGENT_LIMITS = {
    User.SERVICE_FREE: 1,
    User.SERVICE_PRO: 2,
    User.SERVICE_BUSINESS: 3,
}


def _subscription_active(user: User) -> bool:
    if user.service_tier and user.service_tier != User.SERVICE_NONE:
        return True
    return user.stripe_subscription_status in {"active", "trialing", "past_due"}


def _agent_limit_for_user(user: User) -> int:
    if not _subscription_active(user):
        return 0
    return AGENT_LIMITS.get(user.service_tier, 0)


def _stripe_is_configured() -> bool:
    return bool(
        STRIPE_SDK_AVAILABLE
        and settings.STRIPE_SECRET_KEY
        and settings.STRIPE_PUBLISHABLE_KEY
    )


def _price_id_for_tier(tier: str) -> str:
    plan = SERVICE_PLANS.get(tier)
    if not plan:
        return ""
    return getattr(settings, plan["settings_price_key"], "")


def _tier_for_price_id(price_id: str) -> str:
    if not price_id:
        return User.SERVICE_NONE
    for tier, plan in SERVICE_PLANS.items():
        if getattr(settings, plan["settings_price_key"], "") == price_id:
            return tier
    return User.SERVICE_NONE


def _stripe_customer_for_user(user: User) -> str:
    stripe.api_key = settings.STRIPE_SECRET_KEY
    if user.stripe_customer_id:
        return user.stripe_customer_id
    customer = stripe.Customer.create(
        email=user.email or None,
        name=user.get_full_name() or user.display_name or user.username,
        metadata={"user_id": str(user.id), "username": user.username},
    )
    user.stripe_customer_id = customer["id"]
    user.save(update_fields=["stripe_customer_id"])
    return user.stripe_customer_id


def _sync_user_subscription(user: User, subscription: dict) -> None:
    price_id = (
        subscription.get("items", {})
        .get("data", [{}])[0]
        .get("price", {})
        .get("id", "")
    )
    status = subscription.get("status", "")
    subscription_id = subscription.get("id", "")
    current_period_end = subscription.get("current_period_end")
    user.stripe_subscription_id = subscription_id
    user.stripe_price_id = price_id
    user.stripe_subscription_status = status
    user.stripe_current_period_end = (
        datetime.fromtimestamp(current_period_end, tz=dt_timezone.utc)
        if current_period_end
        else None
    )
    if status in {"active", "trialing", "past_due"}:
        user.service_tier = _tier_for_price_id(price_id)
    else:
        user.service_tier = User.SERVICE_NONE
    user.save(
        update_fields=[
            "service_tier",
            "stripe_subscription_id",
            "stripe_price_id",
            "stripe_subscription_status",
            "stripe_current_period_end",
        ],
    )


class UserLoginView(LoginView):
    template_name = "identity/login.html"
    authentication_form = LoginForm


class UserLogoutView(LogoutView):
    next_page = "identity:login"


def _email_verification_token(user: User) -> str:
    signer = TimestampSigner(salt="identity.email_verify")
    return signer.sign(str(user.pk))


def _unsign_email_verification_token(token: str) -> int:
    signer = TimestampSigner(salt="identity.email_verify")
    return int(signer.unsign(token, max_age=settings.EMAIL_VERIFICATION_TTL_SECONDS))


def _send_verification_email(request, user: User) -> None:
    token = _email_verification_token(user)
    verify_url = request.build_absolute_uri(
        reverse("identity:verify_email", args=[token]),
    )
    subject = "Verify your Clawedin email"
    message = (
        "Welcome to Clawedin!\n\n"
        "Please verify your email address by clicking the link below:\n"
        f"{verify_url}\n\n"
        "If you didn't create this account, you can ignore this email."
    )
    send_mail(subject, message, settings.DEFAULT_FROM_EMAIL, [user.email])


def register(request):
    if request.method == "POST":
        form = RegisterForm(request.POST)
        if form.is_valid():
            user = form.save(commit=False)
            user.is_active = True
            user.is_email_verified = False
            user.save()
            _send_verification_email(request, user)
            messages.success(
                request,
                "Account created. You can log in now; verify your email to unlock verification features.",
            )
            return redirect("identity:login")
    else:
        form = RegisterForm()

    return render(request, "identity/register.html", {"form": form})


def verify_email(request, token: str):
    try:
        user_id = _unsign_email_verification_token(token)
    except SignatureExpired:
        messages.error(request, "Verification link expired. Please register again.")
        return redirect("identity:register")
    except BadSignature:
        messages.error(request, "Invalid verification link.")
        return redirect("identity:register")

    user = get_object_or_404(User, pk=user_id)
    if user.is_email_verified:
        messages.info(request, "Your email is already verified. Please log in.")
        return redirect("identity:login")

    user.is_email_verified = True
    user.email_verified_at = timezone.now()
    user.is_active = True
    user.save(update_fields=["is_email_verified", "email_verified_at", "is_active"])
    messages.success(request, "Email verified. You can now log in.")
    return redirect("identity:login")


def _consume_generated_api_token(request):
    token = request.session.pop("generated_api_token", None)
    if not token:
        return None
    request.session.modified = True
    return token


def _get_api_token_for_user(user):
    return ApiToken.objects.filter(user=user).first()


def _upsert_api_token(user, *, replace_existing: bool) -> tuple[ApiToken, str]:
    raw_token = mint_bearer_token(user)
    defaults = {
        "name": "Profile bearer token",
        "token_hash": hash_token(raw_token),
        "prefix": token_prefix(raw_token),
        "revoked_at": None,
        "last_used_at": None,
    }
    api_token = _get_api_token_for_user(user)
    if api_token is None:
        api_token = ApiToken.objects.create(user=user, **defaults)
        return api_token, raw_token
    if not replace_existing:
        raise ValueError("A bearer token already exists for this user.")
    for field, value in defaults.items():
        setattr(api_token, field, value)
    api_token.save(update_fields=["name", "token_hash", "prefix", "revoked_at", "last_used_at"])
    return api_token, raw_token


@login_required
def profile(request):
    current_plan = SERVICE_PLANS.get(request.user.service_tier)
    api_token = _get_api_token_for_user(request.user)
    return render(
        request,
        "identity/profile.html",
        {
            "current_plan": current_plan,
            "is_stripe_ready": _stripe_is_configured(),
            "subscription_active": _subscription_active(request.user),
            "solana_transfer_form": SolanaTransferForm(),
            "api_token": api_token,
            "generated_api_token": _consume_generated_api_token(request),
        },
    )


@login_required
@require_POST
def api_token_create(request):
    if _get_api_token_for_user(request.user):
        messages.info(request, "A bearer token already exists. Regenerate it to rotate the secret.")
        return redirect("identity:profile")
    _, raw_token = _upsert_api_token(request.user, replace_existing=False)
    request.session["generated_api_token"] = raw_token
    messages.success(request, "Bearer token created. Copy it now, it will not be shown again.")
    return redirect("identity:profile")


@login_required
@require_POST
def api_token_regenerate(request):
    _, raw_token = _upsert_api_token(request.user, replace_existing=True)
    request.session["generated_api_token"] = raw_token
    messages.success(request, "Bearer token regenerated. Copy the new token now.")
    return redirect("identity:profile")


@login_required
@user_passes_test(_is_admin_user)
def deployed_agents(request):
    context = {
        "connection_ok": False,
        "error": None,
        "pods": [],
        "pods_count": 0,
        "nodes": [],
        "nodes_count": 0,
        "version": None,
        "checked_at": timezone.now(),
    }

    try:
        from kubernetes import client, config
    except ImportError:
        context["error"] = "Kubernetes client not installed."
        return render(request, "identity/admin_deployed_agents.html", context)

    try:
        load_kube_config()
        v1 = client.CoreV1Api()
        pods = v1.list_pod_for_all_namespaces()
        nodes = v1.list_node()
        version_info = client.VersionApi().get_code()

        context["pods"] = sorted(
            [(pod.metadata.namespace, pod.metadata.name) for pod in pods.items],
            key=lambda item: (item[0], item[1]),
        )
        context["pods_count"] = len(pods.items)
        context["nodes"] = [node.metadata.name for node in nodes.items]
        context["nodes_count"] = len(nodes.items)
        context["version"] = version_info
        context["connection_ok"] = True
    except Exception as exc:  # pragma: no cover - depends on local kube setup
        context["error"] = str(exc)

    return render(request, "identity/admin_deployed_agents.html", context)


@login_required
@user_passes_test(_is_admin_user)
def admin_users(request):
    query = request.GET.get("q", "").strip()
    users_qs = User.objects.annotate(agent_count=Count("agent_deployments"))

    if query:
        users_qs = users_qs.filter(
            Q(username__icontains=query)
            | Q(email__icontains=query)
            | Q(display_name__icontains=query)
            | Q(first_name__icontains=query)
            | Q(last_name__icontains=query)
        )

    users_qs = users_qs.order_by("-date_joined")

    if request.method == "POST":
        user_id = request.POST.get("user_id")
        tier = request.POST.get("service_tier")
        tier_values = {choice[0] for choice in User.SERVICE_TIER_CHOICES}

        target_user = get_object_or_404(User, pk=user_id)
        if tier not in tier_values:
            messages.error(request, "Unknown plan selected.")
        else:
            updates = {
                "service_tier": tier,
                "stripe_subscription_id": "",
                "stripe_price_id": "",
                "stripe_current_period_end": None,
            }
            if tier == User.SERVICE_NONE:
                updates["stripe_subscription_status"] = ""
            else:
                updates["stripe_subscription_status"] = "active"
                updates["stripe_price_id"] = _price_id_for_tier(tier)

            for field, value in updates.items():
                setattr(target_user, field, value)
            target_user.save(update_fields=[*updates.keys(), "updated_at"])
            messages.success(
                request,
                f"Updated plan for {target_user.username} to {target_user.get_service_tier_display()}.",
            )

        if query:
            query_string = urlencode({"q": query})
            return redirect(f"{reverse('identity:admin_users')}?{query_string}")
        return redirect("identity:admin_users")

    return render(
        request,
        "identity/admin_users.html",
        {
            "query": query,
            "users": users_qs,
            "service_tiers": User.SERVICE_TIER_CHOICES,
        },
    )


@login_required
def agent_manager(request):
    if request.user.account_type != User.HUMAN:
        return redirect("identity:profile")
    namespace, namespace_forced = resolve_agent_namespace(request.user.username, request.user.id)
    agents = []
    default_model_provider = _default_agent_model_provider_for_user(request.user)
    form = AgentLaunchForm(initial={"model_provider": default_model_provider})
    error_message = None
    agent_limit = _agent_limit_for_user(request.user)
    current_agent_count = AgentDeployment.objects.filter(user=request.user).count()
    can_launch_agent = agent_limit > 0 and current_agent_count < agent_limit
    subscription_active = _subscription_active(request.user)

    if request.method == "POST":
        if not subscription_active:
            messages.error(request, "You need an active plan to launch agents. Choose a plan first.")
            return redirect("identity:billing")
        if not can_launch_agent:
            messages.error(
                request,
                "Agent limit reached for your plan. Upgrade to launch more agents.",
            )
            return redirect("identity:billing")
        form = AgentLaunchForm(request.POST)
        if form.is_valid():
            resolved_credentials = _resolve_agent_launch_credentials(
                request.user,
                form.cleaned_data,
            )
            for field_name, field_error in resolved_credentials["errors"].items():
                form.add_error(field_name, field_error)
            if not form.errors:
                if resolved_credentials["updates"]:
                    for field_name, field_value in resolved_credentials["updates"].items():
                        setattr(request.user, field_name, field_value)
                    request.user.save(update_fields=[*resolved_credentials["updates"].keys(), "updated_at"])
                try:
                    from kubernetes import client
                except ImportError:
                    messages.error(request, "Kubernetes client not installed.")
                else:
                    try:
                        load_kube_config()
                        v1 = client.CoreV1Api()
                        apps = client.AppsV1Api()

                        if not namespace_forced:
                            try:
                                v1.read_namespace(name=namespace)
                            except client.exceptions.ApiException as exc:
                                if exc.status == 404:
                                    namespace_body = client.V1Namespace(
                                        metadata=client.V1ObjectMeta(name=namespace),
                                    )
                                    v1.create_namespace(namespace_body)
                                else:
                                    raise

                        _ensure_dockerhub_secret(client, v1, namespace)

                        deployment_name = f"openclaw-agent-{request.user.id}-{secrets.token_hex(4)}"
                        secret_name = openai_secret_name_for_deployment(deployment_name, request.user.id)
                        secret_body = client.V1Secret(
                            metadata=client.V1ObjectMeta(name=secret_name),
                            type="Opaque",
                            string_data=resolved_credentials["secret_string_data"],
                        )
                        _upsert_namespaced_secret(
                            v1,
                            namespace,
                            secret_name,
                            secret_body,
                            client.exceptions.ApiException,
                        )

                        gateway_token = secrets.token_urlsafe(32)
                        web_auth_token = generate_api_token()
                        gui_allowed_origins = _gui_allowed_origins_for_pod(deployment_name)
                        browser_ssrf_allowed_hostnames = [
                            value.strip()
                            for value in getattr(settings, "AGENT_BROWSER_SSRF_ALLOWED_HOSTNAMES", [])
                            if isinstance(value, str) and value.strip()
                        ]
                        web_fetch_ssrf_allowed_hostnames = [
                            value.strip()
                            for value in getattr(settings, "AGENT_WEB_FETCH_SSRF_ALLOWED_HOSTNAMES", [])
                            if isinstance(value, str) and value.strip()
                        ]
                        gateway_config = {
                            "gateway": {
                                "mode": "local",
                                "port": int(getattr(settings, "AGENT_GUI_PORT", 18789)),
                                "bind": "lan",
                                "auth": {"token": gateway_token},
                                "controlUi": {
                                    "dangerouslyDisableDeviceAuth": True,
                                },
                                "trustedProxies": [
                                    "10.42.0.0/16",
                                    "10.43.0.0/16",
                                    "127.0.0.1/32",
                                ],
                            },
                            "agents": {
                                **_agent_models_config(resolved_credentials["provider"]),
                            },
                            "plugins": {
                                "entries": {
                                    "clawedin": {
                                        "enabled": True,
                                    }
                                }
                            },
                            "channels": {
                                "clawedin": {
                                    "enabled": True,
                                    "host": "0.0.0.0",
                                    "port": int(
                                        getattr(
                                            settings,
                                            "AGENT_CLAWEDIN_CHANNEL_PORT",
                                            CLAWEDIN_CHANNEL_DEFAULT_PORT,
                                        )
                                    ),
                                    "requestPath": getattr(
                                        settings,
                                        "AGENT_CLAWEDIN_CHANNEL_REQUEST_PATH",
                                        CLAWEDIN_CHANNEL_DEFAULT_PATH,
                                    ),
                                }
                            },
                        }
                        gateway_config["gateway"]["auth"]["mode"] = "token"
                        if gui_allowed_origins:
                            gateway_config["gateway"]["controlUi"]["allowedOrigins"] = gui_allowed_origins
                        if browser_ssrf_allowed_hostnames:
                            gateway_config["browser"] = {
                                "ssrfPolicy": {
                                    "allowedHostnames": browser_ssrf_allowed_hostnames,
                                }
                            }
                        if web_fetch_ssrf_allowed_hostnames:
                            gateway_config.setdefault("tools", {}).setdefault("web", {})["fetch"] = {
                                "ssrfPolicy": {
                                    "allowedHostnames": web_fetch_ssrf_allowed_hostnames,
                                }
                            }
                        gateway_secret = gateway_secret_name_for_deployment(deployment_name, request.user.id)
                        gateway_secret_body = client.V1Secret(
                            metadata=client.V1ObjectMeta(name=gateway_secret),
                            type="Opaque",
                            string_data={
                                "OPENCLAW_GATEWAY_TOKEN": gateway_token,
                                "openclaw.json": json.dumps(gateway_config),
                            },
                        )
                        _upsert_namespaced_secret(
                            v1,
                            namespace,
                            gateway_secret,
                            gateway_secret_body,
                            client.exceptions.ApiException,
                        )
                        web_auth_secret = agent_web_auth_secret_name_for_deployment(
                            deployment_name,
                            request.user.id,
                        )
                        web_auth_secret_body = client.V1Secret(
                            metadata=client.V1ObjectMeta(name=web_auth_secret),
                            type="Opaque",
                            string_data={
                                "AGENT_AUTH_TOKEN": web_auth_token,
                            },
                        )
                        _upsert_namespaced_secret(
                            v1,
                            namespace,
                            web_auth_secret,
                            web_auth_secret_body,
                            client.exceptions.ApiException,
                        )
                        user_bearer_secret = agent_user_bearer_secret_name_for_deployment(
                            deployment_name,
                            request.user.id,
                        )
                        user_bearer_token = mint_bearer_token(request.user)
                        user_bearer_secret_body = client.V1Secret(
                            metadata=client.V1ObjectMeta(name=user_bearer_secret),
                            type="Opaque",
                            string_data={
                                "USER_BEARER_TOKEN": user_bearer_token,
                            },
                        )
                        _upsert_namespaced_secret(
                            v1,
                            namespace,
                            user_bearer_secret,
                            user_bearer_secret_body,
                            client.exceptions.ApiException,
                        )

                        agent_port = int(getattr(settings, "AGENT_GUI_PORT", 18789))
                        proxy_port = int(getattr(settings, "AGENT_GUI_PROXY_PORT", 18790))
                        agent_openclaw_pvc_mode = _agent_openclaw_pvc_mode()
                        shared_agent_openclaw_pvc_name = _shared_agent_openclaw_claim_name()
                        if agent_openclaw_pvc_mode == "per_agent":
                            agent_openclaw_pvc_name = _agent_openclaw_claim_name_for_deployment(
                                deployment_name,
                                request.user.id,
                            )
                        else:
                            agent_openclaw_pvc_name = shared_agent_openclaw_pvc_name
                        agent_openclaw_home = (
                            getattr(settings, "AGENT_OPENCLAW_HOME", "/home/node/.openclaw")
                            or "/home/node/.openclaw"
                        ).strip()
                        agent_openclaw_uid = int(getattr(settings, "AGENT_OPENCLAW_UID", 1000))
                        agent_openclaw_gid = int(getattr(settings, "AGENT_OPENCLAW_GID", 1000))
                        agent_openclaw_pvc_storage_class = (
                            getattr(settings, "AGENT_OPENCLAW_PVC_STORAGE_CLASS", "local-path") or "local-path"
                        ).strip()
                        agent_openclaw_pvc_size = (
                            getattr(settings, "AGENT_OPENCLAW_PVC_SIZE", "5Gi") or "5Gi"
                        ).strip()
                        agent_openclaw_pvc_access_mode = (
                            getattr(settings, "AGENT_OPENCLAW_PVC_ACCESS_MODE", "ReadWriteOnce") or "ReadWriteOnce"
                        ).strip()

                        if agent_openclaw_pvc_name:
                            try:
                                v1.read_namespaced_persistent_volume_claim(
                                    name=agent_openclaw_pvc_name,
                                    namespace=namespace,
                                )
                            except client.exceptions.ApiException as exc:
                                if exc.status == 404:
                                    if agent_openclaw_pvc_mode == "per_agent":
                                        pvc_body = client.V1PersistentVolumeClaim(
                                            metadata=client.V1ObjectMeta(
                                                name=agent_openclaw_pvc_name,
                                                labels={
                                                    "app": "openclaw-agent",
                                                    "owner": request.user.username,
                                                    "deployment": deployment_name,
                                                },
                                            ),
                                            spec=client.V1PersistentVolumeClaimSpec(
                                                access_modes=[agent_openclaw_pvc_access_mode],
                                                resources=client.V1ResourceRequirements(
                                                    requests={"storage": agent_openclaw_pvc_size},
                                                ),
                                                storage_class_name=agent_openclaw_pvc_storage_class,
                                            ),
                                        )
                                        v1.create_namespaced_persistent_volume_claim(namespace, pvc_body)
                                    else:
                                        raise RuntimeError(
                                            f"PersistentVolumeClaim '{agent_openclaw_pvc_name}' was not found in namespace '{namespace}'."
                                        )
                                else:
                                    raise

                        labels = {
                            "app": "openclaw-agent",
                            "owner": request.user.username,
                            "deployment": deployment_name,
                        }
                        agent_node_hostname = (getattr(settings, "AGENT_NODE_HOSTNAME", "") or "").strip()
                        agent_node_selector = (
                            {"kubernetes.io/hostname": agent_node_hostname}
                            if agent_node_hostname
                            else None
                        )
                        pod_affinity = None
                        if not agent_node_selector and getattr(settings, "AGENT_WORKER_ONLY", True):
                            pod_affinity = client.V1Affinity(
                                node_affinity=client.V1NodeAffinity(
                                    required_during_scheduling_ignored_during_execution=client.V1NodeSelector(
                                        node_selector_terms=[
                                            client.V1NodeSelectorTerm(
                                                match_expressions=[
                                                    client.V1NodeSelectorRequirement(
                                                        key="node-role.kubernetes.io/control-plane",
                                                        operator="DoesNotExist",
                                                    )
                                                ]
                                            )
                                        ]
                                    )
                                )
                            )
                        host_aliases = None
                        if getattr(settings, "AGENT_INTERNAL_HOST_ALIAS_ENABLED", True):
                            internal_hosts = [
                                value.strip()
                                for value in getattr(settings, "AGENT_INTERNAL_HOSTS", [])
                                if isinstance(value, str) and value.strip()
                            ]
                            if not internal_hosts:
                                internal_host = (getattr(settings, "AGENT_INTERNAL_HOST", "") or "").strip()
                                if internal_host:
                                    internal_hosts = [internal_host]
                            internal_service_name = (
                                getattr(settings, "AGENT_INTERNAL_SERVICE_NAME", "clawedin") or "clawedin"
                            ).strip()
                            internal_service_namespace = (
                                getattr(settings, "AGENT_INTERNAL_SERVICE_NAMESPACE", "clawedin") or "clawedin"
                            ).strip()
                            if internal_hosts and internal_service_name and internal_service_namespace:
                                try:
                                    internal_service = v1.read_namespaced_service(
                                        name=internal_service_name,
                                        namespace=internal_service_namespace,
                                    )
                                    internal_service_ip = (internal_service.spec.cluster_ip or "").strip()
                                    if internal_service_ip and internal_service_ip.lower() != "none":
                                        host_aliases = [
                                            client.V1HostAlias(
                                                ip=internal_service_ip,
                                                hostnames=internal_hosts,
                                            )
                                        ]
                                except Exception as exc:
                                    logger.warning(
                                        "Failed to set host aliases for %s via service %s/%s: %s",
                                        ",".join(internal_hosts),
                                        internal_service_namespace,
                                        internal_service_name,
                                        exc,
                                    )
                        agent_volume_mounts = [
                            client.V1VolumeMount(
                                name="openclaw-config",
                                mount_path="/etc/openclaw",
                                read_only=True,
                            ),
                            client.V1VolumeMount(
                                name="agent-web-auth",
                                mount_path="/var/run/secrets/clawedin-agent-auth",
                                read_only=True,
                            ),
                            client.V1VolumeMount(
                                name="agent-user-bearer",
                                mount_path="/var/run/secrets/clawedin-user-bearer",
                                read_only=True,
                            ),
                        ]
                        shared_vfs_claim_name = (
                            getattr(settings, "AGENT_SHARED_VFS_CLAIM_NAME", "clawedin-vfs-pvc2") or ""
                        ).strip()
                        shared_vfs_mount_path = _agent_shared_vfs_mount_root()
                        shared_vfs_enabled = bool(
                            getattr(settings, "AGENT_SHARED_VFS_ENABLED", True)
                            and shared_vfs_claim_name
                            and shared_vfs_mount_path
                        )
                        if agent_openclaw_pvc_name:
                            agent_volume_mounts.append(
                                client.V1VolumeMount(
                                    name="openclaw-home",
                                    mount_path=agent_openclaw_home,
                                )
                            )
                            agent_volume_mounts.append(
                                client.V1VolumeMount(
                                    name="openclaw-home",
                                    mount_path="/app/skills",
                                    sub_path="skills",
                                )
                            )
                        if shared_vfs_enabled:
                            agent_volume_mounts.append(
                                client.V1VolumeMount(
                                    name="shared-vfs",
                                    mount_path=shared_vfs_mount_path,
                                )
                            )

                        pod_volumes = [
                            client.V1Volume(
                                name="openclaw-config",
                                secret=client.V1SecretVolumeSource(
                                    secret_name=gateway_secret,
                                    default_mode=0o600,
                                    items=[
                                        client.V1KeyToPath(
                                            key="openclaw.json",
                                            path="openclaw.json",
                                        )
                                    ],
                                ),
                            ),
                            client.V1Volume(
                                name="agent-web-auth",
                                secret=client.V1SecretVolumeSource(
                                    secret_name=web_auth_secret,
                                    default_mode=0o400,
                                    items=[
                                        client.V1KeyToPath(
                                            key="AGENT_AUTH_TOKEN",
                                            path="token",
                                        )
                                    ],
                                ),
                            ),
                            client.V1Volume(
                                name="agent-user-bearer",
                                secret=client.V1SecretVolumeSource(
                                    secret_name=user_bearer_secret,
                                    default_mode=0o400,
                                    items=[
                                        client.V1KeyToPath(
                                            key="USER_BEARER_TOKEN",
                                            path="token",
                                        )
                                    ],
                                ),
                            ),
                        ]
                        if agent_openclaw_pvc_name:
                            pod_volumes.append(
                                client.V1Volume(
                                    name="openclaw-home",
                                    persistent_volume_claim=client.V1PersistentVolumeClaimVolumeSource(
                                        claim_name=agent_openclaw_pvc_name,
                                    ),
                                )
                            )
                        if shared_vfs_enabled:
                            pod_volumes.append(
                                client.V1Volume(
                                    name="shared-vfs",
                                    persistent_volume_claim=client.V1PersistentVolumeClaimVolumeSource(
                                        claim_name=shared_vfs_claim_name,
                                    ),
                                )
                            )

                        init_containers = []
                        pod_security_context = client.V1PodSecurityContext(
                            fs_group=agent_openclaw_gid,
                        )
                        if agent_openclaw_pvc_name:
                            init_containers.append(
                                client.V1Container(
                                    name="openclaw-home-permissions",
                                    image="alpine:3.20",
                                    command=["sh", "-c"],
                                    args=[
                                        (
                                            f"mkdir -p {agent_openclaw_home} "
                                            f"{agent_openclaw_home}/skills "
                                            f"{agent_openclaw_home}/skills/clawedin "
                                            f"{agent_openclaw_home}/skills/clawedin-jobs "
                                            f"{agent_openclaw_home}/agents/main/sessions "
                                            f"{agent_openclaw_home}/credentials "
                                            f"{agent_openclaw_home}/extensions "
                                            f"{agent_openclaw_home}/workspace && "
                                            "apk add --no-cache curl >/dev/null && "
                                            f"curl --fail --silent --show-error --location "
                                            f"{shlex.quote(AGENT_CLAWEDIN_SKILL_URL)} "
                                            f"--output {shlex.quote(f'{agent_openclaw_home}/skills/clawedin/SKILL.md')} && "
                                            f"curl --fail --silent --show-error --location "
                                            f"{shlex.quote(AGENT_JOBS_SKILL_URL)} "
                                            f"--output {shlex.quote(f'{agent_openclaw_home}/skills/clawedin-jobs/SKILL.md')} && "
                                            "cat <<'EOF' > "
                                            f"{agent_openclaw_home}/exec-approvals.json\n"
                                            "{\n"
                                            "  \"version\": 1,\n"
                                            "  \"defaults\": {\n"
                                            "    \"security\": \"full\",\n"
                                            "    \"ask\": \"off\",\n"
                                            "    \"askFallback\": \"full\",\n"
                                            "    \"autoAllowSkills\": true\n"
                                            "  }\n"
                                            "}\n"
                                            "EOF\n"
                                            f"chown {agent_openclaw_uid}:{agent_openclaw_gid} {agent_openclaw_home}/exec-approvals.json && "
                                            f"chown -R {agent_openclaw_uid}:{agent_openclaw_gid} {agent_openclaw_home} && "
                                            f"chmod 700 {agent_openclaw_home} "
                                            f"{agent_openclaw_home}/skills "
                                            f"{agent_openclaw_home}/skills/clawedin "
                                            f"{agent_openclaw_home}/skills/clawedin-jobs "
                                            f"{agent_openclaw_home}/agents "
                                            f"{agent_openclaw_home}/agents/main "
                                            f"{agent_openclaw_home}/agents/main/sessions "
                                            f"{agent_openclaw_home}/credentials "
                                            f"{agent_openclaw_home}/extensions "
                                            f"{agent_openclaw_home}/workspace && "
                                            f"find {agent_openclaw_home}/skills -type f -exec chmod 600 {{}} \\; && "
                                            f"chmod 600 {agent_openclaw_home}/exec-approvals.json"
                                        )
                                    ],
                                    volume_mounts=[
                                        client.V1VolumeMount(
                                            name="openclaw-home",
                                            mount_path=agent_openclaw_home,
                                        )
                                    ],
                                )
                            )

                        pod_spec = client.V1PodSpec(
                            init_containers=init_containers,
                            security_context=pod_security_context,
                            containers=[
                                client.V1Container(
                                    name="openclaw-agent",
                                    image="athenalive/openclaw:latest",
                                    command=["sh", "-lc"],
                                    args=[
                                        (
                                            "exec /usr/local/bin/docker-entrypoint.sh "
                                            "node /app/openclaw.mjs gateway --allow-unconfigured"
                                        )
                                    ],
                                    security_context=client.V1SecurityContext(
                                        run_as_user=agent_openclaw_uid,
                                        run_as_group=agent_openclaw_gid,
                                    ),
                                    ports=[
                                        client.V1ContainerPort(
                                            container_port=agent_port,
                                            name="gui",
                                        ),
                                        client.V1ContainerPort(
                                            container_port=int(
                                                getattr(
                                                    settings,
                                                    "AGENT_CLAWEDIN_CHANNEL_PORT",
                                                    CLAWEDIN_CHANNEL_DEFAULT_PORT,
                                                )
                                            ),
                                            name="clawedin-chat",
                                        )
                                    ],
                                    env=[
                                        client.V1EnvVar(
                                            name="DEFAULT_MODEL",
                                            value=resolved_credentials["default_model"],
                                        ),
                                        client.V1EnvVar(name="OPENCLAW_GATEWAY_BIND", value="lan"),
                                        client.V1EnvVar(
                                            name="OPENCLAW_GATEWAY_PORT",
                                            value=str(agent_port),
                                        ),
                                        client.V1EnvVar(
                                            name="OPENAI_API_KEY",
                                            value_from=client.V1EnvVarSource(
                                                secret_key_ref=client.V1SecretKeySelector(
                                                    name=secret_name,
                                                    key="OPENAI_API_KEY",
                                                    optional=True,
                                                ),
                                            ),
                                        ),
                                        client.V1EnvVar(
                                            name="ANTHROPIC_API_KEY",
                                            value_from=client.V1EnvVarSource(
                                                secret_key_ref=client.V1SecretKeySelector(
                                                    name=secret_name,
                                                    key="ANTHROPIC_API_KEY",
                                                    optional=True,
                                                ),
                                            ),
                                        ),
                                        client.V1EnvVar(
                                            name="OPENCLAW_GATEWAY_TOKEN",
                                            value_from=client.V1EnvVarSource(
                                                secret_key_ref=client.V1SecretKeySelector(
                                                    name=gateway_secret,
                                                    key="OPENCLAW_GATEWAY_TOKEN",
                                                ),
                                            ),
                                        ),
                                        client.V1EnvVar(
                                            name="OPENCLAW_CONFIG_PATH",
                                            value="/etc/openclaw/openclaw.json",
                                        ),
                                        client.V1EnvVar(
                                            name="CLAWEDIN_AGENT_AUTH_TOKEN",
                                            value_from=client.V1EnvVarSource(
                                                secret_key_ref=client.V1SecretKeySelector(
                                                    name=web_auth_secret,
                                                    key="AGENT_AUTH_TOKEN",
                                                ),
                                            ),
                                        ),
                                        client.V1EnvVar(
                                            name="CLAWEDIN_AGENT_AUTH_TOKEN_FILE",
                                            value="/var/run/secrets/clawedin-agent-auth/token",
                                        ),
                                        client.V1EnvVar(
                                            name="CLAWEDIN_USER_BEARER_TOKEN",
                                            value_from=client.V1EnvVarSource(
                                                secret_key_ref=client.V1SecretKeySelector(
                                                    name=user_bearer_secret,
                                                    key="USER_BEARER_TOKEN",
                                                ),
                                            ),
                                        ),
                                        client.V1EnvVar(
                                            name="CLAWEDIN_USER_BEARER_TOKEN_FILE",
                                            value="/var/run/secrets/clawedin-user-bearer/token",
                                        ),
                                        client.V1EnvVar(
                                            name="CLAWEDIN_AGENT_AUTH_USER_ID",
                                            value=str(request.user.id),
                                        ),
                                        client.V1EnvVar(
                                            name="CLAWEDIN_AGENT_AUTH_USERNAME",
                                            value=request.user.username,
                                        ),
                                    ],
                                    # Leave the main agent uncapped for now. OpenClaw is still
                                    # exceeding the current memory ceiling during startup/runtime.
                                    volume_mounts=agent_volume_mounts,
                                ),
                                client.V1Container(
                                    name="openclaw-gui-proxy",
                                    image="alpine/socat:1.7.4.4-r0",
                                    args=[
                                        "-dd",
                                        f"TCP-LISTEN:{int(getattr(settings, 'AGENT_GUI_PROXY_PORT', 18790))},fork,reuseaddr",
                                        f"TCP:127.0.0.1:{int(getattr(settings, 'AGENT_GUI_PORT', 18789))}",
                                    ],
                                    ports=[
                                        client.V1ContainerPort(
                                            container_port=proxy_port,
                                            name="gui-proxy",
                                        )
                                    ],
                                    resources=client.V1ResourceRequirements(
                                        requests={
                                            "cpu": getattr(settings, "AGENT_GUI_PROXY_CPU_REQUEST", "25m"),
                                            "memory": getattr(settings, "AGENT_GUI_PROXY_MEMORY_REQUEST", "64Mi"),
                                        },
                                        limits={
                                            "cpu": getattr(settings, "AGENT_GUI_PROXY_CPU_LIMIT", "100m"),
                                            "memory": getattr(settings, "AGENT_GUI_PROXY_MEMORY_LIMIT", "128Mi"),
                                        },
                                    ),
                                ),
                            ],
                            image_pull_secrets=[
                                client.V1LocalObjectReference(name="dockerhub-secret"),
                            ],
                            volumes=pod_volumes,
                            node_selector=agent_node_selector,
                            affinity=pod_affinity,
                            host_aliases=host_aliases,
                        )
                        template = client.V1PodTemplateSpec(
                            metadata=client.V1ObjectMeta(
                                labels=labels,
                                annotations={
                                    "clawedin.ai/restarted-at": timezone.now().isoformat(),
                                },
                            ),
                            spec=pod_spec,
                        )
                        deployment = client.V1Deployment(
                            metadata=client.V1ObjectMeta(name=deployment_name, labels=labels),
                            spec=client.V1DeploymentSpec(
                                replicas=1,
                                selector=client.V1LabelSelector(match_labels=labels),
                                template=template,
                            ),
                        )

                        apps.create_namespaced_deployment(namespace, deployment)

                        pod_name = ""
                        try:
                            pod_for_gui = _wait_for_agent_pod(
                                v1,
                                namespace,
                                deployment_name,
                                request.user.username,
                            )
                            if pod_for_gui:
                                pod_name = pod_for_gui.metadata.name
                                networking = client.NetworkingV1Api()
                                _ensure_agent_gui_resources(
                                    client,
                                    v1,
                                    networking,
                                    namespace,
                                    pod_for_gui,
                                    request.user.username,
                                )
                        except Exception as exc:  # pragma: no cover - depends on kube setup
                            logger.warning(
                                "Failed to precreate agent GUI ingress for user %s: %s",
                                request.user.id,
                                exc,
                            )

                        AgentDeployment.objects.update_or_create(
                            user=request.user,
                            deployment_name=deployment_name,
                            namespace=namespace,
                            defaults={
                                "gateway_token": gateway_token,
                                "secret_name": gateway_secret,
                                "web_auth_token": web_auth_token,
                                "web_auth_secret_name": web_auth_secret,
                                "pod_name": pod_name,
                            },
                        )

                        messages.success(request, "Agent launch started. Your container is spinning up.")
                        return redirect("identity:agent_manager")
                    except Exception as exc:  # pragma: no cover - depends on kube setup
                        logger.exception("Failed to launch agent for user %s", request.user.id)
                        error_message = str(exc)
                        messages.error(request, f"Could not launch agent: {error_message}")

    try:
        from kubernetes import client
    except ImportError:
        error_message = "Kubernetes client not installed."
    else:
        try:
            load_kube_config()
            v1 = client.CoreV1Api()
            try:
                label_selector = f"app=openclaw-agent,owner={request.user.username}"
                pods = v1.list_namespaced_pod(namespace=namespace, label_selector=label_selector)
                agents = []
                for pod in pods.items:
                    agents.append(
                        {
                            "name": pod.metadata.name,
                            "status": pod.status.phase,
                            "last_seen": pod.status.start_time,
                        }
                    )
            except client.exceptions.ApiException as exc:
                if exc.status != 404:
                    raise
        except Exception as exc:  # pragma: no cover - depends on kube setup
            error_message = str(exc)

    return render(
        request,
        "identity/agent_manager.html",
        {
            "agents": agents,
            "agent_nav": _build_agent_navigation("manager"),
            "form": form,
            "namespace": namespace,
            "openai_key_saved": bool(request.user.openai_api_key),
            "anthropic_key_saved": bool(request.user.anthropic_api_key),
            "selected_model_provider": form["model_provider"].value() or default_model_provider,
            "error_message": error_message,
            "agent_limit": agent_limit,
            "current_agent_count": current_agent_count,
            "can_launch_agent": can_launch_agent,
            "subscription_active": subscription_active,
        },
    )


def public_profile(request, username: str):
    user = get_object_or_404(User, username=username)
    skills = []
    resumes = []
    if user.show_skills:
        skills = UserSkill.objects.filter(user=user).order_by("name")
    if user.show_resumes:
        resumes = Resume.objects.filter(user=user).order_by("-updated_at")
    wants_json = (
        request.GET.get("format", "").strip().lower() == "json"
        or request.path.endswith(".json")
        or "application/json" in request.headers.get("Accept", "").lower()
    )
    if wants_json:
        profile_data = {
            "username": user.username,
            "public_username": user.public_username,
            "display_name": user.public_display_name(),
            "account_type": user.account_type,
            "account_type_display": user.get_account_type_display(),
            "contact": {
                "email": user.email if user.show_email and user.email else None,
                "location": user.location if user.show_location and user.location else None,
                "website": user.website if user.show_website and user.website else None,
            },
            "about": {
                "headline": user.headline or None,
                "bio": user.bio if user.show_bio and user.bio else None,
                "summary": user.summary or None,
                "company": user.company or None,
                "social_links": user.social_links if user.social_links else None,
                "skills": user.skills if user.skills else None,
                "user_agent": user.user_agent if user.show_user_agent and user.user_agent else None,
            },
            "visibility": {
                "is_public": user.is_public,
                "show_email": user.show_email,
                "show_name_public": user.show_name_public,
                "show_location": user.show_location,
                "show_website": user.show_website,
                "show_bio": user.show_bio,
                "show_user_agent": user.show_user_agent,
                "show_skills": user.show_skills,
                "show_resumes": user.show_resumes,
            },
            "skills": [
                {
                    "id": skill.id,
                    "name": skill.name,
                    "proficiency": skill.proficiency or None,
                    "proficiency_display": skill.get_proficiency_display() if skill.proficiency else None,
                    "years_of_experience": skill.years_of_experience,
                    "description": skill.description or None,
                }
                for skill in skills
            ],
            "resumes": [
                {
                    "id": resume.id,
                    "title": resume.title,
                    "headline": resume.headline or None,
                    "location": resume.location or None,
                    "updated_at": resume.updated_at.isoformat(),
                }
                for resume in resumes
            ],
        }
        return JsonResponse(profile_data)
    context = {
        "profile_user": user,
        "skills": skills,
        "resumes": resumes,
    }
    return render(request, "identity/public_profile.html", context)


@login_required
def agent_detail(request, pod_name: str):
    if request.user.account_type != User.HUMAN:
        return redirect("identity:profile")

    namespace, _ = resolve_agent_namespace(request.user.username, request.user.id)
    error_message = None
    pod = None
    logs = None
    tail_lines = request.GET.get("tail_lines") or "200"
    try:
        tail_lines_int = int(tail_lines)
    except (TypeError, ValueError):
        tail_lines_int = 200
    tail_lines_int = max(10, min(tail_lines_int, 2000))

    try:
        from kubernetes import client
    except ImportError:
        error_message = "Kubernetes client not installed."
    else:
        try:
            load_kube_config()
            v1 = client.CoreV1Api()
            networking = client.NetworkingV1Api()

            if request.method == "POST":
                action = request.POST.get("action")
                if action == "restart":
                    v1.delete_namespaced_pod(name=pod_name, namespace=namespace)
                    messages.success(request, "Restart initiated. The pod will be recreated.")
                    return redirect("identity:agent_detail", pod_name=pod_name)
                if action == "delete":
                    allow_cross_namespace = request.user.is_staff or request.user.is_superuser
                    resolved_pod, resolved_namespace = _resolve_pod(v1, pod_name, namespace, allow_cross_namespace)
                    deployment_name = None
                    if resolved_pod and resolved_pod.metadata and resolved_pod.metadata.labels:
                        deployment_name = resolved_pod.metadata.labels.get("deployment")
                    _delete_agent_gui_resources(
                        client,
                        v1,
                        networking,
                        resolved_namespace,
                        pod_name,
                        resource_name=deployment_name or pod_name,
                    )
                    deployment_record = AgentDeployment.objects.filter(
                        user=request.user,
                        deployment_name=deployment_name or "",
                        namespace=resolved_namespace,
                    ).first()
                    if deployment_record:
                        _delete_namespaced_secret_if_present(
                            v1,
                            resolved_namespace,
                            deployment_record.secret_name,
                        )
                        _delete_namespaced_secret_if_present(
                            v1,
                            resolved_namespace,
                            deployment_record.web_auth_secret_name,
                        )
                        _delete_namespaced_secret_if_present(
                            v1,
                            resolved_namespace,
                            agent_user_bearer_secret_name_for_deployment(
                                deployment_record.deployment_name,
                                request.user.id,
                            ),
                        )
                        deployment_record.delete()
                    v1.delete_namespaced_pod(name=pod_name, namespace=resolved_namespace)
                    messages.success(request, "Pod deleted.")
                    return redirect("identity:agent_manager")
                if action == "delete_deployment":
                    allow_cross_namespace = request.user.is_staff or request.user.is_superuser
                    pod, resolved_namespace = _resolve_pod(v1, pod_name, namespace, allow_cross_namespace)
                    if (
                        pod
                        and pod.metadata
                        and pod.metadata.labels
                        and (
                            pod.metadata.labels.get("app") != "openclaw-agent"
                            or (
                                not _is_admin_user(request.user)
                                and pod.metadata.labels.get("owner") != request.user.username
                            )
                        )
                    ):
                        messages.error(request, "You do not have permission to delete this agent.")
                        return redirect("identity:agent_manager")
                    if (
                        pod
                        and pod.metadata
                        and pod.metadata.labels
                        and pod.metadata.labels.get("owner")
                        and pod.metadata.labels.get("owner") != request.user.username
                    ):
                        messages.error(request, "You do not have permission to delete this agent.")
                        return redirect("identity:agent_manager")
                    deployment_name = None
                    if pod and pod.metadata and pod.metadata.labels:
                        deployment_name = pod.metadata.labels.get("deployment")
                    if not deployment_name and pod and pod.metadata and pod.metadata.owner_references:
                        try:
                            apps = client.AppsV1Api()
                            rs_owner = None
                            for ref in pod.metadata.owner_references:
                                if ref.kind == "ReplicaSet" and ref.name:
                                    rs_owner = ref.name
                                    break
                            if rs_owner:
                                rs = apps.read_namespaced_replica_set(rs_owner, resolved_namespace)
                                if rs.metadata and rs.metadata.owner_references:
                                    for ref in rs.metadata.owner_references:
                                        if ref.kind == "Deployment" and ref.name:
                                            deployment_name = ref.name
                                            break
                        except Exception:
                            deployment_name = deployment_name
                    if not deployment_name:
                        messages.error(
                            request,
                            "Deployment not found for this pod. Delete the pod or relaunch the agent.",
                        )
                        return redirect("identity:agent_detail", pod_name=pod_name)
                    try:
                        _delete_agent_gui_resources(
                            client,
                            v1,
                            networking,
                            resolved_namespace,
                            pod_name,
                            resource_name=deployment_name,
                        )
                        apps = client.AppsV1Api()
                        deployment = apps.read_namespaced_deployment(
                            name=deployment_name,
                            namespace=resolved_namespace,
                        )
                        agent_home_claim_name = _openclaw_claim_from_deployment_obj(deployment)
                        if (
                            deployment
                            and deployment.metadata
                            and deployment.metadata.labels
                            and deployment.metadata.labels.get("owner")
                            and deployment.metadata.labels.get("owner") != request.user.username
                        ):
                            messages.error(request, "You do not have permission to delete this agent.")
                            return redirect("identity:agent_manager")
                        if deployment_name == "openclaw-agent":
                            apps.patch_namespaced_deployment_scale(
                                name=deployment_name,
                                namespace=resolved_namespace,
                                body={"spec": {"replicas": 0}},
                            )
                            messages.success(
                                request,
                                "Legacy deployment scaled to 0. The agent will not respawn.",
                            )
                            return redirect("identity:agent_manager")
                        apps.delete_namespaced_deployment(name=deployment_name, namespace=resolved_namespace)
                        if agent_home_claim_name and _is_managed_agent_claim_name(agent_home_claim_name):
                            try:
                                v1.delete_namespaced_persistent_volume_claim(
                                    name=agent_home_claim_name,
                                    namespace=resolved_namespace,
                                )
                            except client.exceptions.ApiException as pvc_exc:
                                if pvc_exc.status != 404:
                                    logger.warning(
                                        "Failed to delete managed PVC %s in %s: %s",
                                        agent_home_claim_name,
                                        resolved_namespace,
                                        pvc_exc,
                                    )
                    except Exception as exc:
                        messages.error(request, f"Failed to delete deployment: {exc}")
                        return redirect("identity:agent_detail", pod_name=pod_name)
                    deployment_record = AgentDeployment.objects.filter(
                        user=request.user,
                        deployment_name=deployment_name,
                        namespace=resolved_namespace,
                    ).first()
                    if deployment_record:
                        _delete_namespaced_secret_if_present(
                            v1,
                            resolved_namespace,
                            deployment_record.secret_name,
                        )
                        _delete_namespaced_secret_if_present(
                            v1,
                            resolved_namespace,
                            deployment_record.web_auth_secret_name,
                        )
                        _delete_namespaced_secret_if_present(
                            v1,
                            resolved_namespace,
                            agent_user_bearer_secret_name_for_deployment(
                                deployment_record.deployment_name,
                                request.user.id,
                            ),
                        )
                        deployment_record.delete()
                    messages.success(request, "Deployment deleted. The agent will not respawn.")
                    return redirect("identity:agent_manager")

            allow_cross_namespace = request.user.is_staff or request.user.is_superuser
            try:
                pod, namespace = _resolve_pod(v1, pod_name, namespace, allow_cross_namespace)
            except client.exceptions.ApiException as exc:
                if exc.status == 404:
                    messages.error(request, "Pod not found. It may have been replaced.")
                    return redirect("identity:agent_manager")
                raise
            if (
                pod
                and pod.metadata
                and pod.metadata.labels
                and (
                    pod.metadata.labels.get("app") != "openclaw-agent"
                    or (
                        not _is_admin_user(request.user)
                        and pod.metadata.labels.get("owner") != request.user.username
                    )
                )
            ):
                messages.error(request, "You do not have permission to view this agent.")
                return redirect("identity:agent_manager")
            startup_wait_seconds = 2
            startup_max_retries = 3
            for attempt in range(startup_max_retries + 1):
                try:
                    logs = v1.read_namespaced_pod_log(
                        name=pod_name,
                        namespace=namespace,
                        container="openclaw-agent",
                        tail_lines=tail_lines_int,
                        timestamps=True,
                    )
                    break
                except client.exceptions.ApiException as exc:
                    body = getattr(exc, "body", "") or ""
                    # During startup, log reads can return 400 while the container is ContainerCreating.
                    if exc.status == 400 and "ContainerCreating" in body:
                        if attempt < startup_max_retries:
                            time.sleep(startup_wait_seconds)
                            continue
                        logs = "Agent container is starting. Please refresh in a few seconds."
                        error_message = None
                        break
                    raise
        except client.exceptions.ApiException as exc:
            if exc.status == 404:
                messages.error(request, "Pod not found.")
                return redirect("identity:agent_manager")
            error_message = str(exc)
        except Exception as exc:  # pragma: no cover - depends on kube setup
            error_message = str(exc)

    deployment_name = ""
    if pod and pod.metadata and pod.metadata.labels:
        deployment_name = pod.metadata.labels.get("deployment") or ""
    deployment_record = AgentDeployment.objects.filter(
        user=request.user,
        deployment_name=deployment_name,
        namespace=namespace,
    ).first()
    user_bearer_secret_name = ""
    if deployment_name:
        user_bearer_secret_name = agent_user_bearer_secret_name_for_deployment(
            deployment_name,
            request.user.id,
        )

    return render(
        request,
        "identity/agent_detail.html",
        {
            "namespace": namespace,
            "pod": pod,
            "agent_nav": _build_agent_navigation("details", pod.metadata.name if pod and pod.metadata else pod_name),
            "deployment_record": deployment_record,
            "logs": logs,
            "tail_lines": tail_lines_int,
            "error_message": error_message,
            "user_bearer_secret_name": user_bearer_secret_name,
        },
    )


@login_required
def agent_dashboard(request, pod_name: str):
    if request.user.account_type != User.HUMAN:
        return redirect("identity:profile")

    gui_context = _prepare_agent_gui_context(request, pod_name)
    resolved_pod_name = gui_context.get("resolved_pod_name") or pod_name
    if resolved_pod_name != pod_name and request.method == "GET" and not gui_context.get("error_message"):
        return redirect("identity:agent_dashboard", pod_name=resolved_pod_name)

    pod = gui_context.get("pod")
    namespace = gui_context.get("namespace")
    error_message = gui_context.get("error_message")
    gui_path = gui_context.get("gui_path")
    gui_wait_url = reverse("identity:agent_gui", args=[resolved_pod_name])
    channel_rows = []
    channels_raw = ""
    status_raw = ""
    capabilities_raw = ""
    channel_choices = None
    create_form = AgentChannelCreateForm(channel_choices=channel_choices)
    gateway_health = {
        "ok": False,
        "label": "Gateway offline",
        "detail": "Waiting for channel startup.",
    }
    status_window_end = timezone.localdate()
    status_window_start = status_window_end - timedelta(days=6)
    deployment_name = ""
    if pod and pod.metadata and pod.metadata.labels:
        deployment_name = pod.metadata.labels.get("deployment") or ""
    deployment_record = AgentDeployment.objects.filter(
        user=request.user,
        deployment_name=deployment_name,
        namespace=namespace,
    ).first()

    if pod and not error_message and request.method == "POST":
        try:
            from kubernetes import client

            load_kube_config()
            v1 = client.CoreV1Api()
            gateway_health = _agent_clawedin_health(
                pod,
                token=getattr(deployment_record, "web_auth_token", "") or "",
            )

            if not _should_query_agent_channels(gateway_health):
                error_message = gateway_health["detail"]
            elif request.method == "POST" and request.POST.get("action") == "create_channel":
                capabilities_result = _run_openclaw_cli(v1, namespace, resolved_pod_name, ["channels", "capabilities", "--json"])
                capabilities_raw = capabilities_result["output"]
                capabilities_payload = (
                    _coerce_openclaw_json(capabilities_result["output"]) if capabilities_result["ok"] else None
                )
                channel_choices = _normalize_channel_choices(capabilities_payload)
                create_form = AgentChannelCreateForm(
                    request.POST,
                    channel_choices=channel_choices,
                )
                if create_form.is_valid():
                    command_args = ["channels", "add", create_form.cleaned_data["channel_type"]]
                    display_name = create_form.cleaned_data["display_name"].strip()
                    account_id = create_form.cleaned_data["account_id"].strip()
                    extra_args = create_form.cleaned_data["extra_args"].strip()
                    if display_name:
                        command_args.extend(["--name", display_name])
                    if account_id:
                        command_args.extend(["--account-id", account_id])
                    if extra_args:
                        try:
                            command_args.extend(shlex.split(extra_args))
                        except ValueError as exc:
                            create_form.add_error("extra_args", f"Could not parse extra CLI args: {exc}")
                    if not create_form.errors:
                        create_result = _run_openclaw_cli(v1, namespace, resolved_pod_name, command_args)
                        if create_result["ok"]:
                            messages.success(request, "Channel created in the agent container.")
                            return redirect("identity:agent_dashboard", pod_name=resolved_pod_name)
                        messages.error(
                            request,
                            f"Channel creation failed: {create_result['output'] or 'OpenClaw returned a non-zero exit code.'}",
                        )
            else:
                create_form = AgentChannelCreateForm(channel_choices=channel_choices)

            if _should_query_agent_channels(gateway_health):
                if channel_choices is None:
                    capabilities_result = _run_openclaw_cli(v1, namespace, resolved_pod_name, ["channels", "capabilities", "--json"])
                    capabilities_raw = capabilities_result["output"]
                    capabilities_payload = (
                        _coerce_openclaw_json(capabilities_result["output"]) if capabilities_result["ok"] else None
                    )
                    channel_choices = _normalize_channel_choices(capabilities_payload)
                    create_form = AgentChannelCreateForm(channel_choices=channel_choices)

                channels_result = _run_openclaw_cli(v1, namespace, resolved_pod_name, ["channels", "list", "--json"])
                status_result = _run_openclaw_cli(v1, namespace, resolved_pod_name, ["channels", "status", "--json"])
                channels_raw = channels_result["output"]
                status_raw = status_result["output"]
                channels_payload = _coerce_openclaw_json(channels_result["output"]) if channels_result["ok"] else None
                status_payload = _coerce_openclaw_json(status_result["output"]) if status_result["ok"] else None
                channel_rows = _normalize_channels(channels_payload, status_payload)

                if not channels_result["ok"] and not error_message:
                    error_message = channels_result["output"] or "Could not fetch channels from the agent."
                elif not status_result["ok"] and not error_message:
                    error_message = status_result["output"] or "Could not fetch channel status from the agent."
        except Exception as exc:  # pragma: no cover - depends on kube setup
            logger.exception("Failed to load OpenClaw dashboard data for pod %s", resolved_pod_name)
            error_message = str(exc)

    can_embed_gui = bool(gui_path) and not bool(getattr(settings, "AGENT_GUI_HOST_SUFFIX", "").strip())
    chat_seed_messages = [
        {
            "role": "agent",
            "author": pod.metadata.name if pod and pod.metadata else "Agent",
            "timestamp": "Live status",
            "text": (
                "The Clawedin channel gateway is ready for prompt-based turns. "
                "Send a message below and I will route it through the agent container on port 31890."
            ),
        },
    ]
    metrics, top_skill_routes, dashboard_cards, _, _ = _agent_dashboard_metrics(
        request.user,
        channel_rows,
        status_window_start,
        status_window_end,
    )

    return render(
        request,
        "identity/agent_dashboard.html",
        {
            "namespace": namespace,
            "pod": pod,
            "deployment_record": deployment_record,
            "error_message": error_message,
            "gui_path": gui_path,
            "gui_wait_url": gui_wait_url,
            "can_embed_gui": can_embed_gui,
            "channel_rows": channel_rows,
            "channels_raw": channels_raw,
            "status_raw": status_raw,
            "capabilities_raw": capabilities_raw,
            "create_form": create_form,
            "chat_seed_messages": chat_seed_messages,
            "dashboard_turns": _recent_dashboard_turns(request.user, resolved_pod_name),
            "pending_attachments": _pending_dashboard_attachments(request.user, resolved_pod_name),
            "chat_endpoint": reverse("identity:agent_dashboard_chat", args=[resolved_pod_name]),
            "chat_upload_endpoint": reverse("identity:agent_dashboard_upload", args=[resolved_pod_name]),
            "chat_updates_endpoint": reverse("identity:agent_dashboard_chat_updates", args=[resolved_pod_name]),
            "runtime_endpoint": reverse("identity:agent_dashboard_runtime", args=[resolved_pod_name]),
            "agent_nav": _build_agent_navigation("dashboard", resolved_pod_name),
            "gateway_health": gateway_health,
            "metrics": metrics,
            "top_skill_routes": top_skill_routes,
            "dashboard_cards": dashboard_cards,
            "status_window_start": status_window_start,
            "status_window_end": status_window_end,
            "dashboard_config_page_url": reverse("identity:agent_dashboard_config_page", args=[resolved_pod_name]),
            "shared_attachment_mount": _agent_shared_vfs_mount_root(),
        },
    )


@login_required
@require_POST
def agent_dashboard_upload(request, pod_name: str):
    if request.user.account_type != User.HUMAN:
        return JsonResponse({"ok": False, "error": "Human accounts only."}, status=403)
    if not getattr(settings, "AGENT_SHARED_VFS_ENABLED", True):
        return JsonResponse({"ok": False, "error": "Shared agent attachments are disabled."}, status=503)

    upload = request.FILES.get("file")
    if not upload:
        return JsonResponse({"ok": False, "error": "Select a file to upload."}, status=400)

    max_bytes = int(getattr(settings, "AGENT_DASHBOARD_ATTACHMENT_MAX_BYTES", 25 * 1024 * 1024))
    if upload.size and upload.size > max_bytes:
        return JsonResponse(
            {"ok": False, "error": f"Files must be {max_bytes // (1024 * 1024)} MB or smaller."},
            status=400,
        )

    namespace, _ = resolve_agent_namespace(request.user.username, request.user.id)
    try:
        from kubernetes import client

        load_kube_config()
        v1 = client.CoreV1Api()
        allow_cross_namespace = request.user.is_staff or request.user.is_superuser
        pod, namespace = _resolve_pod(v1, pod_name, namespace, allow_cross_namespace)
    except Exception as exc:  # pragma: no cover
        return JsonResponse({"ok": False, "error": f"Could not resolve agent pod: {exc}"}, status=502)

    if (
        pod
        and pod.metadata
        and pod.metadata.labels
        and (
            pod.metadata.labels.get("app") != "openclaw-agent"
            or (
                not _is_admin_user(request.user)
                and pod.metadata.labels.get("owner") != request.user.username
            )
        )
    ):
        return JsonResponse({"ok": False, "error": "You do not have permission to use this agent."}, status=403)

    deployment_name = ""
    if pod and pod.metadata and pod.metadata.labels:
        deployment_name = pod.metadata.labels.get("deployment") or ""
    deployment_record = AgentDeployment.objects.filter(
        user=request.user,
        deployment_name=deployment_name,
        namespace=namespace,
    ).first()
    if not deployment_record or not deployment_record.web_auth_token:
        return JsonResponse({"ok": False, "error": "Agent web auth token is not configured."}, status=400)

    relative_path = _agent_attachment_relative_path(request.user, deployment_name or pod_name, upload.name)
    storage_root = _agent_shared_vfs_storage_root()
    storage_path = storage_root / relative_path
    storage_path.parent.mkdir(parents=True, exist_ok=True)
    with storage_path.open("wb") as destination:
        for chunk in upload.chunks():
            destination.write(chunk)

    content_type = (upload.content_type or mimetypes.guess_type(upload.name)[0] or "").strip()
    attachment = AgentDashboardAttachment.objects.create(
        user=request.user,
        deployment=deployment_record,
        pod_name=pod_name,
        namespace=namespace,
        original_name=_safe_agent_attachment_name(upload.name),
        content_type=content_type,
        size_bytes=upload.size or 0,
        storage_path=str(storage_path),
        relative_path=relative_path,
        agent_path=f"{_agent_shared_vfs_mount_root()}/{relative_path}",
    )
    sender_name = request.user.display_name or request.user.username
    dashboard_conversation_id = _dashboard_conversation_id(request.user, deployment_name, pod_name)
    notice_turn = AgentDashboardTurn.objects.create(
        user=request.user,
        deployment=deployment_record,
        pod_name=pod_name,
        namespace=namespace,
        conversation_id=dashboard_conversation_id,
        prompt_text=_build_attachment_notice_text(attachment),
        prompt_author=sender_name,
        status=AgentDashboardTurn.STATUS_QUEUED,
        status_detail="Queued file notice for delivery.",
    )
    attachment.turn = notice_turn
    attachment.save(update_fields=["turn", "updated_at"])
    worker = threading.Thread(
        target=_run_agent_dashboard_turn,
        kwargs={
            "turn_id": notice_turn.id,
            "user_id": request.user.id,
            "username": request.user.username,
            "pod_name": pod_name,
            "namespace": namespace,
            "deployment_record_id": deployment_record.id,
            "conversation_id": notice_turn.conversation_id,
            "prompt": notice_turn.prompt_text,
            "sender_name": sender_name,
        },
        daemon=True,
    )
    worker.start()
    return JsonResponse(
        {
            "ok": True,
            "attachment": _serialize_dashboard_attachment(attachment),
            "pendingAttachments": _pending_dashboard_attachments(request.user, pod_name),
            "turn": _serialize_dashboard_turn(
                AgentDashboardTurn.objects.select_related("user").prefetch_related("attachments").get(pk=notice_turn.pk)
            ),
        }
    )


@login_required
@require_POST
def agent_dashboard_chat(request, pod_name: str):
    if request.user.account_type != User.HUMAN:
        return JsonResponse({"ok": False, "error": "Human accounts only."}, status=403)

    data = _parse_request_json(request)
    if data is None:
        return JsonResponse({"ok": False, "error": "Invalid JSON body."}, status=400)

    prompt = (data.get("text") or "").strip()
    if not prompt:
        return JsonResponse({"ok": False, "error": "Message text is required."}, status=400)

    namespace, _ = resolve_agent_namespace(request.user.username, request.user.id)
    try:
        from kubernetes import client

        load_kube_config()
        v1 = client.CoreV1Api()
        allow_cross_namespace = request.user.is_staff or request.user.is_superuser
        pod, namespace = _resolve_pod(v1, pod_name, namespace, allow_cross_namespace)
    except Exception as exc:  # pragma: no cover - depends on kube setup
        return JsonResponse({"ok": False, "error": f"Could not resolve agent pod: {exc}"}, status=502)

    if (
        pod
        and pod.metadata
        and pod.metadata.labels
        and (
            pod.metadata.labels.get("app") != "openclaw-agent"
            or (
                not _is_admin_user(request.user)
                and pod.metadata.labels.get("owner") != request.user.username
            )
        )
    ):
        return JsonResponse({"ok": False, "error": "You do not have permission to use this agent."}, status=403)

    deployment_name = ""
    if pod and pod.metadata and pod.metadata.labels:
        deployment_name = pod.metadata.labels.get("deployment") or ""
    deployment_record = AgentDeployment.objects.filter(
        user=request.user,
        deployment_name=deployment_name,
        namespace=namespace,
    ).first()
    if not deployment_record or not deployment_record.web_auth_token:
        return JsonResponse({"ok": False, "error": "Agent web auth token is not configured."}, status=400)

    sender_name = request.user.display_name or request.user.username
    conversation_id = (data.get("conversationId") or _dashboard_conversation_id(request.user, deployment_name, pod_name)).strip()
    attachment_ids = data.get("attachmentIds") or []
    if not isinstance(attachment_ids, list):
        return JsonResponse({"ok": False, "error": "attachmentIds must be a list."}, status=400)

    pending_attachments = list(
        AgentDashboardAttachment.objects.filter(
            user=request.user,
            deployment=deployment_record,
            pod_name=pod_name,
            turn__isnull=True,
            id__in=attachment_ids,
        )
    )
    if len(pending_attachments) != len(attachment_ids):
        return JsonResponse({"ok": False, "error": "One or more attachments are invalid."}, status=400)

    turn = AgentDashboardTurn.objects.create(
        user=request.user,
        deployment=deployment_record,
        pod_name=pod_name,
        namespace=namespace,
        conversation_id=conversation_id,
        prompt_text=prompt,
        prompt_author=sender_name,
        status=AgentDashboardTurn.STATUS_QUEUED,
        status_detail="Queued for delivery.",
    )
    if pending_attachments:
        AgentDashboardAttachment.objects.filter(id__in=[item.id for item in pending_attachments]).update(
            turn=turn,
            updated_at=timezone.now(),
        )
    worker = threading.Thread(
        target=_run_agent_dashboard_turn,
        kwargs={
            "turn_id": turn.id,
            "user_id": request.user.id,
            "username": request.user.username,
            "pod_name": pod_name,
            "namespace": namespace,
            "deployment_record_id": deployment_record.id,
            "conversation_id": conversation_id,
            "prompt": prompt,
            "sender_name": sender_name,
        },
        daemon=True,
    )
    worker.start()

    return JsonResponse(
        {
            "ok": True,
            "turn": _serialize_dashboard_turn(
                AgentDashboardTurn.objects.select_related("user").prefetch_related("attachments").get(pk=turn.pk)
            ),
            "conversationId": conversation_id,
            "pendingAttachments": _pending_dashboard_attachments(request.user, pod_name),
        }
    )


@login_required
@require_GET
def agent_dashboard_chat_updates(request, pod_name: str):
    if request.user.account_type != User.HUMAN:
        return JsonResponse({"ok": False, "error": "Human accounts only."}, status=403)

    status_window_end = timezone.localdate()
    status_window_start = status_window_end - timedelta(days=6)
    turns = _recent_dashboard_turns(request.user, pod_name)
    metrics, top_skill_routes, dashboard_cards, available_dashboard_items, selected_dashboard_item_keys = _agent_dashboard_metrics(
        request.user,
        [],
        status_window_start,
        status_window_end,
    )
    return JsonResponse(
        {
            "ok": True,
            "turns": turns,
            "metrics": metrics,
            "dashboardCards": dashboard_cards,
            "pendingAttachments": _pending_dashboard_attachments(request.user, pod_name),
            "availableDashboardItems": available_dashboard_items,
            "selectedDashboardItemKeys": selected_dashboard_item_keys,
            "topSkillRoutes": top_skill_routes,
        }
    )


@login_required
@require_GET
def agent_dashboard_runtime(request, pod_name: str):
    if request.user.account_type != User.HUMAN:
        return JsonResponse({"ok": False, "error": "Human accounts only."}, status=403)

    gui_context = _prepare_agent_gui_context(request, pod_name)
    resolved_pod_name = gui_context.get("resolved_pod_name") or pod_name
    namespace = gui_context.get("namespace")
    pod = gui_context.get("pod")
    error_message = gui_context.get("error_message")

    deployment_name = ""
    if pod and pod.metadata and pod.metadata.labels:
        deployment_name = pod.metadata.labels.get("deployment") or ""
    deployment_record = AgentDeployment.objects.filter(
        user=request.user,
        deployment_name=deployment_name,
        namespace=namespace,
    ).first()

    if not pod or error_message:
        return JsonResponse(
            {
                "ok": False,
                "error": error_message or "Agent pod not available.",
                "resolvedPodName": resolved_pod_name,
            },
            status=502,
        )

    snapshot = _agent_dashboard_runtime_snapshot(request, resolved_pod_name, namespace, deployment_record)
    _maybe_queue_dashboard_bootstrap_turn(
        request.user,
        deployment_record,
        resolved_pod_name,
        namespace,
        snapshot["gateway_health"],
    )
    status_window_end = timezone.localdate()
    status_window_start = status_window_end - timedelta(days=6)
    metrics, _, _, _, _ = _agent_dashboard_metrics(
        request.user,
        snapshot["channel_rows"],
        status_window_start,
        status_window_end,
    )
    return JsonResponse(
        {
            "ok": True,
            "resolvedPodName": resolved_pod_name,
            "gatewayHealth": snapshot["gateway_health"],
            "channelRows": snapshot["channel_rows"],
            "channelsRaw": snapshot["channels_raw"],
            "statusRaw": snapshot["status_raw"],
            "capabilitiesRaw": snapshot["capabilities_raw"],
            "channelChoices": snapshot["channel_choices"],
            "runtimeError": snapshot["runtime_error"],
            "metrics": metrics,
        }
    )


@login_required
def agent_dashboard_config_page(request, pod_name: str):
    if request.user.account_type != User.HUMAN:
        return redirect("identity:profile")

    gui_context = _prepare_agent_gui_context(request, pod_name)
    resolved_pod_name = gui_context.get("resolved_pod_name") or pod_name
    if resolved_pod_name != pod_name and request.method == "GET" and not gui_context.get("error_message"):
        return redirect("identity:agent_dashboard_config_page", pod_name=resolved_pod_name)

    if request.method == "POST":
        selected_keys = _sanitize_agent_dashboard_item_keys(request.POST.getlist("dashboard_item"))
        request.user.agent_dashboard_items = selected_keys
        request.user.save(update_fields=["agent_dashboard_items"])
        messages.success(request, "Dashboard widgets updated.")
        return redirect("identity:agent_dashboard", pod_name=resolved_pod_name)

    status_window_end = timezone.localdate()
    status_window_start = status_window_end - timedelta(days=6)
    _, _, dashboard_cards, available_dashboard_items, selected_dashboard_item_keys = _agent_dashboard_metrics(
        request.user,
        [],
        status_window_start,
        status_window_end,
    )
    return render(
        request,
        "identity/agent_dashboard_config.html",
        {
            "pod": gui_context.get("pod"),
            "resolved_pod_name": resolved_pod_name,
            "agent_nav": _build_agent_navigation("configure", resolved_pod_name),
            "error_message": gui_context.get("error_message"),
            "available_dashboard_items": available_dashboard_items,
            "selected_dashboard_item_keys": selected_dashboard_item_keys,
            "default_dashboard_item_keys": DEFAULT_AGENT_DASHBOARD_ITEM_KEYS,
            "dashboard_cards": dashboard_cards,
            "status_window_start": status_window_start,
            "status_window_end": status_window_end,
            "dashboard_url": reverse("identity:agent_dashboard", args=[resolved_pod_name]),
        },
    )


@login_required
@require_POST
def agent_dashboard_config(request, pod_name: str):
    if request.user.account_type != User.HUMAN:
        return JsonResponse({"ok": False, "error": "Human accounts only."}, status=403)

    data = _parse_request_json(request)
    if data is None:
        return JsonResponse({"ok": False, "error": "Invalid JSON body."}, status=400)

    selected_keys = _sanitize_agent_dashboard_item_keys(data.get("items") or [])
    request.user.agent_dashboard_items = selected_keys
    request.user.save(update_fields=["agent_dashboard_items"])

    status_window_end = timezone.localdate()
    status_window_start = status_window_end - timedelta(days=6)
    metrics, top_skill_routes, dashboard_cards, available_dashboard_items, selected_dashboard_item_keys = _agent_dashboard_metrics(
        request.user,
        [],
        status_window_start,
        status_window_end,
    )
    return JsonResponse(
        {
            "ok": True,
            "metrics": metrics,
            "dashboardCards": dashboard_cards,
            "availableDashboardItems": available_dashboard_items,
            "selectedDashboardItemKeys": selected_dashboard_item_keys,
            "topSkillRoutes": top_skill_routes,
        }
    )


@login_required
def agent_terminal(request, pod_name: str):
    if request.user.account_type != User.HUMAN:
        return redirect("identity:profile")

    namespace, _ = resolve_agent_namespace(request.user.username, request.user.id)
    error_message = None
    pod = None

    try:
        from kubernetes import client
    except ImportError:
        error_message = "Kubernetes client not installed."
    else:
        try:
            load_kube_config()
            v1 = client.CoreV1Api()
            allow_cross_namespace = request.user.is_staff or request.user.is_superuser
            try:
                pod, namespace = _resolve_pod(v1, pod_name, namespace, allow_cross_namespace)
            except client.exceptions.ApiException as exc:
                if exc.status == 404:
                    messages.error(request, "Pod not found. It may have been replaced.")
                    return redirect("identity:agent_manager")
                raise
            if (
                pod
                and pod.metadata
                and pod.metadata.labels
                and (
                    pod.metadata.labels.get("app") != "openclaw-agent"
                    or (
                        not _is_admin_user(request.user)
                        and pod.metadata.labels.get("owner") != request.user.username
                    )
                )
            ):
                messages.error(request, "You do not have permission to access this agent.")
                return redirect("identity:agent_manager")
        except client.exceptions.ApiException as exc:
            if exc.status == 404:
                messages.error(request, "Pod not found.")
                return redirect("identity:agent_manager")
            error_message = str(exc)
        except Exception as exc:  # pragma: no cover - depends on kube setup
            error_message = str(exc)

    return render(
        request,
        "identity/agent_terminal.html",
        {
            "namespace": namespace,
            "pod": pod,
            "agent_nav": _build_agent_navigation("terminal", pod.metadata.name if pod and pod.metadata else pod_name),
            "error_message": error_message,
        },
    )


@login_required
def agent_gui(request, pod_name: str):
    if request.user.account_type != User.HUMAN:
        return redirect("identity:profile")
    context = _prepare_agent_gui_context(request, pod_name)
    resolved_pod_name = context.get("resolved_pod_name") or pod_name
    if resolved_pod_name != pod_name and not context.get("error_message") and request.GET.get("open") != "1":
        return redirect("identity:agent_gui", pod_name=resolved_pod_name)
    if request.GET.get("open") == "1" and context.get("gui_path") and not context.get("error_message"):
        return redirect(context["gui_path"])
    context["status_url"] = reverse("identity:agent_gui_status", args=[resolved_pod_name])
    context["open_url"] = f"{reverse('identity:agent_gui', args=[resolved_pod_name])}?open=1"
    return render(
        request,
        "identity/agent_gui.html",
        context,
    )


@login_required
@require_GET
def agent_gui_status(request, pod_name: str):
    if request.user.account_type != User.HUMAN:
        return JsonResponse({"ready": False, "error": "Authentication required."}, status=403)

    context = _prepare_agent_gui_context(request, pod_name)
    response_payload = {
        "ready": False,
        "gui_path": context.get("gui_path"),
        "message": "Container is not ready yet.",
    }

    if context.get("error_message"):
        response_payload["message"] = context["error_message"]
        response = JsonResponse(response_payload, status=503)
        response["Cache-Control"] = "no-store"
        return response

    pod = context.get("pod")
    gui_path = context.get("gui_path")
    if not pod or not gui_path:
        response = JsonResponse(response_payload)
        response["Cache-Control"] = "no-store"
        return response

    if not _pod_container_ready(pod):
        response_payload["message"] = "Container is not ready yet."
        response = JsonResponse(response_payload)
        response["Cache-Control"] = "no-store"
        return response

    response_payload["ready"] = True
    response_payload["message"] = "Container is ready. Verifying browser HTTPS access..."
    response = JsonResponse(response_payload)
    response["Cache-Control"] = "no-store"
    return response


@login_required
def agent_gui_proxy(request, pod_name: str, subpath: str = ""):
    if request.user.account_type != User.HUMAN:
        return redirect("identity:profile")

    proxy_base = getattr(settings, "AGENT_GUI_PROXY_BASE", "").strip()
    if not proxy_base:
        return HttpResponse("Agent GUI proxy is not configured.", status=503)

    namespace, _ = resolve_agent_namespace(request.user.username, request.user.id)
    try:
        from kubernetes import client
    except ImportError:
        return HttpResponse("Kubernetes client not installed.", status=503)

    try:
        load_kube_config()
        v1 = client.CoreV1Api()
        networking = client.NetworkingV1Api()
        allow_cross_namespace = request.user.is_staff or request.user.is_superuser
        try:
            pod, namespace = _resolve_pod(v1, pod_name, namespace, allow_cross_namespace)
        except client.exceptions.ApiException as exc:
            if exc.status != 404:
                raise
            label_selector = f"app=openclaw-agent,owner={request.user.username}"
            pods = v1.list_namespaced_pod(
                namespace=namespace,
                label_selector=label_selector,
            )
            if not pods.items:
                error_message = (
                    "Pod not found.\n"
                    f"Requested pod: {pod_name}\n"
                    f"Namespace: {namespace}\n"
                    f"Selector: {label_selector}\n"
                    f"{_pod_debug_snapshot(v1, namespace, label_selector)}"
                )
                logger.warning(
                    "Agent GUI proxy pod not found: pod=%s namespace=%s selector=%s",
                    pod_name,
                    namespace,
                    label_selector,
                )
                return HttpResponse(error_message, status=404)
            pods_sorted = sorted(
                pods.items,
                key=lambda item: item.status.start_time or datetime.min.replace(tzinfo=dt_timezone.utc),
                reverse=True,
            )
            pod = pods_sorted[0]
            if pod.metadata.name != pod_name:
                target_path = f"/agents/gui/{pod.metadata.name}/"
                if subpath:
                    target_path = f"{target_path}{subpath}"
                return redirect(target_path)
        if (
            pod
            and pod.metadata
            and pod.metadata.labels
            and (
                pod.metadata.labels.get("app") != "openclaw-agent"
                or (
                    not _is_admin_user(request.user)
                    and pod.metadata.labels.get("owner") != request.user.username
                )
            )
        ):
            return HttpResponse("Agent GUI is only available for your agent pods.", status=403)
        _ensure_agent_gui_resources(client, v1, networking, namespace, pod, request.user.username)
    except client.exceptions.ApiException as exc:
        error_message = (
            "Cluster API error while preparing agent GUI proxy.\n"
            f"Requested pod: {pod_name}\n"
            f"Namespace: {namespace}\n"
            f"Details: {_format_api_exception(exc)}\n"
            f"{_kube_context_snapshot()}"
        )
        logger.warning(
            "Agent GUI proxy API error: pod=%s namespace=%s details=%s",
            pod_name,
            namespace,
            _format_api_exception(exc),
        )
        return HttpResponse(error_message, status=502)
    except Exception as exc:  # pragma: no cover - depends on kube setup
        error_message = (
            "Cluster error while preparing agent GUI proxy.\n"
            f"Requested pod: {pod_name}\n"
            f"Namespace: {namespace}\n"
            f"Details: {exc}\n"
            f"{_kube_context_snapshot()}"
        )
        logger.exception(
            "Agent GUI proxy error: pod=%s namespace=%s",
            pod_name,
            namespace,
        )
        return HttpResponse(error_message, status=502)

    proxy_base = proxy_base.rstrip("/")
    target_url = f"{proxy_base}{request.get_full_path()}"
    data = request.body if request.method not in {"GET", "HEAD"} else None
    headers = _proxy_request_headers(request)
    prefix = _gui_path_for_pod(pod_name)
    headers.setdefault("X-Forwarded-Prefix", prefix)

    try:
        upstream_req = Request(target_url, data=data, headers=headers, method=request.method)
        timeout = int(getattr(settings, "AGENT_GUI_PROXY_TIMEOUT", 30))
        upstream_resp = urlopen(upstream_req, timeout=timeout)
    except HTTPError as exc:
        return HttpResponse(exc.read() or exc.reason, status=exc.code)
    except URLError as exc:
        return HttpResponse(f"Proxy error: {exc.reason}", status=502)

    response = StreamingHttpResponse(_stream_response(upstream_resp), status=upstream_resp.status)
    for key, value in upstream_resp.headers.items():
        lower = key.lower()
        if lower in {"connection", "keep-alive", "proxy-authenticate", "proxy-authorization", "te", "trailers", "transfer-encoding", "upgrade"}:
            continue
        if lower == "location":
            response[key] = _rewrite_location_header(value, prefix, pod_name)
            continue
        response[key] = value
    return response


def _rewrite_location_header(location: str, prefix: str, pod_name: str) -> str:
    if not location:
        return location
    pod_root = f"/openclaw-agent-{pod_name}"
    if location.startswith(pod_root):
        return location.replace(pod_root, prefix.rstrip("/"), 1)
    if location.startswith("/") and not location.startswith(prefix):
        return f"{prefix.rstrip('/')}{location}"

    parsed = urlsplit(location)
    if parsed.scheme and parsed.netloc:
        path = parsed.path or "/"
        if path.startswith(pod_root):
            new_path = path.replace(pod_root, prefix.rstrip("/"), 1)
            return urlunsplit((parsed.scheme, parsed.netloc, new_path, parsed.query, parsed.fragment))
        if path.startswith("/") and not path.startswith(prefix):
            new_path = f"{prefix.rstrip('/')}{path}"
            return urlunsplit((parsed.scheme, parsed.netloc, new_path, parsed.query, parsed.fragment))
    return location


def agent_gui_root_redirect(request, pod_name: str, subpath: str = ""):
    target = f"/agents/gui/{pod_name}/"
    if subpath:
        target = f"{target}{subpath}"
    return redirect(target)


@login_required
def profile_update(request):
    if request.method == "POST":
        form = ProfileUpdateForm(request.POST, instance=request.user)
        if form.is_valid():
            form.save()
            return redirect("identity:profile")
    else:
        form = ProfileUpdateForm(instance=request.user)

    return render(request, "identity/profile_update.html", {"form": form})


@login_required
@require_POST
def solana_wallet_create(request):
    if request.user.solana_public_key:
        messages.info(request, "A Solana wallet already exists for your profile.")
        return redirect("identity:profile")

    try:
        public_key, encrypted_private_key = generate_solana_wallet()
    except Exception as exc:  # pragma: no cover - defensive for crypto failures
        logger.exception("Failed to create Solana wallet for user %s", request.user.id)
        messages.error(request, f"Could not create Solana wallet: {exc}")
        return redirect("identity:profile")

    request.user.solana_public_key = public_key
    request.user.solana_private_key = encrypted_private_key
    request.user.save(update_fields=["solana_public_key", "solana_private_key"])
    messages.success(request, "Solana wallet created and saved to your profile.")
    return redirect("identity:profile")


@login_required
@require_POST
def solana_wallet_regenerate(request):
    if not request.user.solana_public_key:
        messages.error(request, "No Solana wallet found. Create one first.")
        return redirect("identity:profile")

    try:
        public_key, encrypted_private_key = generate_solana_wallet()
    except Exception as exc:  # pragma: no cover - defensive for crypto failures
        logger.exception("Failed to regenerate Solana wallet for user %s", request.user.id)
        messages.error(request, f"Could not regenerate Solana wallet: {exc}")
        return redirect("identity:profile")

    request.user.solana_public_key = public_key
    request.user.solana_private_key = encrypted_private_key
    request.user.save(update_fields=["solana_public_key", "solana_private_key"])
    messages.success(request, "Solana wallet regenerated. Previous wallet is no longer linked.")
    return redirect("identity:profile")


@login_required
@require_POST
def solana_transfer(request):
    if not SOLANA_SDK_AVAILABLE:
        messages.error(request, "Solana SDK is not installed on the server.")
        return redirect("identity:profile")
    if not settings.SOLANA_RPC_URL:
        messages.error(request, "Solana RPC is not configured.")
        return redirect("identity:profile")
    if not request.user.solana_private_key:
        messages.error(request, "Create a Solana wallet before sending tokens.")
        return redirect("identity:profile")

    form = SolanaTransferForm(request.POST)
    if not form.is_valid():
        messages.error(request, "Enter a valid recipient address and amount.")
        return redirect("identity:profile")

    recipient_address = form.cleaned_data["recipient"].strip()
    mint_address = form.cleaned_data["mint_address"].strip()
    amount = form.cleaned_data["amount"]
    if amount <= 0:
        messages.error(request, "Amount must be greater than zero.")
        return redirect("identity:profile")

    try:
        recipient_pubkey = Pubkey.from_string(recipient_address)
    except Exception:
        messages.error(request, "Recipient address is invalid.")
        return redirect("identity:profile")

    try:
        mint_pubkey = Pubkey.from_string(mint_address)
    except Exception:
        messages.error(request, "Token mint address is invalid.")
        return redirect("identity:profile")

    client = Client(settings.SOLANA_RPC_URL)
    try:
        decimals = _solana_mint_decimals(client, mint_pubkey)
    except Exception as exc:
        messages.error(request, f"Could not load token decimals: {exc}")
        return redirect("identity:profile")

    price, price_error = _fetch_birdeye_price(mint_address)
    if price_error:
        messages.error(request, price_error)
        return redirect("identity:profile")

    if amount.as_tuple().exponent < -decimals:
        messages.error(request, f"Amount supports up to {decimals} decimal places.")
        return redirect("identity:profile")

    amount_base = (amount * (Decimal(10) ** decimals)).quantize(
        Decimal("1"),
        rounding=ROUND_DOWN,
    )
    if amount_base <= 0:
        messages.error(request, "Amount is too small.")
        return redirect("identity:profile")

    sender_keypair = load_keypair(request.user.solana_private_key)
    sender_pubkey = sender_keypair.pubkey()
    sender_ata = get_associated_token_address(owner=sender_pubkey, mint=mint_pubkey)
    recipient_ata = get_associated_token_address(owner=recipient_pubkey, mint=mint_pubkey)

    sender_ata_value = _get_rpc_value(client.get_account_info(sender_ata))
    if sender_ata_value is None:
        messages.error(request, "Sender token account not found.")
        return redirect("identity:profile")

    recipient_ata_value = _get_rpc_value(client.get_account_info(recipient_ata))
    tx = Transaction()
    if recipient_ata_value is None:
        tx.add(
            create_associated_token_account(
                CreateAssociatedTokenAccountParams(
                    payer=sender_pubkey,
                    owner=recipient_pubkey,
                    mint=mint_pubkey,
                )
            )
        )

    tx.add(
        transfer_checked(
            TransferCheckedParams(
                program_id=TOKEN_PROGRAM_ID,
                source=sender_ata,
                mint=mint_pubkey,
                dest=recipient_ata,
                owner=sender_pubkey,
                amount=int(amount_base),
                decimals=decimals,
                signers=[],
            )
        )
    )

    try:
        send_resp = client.send_transaction(
            tx,
            sender_keypair,
            opts=TxOpts(skip_preflight=False, preflight_commitment="confirmed"),
        )
    except Exception as exc:
        logger.exception("Failed to send Solana transaction for user %s", request.user.id)
        messages.error(request, f"Transfer failed: {exc}")
        return redirect("identity:profile")

    signature = send_resp.value if hasattr(send_resp, "value") else send_resp.get("result")
    total_value = (amount * price).quantize(Decimal("0.01"), rounding=ROUND_DOWN)
    messages.success(
        request,
        "Transfer submitted. Signature: "
        f"{signature}. Estimated value: ${total_value:,.2f}",
    )
    return redirect("identity:profile")


@login_required
def billing(request):
    plans = []
    for tier, plan in SERVICE_PLANS.items():
        plans.append(
            {
                **plan,
                "tier": tier,
                "is_current": request.user.service_tier == tier,
                "is_available": bool(_price_id_for_tier(tier) and _stripe_is_configured()),
            }
        )
    return render(
        request,
        "identity/billing.html",
        {
            "plans": plans,
            "is_stripe_ready": _stripe_is_configured(),
            "subscription_active": _subscription_active(request.user),
        },
    )


@login_required
@require_POST
def create_checkout_session(request, tier: str):
    if tier not in SERVICE_PLANS:
        messages.error(request, "Unknown plan selected.")
        return redirect("identity:billing")
    price_id = _price_id_for_tier(tier)
    if not _stripe_is_configured() or not price_id:
        messages.error(request, "Billing is not configured yet. Add Stripe keys first.")
        return redirect("identity:billing")
    if request.user.stripe_subscription_status in {"active", "trialing", "past_due"}:
        messages.info(
            request,
            "You already have an active subscription. Use Stripe billing to switch plans.",
        )
        return redirect("identity:billing")

    stripe.api_key = settings.STRIPE_SECRET_KEY
    success_url = request.build_absolute_uri(reverse("identity:billing_success"))
    cancel_url = request.build_absolute_uri(reverse("identity:billing"))
    try:
        customer_id = _stripe_customer_for_user(request.user)
        session = stripe.checkout.Session.create(
            mode="subscription",
            customer=customer_id,
            line_items=[{"price": price_id, "quantity": 1}],
            success_url=f"{success_url}?session_id={{CHECKOUT_SESSION_ID}}",
            cancel_url=cancel_url,
            allow_promotion_codes=True,
            metadata={"user_id": str(request.user.id), "tier": tier},
            subscription_data={"metadata": {"user_id": str(request.user.id), "tier": tier}},
        )
    except stripe.error.StripeError as exc:
        messages.error(request, f"Could not start checkout: {exc.user_message or str(exc)}")
        return redirect("identity:billing")

    return redirect(session["url"], permanent=False)


@login_required
@require_POST
def billing_manage(request):
    if not _stripe_is_configured():
        messages.error(request, "Billing is not configured yet.")
        return redirect("identity:billing")
    if not request.user.stripe_customer_id:
        messages.info(request, "No Stripe billing profile found for your account yet.")
        return redirect("identity:billing")

    stripe.api_key = settings.STRIPE_SECRET_KEY
    try:
        session = stripe.billing_portal.Session.create(
            customer=request.user.stripe_customer_id,
            return_url=request.build_absolute_uri(reverse("identity:billing")),
        )
    except stripe.error.StripeError as exc:
        messages.error(request, f"Could not open billing portal: {exc.user_message or str(exc)}")
        return redirect("identity:billing")
    return redirect(session["url"], permanent=False)


@login_required
def billing_success(request):
    messages.success(request, "Payment received. Your plan will update in a few seconds.")
    return redirect("identity:billing")


@csrf_exempt
@require_POST
def stripe_webhook(request):
    if not STRIPE_SDK_AVAILABLE or not settings.STRIPE_WEBHOOK_SECRET or not settings.STRIPE_SECRET_KEY:
        return HttpResponse("Webhook is not configured.", status=400)

    payload = request.body
    sig_header = request.META.get("HTTP_STRIPE_SIGNATURE", "")
    stripe.api_key = settings.STRIPE_SECRET_KEY

    try:
        event = stripe.Webhook.construct_event(
            payload=payload,
            sig_header=sig_header,
            secret=settings.STRIPE_WEBHOOK_SECRET,
        )
    except ValueError:
        return HttpResponse("Invalid payload.", status=400)
    except stripe.error.SignatureVerificationError:
        return HttpResponse("Invalid signature.", status=400)

    event_type = event["type"]
    data_object = event["data"]["object"]

    if event_type == "checkout.session.completed" and data_object.get("mode") == "subscription":
        user_id = data_object.get("metadata", {}).get("user_id")
        if user_id:
            user = User.objects.filter(id=user_id).first()
            if user:
                customer_id = data_object.get("customer")
                subscription_id = data_object.get("subscription")
                if customer_id and user.stripe_customer_id != customer_id:
                    user.stripe_customer_id = customer_id
                    user.save(update_fields=["stripe_customer_id"])
                if subscription_id:
                    try:
                        subscription = stripe.Subscription.retrieve(subscription_id)
                        _sync_user_subscription(user, subscription)
                    except stripe.error.StripeError:
                        logger.exception("Failed to sync checkout subscription %s", subscription_id)

    if event_type in {"customer.subscription.created", "customer.subscription.updated"}:
        customer_id = data_object.get("customer")
        user = User.objects.filter(stripe_customer_id=customer_id).first()
        if user:
            _sync_user_subscription(user, data_object)
    elif event_type == "customer.subscription.deleted":
        subscription_id = data_object.get("id")
        user = User.objects.filter(stripe_subscription_id=subscription_id).first()
        if user:
            user.service_tier = User.SERVICE_NONE
            user.stripe_subscription_status = data_object.get("status", "canceled")
            user.stripe_subscription_id = ""
            user.stripe_price_id = ""
            user.stripe_current_period_end = None
            user.save(
                update_fields=[
                    "service_tier",
                    "stripe_subscription_status",
                    "stripe_subscription_id",
                    "stripe_price_id",
                    "stripe_current_period_end",
                ],
            )

    return HttpResponse(status=200)


@login_required
def user_skill_list(request):
    skills = UserSkill.objects.filter(user=request.user).order_by("name")
    return render(request, "identity/user_skill_list.html", {"skills": skills})


@login_required
def user_skill_create(request):
    if request.method == "POST":
        form = UserSkillForm(request.POST)
        if form.is_valid():
            skill = form.save(commit=False)
            skill.user = request.user
            skill.save()
            return redirect("identity:user_skill_list")
    else:
        form = UserSkillForm()
    return render(request, "identity/user_skill_form.html", {"form": form, "mode": "create"})


@login_required
def user_skill_update(request, skill_id):
    skill = get_object_or_404(UserSkill, id=skill_id, user=request.user)
    if request.method == "POST":
        form = UserSkillForm(request.POST, instance=skill)
        if form.is_valid():
            form.save()
            return redirect("identity:user_skill_list")
    else:
        form = UserSkillForm(instance=skill)
    return render(request, "identity/user_skill_form.html", {"form": form, "mode": "update"})


@login_required
def user_skill_delete(request, skill_id):
    skill = get_object_or_404(UserSkill, id=skill_id, user=request.user)
    if request.method == "POST":
        skill.delete()
        return redirect("identity:user_skill_list")
    return render(request, "identity/user_skill_confirm_delete.html", {"skill": skill})


@login_required
def resume_list(request):
    resumes = Resume.objects.filter(user=request.user).order_by("-updated_at")
    return render(request, "identity/resume_list.html", {"resumes": resumes})


@login_required
def resume_detail(request, resume_id):
    resume = get_object_or_404(Resume, id=resume_id, user=request.user)
    return render(request, "identity/resume_detail.html", {"resume": resume})


@login_required
def resume_create(request):
    if request.method == "POST":
        form = ResumeForm(request.POST)
        if form.is_valid():
            resume = form.save(commit=False)
            resume.user = request.user
            resume.save()
            return redirect("identity:resume_detail", resume_id=resume.id)
    else:
        form = ResumeForm()

    return render(request, "identity/resume_form.html", {"form": form, "mode": "create"})


@login_required
def resume_update(request, resume_id):
    resume = get_object_or_404(Resume, id=resume_id, user=request.user)
    if request.method == "POST":
        form = ResumeForm(request.POST, instance=resume)
        if form.is_valid():
            form.save()
            return redirect("identity:resume_detail", resume_id=resume.id)
    else:
        form = ResumeForm(instance=resume)

    return render(request, "identity/resume_form.html", {"form": form, "mode": "update"})


@login_required
def resume_delete(request, resume_id):
    resume = get_object_or_404(Resume, id=resume_id, user=request.user)
    if request.method == "POST":
        resume.delete()
        return redirect("identity:resume_list")
    return render(request, "identity/resume_confirm_delete.html", {"resume": resume})


def _resume_for_user(request, resume_id):
    return get_object_or_404(Resume, id=resume_id, user=request.user)


@login_required
def experience_create(request, resume_id):
    resume = _resume_for_user(request, resume_id)
    if request.method == "POST":
        form = ResumeExperienceForm(request.POST)
        if form.is_valid():
            experience = form.save(commit=False)
            experience.resume = resume
            experience.save()
            return redirect("identity:resume_detail", resume_id=resume.id)
    else:
        form = ResumeExperienceForm()
    return render(
        request,
        "identity/resume_item_form.html",
        {"form": form, "resume": resume, "title": "Add experience"},
    )


@login_required
def experience_update(request, resume_id, item_id):
    resume = _resume_for_user(request, resume_id)
    experience = get_object_or_404(ResumeExperience, id=item_id, resume=resume)
    if request.method == "POST":
        form = ResumeExperienceForm(request.POST, instance=experience)
        if form.is_valid():
            form.save()
            return redirect("identity:resume_detail", resume_id=resume.id)
    else:
        form = ResumeExperienceForm(instance=experience)
    return render(
        request,
        "identity/resume_item_form.html",
        {"form": form, "resume": resume, "title": "Edit experience"},
    )


@login_required
def experience_delete(request, resume_id, item_id):
    resume = _resume_for_user(request, resume_id)
    experience = get_object_or_404(ResumeExperience, id=item_id, resume=resume)
    if request.method == "POST":
        experience.delete()
        return redirect("identity:resume_detail", resume_id=resume.id)
    return render(
        request,
        "identity/resume_item_confirm_delete.html",
        {"resume": resume, "item": experience, "title": "Delete experience"},
    )


@login_required
def education_create(request, resume_id):
    resume = _resume_for_user(request, resume_id)
    if request.method == "POST":
        form = ResumeEducationForm(request.POST)
        if form.is_valid():
            education = form.save(commit=False)
            education.resume = resume
            education.save()
            return redirect("identity:resume_detail", resume_id=resume.id)
    else:
        form = ResumeEducationForm()
    return render(
        request,
        "identity/resume_item_form.html",
        {"form": form, "resume": resume, "title": "Add education"},
    )


@login_required
def education_update(request, resume_id, item_id):
    resume = _resume_for_user(request, resume_id)
    education = get_object_or_404(ResumeEducation, id=item_id, resume=resume)
    if request.method == "POST":
        form = ResumeEducationForm(request.POST, instance=education)
        if form.is_valid():
            form.save()
            return redirect("identity:resume_detail", resume_id=resume.id)
    else:
        form = ResumeEducationForm(instance=education)
    return render(
        request,
        "identity/resume_item_form.html",
        {"form": form, "resume": resume, "title": "Edit education"},
    )


@login_required
def education_delete(request, resume_id, item_id):
    resume = _resume_for_user(request, resume_id)
    education = get_object_or_404(ResumeEducation, id=item_id, resume=resume)
    if request.method == "POST":
        education.delete()
        return redirect("identity:resume_detail", resume_id=resume.id)
    return render(
        request,
        "identity/resume_item_confirm_delete.html",
        {"resume": resume, "item": education, "title": "Delete education"},
    )


@login_required
def skill_create(request, resume_id):
    resume = _resume_for_user(request, resume_id)
    if request.method == "POST":
        form = ResumeSkillForm(request.POST)
        if form.is_valid():
            skill = form.save(commit=False)
            skill.resume = resume
            skill.save()
            return redirect("identity:resume_detail", resume_id=resume.id)
    else:
        form = ResumeSkillForm()
    return render(
        request,
        "identity/resume_item_form.html",
        {"form": form, "resume": resume, "title": "Add skill"},
    )


@login_required
def skill_update(request, resume_id, item_id):
    resume = _resume_for_user(request, resume_id)
    skill = get_object_or_404(ResumeSkill, id=item_id, resume=resume)
    if request.method == "POST":
        form = ResumeSkillForm(request.POST, instance=skill)
        if form.is_valid():
            form.save()
            return redirect("identity:resume_detail", resume_id=resume.id)
    else:
        form = ResumeSkillForm(instance=skill)
    return render(
        request,
        "identity/resume_item_form.html",
        {"form": form, "resume": resume, "title": "Edit skill"},
    )


@login_required
def skill_delete(request, resume_id, item_id):
    resume = _resume_for_user(request, resume_id)
    skill = get_object_or_404(ResumeSkill, id=item_id, resume=resume)
    if request.method == "POST":
        skill.delete()
        return redirect("identity:resume_detail", resume_id=resume.id)
    return render(
        request,
        "identity/resume_item_confirm_delete.html",
        {"resume": resume, "item": skill, "title": "Delete skill"},
    )


@login_required
def project_create(request, resume_id):
    resume = _resume_for_user(request, resume_id)
    if request.method == "POST":
        form = ResumeProjectForm(request.POST)
        if form.is_valid():
            project = form.save(commit=False)
            project.resume = resume
            project.save()
            return redirect("identity:resume_detail", resume_id=resume.id)
    else:
        form = ResumeProjectForm()
    return render(
        request,
        "identity/resume_item_form.html",
        {"form": form, "resume": resume, "title": "Add project"},
    )


@login_required
def project_update(request, resume_id, item_id):
    resume = _resume_for_user(request, resume_id)
    project = get_object_or_404(ResumeProject, id=item_id, resume=resume)
    if request.method == "POST":
        form = ResumeProjectForm(request.POST, instance=project)
        if form.is_valid():
            form.save()
            return redirect("identity:resume_detail", resume_id=resume.id)
    else:
        form = ResumeProjectForm(instance=project)
    return render(
        request,
        "identity/resume_item_form.html",
        {"form": form, "resume": resume, "title": "Edit project"},
    )


@login_required
def project_delete(request, resume_id, item_id):
    resume = _resume_for_user(request, resume_id)
    project = get_object_or_404(ResumeProject, id=item_id, resume=resume)
    if request.method == "POST":
        project.delete()
        return redirect("identity:resume_detail", resume_id=resume.id)
    return render(
        request,
        "identity/resume_item_confirm_delete.html",
        {"resume": resume, "item": project, "title": "Delete project"},
    )


@login_required
def certification_create(request, resume_id):
    resume = _resume_for_user(request, resume_id)
    if request.method == "POST":
        form = ResumeCertificationForm(request.POST)
        if form.is_valid():
            certification = form.save(commit=False)
            certification.resume = resume
            certification.save()
            return redirect("identity:resume_detail", resume_id=resume.id)
    else:
        form = ResumeCertificationForm()
    return render(
        request,
        "identity/resume_item_form.html",
        {"form": form, "resume": resume, "title": "Add certification"},
    )


@login_required
def certification_update(request, resume_id, item_id):
    resume = _resume_for_user(request, resume_id)
    certification = get_object_or_404(ResumeCertification, id=item_id, resume=resume)
    if request.method == "POST":
        form = ResumeCertificationForm(request.POST, instance=certification)
        if form.is_valid():
            form.save()
            return redirect("identity:resume_detail", resume_id=resume.id)
    else:
        form = ResumeCertificationForm(instance=certification)
    return render(
        request,
        "identity/resume_item_form.html",
        {"form": form, "resume": resume, "title": "Edit certification"},
    )


@login_required
def certification_delete(request, resume_id, item_id):
    resume = _resume_for_user(request, resume_id)
    certification = get_object_or_404(ResumeCertification, id=item_id, resume=resume)
    if request.method == "POST":
        certification.delete()
        return redirect("identity:resume_detail", resume_id=resume.id)
    return render(
        request,
        "identity/resume_item_confirm_delete.html",
        {"resume": resume, "item": certification, "title": "Delete certification"},
    )
