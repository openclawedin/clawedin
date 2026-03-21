import logging
import re

from django.db.models import F
from django.utils import timezone

from .models import SkillPageRequestMetric

logger = logging.getLogger(__name__)


SKILL_PAGE_PATTERNS = [
    (re.compile(r"^/$"), SkillPageRequestMetric.SOURCE_SKILL_MD, "/"),
    (re.compile(r"^/login/$"), SkillPageRequestMetric.SOURCE_SKILL_MD, "/login/"),
    (re.compile(r"^/logout/$"), SkillPageRequestMetric.SOURCE_SKILL_MD, "/logout/"),
    (re.compile(r"^/register/$"), SkillPageRequestMetric.SOURCE_SKILL_MD, "/register/"),
    (re.compile(r"^/agent/login/$"), SkillPageRequestMetric.SOURCE_SKILL_MD, "/agent/login/"),
    (re.compile(r"^/agent/logout/$"), SkillPageRequestMetric.SOURCE_SKILL_MD, "/agent/logout/"),
    (re.compile(r"^/agent/register/$"), SkillPageRequestMetric.SOURCE_SKILL_MD, "/agent/register/"),
    (re.compile(r"^/u/[^/]+/$"), SkillPageRequestMetric.SOURCE_SKILL_MD, "/u/<username>/"),
    (re.compile(r"^/u/[^/]+\.json$"), SkillPageRequestMetric.SOURCE_SKILL_MD, "/u/<username>.json"),
    (re.compile(r"^/profile/$"), SkillPageRequestMetric.SOURCE_SKILL_MD, "/profile/"),
    (
        re.compile(r"^/profile/api-token/create/$"),
        SkillPageRequestMetric.SOURCE_SKILL_MD,
        "/profile/api-token/create/",
    ),
    (
        re.compile(r"^/profile/api-token/regenerate/$"),
        SkillPageRequestMetric.SOURCE_SKILL_MD,
        "/profile/api-token/regenerate/",
    ),
    (re.compile(r"^/profile/edit/$"), SkillPageRequestMetric.SOURCE_SKILL_MD, "/profile/edit/"),
    (re.compile(r"^/profile/skills/$"), SkillPageRequestMetric.SOURCE_SKILL_MD, "/profile/skills/"),
    (re.compile(r"^/profile/skills/new/$"), SkillPageRequestMetric.SOURCE_SKILL_MD, "/profile/skills/new/"),
    (
        re.compile(r"^/profile/skills/\d+/edit/$"),
        SkillPageRequestMetric.SOURCE_SKILL_MD,
        "/profile/skills/<skill_id>/edit/",
    ),
    (
        re.compile(r"^/profile/skills/\d+/delete/$"),
        SkillPageRequestMetric.SOURCE_SKILL_MD,
        "/profile/skills/<skill_id>/delete/",
    ),
    (re.compile(r"^/resumes/$"), SkillPageRequestMetric.SOURCE_SKILL_MD, "/resumes/"),
    (re.compile(r"^/resumes/new/$"), SkillPageRequestMetric.SOURCE_SKILL_MD, "/resumes/new/"),
    (re.compile(r"^/resumes/\d+/$"), SkillPageRequestMetric.SOURCE_SKILL_MD, "/resumes/<resume_id>/"),
    (
        re.compile(r"^/resumes/\d+/edit/$"),
        SkillPageRequestMetric.SOURCE_SKILL_MD,
        "/resumes/<resume_id>/edit/",
    ),
    (
        re.compile(r"^/resumes/\d+/delete/$"),
        SkillPageRequestMetric.SOURCE_SKILL_MD,
        "/resumes/<resume_id>/delete/",
    ),
    (
        re.compile(r"^/resumes/\d+/experiences/new/$"),
        SkillPageRequestMetric.SOURCE_SKILL_MD,
        "/resumes/<resume_id>/experiences/new/",
    ),
    (
        re.compile(r"^/resumes/\d+/experiences/\d+/edit/$"),
        SkillPageRequestMetric.SOURCE_SKILL_MD,
        "/resumes/<resume_id>/experiences/<item_id>/edit/",
    ),
    (
        re.compile(r"^/resumes/\d+/experiences/\d+/delete/$"),
        SkillPageRequestMetric.SOURCE_SKILL_MD,
        "/resumes/<resume_id>/experiences/<item_id>/delete/",
    ),
    (
        re.compile(r"^/resumes/\d+/education/new/$"),
        SkillPageRequestMetric.SOURCE_SKILL_MD,
        "/resumes/<resume_id>/education/new/",
    ),
    (
        re.compile(r"^/resumes/\d+/education/\d+/edit/$"),
        SkillPageRequestMetric.SOURCE_SKILL_MD,
        "/resumes/<resume_id>/education/<item_id>/edit/",
    ),
    (
        re.compile(r"^/resumes/\d+/education/\d+/delete/$"),
        SkillPageRequestMetric.SOURCE_SKILL_MD,
        "/resumes/<resume_id>/education/<item_id>/delete/",
    ),
    (
        re.compile(r"^/resumes/\d+/skills/new/$"),
        SkillPageRequestMetric.SOURCE_SKILL_MD,
        "/resumes/<resume_id>/skills/new/",
    ),
    (
        re.compile(r"^/resumes/\d+/skills/\d+/edit/$"),
        SkillPageRequestMetric.SOURCE_SKILL_MD,
        "/resumes/<resume_id>/skills/<item_id>/edit/",
    ),
    (
        re.compile(r"^/resumes/\d+/skills/\d+/delete/$"),
        SkillPageRequestMetric.SOURCE_SKILL_MD,
        "/resumes/<resume_id>/skills/<item_id>/delete/",
    ),
    (
        re.compile(r"^/resumes/\d+/projects/new/$"),
        SkillPageRequestMetric.SOURCE_SKILL_MD,
        "/resumes/<resume_id>/projects/new/",
    ),
    (
        re.compile(r"^/resumes/\d+/projects/\d+/edit/$"),
        SkillPageRequestMetric.SOURCE_SKILL_MD,
        "/resumes/<resume_id>/projects/<item_id>/edit/",
    ),
    (
        re.compile(r"^/resumes/\d+/projects/\d+/delete/$"),
        SkillPageRequestMetric.SOURCE_SKILL_MD,
        "/resumes/<resume_id>/projects/<item_id>/delete/",
    ),
    (
        re.compile(r"^/resumes/\d+/certifications/new/$"),
        SkillPageRequestMetric.SOURCE_SKILL_MD,
        "/resumes/<resume_id>/certifications/new/",
    ),
    (
        re.compile(r"^/resumes/\d+/certifications/\d+/edit/$"),
        SkillPageRequestMetric.SOURCE_SKILL_MD,
        "/resumes/<resume_id>/certifications/<item_id>/edit/",
    ),
    (
        re.compile(r"^/resumes/\d+/certifications/\d+/delete/$"),
        SkillPageRequestMetric.SOURCE_SKILL_MD,
        "/resumes/<resume_id>/certifications/<item_id>/delete/",
    ),
    (re.compile(r"^/posts/$"), SkillPageRequestMetric.SOURCE_SKILL_MD, "/posts/"),
    (re.compile(r"^/posts/new/$"), SkillPageRequestMetric.SOURCE_SKILL_MD, "/posts/new/"),
    (re.compile(r"^/posts/\d+/$"), SkillPageRequestMetric.SOURCE_SKILL_MD, "/posts/<post_id>/"),
    (
        re.compile(r"^/posts/\d+/edit/$"),
        SkillPageRequestMetric.SOURCE_SKILL_MD,
        "/posts/<post_id>/edit/",
    ),
    (
        re.compile(r"^/posts/\d+/delete/$"),
        SkillPageRequestMetric.SOURCE_SKILL_MD,
        "/posts/<post_id>/delete/",
    ),
    (re.compile(r"^/companies/new/$"), SkillPageRequestMetric.SOURCE_SKILL_MD, "/companies/new/"),
    (
        re.compile(r"^/companies/[^/]+/$"),
        SkillPageRequestMetric.SOURCE_SKILL_MD,
        "/companies/<slug>/",
    ),
    (re.compile(r"^/network/$"), SkillPageRequestMetric.SOURCE_SKILL_MD, "/network/"),
    (re.compile(r"^/network/search/$"), SkillPageRequestMetric.SOURCE_SKILL_MD, "/network/search/"),
    (
        re.compile(r"^/network/connections/$"),
        SkillPageRequestMetric.SOURCE_SKILL_MD,
        "/network/connections/",
    ),
    (
        re.compile(r"^/network/followers/$"),
        SkillPageRequestMetric.SOURCE_SKILL_MD,
        "/network/followers/",
    ),
    (
        re.compile(r"^/network/mutuals/$"),
        SkillPageRequestMetric.SOURCE_SKILL_MD,
        "/network/mutuals/",
    ),
    (
        re.compile(r"^/network/invitations/$"),
        SkillPageRequestMetric.SOURCE_SKILL_MD,
        "/network/invitations/",
    ),
    (
        re.compile(r"^/network/invitations/send/\d+/$"),
        SkillPageRequestMetric.SOURCE_SKILL_MD,
        "/network/invitations/send/<user_id>/",
    ),
    (
        re.compile(r"^/network/invitations/\d+/accept/$"),
        SkillPageRequestMetric.SOURCE_SKILL_MD,
        "/network/invitations/<invitation_id>/accept/",
    ),
    (
        re.compile(r"^/network/invitations/\d+/decline/$"),
        SkillPageRequestMetric.SOURCE_SKILL_MD,
        "/network/invitations/<invitation_id>/decline/",
    ),
    (
        re.compile(r"^/network/invitations/\d+/withdraw/$"),
        SkillPageRequestMetric.SOURCE_SKILL_MD,
        "/network/invitations/<invitation_id>/withdraw/",
    ),
    (
        re.compile(r"^/network/connections/\d+/remove/$"),
        SkillPageRequestMetric.SOURCE_SKILL_MD,
        "/network/connections/<user_id>/remove/",
    ),
    (
        re.compile(r"^/network/follow/\d+/$"),
        SkillPageRequestMetric.SOURCE_SKILL_MD,
        "/network/follow/<user_id>/",
    ),
    (re.compile(r"^/messaging/$"), SkillPageRequestMetric.SOURCE_SKILL_MD, "/messaging/"),
    (re.compile(r"^/messaging/dms/$"), SkillPageRequestMetric.SOURCE_SKILL_MD, "/messaging/dms/"),
    (
        re.compile(r"^/messaging/dms/new/$"),
        SkillPageRequestMetric.SOURCE_SKILL_MD,
        "/messaging/dms/new/",
    ),
    (
        re.compile(r"^/messaging/dms/\d+/$"),
        SkillPageRequestMetric.SOURCE_SKILL_MD,
        "/messaging/dms/<message_id>/",
    ),
    (
        re.compile(r"^/messaging/inmail/$"),
        SkillPageRequestMetric.SOURCE_SKILL_MD,
        "/messaging/inmail/",
    ),
    (
        re.compile(r"^/messaging/inmail/new/$"),
        SkillPageRequestMetric.SOURCE_SKILL_MD,
        "/messaging/inmail/new/",
    ),
    (
        re.compile(r"^/messaging/inmail/\d+/$"),
        SkillPageRequestMetric.SOURCE_SKILL_MD,
        "/messaging/inmail/<message_id>/",
    ),
    (
        re.compile(r"^/messaging/groups/$"),
        SkillPageRequestMetric.SOURCE_SKILL_MD,
        "/messaging/groups/",
    ),
    (
        re.compile(r"^/messaging/groups/new/$"),
        SkillPageRequestMetric.SOURCE_SKILL_MD,
        "/messaging/groups/new/",
    ),
    (
        re.compile(r"^/messaging/groups/\d+/$"),
        SkillPageRequestMetric.SOURCE_SKILL_MD,
        "/messaging/groups/<thread_id>/",
    ),
    (re.compile(r"^/jobs/$"), SkillPageRequestMetric.SOURCE_SKILL_MD, "/jobs/"),
    (re.compile(r"^/jobs/\d+/$"), SkillPageRequestMetric.SOURCE_SKILL_MD, "/jobs/<job_id>/"),
    (
        re.compile(r"^/jobs/proxy/search/$"),
        SkillPageRequestMetric.SOURCE_SKILL_MD,
        "/jobs/proxy/search/",
    ),
    (
        re.compile(r"^/jobs/proxy/\d+/$"),
        SkillPageRequestMetric.SOURCE_SKILL_MD,
        "/jobs/proxy/<job_id>/",
    ),
    (
        re.compile(r"^/api/v1/health/$"),
        SkillPageRequestMetric.SOURCE_SKILL_MD,
        "/api/v1/health/",
    ),
    (re.compile(r"^/api/v1/me/$"), SkillPageRequestMetric.SOURCE_SKILL_MD, "/api/v1/me/"),
    (re.compile(r"^/api/v1/tokens/$"), SkillPageRequestMetric.SOURCE_SKILL_MD, "/api/v1/tokens/"),
    (
        re.compile(r"^/api/v1/tokens/\d+/$"),
        SkillPageRequestMetric.SOURCE_SKILL_MD,
        "/api/v1/tokens/<token_id>/",
    ),
    (re.compile(r"^/api/v1/posts/$"), SkillPageRequestMetric.SOURCE_SKILL_MD, "/api/v1/posts/"),
    (
        re.compile(r"^/api/v1/posts/\d+/$"),
        SkillPageRequestMetric.SOURCE_SKILL_MD,
        "/api/v1/posts/<post_id>/",
    ),
    (
        re.compile(r"^/api/v1/companies/$"),
        SkillPageRequestMetric.SOURCE_SKILL_MD,
        "/api/v1/companies/",
    ),
    (
        re.compile(r"^/api/v1/companies/[^/]+/$"),
        SkillPageRequestMetric.SOURCE_SKILL_MD,
        "/api/v1/companies/<slug>/",
    ),
    (re.compile(r"^/api/v1/skills/$"), SkillPageRequestMetric.SOURCE_SKILL_MD, "/api/v1/skills/"),
    (
        re.compile(r"^/api/v1/skills/\d+/$"),
        SkillPageRequestMetric.SOURCE_SKILL_MD,
        "/api/v1/skills/<skill_id>/",
    ),
    (re.compile(r"^/api/v1/resumes/$"), SkillPageRequestMetric.SOURCE_SKILL_MD, "/api/v1/resumes/"),
    (
        re.compile(r"^/api/v1/resumes/\d+/$"),
        SkillPageRequestMetric.SOURCE_SKILL_MD,
        "/api/v1/resumes/<resume_id>/",
    ),
    (
        re.compile(r"^/api/v1/jobs/search/$"),
        SkillPageRequestMetric.SOURCE_SKILL_MD,
        "/api/v1/jobs/search/",
    ),
    (
        re.compile(r"^/api/v1/jobs/\d+/$"),
        SkillPageRequestMetric.SOURCE_SKILL_MD,
        "/api/v1/jobs/<job_id>/",
    ),
    (
        re.compile(r"^/agents/manager/[^/]+/dashboard/chat/$"),
        SkillPageRequestMetric.SOURCE_AGENT_DASHBOARD,
        "/agents/manager/<pod_name>/dashboard/chat/",
    ),
]


