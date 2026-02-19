import threading
import time

from asgiref.sync import async_to_sync
from channels.generic.websocket import WebsocketConsumer
from kubernetes import client, stream

from .kube import load_kube_config, resolve_agent_namespace


class PodTerminalConsumer(WebsocketConsumer):
    def connect(self):
        user = self.scope.get("user")
        if not user or not user.is_authenticated:
            self.close()
            return
        from .models import User

        if user.account_type != User.HUMAN:
            self.close()
            return

        self.pod_name = self.scope["url_route"]["kwargs"]["pod_name"]
        self.namespace, _ = resolve_agent_namespace(user.username, user.id)
        self.exec_stream = None
        self.reader_thread = None

        try:
            load_kube_config()
            v1 = client.CoreV1Api()
            pod = v1.read_namespaced_pod(name=self.pod_name, namespace=self.namespace)
            container_name = pod.spec.containers[0].name
            self.exec_stream = self._open_stream(v1, container_name)
        except Exception:
            self.close()
            return

        if not self.exec_stream:
            self.close()
            return

        self.accept()
        self.reader_thread = threading.Thread(target=self._read_loop, daemon=True)
        self.reader_thread.start()

    def _open_stream(self, v1, container_name: str):
        try:
            exec_stream = stream.stream(
                v1.connect_get_namespaced_pod_exec,
                self.pod_name,
                self.namespace,
                container=container_name,
                command=["/bin/bash"],
                stderr=True,
                stdin=True,
                stdout=True,
                tty=True,
                _preload_content=False,
            )
            if exec_stream.is_open():
                return exec_stream
        except Exception:
            pass

        try:
            exec_stream = stream.stream(
                v1.connect_get_namespaced_pod_exec,
                self.pod_name,
                self.namespace,
                container=container_name,
                command=["/bin/sh"],
                stderr=True,
                stdin=True,
                stdout=True,
                tty=True,
                _preload_content=False,
            )
            if exec_stream.is_open():
                return exec_stream
        except Exception:
            pass

        return None

    def _read_loop(self):
        while self.exec_stream and self.exec_stream.is_open():
            self.exec_stream.update(timeout=1)
            stdout = None
            stderr = None
            if self.exec_stream.peek_stdout():
                stdout = self.exec_stream.read_stdout()
            if self.exec_stream.peek_stderr():
                stderr = self.exec_stream.read_stderr()
            if stdout:
                self._emit(stdout)
            if stderr:
                self._emit(stderr)
            time.sleep(0.01)

    def _emit(self, payload: str) -> None:
        if not self.channel_layer:
            return
        async_to_sync(self.channel_layer.send)(
            self.channel_name,
            {
                "type": "terminal.message",
                "text": payload,
            },
        )

    def terminal_message(self, event):
        self.send(text_data=event.get("text", ""))

    def receive(self, text_data=None, bytes_data=None):
        if not self.exec_stream or not self.exec_stream.is_open():
            return
        if text_data:
            self.exec_stream.write_stdin(text_data)
        if bytes_data:
            self.exec_stream.write_stdin(bytes_data)

    def disconnect(self, close_code):
        if self.exec_stream and self.exec_stream.is_open():
            self.exec_stream.write_stdin("exit\n")
            self.exec_stream.close()
        self.exec_stream = None
