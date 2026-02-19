from django.urls import re_path

from . import consumers

websocket_urlpatterns = [
    re_path(r"^ws/agents/terminal/(?P<pod_name>[^/]+)/$", consumers.PodTerminalConsumer.as_asgi()),
]
