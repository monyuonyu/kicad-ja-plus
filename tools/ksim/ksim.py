#!/usr/bin/env python3
"""
ksim: KiCad の回路図から ngspice のシミュレーションを組み立てて回す(ローカル道具、2026-09-25)

  ksim run  <spec.toml> [--out 出力フォルダ] [--runs N]   シミュレーション(＋モンテカルロ)
  ksim nets <回路図.kicad_sch>                            ネット名とつながる端子の一覧
  ksim parts <回路図.kicad_sch>                           部品と、どう SPICE 化されるか(されないか)

- 回路図 → kicad-cli でネットリスト(端子の役割つき) → 部品ごとに SPICE の素子を作る
  (抵抗・コンデンサ・コイルは値の文字列から、ダイオード・トランジスタ・MOSFET は端子の役割から)
- コネクタ・マイコン・テストピン・取付穴はシミュレーションから外す(ネットは残るので、そこに信号源をつなぐ)
- ばらつき: 回路図の Tolerance 欄(例 ±1%)を使う。欄が無い抵抗は ±5%、コンデンサは ±20%
- 結果: 出力フォルダに 結果.md、波形の PNG、実際に流した回路(circuit.cir)、測定値の CSV
"""
import argparse, csv, fnmatch, math, os, random, re, subprocess, sys, tempfile, tomllib
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
# モデルの置き場(対応表 map.toml・定格 ratings.toml・base.lib): 既定はこの道具の models/。
# 別の置き場(自分で足したモデルのある、入れた版の kicad-local-tools など)は KSIM_MODELS=フォルダ で
MODELS = Path(os.path.expanduser(os.environ["KSIM_MODELS"])).resolve() if os.environ.get("KSIM_MODELS") else HERE / "models"
import ngs  # noqa: E402


def _trapz(y, x):
    """台形の積分: numpy 2 は trapezoid、1.x(Ubuntu 24.04 の 1.26)は trapz だけ(定格チェックで止まった)"""
    import numpy as np

    return (getattr(np, "trapezoid", None) or np.trapz)(y, x)

# ---------------------------------------------------------------- ネットリスト

def export_netlist(sch):
    out = Path(tempfile.mkdtemp()) / "n.net"
    cli = "kicad-cli"
    r = subprocess.run([cli, "sch", "export", "netlist", "--format", "kicadsexpr", "-o", str(out), str(sch)],
                       capture_output=True, text=True)
    if not out.exists():
        sys.exit(f"ネットリストを書き出せない: {sch}\n{r.stdout}{r.stderr}")
    return out.read_text(encoding="utf-8")


def parse_netlist(text):
    """kicad-cli の kicadsexpr ネットリストを読む。S 式として読むので、KiCad 8 の 1 行の書き方にも、
    KiCad 10 の要素ごとに改行する書き方にも対応する"""
    tools = str(Path(__file__).resolve().parent.parent)
    if tools not in sys.path:
        sys.path.insert(0, tools)
    import sx

    def val(e, name):
        x = sx.one(e, name)
        return str(x[1]) if x is not None and len(x) > 1 else ""

    root = sx.parse(text)
    comps = {}
    for c in sx.find(sx.one(root, "components") or [], "comp"):
        ref = val(c, "ref")
        lib = sx.one(c, "libsource")
        fields = {}
        for f in sx.find(sx.one(c, "fields") or [], "field"):
            name = val(f, "name")
            vals = [x for x in f[1:] if not isinstance(x, list)]
            fields[name] = str(vals[0]) if vals else ""
        comps[ref] = {"ref": ref, "value": val(c, "value"), "footprint": val(c, "footprint"),
                      "lib": f"{val(lib, 'lib')}:{val(lib, 'part')}" if lib is not None else "",
                      "fields": fields, "pins": {}}
    nets = {}
    for n in sx.find(sx.one(root, "nets") or [], "net"):
        name = val(n, "name")
        raw = [(val(d, "ref"), val(d, "pin"), val(d, "pinfunction"), val(d, "pintype")) for d in sx.find(n, "node")]
        nets[name] = [(r, p, f) for r, p, f, _ in raw]
        for ref, pin, fn, pt in raw:
            if ref in comps:
                comps[ref]["pins"][pin] = {"net": name, "fn": fn, "type": pt}
    return comps, nets

# ---------------------------------------------------------------- 値の読み取り

SI = {"f": 1e-15, "p": 1e-12, "n": 1e-9, "u": 1e-6, "µ": 1e-6, "μ": 1e-6, "m": 1e-3,
      "k": 1e3, "K": 1e3, "M": 1e6, "G": 1e9, "R": 1, "": 1}


def parse_value(s):
    """'4.7kΩ' '22µF/50V' '33µH SRC1317' '4k7' '0.1uF' → 数値"""
    s = s.strip().replace("Ω", "").replace("ohm", "")
    m = re.match(r'^\s*([0-9]*\.?[0-9]+)\s*([fpnuµμmkKMGR]?)([0-9]*)', s)
    if not m:
        return None
    num, pre, tail = m.groups()
    if tail and pre:  # 4k7 形式
        num = f"{num}.{tail}"
    return float(num) * SI[pre]


def parse_tol(s, default):
    m = re.search(r'([0-9.]+)\s*%', s or "")
    return float(m.group(1)) / 100 if m else default

# ---------------------------------------------------------------- 回路の組み立て

SKIP_LIBS = ("Connector", "Connector_Generic", "Mechanical", "MCU_Module", "power", "Graphic")
# 部品番号でもコネクタ・テストピン・取付穴とみなす(自前の記号のライブラリでは上で分からない)
SKIP_REF = re.compile(r"^(J|JP|P|CN|CON|TP|MH|H)\d")


PN_FIELDS = ("MPN", "品番", "型番", "Manufacturer_Part_Number", "Manufacturer Part Number", "MFR_PN", "PartNumber",
             "Part Number", "P/N")


def part_number(c):
    """部品の品番(MPN・品番・型番などの欄。無ければ "")"""
    f = c.get("fields", {})
    return next((f[k].strip() for k in PN_FIELDS if f.get(k, "").strip()), "")


def generic_value(c, val=None):
    """値が記号の名前のまま(D_TVS・Q_NPN_ECB など): 値からは品物が分からない"""
    val = c["value"] if val is None else val
    kind = c["lib"].split(":")[-1]
    return bool(val) and (val == kind or re.fullmatch(r'(D|Q|U|L|C|R)(_[A-Za-z0-9]+)+', val) is not None)


# 電解コンデンサの tanδ(120Hz)の既定: 品番が表に無いとき、耐圧ごとの汎用品の代表値(一般品の規格の上限)
TAND_BY_VOLTAGE = ((6.3, 0.22), (10, 0.19), (16, 0.16), (25, 0.14), (35, 0.12), (50, 0.10), (1e9, 0.08))


