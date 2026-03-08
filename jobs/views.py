import os

import requests
from django.http import JsonResponse
from django.shortcuts import render

ATHENA_JOBS_BASE_URL = os.environ.get("ATHENA_JOBS_BASE_URL", "https://jobs.athena.live")
ATHENA_JOBS_SEARCH_PATH = "/api/jobs/"
ALLOWED_QUERY_PARAMS = {
    "search",
    "q",
    "location",
    "place_id",
    "placeId",
    "latitude",
    "lat",
    "longitude",
    "lng",
    "radius_km",
    "radius",
    "company",
    "scraper",
    "type",
    "employment_type",
    "created_after",
    "created_before",
    "page",
    "page_size",
}


def jobs_search_page(request):
    return render(request, "jobs/search.html")


def jobs_search_proxy(request):
    params = {key: value for key, value in request.GET.items() if key in ALLOWED_QUERY_PARAMS and value}

    if "search" not in params and "q" in params:
        params["search"] = params.pop("q")

    try:
        upstream_response = requests.get(
            f"{ATHENA_JOBS_BASE_URL}{ATHENA_JOBS_SEARCH_PATH}",
            params=params,
            timeout=15,
        )
        upstream_response.raise_for_status()
    except requests.RequestException as exc:
        return JsonResponse({"error": "Unable to fetch jobs from Athena API.", "detail": str(exc)}, status=502)

    try:
        payload = upstream_response.json()
    except ValueError:
        return JsonResponse({"error": "Athena API returned invalid JSON."}, status=502)

    return JsonResponse(payload, status=upstream_response.status_code)
