#!/usr/bin/env python3
"""
kicad-local sch: 画面なしで回路図を直す(ローカル道具、2026-09-25。KiCad 8 形式のまま保存)

  kicad-local sch <入力.kicad_sch> <出力.kicad_sch|-> <手順ファイル|-> [--instructions 指示書.md]
      出力を - にすると保存せず、確認だけ(接続の変化と ERC を表示)

手順(1 行 1 命令。# 以降は注釈。値に空白があれば "..." で囲む):
  info R28                                 部品の値・欄・端子(位置とネット)
  set R1,R2,R5 Value 4.7kΩ                 欄を変える(部品は , 区切り / R* のような指定 / R1-R5 の範囲)
  set R* Tolerance ±1%                     無い欄は追加(隠す)
  rename R12 R40                           部品番号を変える
  symbol C2,C4 Device:C_Polarized          記号を差し替える(端子の番号と位置が同じものだけ)
  delete J2 1=+24V 2=GND 3=GND 4=+12V
                                           部品を消し、つながっていた配線の端にラベルを置く(ネットを保つ)
                                           ラベル名を省くと元のネット名(/+12V → +12V)。自動名のネットは名前の指定が要る
  insert R28 1kΩ Device:R at A1.20 footprint=... MPN=... Tolerance=±1%
                                           A1.20 から出ている配線を切って、途中に部品を直列に入れる
                                           (gap=2.54 で端子から離す距離。配線が短いと失敗する)
  insert R32 470Ω Device:R on 50.80,60.96 ...   配線の途中の点を中心に直列に入れる
  add U2 "L7805" Regulator_Linear:L7805 at 76.20,50.80 1=+12V 2=GND 3=+5V footprint=... MPN=...
                                           新しい部品を置き、端子ごとに短い線とラベルを付ける(rot=90 で回転)
  add "#FLG03" PWR_FLAG power:PWR_FLAG at 20.32,30.48 1=+24V
                                           電源記号(部品番号が # で始まる)は部品表に入れない
  label +3V3 at A1.17                      端子に短い線とラベルを付ける
  text "注記の文" at 45.72,198.12          注記
  title rev="B" date=2026-09-25            表題欄(title / date / rev / company)

保存後に必ず: 元の回路図との「接続の差」「部品表の差」と ERC の結果を表示する(意図どおりか確かめる)。
基板への反映(部品の追加・形状の変更)は KiCad の画面の「回路図から基板を更新」で行う。
"""
import argparse, copy, fnmatch, math, re, shlex, subprocess, sys, tempfile, uuid
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import sx  # noqa: E402
from sx import Q  # noqa: E402

SYMLIB = Path("/usr/share/kicad/symbols")
GRID = 1.27


def U():
    return Q(str(uuid.uuid4()))


def r3(v):
    return round(float(v) + 0.0, 3)


def num(v):
    s = f"{v:.4f}".rstrip("0").rstrip(".")
    return "0" if s in ("-0", "") else s


def eff(hide=False, justify=None):
    e = ["effects", ["font", ["size", "1.27", "1.27"]]]
    if justify:
        e.append(["justify"] + justify)
    if hide:
        e.append(["hide", "yes"])
    return e