class Builder:
    def __init__(self, comps, nets, spec):
        self.comps, self.nets, self.spec = comps, nets, spec
        self.mapping = dict( tomllib.loads( ( MODELS / "map.toml" ).read_text( encoding="utf-8" ) ).get( "map", {} ) )
        self.mapping.update( spec.get("models", {}).get("map", {}) )
        self.exclude = spec.get("exclude", {}).get("refs", [])
        self.params = spec.get("parts", {})
        gnd = list(spec.get("ground", ["GND", "/GND"]))
        # 名前の無い GND(Net-(D1-A) など): GND 機能の電源端子(マイコン・電源モジュールの GND)を含むネットも GND とみなす
        for name, nodes in nets.items():
            if name not in gnd and any((f or "").upper() in ("GND", "VSS", "0V") and
                                       comps.get(r, {}).get("pins", {}).get(p, {}).get("type", "").startswith("power")
                                       for r, p, f in nodes):
                gnd.append(name)
        self.gnd_nets = [n for n in gnd if n in nets]
        if len(self.gnd_nets) > 1:
            print(f"注意: GND とみなしたネットが {len(self.gnd_nets)} 本: {', '.join(self.gnd_nets)}(全部 0 につなぐ)", file=sys.stderr)
        self.gnd = set(gnd) | {"0", "gnd", "GND", "/GND"}
        self.node = {}
        for i, name in enumerate(sorted(nets)):
            self.node[name] = "0" if name in gnd else f"n{i}"
        self.par = load_parasitics(spec, comps)   # {ネット: (起点, {端子: (L nH, R mΩ, 長さ)})}
        self.par_used = {}
        self.generic = {}       # 値が汎用名のまま、品番からもモデルが決まらなかった部品 {部品: (値, 品番)}
        self.no_dcr = set()        # 巻線の抵抗が分からない L
        self.assumed_esr = set()   # ESR を汎用品の代表値にした電解

    def padnode(self, net, ref, pin):
        """基板の寄生分を入れるネットでは、端子ごとに別のノード(起点の端子とは L・R でつなぐ)"""
        base = self.node[net]
        if net not in self.par:
            return base
        hub, tbl = self.par[net]
        pad = f"{ref}.{pin}"
        if pad == hub or not tbl.get(pad):
            return base
        n = f"{'g' if base == '0' else base}_{ref}_{pin}"
        self.par_used[n] = (base, tbl[pad], pad, net)
        return n

    def par_lines(self):
        out = []
        for n, (base, vals, pad, net) in sorted(self.par_used.items()):
            L, R = vals[0], vals[1]
            C = vals[3] if len(vals) > 3 else 0
            out.append(f"LP_{n} {base} {n}_m {max(L, 1e-3):.4g}n")
            out.append(f"RP_{n} {n}_m {n} {max(R, 0.1):.4g}m")
            if C > 0.01:
                out.append(f"CP_{n} {n} 0 {C:.4g}p")
        return out

    def net(self, name):
        """KiCad のネット名 か 部品.端子 → SPICE のノード名"""
        if name in self.node:
            return self.node[name]
        m = re.match(r'^([A-Za-z_]+[0-9]+[A-Za-z0-9_]*)\.([A-Za-z0-9]+)$', name)
        if m and m.group(1) in self.comps and m.group(2) in self.comps[m.group(1)]["pins"]:
            return self.padnode(self.comps[m.group(1)]["pins"][m.group(2)]["net"], m.group(1), m.group(2))
        if name in self.gnd:
            return "0"
        if re.match(r'^[a-z][a-z0-9_]*$', name) and not re.match(r'^n[0-9]+$', name):
            return name   # 手順ファイルの中だけで使う補助の点(例 gpio)。そのままの名前で使う
        import difflib
        near = difflib.get_close_matches(name, list(self.node), n=4, cutoff=0.4)
        upper = name.upper()
        near += [n for n in self.node if upper and upper in n.upper() and n not in near][:4]
        raise SystemExit(
            f"ネットが見つからない: {name}\n"
            "  書き方は 3 通り: 回路図のネット名そのまま(名前の無いネットは Net-(A1-3V3) のような自動の名前)、"
            "部品.端子(例 A1.9、R3.1)、手順の中だけの補助の点(英小文字で始まる名前、例 gpio)\n"
            + (f"  近い名前: {', '.join(near[:6])}\n" if near else "")
            + "  ネット名の一覧: kicad-local sim nets <回路図>")

    def pin(self, c, fn=None, num=None):
        for p, d in c["pins"].items():
            if (fn and d["fn"] == fn) or (num and p == num):
                return self.padnode(d["net"], c["ref"], p)
        # 端子がどのネットにもつながっていない
        return f"nc_{c['ref']}_{fn or num}"

    def element(self, c, rng=None, tolerances=None):
        """部品 1 個 → (SPICE の行, 説明)。シミュレーションしない部品は (None, 理由)"""
        ref, val, lib = c["ref"], c["value"], c["lib"]
        if any(fnmatch.fnmatch(ref, p) for p in self.exclude):
            return None, "除外(spec の exclude)"
        if lib.split(":")[0] in SKIP_LIBS or "TestPoint" in lib or "MountingHole" in lib or SKIP_REF.match(ref):
            return None, "コネクタ・マイコン・機構部品のため除外"
        over = self.params.get(ref, {})
        if isinstance(over, str):
            over = {"spice": over}
        if "spice" in over:  # 部品ごとに SPICE の行を直接書く({n:端子番号} でノードに置換)
            line = re.sub(r'\{n:([^}]+)\}', lambda m: self.pin(c, num=m.group(1)), over["spice"])
            return line, "spec で直接指定"
        if "value" in over:   # 版の比較などで値だけ差し替える
            val = str(over["value"])
        pn = part_number(c)
        # 品番が先: 値が汎用名(D_TVS)のとき、値の対応(6.8V の TVS)で別の品物(P6KE39A)を計算していた
        mapped = (self.mapping.get(pn) if pn else None) or self.mapping.get(val)
        kind = lib.split(":")[1] if ":" in lib else lib
        if generic_value(c, val) and not (pn and self.mapping.get(pn)) and kind not in ("R", "C", "L", "C_Polarized", "CP"):
            self.generic[ref] = (val, pn)
        pins = sorted(c["pins"])

        # 対応表の "R:6.2±19%" のように、許容差をモデル側で指定できる
        map_tol = None
        if mapped and "±" in mapped:
            mapped, t = mapped.split("±", 1)
            map_tol = parse_tol(t, None)

        def jitter(x, default_tol):
            tol = map_tol if map_tol is not None else parse_tol(c["fields"].get("Tolerance", ""), default_tol)
            if tolerances is not None:
                tolerances[ref] = tol
            if rng is None:
                return x
            return x * (1 + rng.uniform(-tol, tol))

        if kind == "R" or (mapped or "").startswith("R:"):
            v = parse_value(mapped[2:] if (mapped or "").startswith("R:") else val)
            if v is None:
                return None, f"抵抗値が読めない: {val}"
            n = [self.pin(c, num=p) for p in pins[:2]]
            return f"R{ref} {n[0]} {n[1]} {jitter(v, 0.05):.6g}", f"{v:.4g}Ω"
        if kind in ("C", "C_Polarized", "CP"):
            v = parse_value(val)
            n = [self.pin(c, num=p) for p in pins[:2]]
            cv = jitter(v, 0.20)
            esr, how = self.esr(c, cv)
            if esr:   # 電解コンデンサの ESR(tanδ から。esr_scale で倍率)
                return (f"C{ref} {n[0]} {n[0]}_esr{ref} {cv:.6g}\nR{ref}_ESR {n[0]}_esr{ref} {n[1]} {esr:.6g}",
                        f"{v:.4g}F、ESR {esr:.3g}Ω({how})")
            return f"C{ref} {n[0]} {n[1]} {cv:.6g}", f"{v:.4g}F"
        if kind == "L":
            v = parse_value(val)
            n = [self.pin(c, num=p) for p in pins[:2]]
            dcr = self.dcr(c, val)
            if dcr:   # 巻線の抵抗(DCR 欄か値の「0.12Ω」)。無い L は入力の C と鳴きやすい
                return (f"L{ref} {n[0]} {n[0]}_dcr{ref} {jitter(v, 0.20):.6g}\nR{ref}_DCR {n[0]}_dcr{ref} {n[1]} {dcr:.6g}",
                        f"{v:.4g}H、DCR {dcr:.3g}Ω")
            self.no_dcr.add(ref)
            return f"L{ref} {n[0]} {n[1]} {jitter(v, 0.20):.6g}", f"{v:.4g}H(DCR なし)"
        if kind == "Fuse":
            if not mapped:
                return None, f"ヒューズ {val} の抵抗値が未指定(models.map に \"R:値\")"
            v = parse_value(mapped[2:])
            n = [self.pin(c, num=p) for p in pins[:2]]
            return f"R{ref} {n[0]} {n[1]} {jitter(v, 0.2):.6g}", f"ヒューズ/PTC を {v}Ω で近似"
        if kind.startswith("D"):
            if not mapped:
                return None, f"ダイオード {val} のモデルが未指定(models.map)"
            return f"D{ref} {self.pin(c, fn='A')} {self.pin(c, fn='K')} {mapped}", f"モデル {mapped}"
        if kind.startswith("Q_NPN") or kind.startswith("Q_PNP"):
            if not mapped:
                return None, f"トランジスタ {val} のモデルが未指定(models.map)"
            return (f"Q{ref} {self.pin(c, fn='C')} {self.pin(c, fn='B')} {self.pin(c, fn='E')} {mapped}",
                    f"モデル {mapped}")
        if kind.startswith("Q_PMOS") or kind.startswith("Q_NMOS"):
            if not mapped:
                return None, f"MOSFET {val} のモデルが未指定(models.map)"
            return (f"M{ref} {self.pin(c, fn='D')} {self.pin(c, fn='G')} {self.pin(c, fn='S')} {mapped}",
                    f"モデル {mapped}")
        if kind.startswith("SW_"):
            state = over.get("state", "open")
            n = [self.pin(c, num=p) for p in pins[:2]]
            return (f"R{ref} {n[0]} {n[1]} {0.05 if state == 'closed' else 1e12}",
                    f"スイッチ({'閉' if state == 'closed' else '開'})。parts.{ref}.state で変更")
        if mapped and mapped.startswith("X:"):  # 例 X:DCDC12(IN GND OUT)
            m = re.match(r'X:(\w+)\(([^)]*)\)', mapped)
            if not m:
                return None, f"X: の書式は X:名前(端子の役割 ...): {mapped}"
            nodes = [self.pin(c, fn=f) for f in m.group(2).split()]
            have = {d["fn"] for d in c["pins"].values()}
            miss = [f for f in m.group(2).split() if f not in have]
            if miss:
                return None, f"対応表の端子名 {'・'.join(miss)} が部品に無い(部品の端子: {'・'.join(sorted(have))})"
            return f"X{ref} {' '.join(nodes)} {m.group(1)}", f"サブ回路 {m.group(1)}"
        return None, f"種類 {lib} の変換規則が無い(parts.{ref}.spice で直接指定できる)"

    def esr(self, c, cv):
        """電解コンデンサの ESR: ratings.toml の tanδ(120Hz)から。品番が表に無ければ耐圧ごとの汎用品の
        代表値(ESR の無い電解は L と鳴きやすい)。[params] esr_scale で倍率(0 で理想)。→ (ESR, 根拠)"""
        if c["lib"].split(":")[-1] not in ("C_Polarized", "CP"):
            return None, ""
        if not hasattr(self, "_caps"):
            self._caps = tomllib.loads((MODELS / "ratings.toml").read_text(encoding="utf-8")).get("capacitor", {})
        mpn = part_number(c)
        d = next((d for k, d in self._caps.items() if mpn and k in mpn), None)
        if d:
            tand, how = d["tand"], f"tanδ {d['tand']:g}"
        else:
            m = re.search(r'/\s*([0-9.]+)\s*V', c["value"]) or re.search(r'([0-9.]+)\s*V', c["fields"].get("Voltage", ""))
            volt = float(m.group(1)) if m else None
            tand = next(t for v, t in TAND_BY_VOLTAGE if volt is not None and volt <= v) if volt else 0.20
            how = f"tanδ {tand:g} は{'耐圧 ' + format(volt, 'g') + 'V の' if volt else '耐圧不明の'}汎用品の代表値"
            self.assumed_esr.add(c["ref"])
        sc = self.spec.get("params", {}).get("esr_scale", 1.0)
        sc = sc["nominal"] if isinstance(sc, dict) else float(sc)
        return (tand / (2 * math.pi * 120 * cv) * sc, how) if sc > 0 else (None, "")

    def dcr(self, c, val):
        """コイルの巻線の抵抗 [Ω]: DCR 欄、または値の中の「0.12Ω」「120mΩ」。分からなければ None"""
        text = c["fields"].get("DCR", "") or c["fields"].get("Rdc", "")
        m = re.search(r'([0-9.]+\s*[mkµu]?)\s*(Ω|ohm)', text or val, re.I)
        if m:
            return parse_value(m.group(1))
        return parse_value(text) if text else None

    def provenance(self, c):
        """どのモデルを、どこから使ったか(計算の前に一覧にする。黙って代用しない):
        {"model", "how", "file", "ok", "why"}。モデルの要らない部品(R・C・L・外す部品)は None"""
        ref, val, lib = c["ref"], c["value"], c["lib"]
        if any(fnmatch.fnmatch(ref, p) for p in self.exclude):
            return None
        if lib.split(":")[0] in SKIP_LIBS or "TestPoint" in lib or "MountingHole" in lib or SKIP_REF.match(ref):
            return None
        over = self.params.get(ref, {})
        if isinstance(over, str):
            over = {"spice": over}
        if "spice" in over:
            return {"model": "(直接の SPICE 行)", "how": "手順の parts の直接指定", "file": "", "ok": True, "why": ""}
        if "value" in over:
            val = str(over["value"])
        kind = lib.split(":")[1] if ":" in lib else lib
        pn = part_number(c)
        by_pn = self.mapping.get(pn) if pn else None
        by_val = self.mapping.get(val)
        if kind in ("R", "C", "L", "C_Polarized", "CP") and not (by_pn or by_val or "").startswith("R:"):
            return None   # 値から作る(ESR・DCR の前提は「モデルの前提」に)
        if kind.startswith("SW_"):
            state = over.get("state")
            return {"model": "抵抗(%s)" % (state or "開"), "how": "手順の parts の state" if state else "既定(開)",
                    "file": "", "ok": bool(state), "why": "" if state else "スイッチの状態を決めていない(既定の「開」で計算)"}
        model = by_pn or by_val
        if not model:
            return {"model": "", "how": "モデル無し", "file": "", "ok": False,
                    "why": "対応表に品番も値も無い(シミュレーションから外した)"}
        name = model.split("(")[0].split("±")[0]
        name = name[2:] if name.startswith(("X:", "R:")) else name
        where = self.model_file(name)
        if by_pn:
            how, ok, why = "対応表(品番 %s)" % pn, True, ""
        elif generic_value(c, val):
            how, ok, why = "対応表(値 %s)" % val, False, "値が記号の名前のまま(%s)。品番%sで当たらず、汎用のモデル" % (val, " " + pn if pn else "が無い")
        else:
            how, ok, why = "対応表(値 %s)" % val, bool(not pn), ("品番 %s では当たらず、値で当てた" % pn) if pn else ""
        if where.startswith("vendor"):
            how += "・メーカー配布"
        return {"model": model, "how": how, "file": where, "ok": ok, "why": why}

    def model_file(self, name):
        """モデル名を定義しているファイル(include の中から。vendor/ はメーカー配布)"""
        if not hasattr(self, "_defs"):
            self._defs = {}
            for x in self.spec.get("models", {}).get("include", ["models/base.lib"]):
                try:
                    f = resolve(self.spec, x)
                    text = Path(f).read_text(encoding="utf-8", errors="replace")
                except (SystemExit, OSError):
                    continue
                tag = ("vendor/" if "vendor" in Path(f).parts else "") + Path(f).name
                for m in re.finditer(r'^\s*\.(?:model|subckt)\s+(\S+)', text, re.M | re.I):
                    self._defs.setdefault(m.group(1).upper(), tag)
        return self._defs.get(name.upper(), "")

    def provenance_all(self):
        return {r: p for r in sorted(self.comps) if (p := self.provenance(self.comps[r])) is not None}

    def build(self, rng=None, tolerances=None, extra_lines=()):
        lines, skipped = [], {}
        self.emitted = {}   # 部品番号 → [素子名, ノード..., 値](定格チェック用。spec で直接書いた部品は除く)
        for ref in sorted(self.comps, key=lambda r: (re.sub(r'\d', '', r), int(re.sub(r'\D', '', r) or 0))):
            line, why = self.element(self.comps[ref], rng, tolerances)
            if line:
                lines.append(line)
                over = self.params.get(ref, {})
                if not (isinstance(over, str) or "spice" in over):
                    self.emitted[ref] = line.split("\n")[0].split()
            else:
                skipped[ref] = why
        return lines, skipped

