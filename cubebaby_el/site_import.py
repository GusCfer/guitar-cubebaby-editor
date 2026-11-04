"""Importa um preset a partir do link do cube-baby-presets.vercel.app.

A página monta os valores com JavaScript, então ela é aberta num navegador do próprio computador
(Brave/Chrome/Edge/Chromium) em modo invisível, com um perfil temporário, e o texto já renderizado
é lido — exatamente o que você veria na tela.
"""
from __future__ import annotations

import html
import os
import re
import shutil
import subprocess
import signal
import sys
import threading
from pathlib import Path

from .library import DATA_DIR, parse_site_text

# Perfil próprio e fixo: na 1ª vez que um perfil é criado o navegador pode travar inicializando;
# depois disso a página abre em ~1 s.
PROFILE_DIR = DATA_DIR / "web-profile"
_lock = threading.Lock()

SITE = "https://cube-baby-presets.vercel.app"
_URL_RE = re.compile(r"https?://cube-baby-presets\.vercel\.app/(?:[a-z]{2}(?:-[A-Z]{2})?/)?presets/(\d+)\b")


def preset_url(text: str) -> str | None:
    """Aceita o link do preset (qualquer idioma) ou só o número. Retorna o link normalizado."""
    text = (text or "").strip()
    m = _URL_RE.search(text)
    if m:
        return f"{SITE}/pt/presets/{m.group(1)}"
    if re.fullmatch(r"\d{1,9}", text):
        return f"{SITE}/pt/presets/{text}"
    return None


def find_browser() -> str | None:
    names = ["brave", "brave-browser", "google-chrome-stable", "google-chrome", "chromium", "chromium-browser",
             "microsoft-edge", "microsoft-edge-stable", "msedge", "chrome"]
    for n in names:
        p = shutil.which(n)
        if p:
            return p
    candidates: list[Path] = []
    if sys.platform == "win32":
        for base in filter(None, (os.environ.get("ProgramFiles"), os.environ.get("ProgramFiles(x86)"),
                                  os.environ.get("LOCALAPPDATA"))):
            b = Path(base)
            candidates += [b / "Microsoft/Edge/Application/msedge.exe",
                           b / "Google/Chrome/Application/chrome.exe",
                           b / "BraveSoftware/Brave-Browser/Application/brave.exe",
                           b / "Chromium/Application/chrome.exe"]
    elif sys.platform == "darwin":
        candidates += [Path("/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"),
                       Path("/Applications/Brave Browser.app/Contents/MacOS/Brave Browser"),
                       Path("/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge"),
                       Path("/Applications/Chromium.app/Contents/MacOS/Chromium")]
    return next((str(c) for c in candidates if c.exists()), None)


def _dom_text(dom: str) -> str:
    t = re.sub(r"<script.*?</script>|<style.*?</style>", " ", dom, flags=re.S | re.I)
    t = html.unescape(re.sub(r"<[^>]+>", "\n", t))
    return "\n".join(line.strip() for line in t.splitlines() if line.strip())


def _run_browser(exe: str, url: str, timeout: float) -> str | None:
    """Roda o navegador invisível; devolve o DOM ou None se estourar o tempo (matando todos os processos)."""
    PROFILE_DIR.mkdir(parents=True, exist_ok=True)
    cmd = [exe, "--headless=new", "--disable-gpu", "--no-first-run", "--no-default-browser-check",
           f"--user-data-dir={PROFILE_DIR}", "--virtual-time-budget=10000", "--dump-dom", url]
    kw: dict = {"stdout": subprocess.PIPE, "stderr": subprocess.DEVNULL}
    if sys.platform == "win32":
        kw["creationflags"] = subprocess.CREATE_NO_WINDOW | subprocess.CREATE_NEW_PROCESS_GROUP
    else:
        kw["start_new_session"] = True
    proc = subprocess.Popen(cmd, **kw)
    try:
        out, _ = proc.communicate(timeout=timeout)
        return out.decode("utf-8", "replace")
    except subprocess.TimeoutExpired:
        if sys.platform == "win32":
            subprocess.run(["taskkill", "/F", "/T", "/PID", str(proc.pid)], capture_output=True,
                           creationflags=subprocess.CREATE_NO_WINDOW)
        else:
            try:
                os.killpg(proc.pid, signal.SIGKILL)
            except OSError:
                pass
        proc.wait(5)
        return None


def _dump_dom(exe: str, url: str, first_timeout: float, timeout: float) -> str:
    dom = _run_browser(exe, url, first_timeout)
    if dom is None:  # provável 1ª inicialização do perfil: agora ele já existe, tenta de novo
        dom = _run_browser(exe, url, timeout)
    if dom is None:
        raise ValueError("A página demorou demais para abrir. Confira a internet e tente de novo.")
    return dom


def fetch_preset(link: str, timeout: float = 45.0) -> dict:
    """Abre o link num navegador invisível e devolve {"name", "author", "params"} (ver parse_site_text)."""
    url = preset_url(link)
    if not url:
        raise ValueError("Link inválido. Use um link como "
                         "https://cube-baby-presets.vercel.app/pt/presets/902 (ou só o número 902).")
    exe = find_browser()
    if not exe:
        raise ValueError("Não encontrei Chrome, Edge, Brave ou Chromium neste computador para abrir a página. "
                         "Instale um deles ou use “Colar preset do site” (Ctrl+A, Ctrl+C na página).")
    with _lock:
        dom = _dump_dom(exe, url, first_timeout=20.0, timeout=timeout)
    text = _dom_text(dom)
    if "preset not found" in text.lower() or "não encontrado" in text.lower():
        raise ValueError("Esse preset não existe (ou foi removido) no site.")
    got = parse_site_text(text)
    got["url"] = url
    return got
