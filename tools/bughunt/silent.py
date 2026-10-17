"""落ちない不具合（静かな食い違い）を機械的に見つけるための比べ方。run.py から使う。

- 正規化: 日時・版・場所（フォルダ）の違いを消してから比べる
- 食い違いの「種類」: 最初に違った行から数字を抜いた形（同じ原因の食い違いを 1 件に寄せるため）
- DRC/ERC の JSON: 違反を（種類, 項目の説明, 位置）の多重集合にして比べる
- 基板の平行移動: 最新形式の .kicad_pcb の座標を全部ずらす（フットプリントの中は相対なので、その (at) だけ）
"""
import collections, difflib, json, os, re

DATE_RES = [
    re.compile(r"\d{4}-\d\d-\d\d[T ]\d\d:\d\d(:\d\d)?([+-]\d\d:?\d\d|Z)?"),
    re.compile(r"\b\d{4}/\d\d/\d\d \d\d:\d\d:\d\d\b"),
    re.compile(r"\b(Mon|Tue|Wed|Thu|Fri|Sat|Sun)\w* \w+ +\d+ \d\d:\d\d:\d\d \d{4}\b"),
    re.compile(r"\b\d\d?/\d\d?/\d{2,4},? \d\d?:\d\d(:\d\d)?( ?[AP]M)?\b"),
]
VERSION_RE = re.compile(r"10\.0\.\d+[-\w.+]*")


def norm_text(s, dirs=()):
    for d in sorted((d for d in dirs if d), key=len, reverse=True):   # up2 を up より先に
        s = s.replace(d, "@DIR@")
    for r in DATE_RES:
        s = r.sub("@DATE@", s)
    s = VERSION_RE.sub("@VER@", s)
    return s


def shape(line):
    """行の形（数字と引用符の中身を抜く）。食い違いの種類に使う"""
    line = re.sub(r'"[^"]*"', '"S"', line.strip())
    line = re.sub(r"-?\d+(\.\d+)?", "N", line)
    return line[:80]


def compare_text(a, b, dirs=(), name="", unordered=False, drop=None):
    """違えば (種類, 抜粋) を返す。同じなら None。
    unordered: 行の多重集合で比べる（差の抜粋も並べ替えた物で）。drop: この正規表現に合う行は見ない"""
    if a == b:
        return None
    na, nb = norm_text(a, dirs), norm_text(b, dirs)
    if na == nb:
        return None
    la, lb = na.splitlines(), nb.splitlines()
    if drop:
        r = re.compile(drop)
        la = [x for x in la if not r.search(x)]
        lb = [x for x in lb if not r.search(x)]
    if sorted(la) == sorted(lb):
        return None       # 並びだけの違いは数えない（中身は同じ）
    if unordered:
        la, lb = sorted(la), sorted(lb)
    if len(la) != len(lb):
        kind = f"{name}: 行の数 {'増' if len(lb) > len(la) else '減'}"
    else:
        kind = None
    sm = difflib.SequenceMatcher(None, la, lb, autojunk=False) if len(la) + len(lb) < 40000 else None
    first = None
    if sm:
        for tag, i1, i2, j1, j2 in sm.get_opcodes():
            if tag != "equal":
                first = (la[i1] if i1 < len(la) else "", lb[j1] if j1 < len(lb) else "")
                break
    else:
        for x, y in zip(la, lb):
            if x != y:
                first = (x, y)
                break
        first = first or ("", "")
    first = first or ("", "")
    kind = kind or f"{name}: {shape(first[0])} → {shape(first[1])}"
    ex = "\n".join(list(difflib.unified_diff(la, lb, lineterm="", n=1))[:30])
    return kind, ex


def read(p):
    with open(p, "rb") as f:
        return f.read().decode("utf-8", "replace")


def compare_dirs(da, db, dirs=(), label="", **kw):
    """フォルダ（または 1 ファイル）どうし。名前で対応させる。違いの一覧 [(種類, 抜粋)]"""
    out = []
    if os.path.isfile(da) or os.path.isfile(db):
        if not (os.path.isfile(da) and os.path.isfile(db)):
            return [(f"{label}: 片方だけに出力", f"{da} {os.path.exists(da)} / {db} {os.path.exists(db)}")]
        r = compare_text(read(da), read(db), dirs, label, **kw)
        return [r] if r else []
    fa = sorted(os.listdir(da)) if os.path.isdir(da) else []
    fb = sorted(os.listdir(db)) if os.path.isdir(db) else []
    if fa != fb:
        out.append((f"{label}: ファイルの組が違う", f"{sorted(set(fa) ^ set(fb))[:10]}"))
    for f in sorted(set(fa) & set(fb)):
        ext = os.path.splitext(f)[1] or f
        r = compare_text(read(os.path.join(da, f)), read(os.path.join(db, f)), dirs, f"{label}{ext}", **kw)
        if r:
            out.append(r)
    return out


# 置き場所やライブラリの表で変わる違反（写した先ではライブラリが見つからない）。比べない
ENV_TYPES = {"lib_footprint_issues", "lib_footprint_mismatch", "lib_symbol_issues", "lib_symbol_mismatch",
             "footprint_link_issues", "schematic_parity"}