# ---------------------------------------------------------------- 信号名の変換

_PAR_CACHE = {}


def load_parasitics(spec, comps):
    """[parasitics] pcb = 基板("auto" で回路図の隣)、hubs = [起点の端子, ...](その端子のネット全体)
    → 各端子までの L・R(pcbpar.py を kicad-python で)"""
    ps = spec.get("parasitics")
    if not ps:
        return {}
    import json
    nets = dict(ps.get("nets", {}))
    for hub in ps.get("hubs", []):
        ref, pin = hub.rsplit(".", 1)
        if ref not in comps or pin not in comps[ref]["pins"]:
            raise SystemExit(f"[parasitics] の起点 {hub} が回路図に無い")
        nets[comps[ref]["pins"][pin]["net"]] = hub
    pcb = ps.get("pcb", "auto")
    pcb = str(Path(spec["_sch"]).with_suffix(".kicad_pcb")) if pcb == "auto" else str(resolve(spec, pcb))
    out = {}
    for net, hub in nets.items():
        key = (pcb, net, hub)
        if key not in _PAR_CACHE:
            r = subprocess.run(["kicad-python", str(HERE / "pcbpar.py"), pcb, net, hub, "--json"], capture_output=True, text=True)
            line = next((l for l in r.stdout.splitlines() if l.startswith("{")), None)
            if not line:
                raise SystemExit(f"基板の寄生分を計算できない({net}、起点 {hub}):\n{r.stdout[-500:]}{r.stderr[-800:]}")
            _PAR_CACHE[key] = (hub, json.loads(line))
        hub_, tbl = _PAR_CACHE[key]
        tbl = dict(tbl)
        for pad, L in ps.get("override", {}).items():   # 例 "D7.1" = 1.0 → 配置を変えた場合の L [nH]
            if pad in tbl:
                old = tbl[pad] or (0, 0, 0)
                tbl[pad] = (float(L), old[1] * float(L) / old[0] if old[0] else 0.1, old[2], old[3] * float(L) / old[0] if len(old) > 3 and old[0] else 0)
        out[net] = (hub_, tbl)
    return out


