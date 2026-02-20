import base64
import logging
import os
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen
from urllib.parse import urlsplit, urlunsplit
from decimal import Decimal, ROUND_DOWN
from datetime import datetime, timezone as dt_timezone

from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import login_required, user_passes_test
from django.contrib.auth.views import LoginView, LogoutView
from django.core.mail import send_mail
from django.core.signing import BadSignature, SignatureExpired, TimestampSigner
from django.http import HttpResponse, StreamingHttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST

try:
    import stripe

    STRIPE_SDK_AVAILABLE = True
except ImportError:  # pragma: no cover - exercised in environments without stripe installed
    stripe = None
    STRIPE_SDK_AVAILABLE = False

from .forms import (
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
from .kube import (
    gui_ingress_name,
    gui_middleware_name,
    gui_service_name,
    load_kube_config,
    normalize_namespace,
    resolve_agent_namespace,
)
from .solana_wallet import generate_solana_wallet, load_keypair
from .solana_wallet import generate_solana_wallet
from .models import (
    Resume,
    ResumeCertification,
    ResumeEducation,
    ResumeExperience,
    ResumeProject,
    ResumeSkill,
    User,
    UserSkill,
)

logger = logging.getLogger(__name__)

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

SOLANA_TOKEN_MINT = "9Dki6G2hiTqxBCi89czJsx8C5vHyLMaujan7q1dmpump"


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
    scheme = "https" if request.is_secure() else "http"
    return f"{scheme}://{host}/"


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

    pods = v1.list_pod_for_all_namespaces(field_selector=f"metadata.name={pod_name}")
    if pods.items:
        pod = pods.items[0]
        return pod, pod.metadata.namespace
    raise


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


def _ensure_agent_gui_resources(client_module, v1, networking, namespace: str, pod, owner_username: str):
    agent_port = int(getattr(settings, "AGENT_GUI_PORT", 18789))
    proxy_port = int(getattr(settings, "AGENT_GUI_PROXY_PORT", 18790))
    service_name = gui_service_name(pod.metadata.name)
    ingress_name = gui_ingress_name(pod.metadata.name)
    ingress_class = getattr(settings, "AGENT_GUI_INGRESS_CLASS", "") or None
    labels = {
        "app": "openclaw-agent",
        "owner": owner_username,
        "pod": pod.metadata.name,
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
        raise ValueError("Pod has no IP yet.")

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

    host = _gui_host_for_pod(pod.metadata.name)
    path_prefix = _gui_path_prefix()
    annotations = {}
    path = f"{path_prefix}/{pod.metadata.name}(/|$)(.*)"
    path_type = "ImplementationSpecific"
    subdomain_mode = bool(getattr(settings, "AGENT_GUI_HOST_SUFFIX", "").strip())
    if ingress_class == "traefik":
        middleware_name = gui_middleware_name(pod.metadata.name)
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
                        "prefixes": [f"{path_prefix}/{pod.metadata.name}"],
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
            path = f"{path_prefix}/{pod.metadata.name}"
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


def _delete_agent_gui_resources(client_module, v1, networking, namespace: str, pod_name: str):
    service_name = gui_service_name(pod_name)
    ingress_name = gui_ingress_name(pod_name)
    ingress_class = getattr(settings, "AGENT_GUI_INGRESS_CLASS", "") or None
    middleware_name = gui_middleware_name(pod_name)

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
        "name": "Clawedin Free",
        "headline": "Expose your agent. Let it work.",
        "price_label": "$3.00 / mo",
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
        "price_label": "$19.99 / mo",
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
        "price_label": "$49.99 / mo",
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


@login_required
def profile(request):
    current_plan = SERVICE_PLANS.get(request.user.service_tier)
    return render(
        request,
        "identity/profile.html",
        {
            "current_plan": current_plan,
            "is_stripe_ready": _stripe_is_configured(),
            "subscription_active": request.user.stripe_subscription_status
            in {"active", "trialing", "past_due"},
            "solana_transfer_form": SolanaTransferForm(),
        },
    )


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
def agent_manager(request):
    if request.user.account_type != User.HUMAN:
        return redirect("identity:profile")
    namespace, namespace_forced = resolve_agent_namespace(request.user.username, request.user.id)
    agents = []
    form = AgentLaunchForm()
    error_message = None

    if request.method == "POST":
        form = AgentLaunchForm(request.POST)
        if form.is_valid():
            openai_key = form.cleaned_data["openai_api_key"].strip()
            if not openai_key and request.user.openai_api_key:
                openai_key = request.user.openai_api_key
            if not openai_key:
                form.add_error("openai_api_key", "Enter an OpenAI API key to launch an agent.")
            else:
                request.user.openai_api_key = openai_key
                request.user.save(update_fields=["openai_api_key", "updated_at"])
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

                        secret_name = "openai-secret"
                        secret_body = client.V1Secret(
                            metadata=client.V1ObjectMeta(name=secret_name),
                            type="Opaque",
                            string_data={"OPENAI_API_KEY": openai_key},
                        )
                        try:
                            v1.read_namespaced_secret(secret_name, namespace)
                            v1.patch_namespaced_secret(secret_name, namespace, secret_body)
                        except client.exceptions.ApiException as exc:
                            if exc.status == 404:
                                v1.create_namespaced_secret(namespace, secret_body)
                            else:
                                raise

                        deployment_name = "openclaw-agent"
                        agent_port = int(getattr(settings, "AGENT_GUI_PORT", 18789))
                        proxy_port = int(getattr(settings, "AGENT_GUI_PROXY_PORT", 18790))
                        labels = {"app": "openclaw-agent", "owner": request.user.username}
                        pod_spec = client.V1PodSpec(
                            containers=[
                                client.V1Container(
                                    name="openclaw-agent",
                                    image="athenalive/openclaw:latest",
                                    ports=[
                                        client.V1ContainerPort(
                                            container_port=agent_port,
                                            name="gui",
                                        )
                                    ],
                                    env=[
                                        client.V1EnvVar(name="DEFAULT_MODEL", value="openai/gpt-4o"),
                                        client.V1EnvVar(name="OPENCLAW_GATEWAY_BIND", value="0.0.0.0"),
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
                                                ),
                                            ),
                                        ),
                                    ],
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
                                ),
                            ],
                            image_pull_secrets=[
                                client.V1LocalObjectReference(name="dockerhub-secret"),
                            ],
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

                        try:
                            apps.read_namespaced_deployment(deployment_name, namespace)
                            apps.patch_namespaced_deployment(deployment_name, namespace, deployment)
                        except client.exceptions.ApiException as exc:
                            if exc.status == 404:
                                apps.create_namespaced_deployment(namespace, deployment)
                            else:
                                raise

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
                pods = v1.list_namespaced_pod(namespace=namespace)
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
            "form": form,
            "namespace": namespace,
            "openai_key_saved": bool(request.user.openai_api_key),
            "error_message": error_message,
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
                    _, resolved_namespace = _resolve_pod(v1, pod_name, namespace, allow_cross_namespace)
                    _delete_agent_gui_resources(client, v1, networking, resolved_namespace, pod_name)
                    v1.delete_namespaced_pod(name=pod_name, namespace=resolved_namespace)
                    messages.success(request, "Pod deleted.")
                    return redirect("identity:agent_manager")

            allow_cross_namespace = request.user.is_staff or request.user.is_superuser
            try:
                pod, namespace = _resolve_pod(v1, pod_name, namespace, allow_cross_namespace)
            except client.exceptions.ApiException as exc:
                if exc.status == 404:
                    messages.error(request, "Pod not found. It may have been replaced.")
                    return redirect("identity:agent_manager")
                raise
            logs = v1.read_namespaced_pod_log(
                name=pod_name,
                namespace=namespace,
                container="openclaw-agent",
                tail_lines=tail_lines_int,
                timestamps=True,
            )
        except client.exceptions.ApiException as exc:
            if exc.status == 404:
                messages.error(request, "Pod not found.")
                return redirect("identity:agent_manager")
            error_message = str(exc)
        except Exception as exc:  # pragma: no cover - depends on kube setup
            error_message = str(exc)

    return render(
        request,
        "identity/agent_detail.html",
        {
            "namespace": namespace,
            "pod": pod,
            "logs": logs,
            "tail_lines": tail_lines_int,
            "error_message": error_message,
        },
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
            "error_message": error_message,
        },
    )