class Sch:
    def __init__(self, path):
        self.path = Path(path)
        self.root = sx.parse(self.path.read_text(encoding="utf-8"))
        self.uuid = sx.one(self.root, "uuid")[1]
        self.project = self.path.stem
        self.log = []      # 指示書(画面で同じことをする手順)
        self.new_syms = [] # 新しく置いた記号(最後に文字の置き場所を決める)

    # ---------------------------------------------------------------- 参照
    def symbols(self):
        return [s for s in sx.find(self.root, "symbol")]

    def ref(self, s):
        return sx.prop(s, "Reference")[2]

    def sym(self, ref):
        for s in self.symbols():
            if self.ref(s) == ref:
                return s
        raise SystemExit(f"部品 {ref} が無い")

    def select(self, spec):
        """'R1,R2' 'R*' 'R1-R5' → 部品のリスト(電源記号 #... は * では選ばない)"""
        out = []
        refs = [self.ref(s) for s in self.symbols()]
        for part in spec.split(","):
            m = re.match(r'^([A-Za-z_]+)(\d+)-(?:[A-Za-z_]+)?(\d+)$', part)
            if m:
                pre, a, b = m.group(1), int(m.group(2)), int(m.group(3))
                hit = [r for r in refs if re.fullmatch(rf'{pre}(\d+)', r) and a <= int(r[len(pre):]) <= b]
            elif any(c in part for c in "*?["):
                hit = [r for r in refs if fnmatch.fnmatchcase(r, part) and not r.startswith("#")]
            else:
                hit = [part] if part in refs else []
            if not hit:
                raise SystemExit(f"部品が見つからない: {part}")
            out += [r for r in hit if r not in out]
        return sorted(out, key=lambda r: (re.sub(r'[0-9]', '', r), int(re.sub(r'[^0-9]', '', r) or 0)))

    def libsym(self, lib_id):
        for s in sx.find(sx.one(self.root, "lib_symbols"), "symbol"):
            if s[1] == lib_id:
                return s
        return None

    def ensure_lib(self, lib_id):
        """回路図の中の記号の写しに無ければ、KiCad のライブラリから持ってくる"""
        if self.libsym(lib_id):
            return self.libsym(lib_id)
        lib, name = lib_id.split(":", 1)
        f = SYMLIB / f"{lib}.kicad_sym"
        if not f.exists():
            raise SystemExit(f"記号ライブラリが無い: {f}")
        S = {s[1]: s for s in sx.find(sx.parse(f.read_text(encoding="utf-8")), "symbol")}
        if name not in S:
            raise SystemExit(f"{lib} に記号 {name} が無い")
        s = copy.deepcopy(S[name])
        ext = sx.one(s, "extends")
        if ext:   # 派生の記号: 元の図形・端子に、派生側の欄をかぶせる
            base = copy.deepcopy(S[ext[1]])
            props = {p[1]: p for p in sx.find(s, "property")}
            body = [e for e in base if not (isinstance(e, list) and e and e[0] == "property")]
            newprops = [props.get(p[1], p) for p in sx.find(base, "property")]
            for k, p in props.items():
                if k not in [q[1] for q in newprops]:
                    newprops.append(p)
            subs = [e for e in body if isinstance(e, list) and e and e[0] == "symbol"]
            s = body[:2] + [e for e in body[2:] if not (isinstance(e, list) and e and e[0] == "symbol")] + newprops + subs
            for e in subs:
                e[1] = Q(e[1].replace(ext[1], name))
        s[1] = Q(lib_id)
        sx.one(self.root, "lib_symbols").append(s)
        return s

    def lib_pins(self, lib_id):
        """記号の端子: [(番号, 名前, x, y, 向き)](記号の座標、y は上が正)"""
        pins = []

        def walk(x):
            for e in x:
                if isinstance(e, list):
                    if e and e[0] == "pin":
                        at = sx.one(e, "at")
                        pins.append((sx.one(e, "number")[1], sx.one(e, "name")[1], float(at[1]), float(at[2]),
                                     float(at[3]) if len(at) > 3 else 0.0))
                    else:
                        walk(e)
        walk(self.libsym(lib_id))
        return pins

    @staticmethod
    def xform(at, mirror, px, py):
        x, y = float(at[1]), float(at[2])
        rot = int(float(at[3])) if len(at) > 3 else 0
        X, Y = px, -py
        r = math.radians(-rot)
        c, s = round(math.cos(r)), round(math.sin(r))
        X, Y = X * c - Y * s, X * s + Y * c
        if mirror:
            if mirror == "x":
                Y = -Y
            if mirror == "y":
                X = -X
        return r3(x + X), r3(y + Y)

    def pins(self, s):
        """配置済みの部品の端子: {番号: ((x, y), 外向き(dx, dy), 名前)}"""
        lib_id = sx.one(s, "lib_id")[1]
        at = sx.one(s, "at")
        mir = sx.one(s, "mirror")
        mir = mir[1] if mir else None
        out = {}
        for n, name, px, py, ang in self.lib_pins(lib_id):
            p0 = self.xform(at, mir, px, py)
            a = math.radians(ang)
            p1 = self.xform(at, mir, px + round(math.cos(a)), py + round(math.sin(a)))
            out[n] = (p0, (r3(p0[0] - p1[0]), r3(p0[1] - p1[1])), name)   # 端子の先から本体と反対の向き
        return out

    def pinpos(self, pinref):
        ref, n = pinref.rsplit(".", 1)
        p = self.pins(self.sym(ref))
        if n not in p:
            raise SystemExit(f"{ref} に端子 {n} が無い(端子: {', '.join(p)})")
        return p[n]

    def wires(self):
        out = []
        for w in sx.find(self.root, "wire"):
            xy = sx.find(sx.one(w, "pts"), "xy")
            out.append(((r3(xy[0][1]), r3(xy[0][2])), (r3(xy[1][1]), r3(xy[1][2])), w))
        return out

    def wires_at(self, p):
        return [(a, b, w) for a, b, w in self.wires() if a == p or b == p]

    # ---------------------------------------------------------------- 追加
    def wire(self, a, b):
        self.root.append(["wire", ["pts", ["xy", num(a[0]), num(a[1])], ["xy", num(b[0]), num(b[1])]],
                          ["stroke", ["width", "0"], ["type", "default"]], ["uuid", U()]])

    def label(self, name, p, d):
        """p に向き d(外向き)のラベル"""
        ang = {(1, 0): 0, (-1, 0): 180, (0, -1): 90, (0, 1): 270}[(int(math.copysign(1, d[0])) if d[0] else 0,
                                                                    int(math.copysign(1, d[1])) if d[1] else 0)]
        j = "right" if ang in (180, 270) else "left"
        self.root.append(["label", Q(name), ["at", num(p[0]), num(p[1]), str(ang)], ["fields_autoplaced", "yes"],
                          eff(justify=[j, "bottom"]), ["uuid", U()]])

    def stub_label(self, name, p, d, length=2.54):
        q = (r3(p[0] + d[0] * length), r3(p[1] + d[1] * length))
        self.wire(p, q)
        self.label(name, q, d)
        return q

    def setprop(self, s, key, val, hide=True):
        p = sx.prop(s, key)
        if p:
            old = p[2]
            p[2] = Q(val)
            return old
        at = sx.one(s, "at")
        idx = max(i for i, e in enumerate(s) if isinstance(e, list) and e and e[0] == "property") + 1
        s.insert(idx, ["property", Q(key), Q(val), ["at", at[1], at[2], "0"], eff(hide=hide)])
        return None

    def newsym(self, lib_id, ref, val, x, y, rot, fields):
        self.ensure_lib(lib_id)
        pins = self.lib_pins(lib_id)
        s = ["symbol", ["lib_id", Q(lib_id)], ["at", num(x), num(y), str(rot)], ["unit", "1"],
             ["exclude_from_sim", "no"], ["in_bom", "yes"], ["on_board", "yes"], ["dnp", "no"], ["fields_autoplaced", "yes"],
             ["uuid", U()],
             ["property", Q("Reference"), Q(ref), ["at", num(x), num(y), "0"], eff()],
             ["property", Q("Value"), Q(val), ["at", num(x), num(y), "0"], eff()],
             ["property", Q("Footprint"), Q(fields.pop("footprint", fields.pop("Footprint", ""))), ["at", num(x), num(y), "0"], eff(hide=True)],
             ["property", Q("Datasheet"), Q("~"), ["at", num(x), num(y), "0"], eff(hide=True)],
             ["property", Q("Description"), Q(fields.pop("Description", "")), ["at", num(x), num(y), "0"], eff(hide=True)]]
        for k, v in fields.items():
            s.append(["property", Q(k), Q(v), ["at", num(x), num(y), "0"], eff(hide=True)])
        for n in dict.fromkeys(p[0] for p in pins):
            s.append(["pin", Q(n), ["uuid", U()]])
        s.append(["instances", ["project", Q(self.project), ["path", Q("/" + self.uuid), ["reference", Q(ref)], ["unit", "1"]]]])
        self.root.append(s)
        self.new_syms.append(s)
        if ref.startswith("#"):   # 電源記号(PWR_FLAG など): 部品表に入れず、部品番号は隠す
            sx.one(s, "in_bom")[1] = "no"
            sx.one(s, "on_board")[1] = "no"
            sx.prop(s, "Reference")[4] = eff(hide=True)
        return s

    # ---------------------------------------------------------------- 文字の置き場所(重ならない所を探す)
    @staticmethod
    def text_w(t, size=1.27):
        return sum(2.0 if ord(ch) > 0x2E80 else 1.0 for ch in t) * size * 0.82

    def body_rect(self, s):
        """記号の本体(図形と端子)を囲む四角 (x0, y0, x1, y1)"""
        lib_id = sx.one(s, "lib_id")[1]
        at = sx.one(s, "at")
        mir = sx.one(s, "mirror")
        mir = mir[1] if mir else None
        pts = []

        def walk(x):
            for e in x:
                if isinstance(e, list) and e:
                    if e[0] == "property":
                        continue
                    if e[0] in ("start", "end", "xy", "center", "mid") and len(e) >= 3:
                        try:
                            pts.append((float(e[1]), float(e[2])))
                        except ValueError:
                            pass
                    elif e[0] == "pin":
                        a = sx.one(e, "at")
                        ln = sx.one(e, "length")
                        px, py, ang = float(a[1]), float(a[2]), math.radians(float(a[3]) if len(a) > 3 else 0)
                        L = float(ln[1]) if ln else 0
                        pts.extend([(px, py), (px + L * round(math.cos(ang)), py + L * round(math.sin(ang)))])
                        continue
                    walk(e)
        walk(self.libsym(lib_id) or [])
        if not pts:
            x, y = float(at[1]), float(at[2])
            return (x - 1, y - 1, x + 1, y + 1)
        T = [self.xform(at, mir, px, py) for px, py in pts]
        return (min(p[0] for p in T), min(p[1] for p in T), max(p[0] for p in T), max(p[1] for p in T))

    def obstacles(self, skip=None):
        """重なってはいけない物の四角: 配線・ほかの記号の本体・ラベル・見えている文字"""
        R = []
        for a, b, _ in self.wires():
            R.append((min(a[0], b[0]) - 0.3, min(a[1], b[1]) - 0.3, max(a[0], b[0]) + 0.3, max(a[1], b[1]) + 0.3))
        for s in self.symbols():
            R.append(self.body_rect(s))
            if s is skip:
                continue
            for p in sx.find(s, "property"):
                e = sx.one(p, "effects")
                if not p[2] or (e and (["hide", "yes"] in e or "hide" in e)) or p[1] not in ("Reference", "Value"):
                    continue
                a = sx.one(p, "at")
                x, y, w = float(a[1]), float(a[2]), self.text_w(p[2])
                j = sx.one(e, "justify") if e else None
                x0 = x if j and "left" in j else x - w if j and "right" in j else x - w / 2
                R.append((x0, y - 0.8, x0 + w, y + 0.8))
        for lb in sx.find(self.root, "label") + sx.find(self.root, "global_label"):
            a = sx.one(lb, "at")
            x, y, ang, w = float(a[1]), float(a[2]), int(float(a[3])) if len(a) > 3 else 0, self.text_w(lb[1])
            R.append({0: (x, y - 1.5, x + w, y + 0.3), 180: (x - w, y - 1.5, x, y + 0.3),
                      90: (x - 1.5, y - w, x + 0.3, y), 270: (x - 1.5, y, x + 0.3, y + w)}.get(ang, (x, y - 1.5, x + w, y)))
        for tx in sx.find(self.root, "text"):
            a = sx.one(tx, "at")
            lines = tx[1].split("\n")
            w = max(self.text_w(l) for l in lines)
            R.append((float(a[1]), float(a[2]) - 1.6 * len(lines), float(a[1]) + w, float(a[2]) + 0.3))
        return R

    @staticmethod
    def hit(r, R):
        return any(not (r[2] <= o[0] or o[2] <= r[0] or r[3] <= o[1] or o[3] <= r[1]) for o in R)

    def place_fields(self, s):
        """部品番号と値を、配線・部品・ほかの文字に重ならない所に置く(本体の右→上→下→左の順に、近い所から)"""
        at = sx.one(s, "at")
        rot = at[3] if len(at) > 3 else "0"
        fang = "90" if rot in ("90", "270") else "0"   # 欄の角度は記号の回転と組み合わさる → 横書きになるように
        texts = [sx.prop(s, k)[2] for k in ("Reference", "Value")]
        w = max(self.text_w(t) for t in texts)
        h = 2.54 + 1.6            # 2 行
        b = self.body_rect(s)
        R = self.obstacles(skip=s)
        cx, cy = (b[0] + b[2]) / 2, (b[1] + b[3]) / 2
        cands = []
        wide = (b[2] - b[0]) > (b[3] - b[1])   # 横長(DCDC など)は上から、縦長(抵抗・コンデンサ)は右から
        for k in range(0, 8):
            d = 1.27 * k
            right, left = (b[2] + 0.8 + w / 2 + d, cy), (b[0] - 0.8 - w / 2 - d, cy)
            up, down = (cx, b[1] - 0.6 - h / 2 - d), (cx, b[3] + 0.6 + h / 2 + d)
            cands += [up, down, right, left] if wide else [right, up, down, left]
            for dy in (-2.54, 2.54, -5.08, 5.08):
                cands += [(b[2] + 0.8 + w / 2 + d, cy + dy), (b[0] - 0.8 - w / 2 - d, cy + dy)]
        best = None
        for gx, gy in cands:
            gx, gy = round(gx / 0.635) * 0.635, round(gy / 0.635) * 0.635
            r = (gx - w / 2, gy - h / 2, gx + w / 2, gy + h / 2)
            if not self.hit(r, R):
                best = (gx, gy)
                break
        if best is None:
            best = cands[0]
            print(f"  注意: {texts[0]} の文字を重ならない所に置けなかった(右に置いた)")
        gx, gy = best
        for (px, py), key in zip([(gx, gy - 1.27), (gx, gy + 1.27)], ("Reference", "Value")):
            p = sx.prop(s, key)
            p[3] = ["at", num(px), num(py), fang]
            p[4] = eff()

    # ---------------------------------------------------------------- 命令
    def cmd_info(self, a):
        for ref in self.select(a[0]):
            s = self.sym(ref)
            print(f"{ref}: {sx.one(s, 'lib_id')[1]}  位置 {sx.one(s, 'at')[1:]}")
            for p in sx.find(s, "property"):
                if p[2]:
                    print(f"  {p[1]}: {p[2]}")
            for n, (pos, d, name) in self.pins(s).items():
                ws = len(self.wires_at(pos))
                print(f"  端子 {n}({name}) {pos} 配線 {ws} 本 ネット {self.netname.get((ref, n), '—')}")

    def cmd_set(self, a):
        refs, key, val = self.select(a[0]), a[1], a[2]
        import review
        olds = {}
        for ref in refs:
            old = self.setprop(self.sym(ref), key, val, hide=key not in ("Reference", "Value"))
            olds.setdefault(old or "", []).append(ref)
        for old, rs in olds.items():
            self.log.append(f"{review.refs_text(rs)} の「{key}」を {'「' + old + '」から' if old else '(新しい欄)'}「{val}」に変える"
                            + ("(「シンボル フィールド テーブル」で一括が楽)" if len(rs) > 3 else ""))
        print(f"set {','.join(sorted(refs, key=lambda r: (re.sub(r'[0-9]', '', r), int(re.sub(r'[^0-9]', '', r) or 0))))} {key} = {val}")

    def cmd_rename(self, a):
        s = self.sym(a[0])
        if a[1] in [self.ref(x) for x in self.symbols()]:
            raise SystemExit(f"{a[1]} はもうある")
        sx.prop(s, "Reference")[2] = Q(a[1])
        for r in self._walk(s, "reference"):
            r[1] = Q(a[1])
        self.log.append(f"{a[0]} の部品番号を {a[1]} に変える")

    def _walk(self, x, name):
        for e in x:
            if isinstance(e, list):
                if e and e[0] == name:
                    yield e
                yield from self._walk(e, name)

    def cmd_symbol(self, a):
        lib_id = a[1]
        self.ensure_lib(lib_id)
        for ref in self.select(a[0]):
            s = self.sym(ref)
            before = {n: p[0] for n, p in self.pins(s).items()}
            old = sx.one(s, "lib_id")[1]
            sx.one(s, "lib_id")[1] = Q(lib_id)
            after = {n: p[0] for n, p in self.pins(s).items()}
            if before != after:
                sx.one(s, "lib_id")[1] = Q(old)
                raise SystemExit(f"{ref}: {old} と {lib_id} で端子の番号か位置が違うので差し替えられない\n  前 {before}\n  後 {after}")
            self.log.append(f"{ref} の記号を {old} から {lib_id} に差し替える(「記号を変更」)")
        print(f"symbol {a[0]} → {lib_id}")

    def cmd_delete(self, a):
        ref = a[0]
        names = dict(x.split("=", 1) for x in a[1:])
        s = self.sym(ref)
        placed = []
        for n, (pos, d, pname) in self.pins(s).items():
            if not self.wires_at(pos):
                continue   # 何もつながっていない端子
            name = names.get(n)
            if name is None:
                net = self.netname.get((ref, n), "")
                if net.startswith("/"):
                    name = net[1:]
                elif net and not net.startswith(("Net-(", "unconnected")):
                    name = net
                else:
                    raise SystemExit(f"{ref}.{n} のネット名が自動の名前({net})。delete {ref} {n}=名前 のように指定して")
            self.label(name, pos, self._wire_dir(pos, d))
            placed.append(f"{n}→{name}")
        self.root.remove(s)
        self.log.append(f"{ref} を削除し、つながっていた配線の端にラベルを置く({', '.join(placed)})")
        print(f"delete {ref}(ラベル {', '.join(placed) or 'なし'})")

    def _wire_dir(self, pos, d):
        """配線の端に置くラベルの向き: 配線と反対(部品のあった側)"""
        ws = self.wires_at(pos)
        a, b, _ = ws[0]
        o = b if a == pos else a
        v = (o[0] - pos[0], o[1] - pos[1])
        return (-(v[0] > 0) + (v[0] < 0), -(v[1] > 0) + (v[1] < 0))

    def cmd_insert(self, a):
        """insert 部品番号 値 記号 at 部品.端子 [gap=2.54] ... / insert ... on x,y(配線の途中の点を中心に)"""
        ref, val, lib_id, how = a[0], a[1], a[2], a[3]
        if how not in ("at", "on"):
            raise SystemExit("insert 部品番号 値 記号 at 部品.端子 ... か、insert ... on x,y ...")
        opts = dict(x.split("=", 1) for x in a[5:])
        gap = float(opts.pop("gap", 2.54))
        self.ensure_lib(lib_id)
        if how == "at":
            P, _, _ = self.pinpos(a[4])
            ws = self.wires_at(P)
            if len(ws) != 1:
                raise SystemExit(f"{a[4]} から出ている配線が {len(ws)} 本(1 本のときだけ挿入できる)")
            wa, wb, w = ws[0]
            Qp = wb if wa == P else wa
            where = a[4]
        else:
            px, py = (r3(v) for v in a[4].split(","))
            hitw = [(wa, wb, w) for wa, wb, w in self.wires()
                    if (wa[0] == wb[0] == px and min(wa[1], wb[1]) < py < max(wa[1], wb[1])) or
                       (wa[1] == wb[1] == py and min(wa[0], wb[0]) < px < max(wa[0], wb[0]))]
            if len(hitw) != 1:
                raise SystemExit(f"({px}, {py}) を通る縦か横の配線が {len(hitw)} 本(1 本のときだけ)")
            P, Qp, w = hitw[0]
            where = f"({num(px)}, {num(py)}) の配線"
        L = abs(Qp[0] - P[0]) + abs(Qp[1] - P[1])
        d = (round((Qp[0] - P[0]) / L), round((Qp[1] - P[1]) / L))
        if abs(d[0]) + abs(d[1]) != 1:
            raise SystemExit(f"{where} が斜め")
        # 記号の向きを配線に合わせる(at のときは 1 番端子を指定の端子側に)
        for rot in (0, 90, 180, 270):
            tmp = ["symbol", ["lib_id", Q(lib_id)], ["at", "0", "0", str(rot)]]
            pp = self.pins(tmp)
            if len(pp) != 2:
                raise SystemExit(f"{lib_id} は端子が 2 つでない(直列に入れられるのは 2 端子の部品)")
            (n1, (p1, _, _)), (n2, (p2, _, _)) = sorted(pp.items())
            v = (p2[0] - p1[0], p2[1] - p1[1])
            span = abs(v[0]) + abs(v[1])
            if (round(v[0] / span), round(v[1] / span)) == d:
                break
        if how == "on":
            off = (abs(px - P[0]) + abs(py - P[1])) - span / 2   # 点が部品の中心
            if off < -1e-6 or off + span > L + 1e-6:
                raise SystemExit(f"{where}: 部品({span:.2f}mm)が配線からはみ出す")
            gap = off
        need = gap + span
        if L < need - 1e-6:
            raise SystemExit(f"{where} の配線が短い({L:.2f}mm。部品に {span:.2f}mm＋端子から {gap:.2f}mm 要る。gap=0 か on x,y も使える)")
        a1 = (r3(P[0] + d[0] * gap), r3(P[1] + d[1] * gap))
        cx, cy = r3(a1[0] - p1[0]), r3(a1[1] - p1[1])
        a2 = (r3(cx + p2[0]), r3(cy + p2[1]))
        self.root.remove(w)
        if a1 != P:
            self.wire(P, a1)
        if a2 != Qp:
            self.wire(a2, Qp)
        self.newsym(lib_id, ref, val, cx, cy, rot, opts)
        self.log.append(f"{where}((" + f"{num(P[0])}, {num(P[1])})〜({num(Qp[0])}, {num(Qp[1])}))を切り、"
                        f"{ref}({lib_id}、{val})を中心 ({num(cx)}, {num(cy)})・{rot}° に置いて直列に入れる")
        print(f"insert {ref} {val} {how} {a[4]}: 中心 ({num(cx)}, {num(cy)}) {rot}°")

    def cmd_add(self, a):
        ref, val, lib_id = a[0], a[1], a[2]
        if a[3] != "at":
            raise SystemExit("add 部品番号 値 記号 at x,y [rot=0] [端子=ネット ...] [欄=値 ...]")
        x, y = (float(v) for v in a[4].split(","))
        opts = dict(t.split("=", 1) for t in a[5:])
        rot = int(opts.pop("rot", 0))
        self.ensure_lib(lib_id)
        pinnums = {p[0] for p in self.lib_pins(lib_id)}
        nets = {k: opts.pop(k) for k in list(opts) if k in pinnums}
        s = self.newsym(lib_id, ref, val, x, y, rot, opts)
        P = self.pins(s)
        for n, name in nets.items():
            pos, d, _ = P[n]
            self.stub_label(name, pos, d)
        self.log.append(f"{ref}({lib_id}、{val})を ({num(x)}, {num(y)})・{rot}° に置き、"
                        + "、".join(f"端子 {n} に短い線とラベル {v}" for n, v in nets.items()))
        print(f"add {ref} {val} at ({num(x)}, {num(y)})")

    def cmd_label(self, a):
        name, pin = a[0], a[2]
        pos, d, _ = self.pinpos(pin)
        self.stub_label(name, pos, d)
        self.log.append(f"{pin} に短い線とラベル {name} を付ける")
        print(f"label {name} at {pin}")

    def cmd_text(self, a):
        x, y = (float(v) for v in a[2].split(","))
        self.root.append(["text", Q(a[0]), ["exclude_from_sim", "no"], ["at", num(x), num(y), "0"],
                          eff(justify=["left", "bottom"]), ["uuid", U()]])
        self.log.append(f"({num(x)}, {num(y)}) に注記「{a[0][:40]}…」")

    def cmd_title(self, a):
        tb = sx.one(self.root, "title_block")
        if not tb:
            idx = [i for i, e in enumerate(self.root) if isinstance(e, list) and e and e[0] == "paper"][0] + 1
            tb = ["title_block"]
            self.root.insert(idx, tb)
        for kv in a:
            k, v = kv.split("=", 1)
            e = sx.one(tb, k)
            if e:
                e[1] = Q(v)
            else:
                tb.append([k, Q(v)])
            self.log.append(f"表題欄の {k} を「{v}」に")

    def finish(self):
        for s in self.new_syms:
            if not self.ref(s).startswith("#"):
                self.place_fields(s)

    def save(self, out):
        Path(out).write_text(sx.dump(self.root) + "\n", encoding="utf-8")


