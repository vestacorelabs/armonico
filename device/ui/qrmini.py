"""qrmini.py - a small QR code encoder for the address of the lesson screen (a URL of up to 106 bytes).

Byte mode, error correction level M, versions 1 to 6 (21 to 41 modules a side). Written from the QR Code
standard (ISO/IEC 18004): the data and the Reed-Solomon codewords, the function patterns, the 8 masks with their
penalty, the format information. Nothing outside Python's standard library, nothing to install on the device.

    modules("http://192.168.1.20:8099") -> a list of rows, each a list of booleans (True is a dark module)
"""

# version: (codewords in all, error correction codewords a block, blocks of data, alignment pattern center)
_M = {
    1: (26, 10, 1, None),
    2: (44, 16, 1, 18),
    3: (70, 26, 1, 22),
    4: (100, 18, 2, 26),
    5: (134, 24, 2, 30),
    6: (172, 16, 4, 34),
}
MAX_BYTES = {v: (t - e * b) - 2 for v, (t, e, b, _) in _M.items()}      # 14, 26, 42, 62, 84, 106
_cache = {}


def _gf_tables():
    exp, log = [0] * 512, [0] * 256
    x = 1
    for i in range(255):
        exp[i], log[x] = x, i
        x <<= 1
        if x & 0x100:
            x ^= 0x11D
    for i in range(255, 512):
        exp[i] = exp[i - 255]
    return exp, log


_EXP, _LOG = _gf_tables()


def _mul(a, b):
    return 0 if not a or not b else _EXP[_LOG[a] + _LOG[b]]


def _generator(degree):
    g = [1]
    for i in range(degree):
        nxt = [0] * (len(g) + 1)
        for j, c in enumerate(g):
            nxt[j] ^= c
            nxt[j + 1] ^= _mul(c, _EXP[i])
        g = nxt
    return g


def _remainder(data, degree):
    gen = _generator(degree)
    rem = [0] * degree
    for byte in data:
        f = byte ^ rem[0]
        rem = rem[1:] + [0]
        if f:
            for i in range(degree):
                rem[i] ^= _mul(gen[i + 1], f)
    return rem


def _codewords(data, version):
    total, ec, blocks, _ = _M[version]
    cap = total - ec * blocks
    bits = "0100" + format(len(data), "08b") + "".join(format(b, "08b") for b in data)
    bits += "0" * min(4, cap * 8 - len(bits))
    bits += "0" * (-len(bits) % 8)
    words = [int(bits[i:i + 8], 2) for i in range(0, len(bits), 8)]
    pad, i = (0xEC, 0x11), 0
    while len(words) < cap:                        # the pad bytes, alternating
        words.append(pad[i % 2])
        i += 1
    per = cap // blocks
    parts = [words[i * per:(i + 1) * per] for i in range(blocks)]
    eccs = [_remainder(p, ec) for p in parts]
    out = []
    for i in range(per):
        out += [p[i] for p in parts]
    for i in range(ec):
        out += [e[i] for e in eccs]
    return out


def _format_bits(mask):
    data = (0b00 << 3) | mask                 # level M is 00
    rem = data
    for _ in range(10):
        rem = (rem << 1) ^ ((rem >> 9) * 0x537)
    return ((data << 10) | rem) ^ 0x5412


def _matrix(codewords, version, mask):
    n = 17 + 4 * version
    m = [[False] * n for _ in range(n)]
    fn = [[False] * n for _ in range(n)]          # function modules: not data

    def put(x, y, dark):
        m[y][x] = dark
        fn[y][x] = True

    def finder(cx, cy):
        for dy in range(-4, 5):
            for dx in range(-4, 5):
                x, y = cx + dx, cy + dy
                if 0 <= x < n and 0 <= y < n:
                    d = max(abs(dx), abs(dy))
                    put(x, y, d not in (2, 4))
    finder(3, 3)
    finder(n - 4, 3)
    finder(3, n - 4)
    for i in range(8, n - 8):
        put(i, 6, i % 2 == 0)
        put(6, i, i % 2 == 0)
    ap = _M[version][3]
    if ap:
        for dy in range(-2, 3):
            for dx in range(-2, 3):
                put(ap + dx, ap + dy, max(abs(dx), abs(dy)) != 1)
    put(8, n - 8, True)                            # the dark module
    # the format areas are reserved now and written below
    for i in range(9):
        fn[8][i] = fn[i][8] = True
    for i in range(8):
        fn[8][n - 1 - i] = fn[n - 1 - i][8] = True
    # the data, in pairs of columns from the right, up and down, the vertical timing column skipped
    bits = []
    for w in codewords:
        bits += [(w >> k) & 1 for k in range(7, -1, -1)]
                                                   # (the remainder bits of versions 2 to 6 are zeros: past the list)
    i, up = 0, True
    x = n - 1
    while x > 0:
        if x == 6:
            x -= 1
        ys = range(n - 1, -1, -1) if up else range(n)
        for y in ys:
            for xx in (x, x - 1):
                if not fn[y][xx]:
                    bit = bits[i] if i < len(bits) else 0
                    i += 1
                    dark = bool(bit)
                    if _mask(mask, xx, y):
                        dark = not dark
                    m[y][xx] = dark
        up = not up
        x -= 2
    f = _format_bits(mask)
    for k in range(15):
        dark = bool((f >> k) & 1)
        # the first copy around the top left finder
        if k < 6:
            m[k][8] = dark
        elif k < 8:
            m[k + 1][8] = dark
        elif k == 8:
            m[8][7] = dark
        else:
            m[8][14 - k] = dark
        # the second copy: under the top right and beside the bottom left finders
        if k < 8:
            m[8][n - 1 - k] = dark
        else:
            m[n - 15 + k][8] = dark
    return m


def _mask(k, x, y):
    return ((x + y) % 2 == 0, y % 2 == 0, x % 3 == 0, (x + y) % 3 == 0, (y // 2 + x // 3) % 2 == 0,
            (x * y) % 2 + (x * y) % 3 == 0, ((x * y) % 2 + (x * y) % 3) % 2 == 0, ((x + y) % 2 + (x * y) % 3) % 2 == 0)[k]


def _penalty(m):
    n = len(m)
    score = 0
    for grid in (m, [list(r) for r in zip(*m)]):
        for row in grid:
            run, prev = 1, row[0]
            for v in row[1:]:
                if v == prev:
                    run += 1
                else:
                    score += run - 2 if run >= 5 else 0
                    run, prev = 1, v
            score += run - 2 if run >= 5 else 0
            s = "".join("1" if v else "0" for v in row)
            for pat in ("10111010000", "00001011101"):
                start = s.find(pat)
                while start != -1:
                    score += 40
                    start = s.find(pat, start + 1)
    for y in range(n - 1):
        for x in range(n - 1):
            if m[y][x] == m[y][x + 1] == m[y + 1][x] == m[y + 1][x + 1]:
                score += 3
    dark = sum(v for row in m for v in row)
    score += 10 * (abs(dark * 20 - n * n * 10) // (n * n))
    return score


def modules(text):
    """The code for text (a str of at most 106 bytes): rows of booleans, True for a dark module. The quiet
    zone around it is the caller's. ValueError when the text does not fit."""
    if text in _cache:
        return _cache[text]
    data = text.encode("utf-8")
    version = next((v for v in sorted(_M) if len(data) <= MAX_BYTES[v]), None)
    if version is None:
        raise ValueError("text too long for a code")
    words = _codewords(data, version)
    best = min((_matrix(words, version, k) for k in range(8)), key=_penalty)
    if len(_cache) > 8:
        _cache.clear()
    _cache[text] = best
    return best