@login_required
def agent_gui(request, pod_name: str):
    if request.user.account_type != User.HUMAN:
        return redirect("identity:profile")

    namespace, _ = resolve_agent_namespace(request.user.username, request.user.id)
    error_message = None
    pod = None
    gui_path = None

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
                    return render(
                        request,
                        "identity/agent_gui.html",
                        {
                            "namespace": namespace,
                            "pod": pod,
                            "gui_path": gui_path,
                            "error_message": error_message,
                        },
                    )
                pods_sorted = sorted(
                    pods.items,
                    key=lambda item: item.status.start_time or datetime.min.replace(tzinfo=dt_timezone.utc),
                    reverse=True,
                )
                pod = pods_sorted[0]
                pod_name = pod.metadata.name

            _ensure_agent_gui_resources(client, v1, networking, namespace, pod, request.user.username)

            gui_path = _gui_url_for_pod(request, pod_name)
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
                return render(
                    request,
                    "identity/agent_gui.html",
                    {
                        "namespace": namespace,
                        "pod": pod,
                        "gui_path": gui_path,
                        "error_message": error_message,
                    },
                )
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

    if gui_path and not error_message:
        return redirect(gui_path)

    return render(
        request,
        "identity/agent_gui.html",
        {
            "namespace": namespace,
            "pod": pod,
            "gui_path": gui_path,
            "error_message": error_message,
        },
    )


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
    amount = form.cleaned_data["amount"]
    if amount <= 0:
        messages.error(request, "Amount must be greater than zero.")
        return redirect("identity:profile")

    try:
        recipient_pubkey = Pubkey.from_string(recipient_address)
    except Exception:
        messages.error(request, "Recipient address is invalid.")
        return redirect("identity:profile")

    client = Client(settings.SOLANA_RPC_URL)
    mint_pubkey = Pubkey.from_string(SOLANA_TOKEN_MINT)
    try:
        decimals = _solana_mint_decimals(client, mint_pubkey)
    except Exception as exc:
        messages.error(request, f"Could not load token decimals: {exc}")
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
    messages.success(request, f"Transfer submitted. Signature: {signature}")
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
            "subscription_active": request.user.stripe_subscription_status
            in {"active", "trialing", "past_due"},
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