def load_nets(sch):
    sys.path.insert(0, str(HERE / "ksim"))
    import ksim
    comps, nets = ksim.parse_netlist(ksim.export_netlist(sch))
    return {(r, p): name for name, nodes in nets.items() for r, p, _ in nodes}, (comps, nets)


def main():
    ap = argparse.ArgumentParser(description="画面なしで回路図を直す", formatter_class=argparse.RawDescriptionHelpFormatter,
                                 epilog=__doc__)
    ap.add_argument("input")
    ap.add_argument("output")
    ap.add_argument("steps")
    ap.add_argument("--instructions", help="画面で同じ操作をするための指示書(Markdown)")
    a = ap.parse_args()

    sch = Sch(a.input)
    sch.netname, before = load_nets(a.input)
    text = sys.stdin.read() if a.steps == "-" else Path(a.steps).read_text(encoding="utf-8")
    for ln, line in enumerate(text.splitlines(), 1):
        line = line.split(" #")[0].strip() if not line.lstrip().startswith("#") else ""
        if not line:
            continue
        t = shlex.split(line)
        f = getattr(sch, f"cmd_{t[0]}", None)
        if not f:
            raise SystemExit(f"{ln} 行目: 命令 {t[0]} が無い(info/set/rename/symbol/delete/insert/add/label/text/title)")
        try:
            f(t[1:])
        except (IndexError, ValueError) as e:
            raise SystemExit(f"{ln} 行目「{line}」の書き方が違う({e})。kicad-local sch --help")

    if a.output == "-":
        tmp = Path(tempfile.mkdtemp()) / Path(a.input).name
        out = tmp
    else:
        out = Path(a.output)
    if not sch.log and a.output == "-":
        return 0
    sch.finish()
    sch.save(out)

    # 確かめる: 接続・部品表の差と ERC
    import review
    _, after = load_nets(out)
    rows, n = review.bom_diff(before, after)
    nd = review.net_diff(before, after)
    print(f"\n--- 部品表の差({n} 個)")
    print("\n".join(rows) or "なし")
    print(f"--- 接続の差({len(nd)} ネット)")
    print("\n".join(nd) or "なし")
    s, lines, k = review.erc(out, Path(tempfile.mkdtemp()))
    print(f"--- ERC: {s}")
    if lines and k:
        print("\n".join(lines[:20]))
    if a.instructions:
        md = [f"# 回路図の変更手順({Path(a.input).name})", "", "KiCad の回路図エディタで、上から順に:", ""]
        md += [f"{i + 1}. {x}" for i, x in enumerate(sch.log)]
        md += ["", "終わったら「回路図から基板を更新」(F8)で基板に反映する。", "",
               "## この変更で変わる接続", ""] + (nd or ["なし"])
        Path(a.instructions).write_text("\n".join(md) + "\n", encoding="utf-8")
        print(f"指示書 → {a.instructions}")
    if a.output != "-":
        print(f"→ {out}")
    return 1 if k else 0


if __name__ == "__main__":
    sys.exit(main())