def translate_signal(sig, b):
    """v(ネット名) v(A,B) i(R1) i(V名) → ngspice のベクトル名。@素子[i] は .save が要る"""
    m = re.match(r'^v\((.+)\)$', sig)
    if m:
        parts = [p.strip() for p in m.group(1).split(",")]
        nodes = [b.net(p) for p in parts]
        nodes = [n for n in nodes]
        if len(nodes) == 2:
            return f"v({nodes[0]},{nodes[1]})", []
        return f"v({nodes[0]})", []
    if sig.startswith("@"):   # @dup[id] のように ngspice の書き方で直接指定
        return sig.lower(), [sig.lower()]
    m = re.match(r'^i\((.+)\)$', sig)
    if m:
        name = m.group(1)
        if name in b.comps:
            kind = b.comps[name]["lib"].split(":")[-1]
            letter, key = ("R", "i")
            if kind.startswith("D"):
                letter, key = "D", "id"
            elif kind.startswith("Q_PMOS") or kind.startswith("Q_NMOS"):
                letter, key = "M", "id"
            elif kind.startswith("Q_NPN") or kind.startswith("Q_PNP"):
                letter, key = "Q", "ic"
            elif kind in ("C", "C_Polarized"):
                letter, key = "C", "i"
            elif kind == "L":
                letter, key = "L", "i"
            vec = f"@{letter}{name}[{key}]".lower()
            return vec, [vec]
        for src in b.spec.get("sources", []):   # [[sources]] の名前(V を付けて SPICE の素子名にする)
            if src["name"].lower() == name.lower():   # SPICE は大文字小文字を区別しない
                if src.get("type", "V").upper() == "I":
                    vec = f"@i{src['name']}[current]".lower()
                    return vec, [vec]
                return f"i(v{src['name'].lower()})", []
        if re.match(r'^[A-Za-z][A-Za-z0-9_]*$', name) and name.upper()[0] in "VIEHBRLC":
            return f"i({name})", []   # [extra] に書いた素子(VSENSE など)
        raise SystemExit(f"電流の信号 i({name}) の {name} が分からない: 電源の名前({', '.join(s['name'] for s in b.spec.get('sources', [])) or 'なし'})"
                         f"、部品番号(例 R3)、[extra] に書いた素子(V・I・R・L・C で始まる名前)のどれか")
    return sig, []

# ---------------------------------------------------------------- 測定

def measure(t, y, kind, at=None, window=None):
    import numpy as np
    if window:
        sel = (t >= window[0]) & (t <= window[1])
        t, y = t[sel], y[sel]
    if kind == "at":
        return float(np.interp(at, t, y))
    if kind == "max":
        return float(y.max())
    if kind == "min":
        return float(y.min())
    if kind == "avg":
        return float(_trapz(y, t) / (t[-1] - t[0])) if len(t) > 1 else float(y[0])
    if kind == "final":
        return float(y[-1])
    if kind == "pp":
        return float(y.max() - y.min())
    if kind == "integral":   # 例: 電流を積分して電荷
        return float(_trapz(y, t))
    if kind == "integral_pos":   # 正の向きだけ積分
        return float(_trapz(np.clip(y, 0, None), t))
    if kind == "integral_abs":   # 向きを問わず積分(振動の行き来も数える)
        return float(_trapz(np.abs(y), t))
    if kind == "i2t":        # 電流の 2 乗の積分(ヒューズの溶断の目安 A²s)
        return float(_trapz(y * y, t))
    if kind == "cross":      # 初めて at の値を超えた時刻(立ち上がり時間など)
        idx = np.nonzero(y >= at)[0]
        return float(t[idx[0]]) if len(idx) else float("nan")
    if kind == "absmax":
        return float(np.abs(y).max())
    raise SystemExit(f"測定の種類が不明: {kind}(at/max/min/avg/final/pp/integral/integral_pos/integral_abs/absmax/i2t/cross)")


def fmt(v, unit=""):
    if v is None or (isinstance(v, float) and math.isnan(v)):
        return "—"
    a = abs(v)
    for s, f in (("G", 1e9), ("M", 1e6), ("k", 1e3), ("", 1), ("m", 1e-3), ("µ", 1e-6), ("n", 1e-9), ("p", 1e-12)):
        if a >= f or f == 1e-12:
            return f"{v / f:.4g} {s}{unit}"
    return f"{v:.4g} {unit}"

# ---------------------------------------------------------------- 部品の定格チェック

def stress_on(spec):
    return spec.get("stress", {}).get("enable", True)


def stress_saves(b):
    """定格チェックに要る素子の電流(ノード電圧は .save all で残る)"""
    out = []
    for ref, tok in getattr(b, "emitted", {}).items():
        n = tok[0].lower()
        if n[0] == "d":
            out.append(f"@{n}[id]")
        elif n[0] == "q":
            out += [f"@{n}[ic]", f"@{n}[ib]"]
        elif n[0] == "m":
            out.append(f"@{n}[id]")
    return out


def stress_collect(b, spec):
    """1 回分の結果から、部品ごとの使用値 {(部品, 項目): 値} を出す"""
    import numpy as np
    st = spec.get("stress", {})
    pulse = st.get("kind", "steady") == "pulse"
    an = spec.get("analysis", {})
    x = ngs.vec("time") if "tran" in an else None
    sel = slice(None)   # 平均・実効値を取る区間(尖頭は全区間で見る)
    if x is not None and st.get("window"):
        sel = (x >= st["window"][0]) & (x <= st["window"][1])
    xw = x[sel] if x is not None else None

    def v(node):
        if node == "0":
            return 0.0
        y = ngs.vec(node)
        return 0.0 if y is None else y

    def g(name):
        return ngs.vec(name)

    def w(y):
        return y[sel] if np.ndim(y) else y

    def absmax(y):
        return float(np.max(np.abs(y))) if np.ndim(y) else abs(float(y))

    def avg(y):   # 過渡解析は時間平均(window の区間)、DC 掃引は最悪点、動作点はその値
        if np.ndim(y) == 0:
            return float(y)
        y = w(y)
        if xw is not None and len(xw) > 1:
            return float(_trapz(y, xw) / (xw[-1] - xw[0]))
        return float(np.max(y))

    def rms(y):
        if np.ndim(y) == 0:
            return abs(float(y))
        y = w(y)
        if xw is not None and len(xw) > 1:
            return float(np.sqrt(_trapz(y * y, xw) / (xw[-1] - xw[0])))
        return absmax(y)

    out = {}
    for ref, tok in getattr(b, "emitted", {}).items():
        c = b.comps[ref]
        kind = c["lib"].split(":")[-1]
        name, nodes = tok[0], tok[1:-1]
        L = name[0].upper()
        if L == "R" and not kind.startswith("SW_"):
            va, vb = v(nodes[0]), v(nodes[1])
            dv = va - vb
            if kind == "Fuse":
                i = dv / float(tok[-1])
                out[(ref, "I")] = absmax(i) if pulse else rms(i)
                continue
            p = dv * dv / float(tok[-1])
            out[(ref, "V")] = absmax(dv)
            out[(ref, "P")] = avg(p)
            out[(ref, "Ppk")] = absmax(p)
        elif L == "C":
            dv = v(nodes[0]) - v(nodes[1])
            out[(ref, "V")] = absmax(dv)
            if kind in ("C_Polarized", "CP"):
                out[(ref, "Vrev")] = max(0.0, float(np.max(-dv)) if np.ndim(dv) else -float(dv))
        elif L == "D":
            i = g(f"@{name.lower()}[id]")
            if i is None:
                continue
            p = (v(nodes[0]) - v(nodes[1])) * i
            out[(ref, "P")] = avg(p)
            out[(ref, "Ppk")] = absmax(p)
            if x is not None and len(x) > 1:
                out[(ref, "E")] = float(_trapz(np.clip(p, 0, None), x))
        elif L == "Q":
            vc, vb, ve = v(nodes[0]), v(nodes[1]), v(nodes[2])
            ic, ib = g(f"@{name.lower()}[ic]"), g(f"@{name.lower()}[ib]")
            out[(ref, "Vce")] = absmax(vc - ve)
            out[(ref, "Veb")] = max(0.0, float(np.max(ve - vb)) if np.ndim(ve - vb) else float(ve - vb))
            if ic is not None and ib is not None:
                out[(ref, "Ic")] = absmax(ic)
                out[(ref, "Ib")] = absmax(ib)
                out[(ref, "P")] = abs(avg((vc - ve) * ic + (vb - ve) * ib))
        elif L == "M":
            vd, vg, vs = v(nodes[0]), v(nodes[1]), v(nodes[2])
            i = g(f"@{name.lower()}[id]")
            out[(ref, "Vds")] = absmax(vd - vs)
            out[(ref, "Vgs")] = absmax(vg - vs)
            if i is not None:
                out[(ref, "Id")] = absmax(i)
                out[(ref, "P")] = abs(avg((vd - vs) * i))   # 電流の向きは型(P/N)で変わるので大きさで見る
    return out


def load_ratings():
    return tomllib.loads((MODELS / "ratings.toml").read_text(encoding="utf-8"))


