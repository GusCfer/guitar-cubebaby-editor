"""Gerador de QR code embutido (sem dependências).

Implementa o padrão ISO/IEC 18004: modo byte, correção de erro nível M, versões 1–10
(até 213 bytes — sobra para o link do controle pelo celular). Estrutura baseada no
algoritmo de referência de Project Nayuki (MIT).
"""
from __future__ import annotations

# Por versão 1..10 (índice 0 sem uso), nível M
_ECC_PER_BLOCK = [-1, 10, 16, 26, 18, 24, 16, 18, 22, 22, 26]
_NUM_BLOCKS = [-1, 1, 1, 1, 2, 2, 4, 4, 4, 5, 5]
_ECL_FORMAT_BITS = 0  # M


def _gf_mul(x: int, y: int) -> int:
    z = 0
    for i in range(7, -1, -1):
        z = (z << 1) ^ ((z >> 7) * 0x11D)
        z ^= ((y >> i) & 1) * x
    return z


def _rs_divisor(degree: int) -> list[int]:
    result = [0] * (degree - 1) + [1]
    root = 1
    for _ in range(degree):
        for j in range(degree):
            result[j] = _gf_mul(result[j], root)
            if j + 1 < degree:
                result[j] ^= result[j + 1]
        root = _gf_mul(root, 0x02)
    return result


def _rs_remainder(data: list[int], divisor: list[int]) -> list[int]:
    result = [0] * len(divisor)
    for b in data:
        factor = b ^ result.pop(0)
        result.append(0)
        for i, coef in enumerate(divisor):
            result[i] ^= _gf_mul(coef, factor)
    return result


def _raw_modules(ver: int) -> int:
    result = (16 * ver + 128) * ver + 64
    if ver >= 2:
        n = ver // 7 + 2
        result -= (25 * n - 10) * n - 55
        if ver >= 7:
            result -= 36
    return result


def _data_codewords(ver: int) -> int:
    return _raw_modules(ver) // 8 - _ECC_PER_BLOCK[ver] * _NUM_BLOCKS[ver]


def _alignment_positions(ver: int, size: int) -> list[int]:
    if ver == 1:
        return []
    n = ver // 7 + 2
    step = (ver * 8 + n * 3 + 5) // (n * 4 - 4) * 2
    return list(reversed([size - 7 - i * step for i in range(n - 1)] + [6]))


