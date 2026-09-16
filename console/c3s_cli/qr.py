"""A QR encoder in the standard library alone, so pairing needs no network and no wheel.

Byte mode only, versions 1–10, one error-correction level at a time — enough for a
pairing URL (`http://192.168.1.23:8765/#approvals&token=…` is about 55 characters, which
fits version 4 at level M with room to spare). Everything here is ISO/IEC 18004: the
Reed–Solomon code over GF(256) with the primitive polynomial 0x11d, the block structure
table for versions 1–10, the two-column zigzag placement, the eight masks and the four
penalty rules that choose between them, and the BCH format and version information.

`matrix(text)` returns the modules as rows of 0/1 with no quiet zone; `render(text)`
returns the block-character drawing that goes in a terminal. The matrices this produces
are compared module for module against the `qrcode` package — every version 1–10, every
level, every mask forced on both sides — by `python -m c3s_cli.qr_verify`, which skips
when that package is absent. Nothing here needs it: pairing has to work with no network.
"""

from __future__ import annotations

# ---------------------------------------------------------------- GF(256) and Reed–Solomon

_EXP = [1] * 512
_LOG = [0] * 256
_x = 1
for _i in range(255):
    _EXP[_i] = _x
    _LOG[_x] = _i
    _x <<= 1
    if _x & 0x100:
        _x ^= 0x11D
for _i in range(255, 512):
    _EXP[_i] = _EXP[_i - 255]


def _mul(a: int, b: int) -> int:
    if a == 0 or b == 0:
        return 0
    return _EXP[_LOG[a] + _LOG[b]]


def _generator(degree: int) -> list[int]:
    """The generator polynomial of the Reed–Solomon code of that many check symbols."""
    poly = [1]
    for i in range(degree):
        poly = _poly_mul(poly, [1, _EXP[i]])
    return poly


def _poly_mul(a: list[int], b: list[int]) -> list[int]:
    out = [0] * (len(a) + len(b) - 1)
    for i, av in enumerate(a):
        for j, bv in enumerate(b):
            out[i + j] ^= _mul(av, bv)
    return out


def ec_codewords(data: bytes, count: int) -> list[int]:
    """The `count` error-correction codewords of one block: polynomial remainder."""
    gen = _generator(count)
    rem = list(data) + [0] * count
    for i in range(len(data)):
        coef = rem[i]
        if coef:
            for j, g in enumerate(gen):
                rem[i + j] ^= _mul(g, coef)
    return rem[len(data):]


# ---------------------------------------------------------------- the version tables

# version -> total codewords (data + error correction)
_TOTAL = {1: 26, 2: 44, 3: 70, 4: 100, 5: 134, 6: 172, 7: 196, 8: 242, 9: 292, 10: 346}

# (version, level) -> (error-correction codewords per block, [(blocks, data codewords), …])
_BLOCKS: dict[tuple[int, str], tuple[int, list[tuple[int, int]]]] = {
    (1, "L"): (7, [(1, 19)]), (1, "M"): (10, [(1, 16)]), (1, "Q"): (13, [(1, 13)]), (1, "H"): (17, [(1, 9)]),
    (2, "L"): (10, [(1, 34)]), (2, "M"): (16, [(1, 28)]), (2, "Q"): (22, [(1, 22)]), (2, "H"): (28, [(1, 16)]),
    (3, "L"): (15, [(1, 55)]), (3, "M"): (26, [(1, 44)]), (3, "Q"): (18, [(2, 17)]), (3, "H"): (22, [(2, 13)]),
    (4, "L"): (20, [(1, 80)]), (4, "M"): (18, [(2, 32)]), (4, "Q"): (26, [(2, 24)]), (4, "H"): (16, [(4, 9)]),
    (5, "L"): (26, [(1, 108)]), (5, "M"): (24, [(2, 43)]),
    (5, "Q"): (18, [(2, 15), (2, 16)]), (5, "H"): (22, [(2, 11), (2, 12)]),
    (6, "L"): (18, [(2, 68)]), (6, "M"): (16, [(4, 27)]), (6, "Q"): (24, [(4, 19)]), (6, "H"): (28, [(4, 15)]),
    (7, "L"): (20, [(2, 78)]), (7, "M"): (18, [(4, 31)]),
    (7, "Q"): (18, [(2, 14), (4, 15)]), (7, "H"): (26, [(4, 13), (1, 14)]),
    (8, "L"): (24, [(2, 97)]), (8, "M"): (22, [(2, 38), (2, 39)]),
    (8, "Q"): (22, [(4, 18), (2, 19)]), (8, "H"): (26, [(4, 14), (2, 15)]),
    (9, "L"): (30, [(2, 116)]), (9, "M"): (22, [(3, 36), (2, 37)]),
    (9, "Q"): (20, [(4, 16), (4, 17)]), (9, "H"): (24, [(4, 12), (4, 13)]),
    (10, "L"): (18, [(2, 68), (2, 69)]), (10, "M"): (26, [(4, 43), (1, 44)]),
    (10, "Q"): (24, [(6, 19), (2, 20)]), (10, "H"): (28, [(6, 15), (2, 16)]),
}