def violations(json_path, positions=True):
    """DRC/ERC の JSON → 違反の多重集合（Counter）。読めなければ None"""
    try:
        d = json.load(open(json_path))
    except Exception:
        return None
    c = collections.Counter()
    vs = list(d.get("violations", [])) + list(d.get("unconnected_items", [])) + list(d.get("schematic_parity", []))
    for s in d.get("sheets", []):
        vs += s.get("violations", [])
    for v in vs:
        if v.get("type") in ENV_TYPES:
            continue
        items = []
        for it in v.get("items", []):
            p = it.get("pos", {})
            # 未接続は、同じ場所のパッドと線のどちらを端に選ぶかが毎回揺れる。場所だけで比べる
            desc = "" if v.get("type") == "unconnected_items" else it.get("description", "")
            items.append((desc, round(p.get("x", 0), 3), round(p.get("y", 0), 3)) if positions else (desc,))
        c[(v.get("type"), v.get("severity"), tuple(sorted(items)))] += 1
    return c


def compare_violations(a, b, label, positions=True):
    va, vb = violations(a, positions), violations(b, positions)
    if va is None or vb is None:
        if (va is None) != (vb is None):
            return [(f"{label}: 片方だけ読めない", f"{a} / {b}")]
        return []
    if va == vb:
        return []
    only_a, only_b = va - vb, vb - va
    types = sorted({k[0] for k in only_a} | {k[0] for k in only_b})
    kind = f"{label}: 違反が{'消えた' if only_a and not only_b else '増えた' if only_b and not only_a else '入れ替わった'} [{','.join(types[:4])}]"
    ex = "前だけ: " + json.dumps([list(k) for k in list(only_a)[:5]], ensure_ascii=False)[:1500] + \
         "\n後だけ: " + json.dumps([list(k) for k in list(only_b)[:5]], ensure_ascii=False)[:1500]
    return [(kind, ex)]


def compare_violation_counts(a, b, label):
    """違反の数を種類ごとに比べる。DRC は同じ物を何度かけても、どの組を代表に出すか
    （マスクの橋渡し・間隔）が並列処理で揺れるので、場所までは比べない"""
    ca, cb = violation_counts(a), violation_counts(b)
    if ca is None or cb is None:
        return [(f"{label}: 片方だけ読めない", f"{a} / {b}")] if (ca is None) != (cb is None) else []
    if ca == cb:
        return []
    ch = [f"{k}:{ca.get(k, 0)}→{cb.get(k, 0)}" for k in sorted(set(ca) | set(cb), key=str) if ca.get(k, 0) != cb.get(k, 0)]
    return [(f"{label}: 違反の数が変わる [{','.join(sorted(x.split(':')[0] for x in ch)[:4])}]", " ".join(ch))]


def violation_counts(path):
    v = violations(path, positions=False)
    if v is None:
        return None
    c = collections.Counter()
    for (t, sev, _), n in v.items():
        c[t] += n
    return c


# ---- 基板の平行移動（最新形式だけ） ----
TOKEN = re.compile(r'\s*(\(|\)|"(?:[^"\\]|\\.)*"|[^\s()"]+)', re.S)
SHIFT_KEYS = {"at", "start", "end", "mid", "center", "xy", "pos"}
NO_DESCEND = {"setup", "general", "layers", "net", "paper", "title_block", "property", "version", "generator",
              "generator_version", "embedded_files", "embedded_fonts"}


def parse(s):
    stack, cur = [], []
    for m in TOKEN.finditer(s):
        t = m.group(1)
        if t == "(":
            stack.append(cur)
            cur = []
        elif t == ")":
            done = cur
            cur = stack.pop()
            cur.append(done)
        else:
            cur.append(t)
    return cur[0] if cur else None


def dump(n, out):
    if isinstance(n, list):
        out.append("(")
        for i, c in enumerate(n):
            if i:
                out.append(" ")
            dump(c, out)
        out.append(")\n" if n and n[0] in ("footprint", "segment", "via", "zone", "arc") else ")")
    else:
        out.append(n)


def fmt(x):
    s = f"{x:.6f}".rstrip("0").rstrip(".")
    return "0" if s in ("-0", "") else s


def shift_node(n, dx, dy):
    if not isinstance(n, list) or not n:
        return
    if n[0] in SHIFT_KEYS and len(n) >= 3:
        try:
            n[1] = fmt(float(n[1]) + dx)
            n[2] = fmt(float(n[2]) + dy)
        except ValueError:
            pass
    for c in n[1:]:
        if isinstance(c, list):
            shift_node(c, dx, dy)


def translate_board(src, dst, dx=25.4, dy=12.7, max_bytes=8_000_000):
    """できたら True。古い形式・大きすぎる・フットプリントの中に zone がある物は False（比べない）"""
    if os.path.getsize(src) > max_bytes:
        return False
    s = read(src)
    root = parse(s)
    if not root or root[0] != "kicad_pcb":
        return False
    for c in root[1:]:
        if isinstance(c, list) and c and c[0] == "footprint":
            if any(isinstance(x, list) and x and x[0] == "zone" for x in c):
                return False
    for c in root[1:]:
        if not isinstance(c, list) or not c or c[0] in NO_DESCEND:
            continue
        if c[0] == "footprint":
            for x in c:
                if isinstance(x, list) and x and x[0] == "at":
                    shift_node(x, dx, dy)
                    break
        else:
            shift_node(c, dx, dy)
    out = []
    dump(root, out)
    with open(dst, "w") as f:
        f.write("".join(out))
    return True