def rating_match(c, rt):
    """定格の表にどう当たったか: "品番" / "品番の一部 <鍵>" / "値" / None"""
    val, mpn = c["value"], part_number(c)
    for key in rt.get("rating", {}):
        if mpn and key == mpn:
            return "品番"
    for key in rt.get("rating", {}):
        if mpn and key in mpn:
            return "品番の一部 " + key
    if not generic_value(c) and val in rt.get("rating", {}):
        return "値"
    return None


def part_rating(c, rt):
    """部品 → 定格の辞書(src つき)。値・MPN・ratings.toml から"""
    val, mpn = c["value"], part_number(c)
    kind = c["lib"].split(":")[-1]
    r = {}
    # 品番が先(値が汎用名 D_TVS のとき、値の行で別の品物の定格になっていた)
    hit = next((d for key, d in rt.get("rating", {}).items() if mpn and key in mpn), None)
    if hit is None and not generic_value(c):
        hit = next((d for key, d in rt.get("rating", {}).items() if key == val), None)
    if hit is not None:
        r = dict(hit)
    if kind == "R":
        for key, d in rt.get("resistor", {}).items():
            if key in mpn:
                r = dict(d)
                break
        if "p" not in r:
            m = re.search(r'(\d+)\s*/\s*(\d+)\s*W', mpn + " " + val) or None
            if m:
                r["p"], r["src"] = int(m.group(1)) / int(m.group(2)), f"MPN・値の「{m.group(0)}」"
            else:
                m = re.search(r'([0-9.]+)\s*W\b', c["fields"].get("Power", ""))
                if m:
                    r["p"], r["src"] = float(m.group(1)), "Power 欄"
    if kind in ("C", "C_Polarized", "CP"):
        m = re.search(r'/\s*([0-9.]+)\s*V', val) or re.search(r'([0-9.]+)\s*V', c["fields"].get("Voltage", ""))
        if m:
            r["v"], r["src"] = float(m.group(1)), "値の耐圧表記" if "/" in val else "Voltage 欄"
    return r


# 項目 → (表示名, 単位, 定格の鍵, 余裕の種類)
STRESS_ITEMS = {
    "P": ("損失(平均)", "W", "p", "power"),
    "Ppk": ("損失(尖頭)", "W", None, None),
    "E": ("パルスのエネルギー", "J", None, None),
    "V": ("電圧", "V", "v", "voltage"),
    "Vrev": ("逆電圧", "V", None, None),
    "I": ("電流", "A", "i", "current"),
    "Vce": ("Vce", "V", "vceo", "voltage"),
    "Veb": ("Veb(逆)", "V", "vebo", "voltage"),
    "Ic": ("Ic(尖頭)", "A", "ic", "current"),
    "Ib": ("Ib(尖頭)", "A", "ib", "current"),
    "Vds": ("Vds", "V", "vds", "voltage"),
    "Vgs": ("Vgs", "V", "vgs", "voltage"),
    "Id": ("Id(尖頭)", "A", "id", "current"),
}


def stress_table(comps, worst, spec):
    """最悪値 {(部品, 項目): 値} → 表の行と、定格が分からない部品"""
    rt = load_ratings()
    der = dict(rt.get("derating", {}))
    der.update(spec.get("stress", {}).get("derating", {}))
    pulse = spec.get("stress", {}).get("kind", "steady") == "pulse"
    rows, unknown = [], {}
    for (ref, item), (val, cond) in worst.items():
        c = comps[ref]
        kind = c["lib"].split(":")[-1]
        r = part_rating(c, rt)
        name, unit, key, dk = STRESS_ITEMS[item]
        # 版ごとの特例
        if item == "Vrev" and val > 0.5:     # 電解コンデンサの逆電圧は 0 が前提
            rows.append((ref, c["value"], "逆電圧", val, "V", None, None, "注意", "電解コンデンサに逆電圧", cond))
            continue
        if item == "E":
            if not (pulse and kind.startswith("D") and "ppk" in r):
                continue
            # 10/1000µs の尖頭電力の定格を熱量に換算(指数減衰 τ=1000µs/ln2 ≈ 1.44ms)。ns の静電気などに使う
            r = dict(r, e=r["ppk"] * 1.44e-3)
            key, dk, name = "e", "pulse", "パルスのエネルギー(定格は 10/1000µs の尖頭電力×1.44ms)"
        if item == "Ppk" and pulse and not kind.startswith("D"):
            continue
        if item == "V" and kind == "R" and pulse and "v_pulse" in r:
            key, dk, name = "v_pulse", "pulse", "電圧(尖頭。定格は 5 秒の過負荷電圧なので目安)"
        if pulse and item in ("P", "Ppk"):
            continue      # 瞬間の現象では損失の平均・尖頭は判定しない(ダイオードはエネルギーで判定)
        if kind.startswith("D") and item == "P" and pulse:
            continue
        if item == "Ppk" and key is None and not pulse:
            continue      # 平時は尖頭の損失は出さない(平均で判定)
        if item in ("Vrev",):
            continue
        rating = r.get(key) if key else None
        if key and rating is None:
            unknown.setdefault(ref, set()).add(name)
        if rating:
            use = val / rating
            lim = r.get("derate", der.get(dk, 1.0))
            judge = "OK" if use <= lim else ("注意" if use <= 1.0 else "NG")
            if key == "v_pulse" and judge == "NG":
                judge = "注意"   # 過負荷電圧は 5 秒の定格。ns のパルスとは直接比べられないので NG にはしない
        else:
            use, judge = None, "—"
        rows.append((ref, c["value"], name, val, unit, rating, use, judge, r.get("src", ""), cond))
    order = {"NG": 0, "注意": 1, "OK": 2, "—": 3}
    rows.sort(key=lambda t: (order[t[7]], -(t[6] or 0), t[0]))
    return rows, unknown, der


def stress_worst(runs):
    """[(条件, {(部品, 項目): 値})] → {(部品, 項目): (最悪値, その条件)}"""
    worst = {}
    for label, st in runs:
        for k, v in st.items():
            if k not in worst or v > worst[k][0]:
                worst[k] = (v, label)
    return worst


def corner_combos(spec):
    """[corners] 名前 = [値, ...] の全組み合わせ。名前が 部品.欄(SW1.state・R9.value)なら部品、ほかは [params]"""
    import itertools
    cs = spec.get("corners", {})
    keys = list(cs)
    combos = [dict(zip(keys, vals)) for vals in itertools.product(*(cs[k] for k in keys))]
    if len(combos) > 4096:
        raise SystemExit(f"[corners] の組み合わせが {len(combos)} 通り(4096 まで)。値を減らして")
    return combos


def with_corner(spec, combo):
    import copy
    s2 = copy.deepcopy(spec)
    for k, v in combo.items():
        if "." in k:
            ref, fld = k.split(".", 1)
            s2.setdefault("parts", {}).setdefault(ref, {})[fld] = v
        else:
            s2.setdefault("params", {})[k] = float(v)
    return s2


def corner_label(combo):
    return "、".join(f"{k}={v:g}" if isinstance(v, (int, float)) else f"{k}={v}" for k, v in combo.items())

# ---------------------------------------------------------------- 実行

def load_spec(path):
    spec = tomllib.loads(Path(path).read_text(encoding="utf-8"))
    spec["_dir"] = Path(path).resolve().parent
    return spec


def resolve(spec, p):
    p = Path(os.path.expanduser(p))
    if p.is_absolute():
        return p
    # 手順のフォルダ → モデルの置き場(KSIM_MODELS。"models/x.lib" は置き場の x.lib)→ この道具
    for cand in (spec["_dir"] / p, MODELS / (p.relative_to("models") if p.parts[:1] == ("models",) else p), HERE / p):
        if cand.exists():
            return cand.resolve()
    raise SystemExit(f"モデルのファイルが見つからない: {p}(探した所: {spec['_dir']}、{MODELS}、{HERE})")


def draw_params(spec, rng):
    """[params] 名前 = { nominal = 値, tol = 0.05 (相対) か abs = 0.1 (絶対) } → 今回の値"""
    vals = {}
    for k, p in spec.get("params", {}).items():
        if not isinstance(p, dict):
            vals[k] = float(p)
            continue
        v = float(p["nominal"])
        if rng is not None:
            if "tol" in p:
                v *= 1 + rng.uniform(-p["tol"], p["tol"])
            elif "abs" in p:
                v += rng.uniform(-p["abs"], p["abs"])
        vals[k] = v
    return vals


def subst(text, b, pv):
    text = re.sub(r'\{net:([^}]+)\}', lambda m: b.net(m.group(1)), text)
    def pr(m):
        if m.group(1) not in pv:
            raise SystemExit(f"[params] に {m.group(1)} が無い")
        return f"{pv[m.group(1)]:.6g}"
    return re.sub(r'\{p:([^}]+)\}', pr, text)


