"""Protocolo SysEx do Cube Baby (Cuvave/M-VAVE), reconstruído por interoperabilidade.

Quadro (antes do empacotamento): 00 59 | CMD | LEN (u24 LE) | DATA | CHK
CHK = ~soma(DATA) & 0xFF. O quadro vai dentro de F0 ... F7, empacotado em 7 bits
(fluxo de bits LSB-first). Baseado em walbertfreitas/cubebaby-editor e MrGariZack/cubecontrol (MIT).
"""
from __future__ import annotations

from dataclasses import dataclass

MAGIC = b"\x00\x59"

CMD_ACK = 0x00
CMD_INFO = 0x11
CMD_STATUS = 0x12
CMD_ERASE = 0x21
CMD_WRITE = 0x22
CMD_READ = 0x23
CMD_SUM = 0x24

# Endereços com este bit são aplicados ao vivo (RAM); sem ele, vão para a memória permanente.
LIVE = 0x80000000

FLASH_INFO_ADDR = 0x2100  # banco 0: 20 bytes com id do chip e endereços (FUN_005071f0)

# Bloco por transação conforme o id do chip (FUN_005071f0); o leitor desconta 15 bytes de cabeçalho.
SMALL_BLOCK_IDS = {0x76DF, 0x4F06, 0x272E, 0x5032, 0x4FCE, 0x2792, 0x4E3E, 0x4EA2, 0x4F09, 0x272F, 0x4E3F}
MEDIUM_BLOCK_IDS = {0x2733, 0x29E5, 0x2A49}


def block_size(chip_id: int) -> int:
    if chip_id in SMALL_BLOCK_IDS:
        return 0x80
    if chip_id in MEDIUM_BLOCK_IDS:
        return 0x200
    return 0x400


class ProtocolError(Exception):
    pass


def pack7(data: bytes) -> bytes:
    """Empacota bytes de 8 bits em bytes de 7 bits (LSB-first), como FUN_00486980."""
    out = bytearray()
    acc = nbits = 0
    for b in data:
        acc |= b << nbits
        nbits += 8
        while nbits >= 7:
            out.append(acc & 0x7F)
            acc >>= 7
            nbits -= 7
    if nbits:
        out.append(acc & 0x7F)
    return bytes(out)


def unpack7(data: bytes) -> bytes:
    out = bytearray()
    acc = nbits = 0
    for b in data:
        acc |= (b & 0x7F) << nbits
        nbits += 7
        while nbits >= 8:
            out.append(acc & 0xFF)
            acc >>= 8
            nbits -= 8
    return bytes(out)


def checksum(data: bytes) -> int:
    return ~sum(data) & 0xFF


def build_frame(cmd: int, data: bytes = b"") -> bytes:
    frame = MAGIC + bytes([cmd]) + len(data).to_bytes(3, "little") + data + bytes([checksum(data)])
    return b"\xF0" + pack7(frame) + b"\xF7"


@dataclass
class Frame:
    cmd: int
    data: bytes


def parse_sysex(sysex: bytes) -> Frame:
    if len(sysex) < 3 or sysex[0] != 0xF0 or sysex[-1] != 0xF7:
        raise ProtocolError("não é SysEx")
    raw = unpack7(sysex[1:-1])
    if raw[:2] != MAGIC or len(raw) < 7:
        raise ProtocolError(f"cabeçalho inválido: {raw[:6].hex(' ')}")
    cmd = raw[2]
    length = int.from_bytes(raw[3:6], "little")
    data = raw[6:6 + length]
    if len(data) != length or len(raw) < 7 + length:
        raise ProtocolError(f"tamanho incorreto: esperado {length}, veio {len(data)}")
    if raw[6 + length] != checksum(data):
        raise ProtocolError("checksum inválido")
    return Frame(cmd, data)


def mem_header(bank: int, addr: int, length: int) -> bytes:
    return bytes([bank]) + addr.to_bytes(4, "little") + length.to_bytes(3, "little")


def write_frame(bank: int, addr: int, payload: bytes) -> bytes:
    return build_frame(CMD_WRITE, mem_header(bank, addr, len(payload)) + payload)


def read_frame(bank: int, addr: int, length: int) -> bytes:
    return build_frame(CMD_READ, mem_header(bank, addr, length))
