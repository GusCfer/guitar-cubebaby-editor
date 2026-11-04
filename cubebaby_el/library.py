"""Biblioteca local de presets (JSON) e backups automáticos do banco do pedal."""
from __future__ import annotations

import json
import os
import re
import sys
import time
from pathlib import Path

from .device import BANK_SIZE, PARAMS, PARAM_BY_KEY, SECTIONS

def _data_root() -> Path:
    if sys.platform == "win32":
        return Path(os.environ.get("APPDATA", Path.home() / "AppData/Roaming"))
    if sys.platform == "darwin":
        return Path.home() / "Library/Application Support"
    return Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local/share"))


DATA_DIR = _data_root() / "cube-baby-editor"
PRESET_DIR = DATA_DIR / "presets"
BACKUP_DIR = DATA_DIR / "backups"
FORMAT = "cube-baby-preset/1"


def fields_to_dict(fields: bytes) -> dict:
    d = {prm.key: fields[prm.offset] for prm in PARAMS}
    for name, off in SECTIONS.values():
        d[name] = fields[off]
    return d


def dict_to_fields(d: dict, base: bytes) -> bytes:
    out = bytearray(base[:16].ljust(16, b"\0"))
    for key, prm in PARAM_BY_KEY.items():
        if key in d:
            out[prm.offset] = max(0, min(prm.maximum, int(d[key])))
    for name, off in SECTIONS.values():
        if name in d:
            out[off] = 1 if int(d[name]) else 0
    return bytes(out)


def _slug(name: str) -> str:
    s = re.sub(r"[^\w\- ]+", "", name, flags=re.UNICODE).strip().replace(" ", "_")
    return s[:60] or "preset"


class Library:
    def __init__(self, root: Path = PRESET_DIR):
        self.root = root
        self.root.mkdir(parents=True, exist_ok=True)

    def list(self) -> list[dict]:
        items = []
        for f in sorted(self.root.glob("*.json")):
            try:
                doc = json.loads(f.read_text("utf-8"))
                if doc.get("format") == FORMAT:
                    doc["_path"] = f
                    items.append(doc)
            except (OSError, ValueError):
                continue
        items.sort(key=lambda d: d.get("name", "").lower())
        return items

    def save(self, name: str, fields: bytes, path: Path | None = None) -> Path:
        doc = {"format": FORMAT, "name": name, "updated": time.strftime("%Y-%m-%d %H:%M"),
               "params": fields_to_dict(fields)}
        if path is None:
            path = self.root / f"{_slug(name)}.json"
            n = 2
            while path.exists():
                path = self.root / f"{_slug(name)}_{n}.json"
                n += 1
        path.write_text(json.dumps(doc, indent=2, ensure_ascii=False), "utf-8")
        return path

    def find(self, name: str) -> dict | None:
        return next((d for d in self.list() if d.get("name", "").lower() == name.lower()), None)

    @staticmethod
    def delete(doc: dict) -> None:
        doc["_path"].unlink(missing_ok=True)

    @staticmethod
    def read_file(path: Path) -> dict:
        doc = json.loads(Path(path).read_text("utf-8"))
        if doc.get("format") != FORMAT or not isinstance(doc.get("params"), dict):
            raise ValueError("Arquivo não é um preset do Cube Baby Editor")
        return doc

    @staticmethod
    def export(doc: dict, path: Path) -> None:
        clean = {k: v for k, v in doc.items() if not k.startswith("_")}
        Path(path).write_text(json.dumps(clean, indent=2, ensure_ascii=False), "utf-8")


def backup_bank(bank: bytes) -> Path:
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    path = BACKUP_DIR / f"banco-{time.strftime('%Y%m%d-%H%M%S')}.json"
    doc = {"format": "cube-baby-bank/1", "hex": bank.hex(),
           "slots": {s: fields_to_dict(bank[i * 16:(i + 1) * 16]) for i, s in enumerate("ABC")}}
    path.write_text(json.dumps(doc, indent=2), "utf-8")
    return path


def read_bank_file(path: Path) -> bytes:
    doc = json.loads(Path(path).read_text("utf-8"))
    data = bytes.fromhex(doc.get("hex", ""))
    if doc.get("format") != "cube-baby-bank/1" or len(data) != BANK_SIZE:
        raise ValueError("Arquivo não é um backup de banco válido")
    for i in range(3):
        for prm in PARAMS:
            if data[i * 16 + prm.offset] > prm.maximum:
                raise ValueError("Backup contém valores fora da faixa")
        if any(data[i * 16 + off] > 1 for _, off in SECTIONS.values()):
            raise ValueError("Backup contém valores fora da faixa")
    return data



# ---- importação a partir do texto copiado de cube-baby-presets.vercel.app
SITE_LABELS = {  # rótulo no site -> parâmetro
    "volume": "volume", "ir cab": "cabinet", "reverb": "reverb", "mix": "mix", "fb": "feedback",
    "time": "time", "mod": "modulation", "tone": "tone", "gain": "gain", "type": "type",
}


def parse_site_text(text: str) -> dict:
    """Extrai nome e valores dos knobs do texto de uma página de preset do site.

    Funciona com o texto copiado (Ctrl+A, Ctrl+C) da página, com quebras de linha ou tudo numa linha só.
    Retorna {"name", "author", "params"}; levanta ValueError se não achar os knobs ou se for de outro modelo.
    """
    flat = re.sub(r"\s+", " ", text).strip()
    if re.search(r"\bCube Baby (AC|Bass)\b", flat, re.I):
        raise ValueError("Esse preset é de outro modelo (Cube Baby AC ou Bass), não do CUBE Baby elétrico.")
    m = re.search(r"valores dos knobs|knob values", flat, re.I)
    body = flat[m.end():] if m else flat
    params: dict[str, int] = {}
    for label, key in SITE_LABELS.items():
        hit = re.search(rf"(?<![a-z]){label}\s*:?\s*(\d{{1,3}})\b", body, re.I)
        if hit:
            params[key] = int(hit.group(1))
    if len(params) < 6:
        raise ValueError("Não encontrei os valores dos knobs. Abra a página do preset no site, selecione tudo "
                         "(Ctrl+A), copie (Ctrl+C) e tente de novo.")
    for key, v in params.items():
        mx = PARAM_BY_KEY[key].maximum
        if v > mx:
            raise ValueError(f"{PARAM_BY_KEY[key].label} = {v} passa do máximo do pedal ({mx}).")
    name = author = ""
    t = re.search(r"Cube Baby Presets\s*-\s*(.+?)\s+Presets\b", flat)
    if t:
        name = re.sub(r"\s+", " ", t.group(1)).strip()
        a = re.search(re.escape(name) + r"\s+(.+?)\s+\d+\s+\d+\s", flat[t.end():])
        if a and len(a.group(1)) <= 40:
            author = a.group(1).strip()
    for sec in ("irSection", "delaySection", "toneSection"):  # o site não guarda liga/desliga
        params.setdefault(sec, 1)
    return {"name": name, "author": author, "params": params}