def check_sources(spec):
    """[[sources]] の名前は SPICE の素子名になる: 英字で始まる英数字と _ だけ"""
    for s in spec.get("sources", []):
        name = str(s.get("name", ""))
        if not re.match(r'^[A-Za-z][A-Za-z0-9_]*$', name):
            raise SystemExit(f"[[sources]] の name = {name!r} は使えない: SPICE の素子名になるので、英字で始まる英数字と _ だけ"
                             "(例 BAT、VIN_24)。大文字小文字は区別しない(i(bat) と i(BAT) は同じ)")
        for key in ("plus", "minus", "value"):
            if key not in s:
                raise SystemExit(f"[[sources]] {name}: {key} が無い(plus・minus はネット名 / 部品.端子 / GND、value は \"DC 24\" など)")


def make_circuit(b, spec, rng=None, tolerances=None):
    check_sources(spec)
    lines, skipped = b.build(rng, tolerances)
    pv = draw_params(spec, rng)
    inc = [f".include {resolve(spec, x)}" for x in spec.get("models", {}).get("include", ["models/base.lib"])]
    src = []
    for s in spec.get("sources", []):
        kind = s.get("type", "V").upper()
        src.append(f"{kind}{s['name']} {b.net(s['plus'])} {b.net(s['minus'])} {subst(str(s['value']), b, pv)}")
    extra = []
    for raw in spec.get("extra", {}).get("spice", "").splitlines():
        extra.append(subst(raw, b, pv))
    saves = set()
    for m in spec.get("measure", []):
        _, sv = translate_signal(m["signal"], b)
        saves.update(sv)
    for p in spec.get("plot", []):
        for sg in p["signals"]:
            _, sv = translate_signal(sg, b)
            saves.update(sv)
    if stress_on(spec):
        saves.update(stress_saves(b))
    save = [".save all " + " ".join(sorted(saves))] if saves else []
    # どこにも DC の経路が無いノード(マイコン端子だけにつながる配線など)で解けなくなるのを防ぐ
    opts = ("rshunt=1e12 " + spec.get("analysis", {}).get("options", "")).strip()
    par = b.par_lines()
    if par:
        par = ["* 基板の配線の寄生インダクタンス・抵抗(kicad_pcb から概算。pcbpar.py)"] + par
    cir = "\n".join([f"* {spec.get('title', 'ksim')}", *inc, *lines, *par, *src, *extra, *save,
                     *( [f".options {opts}"] if opts else [] ), ".end"])
    return cir, skipped


def run_once(b, spec, rng=None, tolerances=None, keep=None):
    import numpy as np
    cir, skipped = make_circuit(b, spec, rng, tolerances)
    if keep:
        Path(keep).write_text(cir, encoding="utf-8")
    an = spec.get("analysis", {})
    ngs.load(cir)
    if "tran" in an:
        ngs.lib.ngSpice_Command(f"tran {an['tran']}".encode())
    elif "dc" in an:
        ngs.lib.ngSpice_Command(f"dc {an['dc']}".encode())
    else:
        ngs.lib.ngSpice_Command(b"op")
    # 必要なベクトルを取り出す
    sigs = [m["signal"] for m in spec.get("measure", [])] + [s for p in spec.get("plot", []) for s in p["signals"]]
    vecs = {s: translate_signal(s, b)[0] for s in sigs}
    uniq = sorted(set(vecs.values()))
    f = tempfile.mktemp(suffix=".dat")
    ngs.lib.ngSpice_Command(b"set wr_singlescale")
    ngs.lib.ngSpice_Command(b"set wr_vecnames")
    ngs.OUT.clear()
    ngs.lib.ngSpice_Command(f"wrdata {f} {' '.join(uniq)}".encode())
    errs = [o for o in ngs.OUT if "rror" in o or "not found" in o]
    if not Path(f).exists():
        raise SystemExit("シミュレーション結果が出ない:\n" + "\n".join(ngs.OUT[-20:]))
    data = np.genfromtxt(f, names=True)
    os.unlink(f)
    names = data.dtype.names
    x = data[names[0]]
    arr = {}
    for i, v in enumerate(uniq):
        arr[v] = data[names[i + 1]] if len(names) > i + 1 else None
    res = {}
    for m in spec.get("measure", []):
        y = arr[vecs[m["signal"]]] * m.get("scale", 1.0)
        res[m["name"]] = measure(x, y, m.get("kind", "final"), m.get("at"), m.get("window"))
    if stress_on(spec):
        res["_stress"] = stress_collect(b, spec)
    return x, {s: arr[vecs[s]] for s in sigs}, res, skipped, errs


def time_axis(spec, x):
    """時間軸を見やすい単位に(倍率, 軸ラベル)。"""
    if "tran" not in spec.get("analysis", {}):
        return 1.0, "掃引"
    import numpy as np
    span = float(np.max(x)) if len(x) else 1.0
    for k, u in ((1e-9, "ns"), (1e-6, "µs"), (1e-3, "ms"), (1.0, "s")):
        if span < k * 2000:
            return 1 / k, f"時間 [{u}]"
    return 1.0, "時間 [s]"


def plot(spec, x, sig, out, runs_traces=None):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib import font_manager
    for fp in ("/usr/share/fonts/opentype/ipaexfont-gothic/ipaexg.ttf",):
        if Path(fp).exists():
            font_manager.fontManager.addfont(fp)
            plt.rcParams["font.family"] = font_manager.FontProperties(fname=fp).get_name()
    files = []
    xk, xlabel = time_axis(spec, x)
    for i, p in enumerate(spec.get("plot", [])):
        fig, ax = plt.subplots(figsize=(10, 4.5))
        for s in p["signals"]:
            scale = p.get("scale", 1.0)
            if runs_traces:
                for tx, ty in runs_traces.get(s, [])[:60]:
                    ax.plot(tx * xk, ty * scale, color="0.8", lw=0.6, zorder=1)
            ax.plot(x * xk, sig[s] * scale, lw=1.6, label=s, zorder=2)
        ax.set_title(p.get("title", ""))
        ax.set_xlabel(xlabel)
        ax.set_ylabel(p.get("ylabel", ""))
        ax.grid(alpha=0.3)
        ax.legend(loc="best", fontsize=9)
        fn = out / f"plot{i + 1}.png"
        fig.tight_layout()
        fig.savefig(fn, dpi=110)
        plt.close(fig)
        files.append(fn)
    return files


def with_variant(spec, v):
    """[[variants]] の 1 つを spec に重ねる(parts・params・sources の value を上書き)"""
    import copy
    s2 = copy.deepcopy(spec)
    s2.setdefault("parts", {}).update(v.get("parts", {}))
    if "parasitics" in v:
        s2.setdefault("parasitics", {}).setdefault("override", {}).update(v["parasitics"].get("override", {}))
    for k, val in v.get("params", {}).items():
        s2.setdefault("params", {})[k] = val
    for name, val in v.get("sources", {}).items():
        for src in s2.get("sources", []):
            if src["name"] == name:
                src["value"] = val
    return s2


def run_variants(spec, comps, nets, out):
    rows = []
    for v in spec["variants"]:
        s2 = with_variant(spec, v)
        b2 = Builder(comps, nets, s2)
        x, sig, res, _, _ = run_once(b2, s2, keep=out / f"circuit_{v['name']}.cir")
        res.pop("_stress", None)
        rows.append((v["name"], res, x, sig))
    return rows