# version -> the row/column centres of the alignment patterns
_ALIGN = {1: [], 2: [6, 18], 3: [6, 22], 4: [6, 26], 5: [6, 30], 6: [6, 34],
          7: [6, 22, 38], 8: [6, 24, 42], 9: [6, 26, 46], 10: [6, 28, 50]}

_LEVEL_BITS = {"L": 0b01, "M": 0b00, "Q": 0b11, "H": 0b10}
MAX_VERSION = 10


def capacity(version: int, level: str) -> int:
    """How many bytes of payload fit, after the mode indicator and the count."""
    ec, blocks = _BLOCKS[(version, level)]
    data_codewords = sum(n * k for n, k in blocks)
    header_bits = 4 + (8 if version < 10 else 16)
    return (data_codewords * 8 - header_bits) // 8


def _smallest_version(payload: bytes, level: str) -> int:
    for version in range(1, MAX_VERSION + 1):
        if len(payload) <= capacity(version, level):
            return version
    raise ValueError(
        f"{len(payload)} bytes will not fit a version-{MAX_VERSION} QR code at level {level}: "
        "shorten the URL or print it as text")


# ---------------------------------------------------------------- bit stream and codewords


def _codewords(payload: bytes, version: int, level: str) -> list[int]:
    ec_per_block, blocks = _BLOCKS[(version, level)]
    data_codewords = sum(n * k for n, k in blocks)

    bits: list[int] = []

    def put(value: int, width: int) -> None:
        for shift in range(width - 1, -1, -1):
            bits.append((value >> shift) & 1)

    put(0b0100, 4)                                   # byte mode
    put(len(payload), 8 if version < 10 else 16)     # character count
    for byte in payload:
        put(byte, 8)
    put(0, min(4, data_codewords * 8 - len(bits)))   # terminator
    while len(bits) % 8:
        bits.append(0)
    stream = [int("".join(str(b) for b in bits[i:i + 8]), 2) for i in range(0, len(bits), 8)]
    pad = [0xEC, 0x11]                               # the pad codewords, alternating
    while len(stream) < data_codewords:
        stream.append(pad[(len(stream) - len(bits) // 8) % 2])

    # split into blocks, compute each block's check symbols, then interleave both
    data_blocks: list[list[int]] = []
    ec_blocks: list[list[int]] = []
    at = 0
    for count, size in blocks:
        for _ in range(count):
            chunk = stream[at:at + size]
            at += size
            data_blocks.append(chunk)
            ec_blocks.append(ec_codewords(bytes(chunk), ec_per_block))

    out: list[int] = []
    for i in range(max(len(b) for b in data_blocks)):
        for block in data_blocks:
            if i < len(block):
                out.append(block[i])
    for i in range(ec_per_block):
        for block in ec_blocks:
            out.append(block[i])
    return out


# ---------------------------------------------------------------- the matrix


def _bch15(data: int) -> int:
    value = data << 10
    while value.bit_length() - 1 >= 10:
        value ^= 0b10100110111 << (value.bit_length() - 11)
    return ((data << 10) | value) ^ 0b101010000010010


def _bch18(version: int) -> int:
    value = version << 12
    while value.bit_length() - 1 >= 12:
        value ^= 0b1111100100101 << (value.bit_length() - 13)
    return (version << 12) | value


def _mask(pattern: int, row: int, col: int) -> bool:
    if pattern == 0:
        return (row + col) % 2 == 0
    if pattern == 1:
        return row % 2 == 0
    if pattern == 2:
        return col % 3 == 0
    if pattern == 3:
        return (row + col) % 3 == 0
    if pattern == 4:
        return (row // 2 + col // 3) % 2 == 0
    if pattern == 5:
        return (row * col) % 2 + (row * col) % 3 == 0
    if pattern == 6:
        return ((row * col) % 2 + (row * col) % 3) % 2 == 0
    return ((row + col) % 2 + (row * col) % 3) % 2 == 0


def _skeleton(version: int) -> tuple[list[list[int | None]], list[list[bool]]]:
    """The function patterns, and which modules they reserve."""
    size = version * 4 + 17
    grid: list[list[int | None]] = [[None] * size for _ in range(size)]
    fixed = [[False] * size for _ in range(size)]

    def set_module(r: int, c: int, dark: int) -> None:
        grid[r][c] = dark
        fixed[r][c] = True

    for base_r, base_c in ((0, 0), (0, size - 7), (size - 7, 0)):          # finders
        for dr in range(-1, 8):
            for dc in range(-1, 8):
                r, c = base_r + dr, base_c + dc
                if not (0 <= r < size and 0 <= c < size):
                    continue
                edge = max(abs(dr - 3), abs(dc - 3))
                set_module(r, c, 1 if edge in (0, 1, 3) and dr in range(0, 7) and dc in range(0, 7) else 0)
    for i in range(size):                                                   # timing
        if grid[6][i] is None:
            set_module(6, i, 1 if i % 2 == 0 else 0)
        if grid[i][6] is None:
            set_module(i, 6, 1 if i % 2 == 0 else 0)
    centres = _ALIGN[version]
    if centres:                                                             # alignment
        # Every combination of centres carries one, except the three that would sit on a
        # finder. A centre on row or column 6 does get its pattern: it overwrites the
        # timing modules it covers, which is why this runs after the timing patterns.
        corners = {(centres[0], centres[0]), (centres[0], centres[-1]), (centres[-1], centres[0])}
        for r in centres:
            for c in centres:
                if (r, c) in corners:
                    continue
                for dr in range(-2, 3):
                    for dc in range(-2, 3):
                        set_module(r + dr, c + dc, 1 if max(abs(dr), abs(dc)) != 1 else 0)
    set_module(size - 8, 8, 1)                                              # the dark module
    for i in range(9):                                                      # format areas
        for r, c in ((8, i), (i, 8)):
            if grid[r][c] is None:
                set_module(r, c, 0)
    for i in range(8):
        for r, c in ((8, size - 1 - i), (size - 1 - i, 8)):
            if grid[r][c] is None:
                set_module(r, c, 0)
    if version >= 7:                                                        # version areas
        for i in range(6):
            for j in range(3):
                set_module(size - 11 + j, i, 0)
                set_module(i, size - 11 + j, 0)
    return grid, fixed


def _place(grid: list[list[int | None]], fixed: list[list[bool]], codewords: list[int]) -> None:
    size = len(grid)
    bits = [(word >> shift) & 1 for word in codewords for shift in range(7, -1, -1)]
    at = 0
    col = size - 1
    upward = True
    while col > 0:
        if col == 6:                      # the vertical timing pattern is not a data column
            col -= 1
        rows = range(size - 1, -1, -1) if upward else range(size)
        for row in rows:
            for c in (col, col - 1):
                if fixed[row][c]:
                    continue
                grid[row][c] = bits[at] if at < len(bits) else 0
                at += 1
        col -= 2
        upward = not upward


def _penalty(grid: list[list[int]]) -> int:
    size = len(grid)
    score = 0
    lines = [row[:] for row in grid] + [[grid[r][c] for r in range(size)] for c in range(size)]
    for line in lines:                                        # rule 1: runs of five or more
        run, prev = 1, line[0]
        for value in line[1:]:
            if value == prev:
                run += 1
            else:
                if run >= 5:
                    score += 3 + (run - 5)
                run, prev = 1, value
        if run >= 5:
            score += 3 + (run - 5)
    for r in range(size - 1):                                 # rule 2: 2×2 of one colour
        for c in range(size - 1):
            v = grid[r][c]
            if grid[r][c + 1] == v and grid[r + 1][c] == v and grid[r + 1][c + 1] == v:
                score += 3
    finder = [1, 0, 1, 1, 1, 0, 1, 0, 0, 0, 0]
    reversed_finder = finder[::-1]
    for line in lines:                                        # rule 3: the finder-like run
        for i in range(size - 10):
            window = line[i:i + 11]
            if window == finder or window == reversed_finder:
                score += 40
    dark = sum(sum(row) for row in grid)                      # rule 4: the dark ratio
    percent = dark * 100 / (size * size)
    score += 10 * int(abs(percent - 50) / 5)
    return score


def _stamp_format(grid: list[list[int]], level: str, pattern: int) -> None:
    """The fifteen format bits, twice, indexed from the least significant as in the spec."""
    size = len(grid)
    value = _bch15((_LEVEL_BITS[level] << 3) | pattern)
    for i in range(15):
        bit = (value >> (14 - i)) & 1
        # the copy that wraps the top-left finder
        if i < 6:
            grid[8][i] = bit
        elif i == 6:
            grid[8][7] = bit
        elif i == 7:
            grid[8][8] = bit
        elif i == 8:
            grid[7][8] = bit
        else:
            grid[14 - i][8] = bit
        # the copy split between the other two finders: seven modules climbing the
        # bottom-left finder, then eight running to the right edge past the dark module
        if i < 7:
            grid[size - 1 - i][8] = bit
        else:
            grid[8][size - 15 + i] = bit


def _stamp_version(grid: list[list[int]], version: int) -> None:
    if version < 7:
        return
    size = len(grid)
    value = _bch18(version)
    for i in range(18):
        bit = (value >> i) & 1
        grid[size - 11 + i % 3][i // 3] = bit
        grid[i // 3][size - 11 + i % 3] = bit


def matrix(text: str, level: str = "M", version: int | None = None,
           mask_pattern: int | None = None) -> list[list[int]]:
    """The modules of the QR code for `text`, as rows of 0 and 1, with no quiet zone."""
    if level not in _LEVEL_BITS:
        raise ValueError(f"unknown error-correction level {level!r}: use L, M, Q or H")
    payload = text.encode("utf-8")
    version = version or _smallest_version(payload, level)
    if not 1 <= version <= MAX_VERSION:
        raise ValueError(f"this encoder covers versions 1–{MAX_VERSION}, not {version}")
    if len(payload) > capacity(version, level):
        raise ValueError(f"{len(payload)} bytes do not fit version {version} at level {level}")
    words = _codewords(payload, version, level)
    skeleton, fixed = _skeleton(version)
    _place(skeleton, fixed, words)
    base = [[int(v or 0) for v in row] for row in skeleton]

    best, best_score = None, None
    patterns = [mask_pattern] if mask_pattern is not None else range(8)
    for pattern in patterns:
        grid = [row[:] for row in base]
        for r in range(len(grid)):
            for c in range(len(grid)):
                if not fixed[r][c] and _mask(pattern, r, c):
                    grid[r][c] ^= 1
        _stamp_format(grid, level, pattern)
        _stamp_version(grid, version)
        score = _penalty(grid)
        if best_score is None or score < best_score:
            best, best_score = grid, score
    assert best is not None
    return best


def render(text: str, level: str = "M", quiet: int = 2, invert: bool = False) -> str:
    """The QR code as half-block characters: two module rows per text row, so a version-4
    code and its quiet zone fit in 41 columns and 21 lines of a terminal."""
    grid = matrix(text, level=level)
    size = len(grid)
    width = size + quiet * 2
    rows = [[0] * width for _ in range(quiet)] + \
           [[0] * quiet + row + [0] * quiet for row in grid] + \
           [[0] * width for _ in range(quiet)]
    if len(rows) % 2:
        rows.append([0] * width)
    # A dark module prints as ink. Terminals differ: on a light background ink is the block
    # character, on a dark one it is the space, which `invert` swaps.
    glyph = {(0, 0): " ", (1, 0): "▀", (0, 1): "▄", (1, 1): "█"}
    if invert:
        glyph = {(0, 0): "█", (1, 0): "▄", (0, 1): "▀", (1, 1): " "}
    lines = []
    for r in range(0, len(rows), 2):
        lines.append("".join(glyph[(rows[r][c], rows[r + 1][c])] for c in range(width)))
    return "\n".join(lines)
