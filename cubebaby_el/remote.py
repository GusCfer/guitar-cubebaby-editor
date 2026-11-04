"""Controle pelo celular: servidor HTTP na rede local + página web.

O celular abre http://IP:PORTA/?t=TOKEN (via QR code). A página lê o estado por long-poll
(/api/state) e manda comandos (/api/cmd). O servidor nunca fala com o pedal direto: os
comandos são entregues à janela principal (thread do Qt), que usa o mesmo caminho dos knobs.
"""
from __future__ import annotations

import json
import secrets
import socket
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

PAGE = Path(__file__).resolve().parent / "assets" / "remote.html"
DEFAULT_PORT = 8765


def local_ips() -> list[str]:
    """IP da rede principal primeiro (o da rota padrão), depois os outros."""
    ips: list[str] = []
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(("10.255.255.255", 1))  # não envia nada; só escolhe a interface
            ips.append(s.getsockname()[0])
    except OSError:
        pass
    try:
        for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            ip = info[4][0]
            if ip not in ips and not ip.startswith("127."):
                ips.append(ip)
    except OSError:
        pass
    return ips or ["127.0.0.1"]


class RemoteServer:
    def __init__(self, on_command):
        self.on_command = on_command          # chamado na thread HTTP; deve só repassar (sinal Qt)
        self.token = secrets.token_urlsafe(9)
        self._state: dict = {"v": 0}
        self._cond = threading.Condition()
        self._httpd: ThreadingHTTPServer | None = None
        self.port = 0

    # --- estado (escrito pela thread do Qt, lido pelas threads HTTP)
    def publish(self, state: dict) -> None:
        with self._cond:
            state = dict(state)
            state["v"] = self._state["v"] + 1
            self._state = state
            self._cond.notify_all()

    def _wait_state(self, after: int, timeout: float = 20.0) -> dict:
        with self._cond:
            self._cond.wait_for(lambda: self._state["v"] != after, timeout)
            return self._state

    # --- ciclo de vida
    @property
    def running(self) -> bool:
        return self._httpd is not None

    def start(self) -> int:
        if self._httpd:
            return self.port
        handler = _make_handler(self)
        for port in (DEFAULT_PORT, DEFAULT_PORT + 1, DEFAULT_PORT + 2, 0):
            try:
                self._httpd = ThreadingHTTPServer(("0.0.0.0", port), handler)
                break
            except OSError:
                continue
        if not self._httpd:
            raise OSError("Não foi possível abrir uma porta para o celular")
        self._httpd.daemon_threads = True
        self.port = self._httpd.server_address[1]
        threading.Thread(target=self._httpd.serve_forever, daemon=True).start()
        return self.port

    def stop(self) -> None:
        httpd, self._httpd = self._httpd, None
        if httpd:
            self.publish({"closed": True})   # avisa e libera quem está em long-poll
            httpd.shutdown()
            httpd.server_close()

    def url(self, ip: str) -> str:
        return f"http://{ip}:{self.port}/?t={self.token}"


def _make_handler(server: RemoteServer):
    class Handler(BaseHTTPRequestHandler):
        server_version = "CubeBabyEditor"

        def log_message(self, *_):  # silencioso
            pass

        def _authorized(self, query: dict) -> bool:
            tok = (query.get("t") or [""])[0]
            return secrets.compare_digest(tok, server.token)

        def _send(self, code: int, body: bytes, ctype: str):
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            try:
                self.wfile.write(body)
            except (BrokenPipeError, ConnectionResetError):
                pass  # o celular fechou a página no meio do long-poll

        def _json(self, obj, code=200):
            self._send(code, json.dumps(obj, ensure_ascii=False).encode(), "application/json; charset=utf-8")

        def do_GET(self):
            url = urlparse(self.path)
            q = parse_qs(url.query)
            if not self._authorized(q):
                return self._send(403, "Link inválido. Leia o QR code de novo no computador.".encode(),
                                  "text/plain; charset=utf-8")
            if url.path == "/":
                return self._send(200, PAGE.read_bytes(), "text/html; charset=utf-8")
            if url.path == "/api/state":
                try:
                    after = int((q.get("v") or ["-1"])[0])
                except ValueError:
                    after = -1
                return self._json(server._wait_state(after))
            self._send(404, b"not found", "text/plain")

        def do_POST(self):
            url = urlparse(self.path)
            if not self._authorized(parse_qs(url.query)) or url.path != "/api/cmd":
                return self._send(403, b"forbidden", "text/plain")
            try:
                n = min(int(self.headers.get("Content-Length", "0")), 40_000)
                cmd = json.loads(self.rfile.read(n) or b"{}")
                if not isinstance(cmd, dict) or not isinstance(cmd.get("op"), str):
                    raise ValueError
            except (ValueError, json.JSONDecodeError):
                return self._json({"ok": False, "error": "comando inválido"}, 400)
            server.on_command(cmd)
            self._json({"ok": True})

    return Handler
