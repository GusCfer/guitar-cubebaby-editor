"""Conexão USB-MIDI com o CUBE Baby (elétrico, chip 0x4F06).

Transporte reaproveitado de walbertfreitas/cubebaby-editor (MIT).
Mapa de presets confirmado por MrGariZack/cubecontrol (MIT) e por leitura do pedal.

Mapa de memória usado (somente banco 5 — RAM/presets; o app NUNCA escreve no banco 0/flash):
  banco 5, addr 0                 48 bytes = presets A/B/C (16 bytes cada)
  banco 5, 0x80000000 + slot*16   mesmo layout, aplicado ao vivo (não persiste)
"""
from __future__ import annotations

import queue
import threading
import time
from dataclasses import dataclass

import rtmidi

from . import protocol as p

PORT_HINT = "USB2.0 Device"
SUPPORTED_CHIP = 0x4F06

PRESET_BANK = 5
SLOT_SIZE = 16
SLOT_COUNT = 3
BANK_SIZE = SLOT_SIZE * SLOT_COUNT


class DeviceError(Exception):
    pass


@dataclass(frozen=True)
class Param:
    key: str
    label: str
    offset: int
    maximum: int
    section: str  # "drive" | "fx" | "amb" | "master"


# Ordem dos bytes 0..9 dentro de cada slot (cubecontrol: LIVE_PARAM_BANK_OFFSET / LIVE_PARAM_MAX)
PARAMS: list[Param] = [
    Param("type", "TYPE", 0, 8, "drive"),
    Param("gain", "GAIN", 1, 7, "drive"),
    Param("tone", "TONE", 2, 15, "drive"),
    Param("reverb", "REVERB", 3, 15, "amb"),
    Param("feedback", "FB", 4, 127, "fx"),
    Param("volume", "VOLUME", 5, 127, "master"),
    Param("time", "TIME", 6, 31, "fx"),
    Param("mix", "MIX", 7, 118, "fx"),
    Param("modulation", "MOD", 8, 15, "fx"),
    Param("cabinet", "IR CAB", 9, 8, "amb"),
]
PARAM_BY_KEY = {prm.key: prm for prm in PARAMS}

# Liga/desliga de seção (bytes 10..12)
SECTIONS = {
    "amb": ("irSection", 10),    # IR CAB + REVERB
    "fx": ("delaySection", 11),  # MOD + MIX/FB/TIME
    "drive": ("toneSection", 12),  # TYPE/GAIN/TONE
}
FIELD_COUNT = 13  # bytes 0..12 são editáveis; 13..15 ficam como estão


def find_port(midi, hint: str = PORT_HINT) -> int | None:
    for i, name in enumerate(midi.get_ports()):
        if hint in name and "WINE" not in name:
            return i
    return None


class CubeBaby:
    """Uma transação por vez; o pedal responde a cada quadro enviado."""

    def __init__(self, hint: str = PORT_HINT):
        self._out = rtmidi.MidiOut(name="Cube Baby Editor")
        self._in = rtmidi.MidiIn(name="Cube Baby Editor")
        self._in.ignore_types(sysex=False)
        oi, ii = find_port(self._out, hint), find_port(self._in, hint)
        if oi is None or ii is None:
            raise DeviceError("Pedal não encontrado. Ele está ligado e conectado no USB?")
        self._out.open_port(oi)
        self._in.open_port(ii)
        self._rx: queue.Queue[p.Frame] = queue.Queue()
        self._lock = threading.Lock()
        self._in.set_callback(self._on_midi)
        self.block = 0x80
        try:
            name = self.info()[:14]
            raw = self.read(0, p.FLASH_INFO_ADDR, 20)
        except DeviceError:
            self.close()
            raise
        self.chip_id = int.from_bytes(raw[4:8], "little")
        if name != b"SINCO-CubeBaby" or self.chip_id != SUPPORTED_CHIP:
            self.close()
            raise DeviceError(
                f"Modelo não suportado (id 0x{self.chip_id:04X}). "
                "Este app é para o CUBE Baby elétrico (0x4F06)."
            )
        self.block = p.block_size(self.chip_id)

    def close(self):
        try:
            self._in.cancel_callback()
            self._in.close_port()
            self._out.close_port()
        except Exception:  # noqa: BLE001
            pass

    def _on_midi(self, event, _data=None):
        msg, _dt = event
        if msg and msg[0] == 0xF0:
            try:
                self._rx.put(p.parse_sysex(bytes(msg)))
            except p.ProtocolError:
                pass

    def transact(self, sysex: bytes, expect_cmd: int, timeout: float = 2.0) -> p.Frame:
        with self._lock:
            while not self._rx.empty():
                self._rx.get_nowait()
            self._out.send_message(list(sysex))
            deadline = time.monotonic() + timeout
            while (left := deadline - time.monotonic()) > 0:
                try:
                    frame = self._rx.get(timeout=left)
                except queue.Empty:
                    break
                if frame.cmd == expect_cmd:
                    return frame
            raise DeviceError(f"Sem resposta do pedal (comando 0x{expect_cmd:02X}). O cabo está conectado?")

    def info(self) -> bytes:
        return self.transact(p.build_frame(p.CMD_INFO), p.CMD_INFO).data

    def read(self, bank: int, addr: int, length: int) -> bytes:
        out = bytearray()
        while length > 0:
            n = min(length, self.block - 15)
            fr = self.transact(p.read_frame(bank, addr, n), p.CMD_READ)
            hdr, payload = fr.data[:8], fr.data[8:]
            if hdr != p.mem_header(bank, addr, n) or len(payload) != n:
                raise DeviceError("Resposta de leitura inesperada")
            out += payload
            addr += n
            length -= n
        return bytes(out)

    def _write(self, bank: int, addr: int, data: bytes) -> None:
        # Trava de segurança: só o banco 5 (presets). Flash/firmware (banco 0) é intocável.
        if bank != PRESET_BANK:
            raise DeviceError("Bloqueado: o app só escreve no banco de presets.")
        live = addr & p.LIVE
        off = addr & ~p.LIVE
        if off + len(data) > BANK_SIZE:
            raise DeviceError("Bloqueado: escrita fora da área de presets.")
        for i in range(0, len(data), self.block):
            chunk = data[i:i + self.block]
            self.transact(p.write_frame(bank, live | (off + i), chunk), p.CMD_ACK)

    # --- alto nível ---------------------------------------------------------

    def read_bank(self) -> bytes:
        return self.read(PRESET_BANK, 0, BANK_SIZE)

    def write_live(self, slot: int, offset: int, value: int) -> None:
        if not (0 <= slot < SLOT_COUNT and 0 <= offset < FIELD_COUNT and 0 <= value <= 127):
            raise DeviceError("Valor ao vivo inválido")
        self._write(PRESET_BANK, p.LIVE + slot * SLOT_SIZE + offset, bytes([value]))

    def push_slot_live(self, slot: int, fields: bytes) -> None:
        self.info()  # "acorda" o DSP antes de uma rajada (igual ao cubecontrol)
        for off in range(FIELD_COUNT):
            self.write_live(slot, off, fields[off])
            time.sleep(0.03)

    def save_bank(self, data: bytes) -> None:
        if len(data) != BANK_SIZE:
            raise DeviceError("Banco de presets deve ter 48 bytes")
        self._write(PRESET_BANK, 0, data)
        time.sleep(0.25)
        back = self.read_bank()
        if back != data:
            time.sleep(0.25)
            back = self.read_bank()
        if back != data:
            raise DeviceError("A verificação falhou: o pedal não confirmou o que foi gravado.")