def _mask_bit(mask: int, x: int, y: int) -> bool:
    return [
        lambda: (x + y) % 2 == 0,
        lambda: y % 2 == 0,
        lambda: x % 3 == 0,
        lambda: (x + y) % 3 == 0,
        lambda: (x // 3 + y // 2) % 2 == 0,
        lambda: x * y % 2 + x * y % 3 == 0,
        lambda: (x * y % 2 + x * y % 3) % 2 == 0,
        lambda: ((x + y) % 2 + x * y % 3) % 2 == 0,
    ][mask]()


class _Builder:
    def __init__(self, ver: int, codewords: list[int]):
        self.ver = ver
        self.size = ver * 4 + 17
        n = self.size
        self.m = [[False] * n for _ in range(n)]
        self.fn = [[False] * n for _ in range(n)]
        self._function_patterns()
        self._draw_codewords(codewords)

    def _set(self, x, y, dark):
        self.m[y][x] = dark
        self.fn[y][x] = True

    def _function_patterns(self):
        n = self.size
        for i in range(n):
            self._set(6, i, i % 2 == 0)
            self._set(i, 6, i % 2 == 0)
        for cx, cy in ((3, 3), (n - 4, 3), (3, n - 4)):
            for dy in range(-4, 5):
                for dx in range(-4, 5):
                    x, y = cx + dx, cy + dy
                    if 0 <= x < n and 0 <= y < n:
                        self._set(x, y, max(abs(dx), abs(dy)) not in (2, 4))
        pos = _alignment_positions(self.ver, n)
        last = len(pos) - 1
        for i, a in enumerate(pos):
            for j, b in enumerate(pos):
                if (i, j) in ((0, 0), (0, last), (last, 0)):
                    continue
                for dy in range(-2, 3):
                    for dx in range(-2, 3):
                        self._set(a + dx, b + dy, max(abs(dx), abs(dy)) != 1)
        self._format_bits(0)
        if self.ver >= 7:
            rem = self.ver
            for _ in range(12):
                rem = (rem << 1) ^ ((rem >> 11) * 0x1F25)
            bits = self.ver << 12 | rem
            for i in range(18):
                bit = (bits >> i) & 1 == 1
                a, b = n - 11 + i % 3, i // 3
                self._set(a, b, bit)
                self._set(b, a, bit)

    def _format_bits(self, mask: int):
        data = _ECL_FORMAT_BITS << 3 | mask
        rem = data
        for _ in range(10):
            rem = (rem << 1) ^ ((rem >> 9) * 0x537)
        bits = (data << 10 | rem) ^ 0x5412
        bit = lambda i: (bits >> i) & 1 == 1  # noqa: E731
        n = self.size
        for i in range(6):
            self._set(8, i, bit(i))
        self._set(8, 7, bit(6))
        self._set(8, 8, bit(7))
        self._set(7, 8, bit(8))
        for i in range(9, 15):
            self._set(14 - i, 8, bit(i))
        for i in range(8):
            self._set(n - 1 - i, 8, bit(i))
        for i in range(8, 15):
            self._set(8, n - 15 + i, bit(i))
        self._set(8, n - 8, True)

    def _draw_codewords(self, data: list[int]):
        n = self.size
        i = 0
        right = n - 1
        while right >= 1:
            if right == 6:
                right = 5
            for vert in range(n):
                for j in range(2):
                    x = right - j
                    upward = (right + 1) & 2 == 0
                    y = n - 1 - vert if upward else vert
                    if not self.fn[y][x] and i < len(data) * 8:
                        self.m[y][x] = (data[i >> 3] >> (7 - (i & 7))) & 1 == 1
                        i += 1
            right -= 2

    def masked(self, mask: int) -> list[list[bool]]:
        n = self.size
        out = [row[:] for row in self.m]
        for y in range(n):
            for x in range(n):
                if not self.fn[y][x] and _mask_bit(mask, x, y):
                    out[y][x] = not out[y][x]
        saved = (self.m, self.fn)
        self.m, self.fn = out, [row[:] for row in self.fn]
        self._format_bits(mask)
        out = self.m
        self.m, self.fn = saved
        return out


def _penalty(m: list[list[bool]]) -> int:
    n = len(m)
    score = 0
    lines = m + [list(col) for col in zip(*m)]
    for line in lines:  # regra 1: sequências de 5+ da mesma cor
        run, prev = 0, None
        for c in line:
            if c == prev:
                run += 1
                if run == 5:
                    score += 3
                elif run > 5:
                    score += 1
            else:
                run, prev = 1, c
        s = "".join("1" if c else "0" for c in line)  # regra 3: padrão parecido com o localizador
        for pat in ("10111010000", "00001011101"):
            start = s.find(pat)
            while start != -1:
                score += 40
                start = s.find(pat, start + 1)
    for y in range(n - 1):  # regra 2: blocos 2x2
        for x in range(n - 1):
            c = m[y][x]
            if c == m[y][x + 1] == m[y + 1][x] == m[y + 1][x + 1]:
                score += 3
    dark = sum(map(sum, m))  # regra 4: equilíbrio claro/escuro
    total = n * n
    k = (abs(dark * 20 - total * 10) + total - 1) // total - 1
    return score + max(0, k) * 10


def encode(text: str, mask: int | None = None) -> list[list[bool]]:
    """Retorna a matriz do QR code (True = módulo escuro), sem borda."""
    data = text.encode("utf-8")
    for ver in range(1, 11):
        count_bits = 8 if ver <= 9 else 16
        if 4 + count_bits + len(data) * 8 <= _data_codewords(ver) * 8:
            break
    else:
        raise ValueError("Texto longo demais para o QR code")
    capacity = _data_codewords(ver) * 8

    bits: list[int] = []
    put = lambda val, n: bits.extend((val >> i) & 1 for i in range(n - 1, -1, -1))  # noqa: E731
    put(0b0100, 4)
    put(len(data), count_bits)
    for b in data:
        put(b, 8)
    put(0, min(4, capacity - len(bits)))
    put(0, -len(bits) % 8)
    pad = 0xEC
    while len(bits) < capacity:
        put(pad, 8)
        pad ^= 0xEC ^ 0x11
    codewords = [int("".join(map(str, bits[i:i + 8])), 2) for i in range(0, len(bits), 8)]

    nblocks, ecclen = _NUM_BLOCKS[ver], _ECC_PER_BLOCK[ver]
    raw = _raw_modules(ver) // 8
    nshort = nblocks - raw % nblocks
    shortlen = raw // nblocks
    div = _rs_divisor(ecclen)
    blocks, k = [], 0
    for i in range(nblocks):
        dat = codewords[k:k + shortlen - ecclen + (0 if i < nshort else 1)]
        k += len(dat)
        ecc = _rs_remainder(dat, div)
        if i < nshort:
            dat = dat + [0]
        blocks.append(dat + ecc)
    final = [blk[i] for i in range(len(blocks[0])) for j, blk in enumerate(blocks)
             if i != shortlen - ecclen or j >= nshort]

    b = _Builder(ver, final)
    if mask is not None:
        return b.masked(mask)
    return min((b.masked(mk) for mk in range(8)), key=_penalty)
