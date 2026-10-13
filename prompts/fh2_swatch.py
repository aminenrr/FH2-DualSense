"""fh2_swatch.py - Forza Horizon 2 (Xbox One build, Azimuth) UI texture helpers.

Pure Python standard library, so the installer runs on any Python 3 with nothing to pip install.

- parse(): read the size/mips/payload of a .swatchbin UI texture header.
- block_map(): where each 4x4 BC7 block (or RGBA8 pixel) of each mip lives inside the
  Xbox One tiled payload. These layouts were worked out by us from the texture data;
  no game code or art is contained here.
- write_payload(): put our own pre-encoded pixel data into a texture from the player's own game.
- rebuild_zip(): rewrite a UI zip with replaced entries, aligning data to 4096 bytes like the game expects.
"""
import struct, zlib, zipfile, io

HDR = 0x53

def parse(data):
    if data[:4] != b"burG" or data[0x10:0x14] != b"BCXT":
        raise ValueError("not a Grub/TXCB swatchbin")
    w, h = struct.unpack_from("<HH", data, 0x40)
    return dict(w=w, h=h, mips=data[0x4C], payload=struct.unpack_from("<I", data, 0x20)[0])

def layout_key(info):
    return "%dx%d_m%d_%d" % (info["w"], info["h"], info["mips"], info["payload"])

# ------------------------------------------------------------------ tiling maps
def _morton(i, nbits):
    x = y = 0
    for k in range(nbits):
        x |= ((i >> (2 * k)) & 1) << k
        y |= ((i >> (2 * k + 1)) & 1) << k
    return x, y

def _unit4(i):
    return (i & 1) | (((i >> 2) & 1) << 1), ((i >> 1) & 1) | (((i >> 3) & 1) << 1)

def _par(v, mask):
    return bin(v & mask).count("1") & 1

