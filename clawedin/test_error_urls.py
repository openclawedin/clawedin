from django.http import HttpResponse
from django.urls import path


def ok_view(request):
    return HttpResponse("ok")


def boom_view(request):
    raise RuntimeError("sensitive internal failure")


handler404 = "clawedin.error_views.page_not_found"
handler500 = "clawedin.error_views.server_error"

urlpatterns = [
    path("ok/", ok_view, name="ok"),
    path("boom/", boom_view, name="boom"),
]