def cmd_run(args):
    spec = load_spec(args.spec)
    sch = Path(args.sch).resolve() if args.sch else resolve(spec, spec["schematic"])
    spec["_sch"] = str(sch)
    comps, nets = parse_netlist(export_netlist(sch))
    b = Builder(comps, nets, spec)
    out = Path(args.out or (spec["_dir"] / f"ksim_{Path(args.spec).stem}"))
    out.mkdir(parents=True, exist_ok=True)
    # 計算の前に: どの部品にどのモデルを、どこから(黙って代用しない。汎用・値で当てた・無しは未確認)
    prov = b.provenance_all()
    unconf = {r: p for r, p in prov.items() if not p["ok"]}
    print(f"モデルの出どころ: {len(prov)} 部品(未確認 {len(unconf)})")
    for r, p in prov.items():
        mark = "  " if p["ok"] else "? "
        print(f"  {mark}{r}({comps[r]['value']}): {p['model'] or '—'}  ← {p['how']}"
              + (f" [{p['file']}]" if p["file"] else "") + (f"  未確認: {p['why']}" if not p["ok"] else ""))
    if unconf and getattr(args, "strict", False):
        print(f"--strict: モデルが未確認の部品が {len(unconf)} 個あるので止める(手順の parts か models の map で決める)", file=sys.stderr)
        return 1

    tols = {}
    x, sig, res, skipped, errs = run_once(b, spec, None, tols, keep=out / "circuit.cir")
    stress_runs = [("公称", res.pop("_stress"))] if "_stress" in res else []
    unit = {m["name"]: m.get("unit", "") for m in spec.get("measure", [])}
    lim = {m["name"]: (m.get("min"), m.get("max")) for m in spec.get("measure", [])}

    runs = args.runs if args.runs is not None else spec.get("montecarlo", {}).get("runs", 0)
    mc = []
    traces = {}
    if runs:
        rng = random.Random(spec.get("montecarlo", {}).get("seed", 1))
        for k in range(runs):
            tx, ts, r, _, _ = run_once(b, spec, rng)
            if "_stress" in r:
                stress_runs.append((f"ばらつき {k + 1} 回目", r.pop("_stress")))
            mc.append(r)
            for s, y in ts.items():
                traces.setdefault(s, []).append((tx, y))
    crows = []   # 条件の総当たり: (条件, 測定値)
    if spec.get("corners"):
        combos = corner_combos(spec)
        print(f"条件の総当たり: {len(combos)} 通り", flush=True)
        for combo in combos:
            s2 = with_corner(spec, combo)
            _, _, r, _, _ = run_once(Builder(comps, nets, s2), s2)
            if "_stress" in r:
                stress_runs.append((corner_label(combo), r.pop("_stress")))
            crows.append((corner_label(combo), r))
    plots = plot(spec, x, sig, out, traces if runs else None)
    vrows = run_variants(spec, comps, nets, out) if spec.get("variants") else []
    if vrows:   # 比べる版の波形を 1 枚に重ねる
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        for i, p in enumerate(spec.get("plot", [])):
            fig, ax = plt.subplots(figsize=(10, 4.5))
            xk, xlabel = time_axis(spec, vrows[0][2])
            for name, _, vx, vs in vrows:
                for sg in p["signals"]:
                    ax.plot(vx * xk, vs[sg] * p.get("scale", 1.0), lw=1.4, label=f"{name}: {sg}")
            ax.set_title(p.get("title", "") + "(版の比較)")
            ax.set_xlabel(xlabel)
            ax.set_ylabel(p.get("ylabel", ""))
            ax.grid(alpha=0.3)
            ax.legend(fontsize=8)
            fn = out / f"compare{i + 1}.png"
            fig.tight_layout()
            fig.savefig(fn, dpi=110)
            plt.close(fig)
            plots.append(fn)

    with open(out / "測定値.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["run"] + list(res))
        w.writerow(["公称"] + [res[k] for k in res])
        for i, r in enumerate(mc):
            w.writerow([i + 1] + [r[k] for k in res])

    md = [f"# {spec.get('title', 'シミュレーション')}", "",
          f"- 回路図: `{sch}`", f"- 解析: {describe_analysis(spec.get('analysis', {}))}",
          f"- モデル: {', '.join(str(resolve(spec, x)) for x in spec.get('models', {}).get('include', ['models/base.lib']))}"
          "(データシートの値に当てはめたもの。根拠は models/base.lib の注記)", ""]
    if spec.get("note"):
        md += [spec["note"], ""]
    md += ["## モデルの出どころ", "",
           f"{len(prov)} 部品のうち **未確認 {len(unconf)}**(汎用のモデル・値で当てた・モデル無し・状態を決めていないスイッチ)。"
           "未確認の部品の結果は、別の品物として計算されているかもしれない。", "",
           "| 部品 | 値 | モデル | 出どころ | 未確認の理由 |", "|---|---|---|---|---|"]
    for r, p in prov.items():
        md.append(f"| {r} | {comps[r]['value']} | {p['model'] or '—'} | {p['how']}{' [' + p['file'] + ']' if p['file'] else ''} | "
                  f"{'**' + p['why'] + '**' if not p['ok'] else ''} |")
    md += [""]
    md += ["## 測定値", "", "| 項目 | 公称 | " + ("最小 | 最大 | 平均 | " if mc else "") + "判定 |",
           "|---|---:|" + ("---:|---:|---:|" if mc else "") + "---|"]
    allok = True
    for k, v in res.items():
        lo, hi = lim[k]
        vals = [v] + [r[k] for r in mc]
        mn, mx = min(vals), max(vals)
        ok = (lo is None or mn >= lo) and (hi is None or mx <= hi)
        judge = "—" if lo is None and hi is None else ("OK" if ok else "**NG**")
        if lo is not None or hi is not None:
            rng_txt = f"(基準 {fmt(lo, unit[k]) if lo is not None else ''}〜{fmt(hi, unit[k]) if hi is not None else ''})"
            judge += " " + rng_txt
        allok &= ok
        row = f"| {k} | {fmt(v, unit[k])} | "
        if mc:
            avg = sum(vals) / len(vals)
            row += f"{fmt(mn, unit[k])} | {fmt(mx, unit[k])} | {fmt(avg, unit[k])} | "
        md.append(row + judge + " |")
    if mc:
        tl = sorted(set(f"{v * 100:g}%" for v in tols.values()))
        pl = [f"{k} {v['nominal']:g}" + (f"±{v['tol'] * 100:g}%" if 'tol' in v else f"±{v['abs']:g}" if 'abs' in v else "")
              for k, v in spec.get("params", {}).items() if isinstance(v, dict)]
        if pl:
            md += ["", "基板の外の値のばらつき: " + "、".join(pl)]
        md += ["", f"ばらつき: {runs} 回(一様分布。許容差は回路図の Tolerance 欄、無い抵抗 ±5%・コンデンサ/コイル ±20%。"
               f"今回の許容差: {', '.join(tl)})。グラフの灰色がばらつきの各回。"]
    if vrows:
        md += ["", "## 版の比較", "", "| 版 | " + " | ".join(res) + " |", "|---|" + "---:|" * len(res)]
        for name, r, _, _ in vrows:
            desc = next((v.get("desc", "") for v in spec["variants"] if v["name"] == name), "")
            md.append(f"| {name}{'(' + desc + ')' if desc else ''} | " + " | ".join(fmt(r[k], unit[k]) for k in res) + " |")
    if crows:
        md += ["", "## 条件の総当たり", "",
               f"{len(crows)} 通り: " + "、".join(f"{k} = {v}" for k, v in spec["corners"].items()) + "(全部の組み合わせ)", "",
               "| 項目 | 最小 | その条件 | 最大 | その条件 | 判定 |", "|---|---:|---|---:|---|---|"]
        for k in res:
            vals = [(r[k], lab) for lab, r in crows]
            lo_v, lo_c = min(vals)
            hi_v, hi_c = max(vals)
            lo, hi = lim[k]
            ok = (lo is None or lo_v >= lo) and (hi is None or hi_v <= hi)
            judge = "—" if lo is None and hi is None else ("OK" if ok else "**NG**")
            allok &= ok
            md.append(f"| {k} | {fmt(lo_v, unit[k])} | {lo_c} | {fmt(hi_v, unit[k])} | {hi_c} | {judge} |")
        with open(out / "条件ごとの測定値.csv", "w", newline="", encoding="utf-8") as f:
            w = csv.writer(f)
            w.writerow(["条件"] + list(res))
            for lab, r in crows:
                w.writerow([lab] + [r[k] for k in res])
    srows, unknown, partial, rated = [], {}, {}, set()
    if stress_runs:
        srows, unknown, der = stress_table(comps, stress_worst(stress_runs), spec)
        rt_all = load_ratings()
        for ref in {t[0] for t in srows}:
            how = rating_match(comps[ref], rt_all)
            if how and how.startswith("品番の一部"):
                partial[ref] = how
        rated = {t[0] for t in srows if t[5]} - set(partial)
        kind = spec.get("stress", {}).get("kind", "steady")
        md += ["", "## 部品の定格チェック", "",
               f"使用値は{'公称とばらつき ' + str(runs) + ' 回の' if runs else '公称の'}最悪値。"
               + ("瞬間の現象(pulse): 電圧と尖頭の値で判定し、平均の損失は判定しない。" if kind == "pulse" else
                  "損失は解析時間の平均、電圧・電流は尖頭。")
               + f"余裕の目安: 損失 {der.get('power', 1) * 100:g}%、電圧 {der.get('voltage', 1) * 100:g}%、"
               f"電流 {der.get('current', 1) * 100:g}%、パルス {der.get('pulse', 1) * 100:g}% 以下で OK、100% 以下は注意、超えたら NG。",
               "", "| 部品 | 値 | 項目 | 使用値 | 定格 | 使用率 | 判定 | 出典 |" + (" 最悪の条件 |" if crows else ""),
               "|---|---|---|---:|---:|---:|---|---|" + ("---|" if crows else "")]
        srcs, small = [], 0
        for ref, val, name, u, unit_, rating, use, judge, src, cond in srows:
            if judge == "OK" and use < 0.01 or judge == "—" and u < 1e-3:
                small += 1   # 使用率 1% 未満(定格が無いものは 1mA/1mW/1mV 未満)は数だけ
                continue
            if src and src not in srcs:
                srcs.append(src)
            note = f"[{srcs.index(src) + 1}]" if src else ""
            md.append(f"| {ref} | {val} | {name} | {fmt(u, unit_)} | {fmt(rating, unit_) if rating else '—'} | "
                      f"{f'{use * 100:.0f}%' if use is not None else '—'} | {'**' + judge + '**' if judge in ('NG', '注意') else judge} | {note} |" + (f" {cond} |" if crows else ""))
        if small:
            md += ["", f"ほかに {small} 項目は使用率 1% 未満(全項目は `定格チェック.csv`)。"]
        md += [""] + [f"[{i + 1}] {t}  " for i, t in enumerate(srcs)]
        if unknown:
            md += ["", f"**未判定 {len(unknown)} 部品**(定格が分からず判定できなかった。NG 0 でもこれらは確かめていない。"
                   "models/ratings.toml か回路図の欄(MPN・品番)に足すと判定する): "
                   + "、".join(f"{r}({comps[r]['value']}: {'・'.join(sorted(n))})" for r, n in sorted(unknown.items()))]
        allok &= not any(t[7] == "NG" for t in srows)
        if partial:
            md += ["", "**品番の一部で定格の表に当てた部品**(別の品物の定格かもしれない): "
                   + "、".join(f"{r}({how})" for r, how in sorted(partial.items()))]
        if not rated:   # 1 部品も確かめていないのに「NG 0」で合格に見えた
            allok = False
            md += ["", "**定格を確かめられた部品が 0 個**: 定格の判定はできていない(合格ではない)"]
        with open(out / "定格チェック.csv", "w", newline="", encoding="utf-8") as f:
            w = csv.writer(f)
            w.writerow(["部品", "値", "項目", "使用値", "単位", "定格", "使用率", "判定", "出典", "最悪の条件"])
            for t in srows:
                w.writerow(t)
    notes = model_notes(b)
    if notes:
        md += ["", "## モデルの前提(確かめてほしいもの)", ""] + [f"- {t}" for t in notes]
    md += ["", "## 波形", ""] + [f"![]({p.name})" for p in plots]
    md += ["", "## シミュレーションから外した部品", ""]
    for r, why in sorted(skipped.items()):
        md.append(f"- {r}({comps[r]['value']}): {why}")
    if errs:
        md += ["", "## ngspice のメッセージ", ""] + [f"- {e.strip()}" for e in errs[:20]]
    md += ["", f"実際に流した回路: `circuit.cir`。測定値の全回分: `測定値.csv`"]
    (out / "結果.md").write_text("\n".join(md) + "\n", encoding="utf-8")

    print(f"{spec.get('title', '')}")
    miss = {r: w for r, w in skipped.items() if not w.startswith(("コネクタ", "除外"))}
    if miss:
        print(f"  ！ モデルが無く外した部品 {len(miss)} 個(結果に影響しうる): "
              + "、".join(f"{r}({comps[r]['value']})" for r in sorted(miss)))
    for k, v in res.items():
        extra = ""
        if mc:
            vals = [v] + [r[k] for r in mc]
            extra = f"  (ばらつき {runs} 回: {fmt(min(vals), unit[k])} 〜 {fmt(max(vals), unit[k])})"
        print(f"  {k}: {fmt(v, unit[k])}{extra}")
    if crows:
        print(f"  条件の総当たり {len(crows)} 通り:")
        for k in res:
            vals = [(r[k], lab) for lab, r in crows]
            (lo_v, lo_c), (hi_v, hi_c) = min(vals), max(vals)
            print(f"    {k}: {fmt(lo_v, unit[k])}({lo_c}) 〜 {fmt(hi_v, unit[k])}({hi_c})")
    for name, r, _, _ in vrows:
        print(f"  [{name}] " + " / ".join(f"{k} {fmt(r[k], unit[k])}" for k in r))
    if srows:
        bad = [t for t in srows if t[7] in ("NG", "注意")]
        print(f"  部品の定格: {len(srows)} 項目を確認、NG {sum(t[7] == 'NG' for t in srows)}・注意 {sum(t[7] == '注意' for t in srows)}"
              f"・未判定 {len(unknown)} 部品")
        for ref, val, name, u, unit_, rating, use, judge, _, cond in bad[:10]:
            print(f"    {judge} {ref}({val}) {name} {fmt(u, unit_)} / 定格 {fmt(rating, unit_)} = {use * 100:.0f}%(最悪: {cond})")
        if unknown:
            print("    定格未確認(表に無い): " + "、".join(f"{r}({comps[r]['value']})" for r in sorted(unknown)))
        if partial:
            print("    品番の一部で当てた: " + "、".join(f"{r}({h})" for r, h in sorted(partial.items())))
        if not rated:
            print("    定格を確かめられた部品が 0 個: 定格の判定はできていない(合格ではない)")
    for t in model_notes(b):
        print(f"  注意: {t}")
    if unconf:
        print(f"  未確認のモデル {len(unconf)} 部品: " + "、".join(sorted(unconf)) + "(結果.md の「モデルの出どころ」)")
    strict_fail = getattr(args, "strict", False) and (unconf or unknown or partial)
    if strict_fail:
        print("--strict: 未確認(モデル・定格)があるので NG にする", file=sys.stderr)
    pending = [f"モデル未確認 {len(unconf)} 部品"] if unconf else []
    pending += [f"定格未確認 {len(unknown)} 部品"] if unknown else []
    print(f"判定: {'OK' if allok and not strict_fail else 'NG あり(または未判定)'}"
          + (f"(確かめた範囲で。{'・'.join(pending)})" if pending and allok and not strict_fail else
             (f"({'・'.join(pending)})" if pending else "")) + f"  → {out / '結果.md'}")
    return 0 if allok and not strict_fail else 1


def model_notes(b):
    """モデルの前提で、結果を信じる前に確かめてほしいもの"""
    out = []
    if b.generic:
        out.append("値が記号の名前のまま(品番からもモデルが決まらない): " + "、".join(
            f"{r}({v}{'、品番 ' + pn if pn else '、品番なし'})" for r, (v, pn) in sorted(b.generic.items()))
            + "。対応表の汎用のモデルで計算した(別の品物の値かもしれない)。回路図の値か MPN 欄に品番を")
    if b.no_dcr:
        out.append("巻線の抵抗(DCR)が分からないコイル: " + "、".join(sorted(b.no_dcr))
                   + "。抵抗 0 の L は C と鳴き続ける(DCR 欄か値に「0.12Ω」を)")
    if b.assumed_esr:
        out.append("ESR を汎用品の代表値(耐圧ごとの tanδ)にした電解: " + "、".join(sorted(b.assumed_esr))
                   + "(品番を models/ratings.toml の [capacitor] に足すとその値)")
    return out


def describe_analysis(an):
    if "tran" in an:
        return f"過渡解析(tran {an['tran']})"
    if "dc" in an:
        return f"DC 掃引(dc {an['dc']})"
    return "動作点(op)"


def cmd_nets(args):
    comps, nets = parse_netlist(export_netlist(args.sch))
    for n in sorted(nets):
        print(f"{n:28s} " + " ".join(f"{r}.{p}" + (f"({f})" if f else "") for r, p, f in nets[n]))


def cmd_parts(args):
    spec = load_spec(args.spec) if args.spec else {"_dir": Path.cwd()}
    sch = args.sch or resolve(spec, spec["schematic"])
    comps, nets = parse_netlist(export_netlist(sch))
    b = Builder(comps, nets, spec)
    for ref in sorted(comps):
        line, why = b.element(comps[ref])
        p = b.provenance(comps[ref])
        tag = "" if p is None else (f"  [{p['how']}{' ' + p['file'] if p['file'] else ''}]" + ("" if p["ok"] else f" 未確認: {p['why']}"))
        print(f"{ref:10s} {comps[ref]['value'][:22]:22s} " + (f"→ {line}" if line else f"× {why}") + tag)


def main():
    ap = argparse.ArgumentParser(description="KiCad の回路図から ngspice シミュレーション")
    sp = ap.add_subparsers(dest="cmd", required=True)
    r = sp.add_parser("run"); r.add_argument("spec"); r.add_argument("--out"); r.add_argument("--runs", type=int)
    r.add_argument("--sch", help="手順ファイルの schematic の代わりにこの回路図で回す(版を比べるとき)")
    r.add_argument("--strict", action="store_true", help="モデルや定格が未確認の部品があれば NG(終了コード 1)。計算の前にモデルが未確認なら止める")
    n = sp.add_parser("nets"); n.add_argument("sch")
    p = sp.add_parser("parts"); p.add_argument("--spec"); p.add_argument("--sch")
    a = ap.parse_args()
    sys.exit({"run": cmd_run, "nets": cmd_nets, "parts": cmd_parts}[a.cmd](a) or 0)


if __name__ == "__main__":
    main()