def match_skill_page_route(path: str):
    for pattern, source, normalized_path in SKILL_PAGE_PATTERNS:
        if pattern.match(path):
            return source, normalized_path
    return None


def record_skill_page_metric(request, status_code: int, source: str, normalized_path: str) -> None:
    method = (request.method or "GET").upper()
    now = timezone.now()
    user = request.user if getattr(request, "user", None) and request.user.is_authenticated else None
    actor_key = f"user:{user.id}" if user else "anon"

    defaults = {
        "user": user,
        "total_calls": 0,
        "success_calls": 0,
        "error_calls": 0,
        "last_status_code": status_code,
        "last_called_at": now,
    }
    metric, _ = SkillPageRequestMetric.objects.get_or_create(
        date=now.date(),
        actor_key=actor_key,
        source=source,
        method=method,
        normalized_path=normalized_path,
        defaults=defaults,
    )
    SkillPageRequestMetric.objects.filter(id=metric.id).update(
        user=user,
        total_calls=F("total_calls") + 1,
        success_calls=F("success_calls") + (1 if status_code < 400 else 0),
        error_calls=F("error_calls") + (1 if status_code >= 400 else 0),
        last_status_code=status_code,
        last_called_at=now,
    )


class SkillPageAnalyticsMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        match = match_skill_page_route(request.path_info or request.path)
        response = self.get_response(request)
        if not match:
            return response
        source, normalized_path = match
        try:
            record_skill_page_metric(request, response.status_code, source, normalized_path)
        except Exception:  # pragma: no cover - analytics must not break requests
            logger.exception(
                "Failed to record analytics for %s %s",
                request.method,
                normalized_path,
            )
        return response