def _units(seglist, nunits_bits, unit_coord):
    out = []
    for g in range(16 << nunits_bits):
        off = seglist[g // 32] * 512 + (g % 32) * 16
        u, i = divmod(g, 16)
        ux, uy = unit_coord(u); ix, iy = _unit4(i)
        out.append((off, ux * 4 + ix, uy * 4 + iy))
    return out

def _help22():
    return [[(i * 16,) + _morton(i, 3) for i in range(64)]]

def _medium64():
    m = []
    for g in range(256):
        off = (g // 32) * 4096 + (g % 32) * 16
        b = [(g >> k) & 1 for k in range(8)]
        x = b[0] | b[2] << 1 | b[6] << 2 | b[4] << 3
        y = b[1] | b[3] << 1 | b[7] << 2 | b[5] << 3
        m.append((off, x ^ (b[5] << 3), y))
    return [m]

def _large128():
    M = [0b010000, 0b001111, 0b000110, 0b100000, 0b001000, 0b000100]
    def uc(u):
        return (_par(u, M[0]) | _par(u, M[1]) << 1 | _par(u, M[2]) << 2,
                _par(u, M[3]) | _par(u, M[4]) << 1 | _par(u, M[5]) << 2)
    mip0 = []
    for g in range(1024):
        chunk, r = divmod(g, 64)
        u, i = divmod(g, 16)
        ux, uy = uc(u); ix, iy = _unit4(i)
        mip0.append((chunk * 2048 + r * 16, ux * 4 + ix, uy * 4 + iy))
    mip1 = _units([64, 65, 66, 67, 72, 73, 74, 75], 4, lambda u: _morton(u, 2))
    mip2 = _units([80, 81], 2, lambda u: _morton(u, 1))
    mip3 = [(84 * 512 + i * 16,) + _unit4(i) for i in range(16)]
    return [mip0, mip1, mip2, mip3]

def _small32():
    out = []
    for i in range(1024):
        mx, my = _morton(i & 63, 3); t = i >> 6
        out.append((i * 4, (t & 3) * 8 + mx, (t >> 2) * 8 + my))
    return [out]

def _morton_blocks(nbits):
    return lambda: [[(i * 16,) + _morton(i, nbits) for i in range(1 << (2 * nbits))]]

_G512 = [int(v) for v in """
0,32,1,33,3,35,2,34,4,36,5,37,7,39,6,38,136,168,137,169,139,171,138,170,140,172,141,173,143,175,142,
174,64,96,65,97,67,99,66,98,68,100,69,101,71,103,70,102,200,232,201,233,203,235,202,234,204,236,205,
237,207,239,206,238,17,49,16,48,18,50,19,51,21,53,20,52,22,54,23,55,153,185,152,184,154,186,155,187,
157,189,156,188,158,190,159,191,81,113,80,112,82,114,83,115,85,117,84,116,86,118,87,119,217,249,216,
248,218,250,219,251,221,253,220,252,222,254,223,255,10,42,11,43,9,41,8,40,14,46,15,47,13,45,12,44,13
0,162,131,163,129,161,128,160,134,166,135,167,133,165,132,164,74,106,75,107,73,105,72,104,78,110,79,
111,77,109,76,108,194,226,195,227,193,225,192,224,198,230,199,231,197,229,196,228,27,59,26,58,24,56,
25,57,31,63,30,62,28,60,29,61,147,179,146,178,144,176,145,177,151,183,150,182,148,180,149,181,91,123
,90,122,88,120,89,121,95,127,94,126,92,124,93,125,211,243,210,242,208,240,209,241,215,247,214,246,21
2,244,213,245,268,300,269,301,271,303,270,302,264,296,265,297,267,299,266,298,388,420,389,421,391,42
3,390,422,384,416,385,417,387,419,386,418,332,364,333,365,335,367,334,366,328,360,329,361,331,363,33
0,362,452,484,453,485,455,487,454,486,448,480,449,481,451,483,450,482,285,317,284,316,286,318,287,31
9,281,313,280,312,282,314,283,315,405,437,404,436,406,438,407,439,401,433,400,432,402,434,403,435,34
9,381,348,380,350,382,351,383,345,377,344,376,346,378,347,379,469,501,468,500,470,502,471,503,465,49
7,464,496,466,498,467,499,262,294,263,295,261,293,260,292,258,290,259,291,257,289,256,288,398,430,39
9,431,397,429,396,428,394,426,395,427,393,425,392,424,326,358,327,359,325,357,324,356,322,354,323,35
5,321,353,320,352,462,494,463,495,461,493,460,492,458,490,459,491,457,489,456,488,279,311,278,310,27
6,308,277,309,275,307,274,306,272,304,273,305,415,447,414,446,412,444,413,445,411,443,410,442,408,44
0,409,441,343,375,342,374,340,372,341,373,339,371,338,370,336,368,337,369,479,511,478,510,476,508,47
7,509,475,507,474,506,472,504,473,505,512,544,513,545,515,547,514,546,516,548,517,549,519,551,518,55
0,648,680,649,681,651,683,650,682,652,684,653,685,655,687,654,686,576,608,577,609,579,611,578,610,58
0,612,581,613,583,615,582,614,712,744,713,745,715,747,714,746,716,748,717,749,719,751,718,750,529,56
1,528,560,530,562,531,563,533,565,532,564,534,566,535,567,665,697,664,696,666,698,667,699,669,701,66
8,700,670,702,671,703,593,625,592,624,594,626,595,627,597,629,596,628,598,630,599,631,729,761,728,76
0,730,762,731,763,733,765,732,764,734,766,735,767,522,554,523,555,521,553,520,552,526,558,527,559,52
5,557,524,556,642,674,643,675,641,673,640,672,646,678,647,679,645,677,644,676,586,618,587,619,585,61
7,584,616,590,622,591,623,589,621,588,620,706,738,707,739,705,737,704,736,710,742,711,743,709,741,70
8,740,539,571,538,570,536,568,537,569,543,575,542,574,540,572,541,573,659,691,658,690,656,688,657,68
9,663,695,662,694,660,692,661,693,603,635,602,634,600,632,601,633,607,639,606,638,604,636,605,637,72
3,755,722,754,720,752,721,753,727,759,726,758,724,756,725,757,780,812,781,813,783,815,782,814,776,80
8,777,809,779,811,778,810,900,932,901,933,903,935,902,934,896,928,897,929,899,931,898,930,844,876,84
5,877,847,879,846,878,840,872,841,873,843,875,842,874,964,996,965,997,967,999,966,998,960,992,961,99
3,963,995,962,994,797,829,796,828,798,830,799,831,793,825,792,824,794,826,795,827,917,949,916,948,91
8,950,919,951,913,945,912,944,914,946,915,947,861,893,860,892,862,894,863,895,857,889,856,888,858,89
0,859,891,981,1013,980,1012,982,1014,983,1015,977,1009,976,1008,978,1010,979,1011,774,806,775,807,77
3,805,772,804,770,802,771,803,769,801,768,800,910,942,911,943,909,941,908,940,906,938,907,939,905,93
7,904,936,838,870,839,871,837,869,836,868,834,866,835,867,833,865,832,864,974,1006,975,1007,973,1005
,972,1004,970,1002,971,1003,969,1001,968,1000,791,823,790,822,788,820,789,821,787,819,786,818,784,81
6,785,817,927,959,926,958,924,956,925,957,923,955,922,954,920,952,921,953,855,887,854,886,852,884,85
3,885,851,883,850,882,848,880,849,881,991,1023,990,1022,988,1020,989,1021,987,1019,986,1018,984,1016
,985,1017
""".replace("\n", "").split(",")]

def _diagram512():   # 512x512 BC7, 1 mip: 32x32 grid of 256-byte units, 4x4 blocks per unit
    m = []
    for Y in range(32):
        for X in range(32):
            u = _G512[Y * 32 + X]
            for i in range(16):
                ix, iy = _unit4(i)
                m.append((u * 256 + i * 16, X * 4 + ix, Y * 4 + iy))
    return [m]

LAYOUTS = {   # key -> (format, map function)
    "22x22_m1_1024": ("bc7", _help22),
    "32x22_m1_1024": ("bc7", _help22),
    "46x46_m1_4096": ("bc7", _morton_blocks(4)),
    "32x32_m1_4096": ("rgba8", _small32),
    "64x64_m1_32768": ("bc7", _medium64),
    "128x128_m1_32768": ("bc7", lambda: [_large128()[0]]),
    "128x128_m4_44032": ("bc7", _large128),
    "512x512_m1_262144": ("bc7", _diagram512),
}

def mip_sizes(info):
    return [(max(1, info["w"] >> l), max(1, info["h"] >> l)) for l in range(info["mips"])]

def write_payload(template, linear):
    """template: the texture from the player's game. linear: our pixel data, mip by mip,
    BC7 blocks row-major (or RGBA8 pixels row-major). Returns the new texture bytes."""
    info = parse(template)
    key = layout_key(info)
    if key not in LAYOUTS:
        raise ValueError("unsupported texture layout " + key)
    fmt, fn = LAYOUTS[key]
    out = bytearray(template)
    p = memoryview(out)[HDR:]
    pos = 0
    maps = fn()
    for (lw, lh), mp in zip(mip_sizes(info), maps):
        if fmt == "rgba8":
            for off, x, y in mp:
                if x < lw and y < lh:
                    s = pos + (y * lw + x) * 4
                    p[off:off + 4] = linear[s:s + 4]
            pos += lw * lh * 4
        else:
            bw, bh = (lw + 3) // 4, (lh + 3) // 4
            for off, bx, by in mp:
                if bx < bw and by < bh:
                    s = pos + (by * bw + bx) * 16
                    p[off:off + 16] = linear[s:s + 16]
            pos += bw * bh * 16
    if pos != len(linear):
        raise ValueError("payload size mismatch (%d vs %d)" % (pos, len(linear)))
    return bytes(out)

def linear_size(info):
    fmt = LAYOUTS[layout_key(info)][0]
    n = 0
    for lw, lh in mip_sizes(info)[:len(LAYOUTS[layout_key(info)][1]())]:
        n += lw * lh * 4 if fmt == "rgba8" else ((lw + 3) // 4) * ((lh + 3) // 4) * 16
    return n

# ------------------------------------------------------------------ zip rebuild
ALIGN = 4096

def rebuild_zip(src_bytes, replacements):
    """Rewrite a zip keeping entry order, names, dates and methods; each entry's data is
    aligned to 4096 bytes with padding in the local extra field. Returns the new zip bytes."""
    src = src_bytes
    z = zipfile.ZipFile(io.BytesIO(src))
    out = bytearray(); central = bytearray()
    pad_id = 0xD935
    for i in z.infolist():
        nl, el = struct.unpack_from("<HH", src, i.header_offset + 26)
        if el >= 4:
            pad_id = struct.unpack_from("<H", src, i.header_offset + 30 + nl)[0]
            break
    for i in z.infolist():
        raw = replacements.get(i.filename)
        if raw is None:
            raw = z.read(i)
        if i.compress_type == zipfile.ZIP_STORED:
            comp = raw
        elif i.compress_type == zipfile.ZIP_DEFLATED:
            c = zlib.compressobj(9, zlib.DEFLATED, -15); comp = c.compress(raw) + c.flush()
        else:
            raise ValueError("unsupported zip method %d for %s" % (i.compress_type, i.filename))
        crc = zlib.crc32(raw) & 0xFFFFFFFF
        name = i.filename.encode("utf-8" if i.flag_bits & 0x800 else "cp437")
        dt = i.date_time
        dosdate = (dt[0] - 1980) << 9 | dt[1] << 5 | dt[2]
        dostime = dt[3] << 11 | dt[4] << 5 | dt[5] // 2
        local_off = len(out)
        pad = (-(local_off + 30 + len(name))) % ALIGN
        if 0 < pad < 4:
            pad += ALIGN
        extra = struct.pack("<HH", pad_id, pad - 4) + b"\0" * (pad - 4) if pad else b""
        flags = i.flag_bits & ~0x08
        out += struct.pack("<IHHHHHIIIHH", 0x04034B50, i.extract_version or 10, flags,
                           i.compress_type, dostime, dosdate, crc, len(comp), len(raw),
                           len(name), len(extra))
        out += name + extra
        out += comp
        central += struct.pack("<IHHHHHHIIIHHHHHII", 0x02014B50, i.create_version or 20,
                               i.extract_version or 10, flags, i.compress_type, dostime,
                               dosdate, crc, len(comp), len(raw), len(name), 0, 0, 0, 0,
                               i.external_attr, local_off)
        central += name
    cd_off = len(out); out += central
    n = len(z.infolist())
    out += struct.pack("<IHHHHIIH", 0x06054B50, 0, 0, n, n, len(central), cd_off, 0)
    return bytes(out)
