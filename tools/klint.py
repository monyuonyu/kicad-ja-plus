#!/usr/bin/env python3
"""
kicad-local lint: 回路図のつながりから「よくある抜け」を点検する(ローカル道具、2026-09-25)

  kicad-local lint <回路図.kicad_sch> [--md 出力.md]

ERC(電気的な規則)では拾えない、設計の定石からの外れを拾う。判定ではなく「要確認」の一覧。
  外部の線  : マイコン端子が外に出る線(コネクタ・外付けスイッチ)に直結している / 外に出る線に TVS が無い
  トランジスタ: ベースがマイコン端子に直結(ベース抵抗なし) / ベースに引き下げ抵抗が無い
  MOSFET    : ゲートとソースの間に抵抗が無い(ゲートが浮く)
  向き      : TVS・ツェナーのアノードが GND に無い / 電解コンデンサの - 側が GND に無く + 側が GND
  電源      : 電源モジュール・レギュレータの入力と出力にコンデンサが無い
  つながり  : 端子が 1 つしか無いネット(つなぎ忘れ)
"""
import argparse, re, sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "ksim"))
import ksim  # noqa: E402

GND_NAMES = {"GND", "/GND", "GNDD", "GNDA", "/GNDD", "/GNDA", "0", "VSS", "/VSS"}
TVS_WORDS = ("TVS", "P4KE", "P6KE", "SA", "SMAJ", "SMBJ", "SMCJ", "1N52", "1N47", "BZX", "Zener", "ESD", "PESD", "DF2S", "TPD")


# 外付けコンデンサが要らないと明記された電源モジュール(値・MPN の一部 → 出典)
NO_EXT_CAPS = {
    "TSR 2": "Traco TSR 2 データシート「no requirement of external capacitors」(入力を機械式スイッチで入り切りするときだけ入力に 22µF/50V 推奨)",
}


def kind(c):
    return c["lib"].split(":")[-1]


def is_mcu(c):
    return c["lib"].split(":")[0] in ("MCU_Module",) or c["lib"].startswith("MCU_")


def is_external(c):
    """外に線が出る部品(コネクタ、ケーブルの先の外付けスイッチ)"""
    lib = c["lib"].split(":")[0]
    fp = c.get("footprint", "")
    return lib.startswith("Connector") or fp.startswith("Connector") or fp.startswith("TerminalBlock")


def is_gnd(net):
    return net in GND_NAMES or net.upper().endswith("GND")


COMPS = {}


def is_power(net, nodes):
    """電源のネット(+3V3・+12V・GND などの名前か、電源端子(power_in/out)がある)"""
    n = net.lstrip("/")
    if re.match(r'^[+-]?\d+V\d*|^\+|^VCC|^VDD|^VIN|^VBAT', n, re.I) or is_gnd(net):
        return True
    return any(COMPS.get(r, {}).get("pins", {}).get(p, {}).get("type", "").startswith("power") for r, p, _ in nodes)


def is_tvs(c):
    k = kind(c)
    return k.startswith("D") and (any(w.lower() in (c["value"] + " " + c["fields"].get("MPN", "")).lower() for w in TVS_WORDS)
                                 or "TVS" in k or "Zener" in k)


def lint(sch):
    comps, nets = ksim.parse_netlist(ksim.export_netlist(sch))
    COMPS.clear()
    COMPS.update(comps)
    pinnet = {(r, p): n for n, nodes in nets.items() for r, p, _ in nodes}
    out = []

    def add(cat, where, msg):
        out.append((cat, where, msg))

    def others(net, exclude=()):
        return [(r, p, f) for r, p, f in nets[net] if r not in exclude]

    # ---- 外部の線
    for net, nodes in nets.items():
        if is_power(net, nodes) or net.startswith("unconnected"):
            continue
        ext = [f"{r}.{p}" for r, p, _ in nodes if r in comps and is_external(comps[r])]
        if not ext:
            continue
        mcu = [f"{r}.{p}" + (f"({f})" if f else "") for r, p, f in nodes if r in comps and is_mcu(comps[r])
               and not comps[r]["pins"][p].get("type", "").startswith("power")]
        if mcu:
            add("外部の線", net, f"マイコン端子 {', '.join(mcu)} が外に出る線({', '.join(ext)})に直結。直列抵抗(1kΩ 程度)で静電気・誤配線の電流を制限したい")
        tvs = [r for r, p, _ in nodes if r in comps and is_tvs(comps[r])]
        if not tvs and any(r in comps and is_mcu(comps[r]) and not comps[r]["pins"][p].get("type", "").startswith("power")
                           for r, p, _ in self_or_neighbors(net, nets, comps)):
            add("外部の線", net, f"外に出る線({', '.join(ext)})に TVS・ツェナーが無い(マイコンまで保護なし)")

    # ---- トランジスタ・MOSFET
    for ref, c in comps.items():
        k = kind(c)
        pins = {d["fn"]: d["net"] for d in c["pins"].values()}
        if k.startswith("Q_NPN") or k.startswith("Q_PNP"):
            b, e = pins.get("B"), pins.get("E")
            if not b:
                continue
            if any(is_mcu(comps[r]) and not comps[r]["pins"][p].get("type", "").startswith("power") for r, p, _ in nets[b] if r in comps):
                add("トランジスタ", ref, f"ベース({b})がマイコン端子に直結。ベース抵抗が要る")
            def pulldown(r):
                other = {d["net"] for d in comps[r]["pins"].values()} - {b}
                return kind(comps[r]) == "R" and other and all(x == e or is_gnd(x) for x in other)
            if not any(pulldown(r) for r, _, _ in nets[b] if r in comps and r != ref):
                add("トランジスタ", ref, f"ベースに引き下げ抵抗(エミッタか GND へ)が無い(マイコンの起動中・リセット中にベースが浮いて ON になりうる)")
        if k.startswith("Q_PMOS") or k.startswith("Q_NMOS"):
            g, s_ = pins.get("G"), pins.get("S")
            if g and not any(kind(comps[r]) == "R" and {d["net"] for d in comps[r]["pins"].values()} == {g, s_}
                             for r, _, _ in nets[g] if r in comps and r != ref):
                add("MOSFET", ref, "ゲートとソースの間に抵抗が無い(ゲートが浮くと半端に ON)")

    # ---- 向き
    for ref, c in comps.items():
        k = kind(c)
        pins = {d["fn"] or n: d["net"] for n, d in c["pins"].items()}
        if is_tvs(c):
            a, kk = pins.get("A"), pins.get("K")
            if kk and is_gnd(kk):
                add("向き", ref, f"{c['value']} のカソードが GND({kk})。単方向の TVS・ツェナーならアノードを GND に")
            elif a and not is_gnd(a) and not a.startswith("Net-("):
                add("向き", ref, f"{c['value']} のアノードが GND でない({a})。意図を確認")
        if k in ("C_Polarized", "CP", "C_Polarized_Small"):
            n1 = c["pins"].get("1", {}).get("net")
            n2 = c["pins"].get("2", {}).get("net")
            if n1 and is_gnd(n1) and n2 and not is_gnd(n2):
                add("向き", ref, f"電解コンデンサの + 側(1 番)が GND、- 側が {n2}。負電源でなければ逆")

    # ---- 電源モジュール・レギュレータの入出力のコンデンサ
    for ref, c in comps.items():
        if not (c["lib"].startswith("Regulator") or c["lib"].startswith("Converter")):
            continue
        if any(k in c["value"] + " " + c["fields"].get("MPN", "") for k in NO_EXT_CAPS):
            continue   # 外付けコンデンサ不要のモジュール
        for n, d in c["pins"].items():
            fn = (d["fn"] or "").upper()
            if fn in ("IN", "VIN", "OUT", "VOUT"):
                caps = [r for r, _, _ in nets[d["net"]] if r in comps and kind(comps[r]).startswith("C")
                        and any(is_gnd(x["net"]) for x in comps[r]["pins"].values())]
                if not caps:
                    add("電源", ref, f"{fn} 端子({d['net']})に GND へのコンデンサが無い(データシートの推奨を確認)")

    # ---- つながり
    for net, nodes in nets.items():
        if len(nodes) == 1 and not net.startswith("unconnected"):
            r, p, f = nodes[0]
            add("つながり", net, f"端子が 1 つだけ({r}.{p})。つなぎ忘れか、ラベルの綴り違い")
    return out, comps


def self_or_neighbors(net, nets, comps):
    """そのネットと、抵抗 1 本でつながった先のネットの端子(TVS がマイコン側にあってもよいので)"""
    res = list(nets[net])
    for r, p, _ in nets[net]:
        c = comps.get(r)
        if c and kind(c) == "R":
            for q, d in c["pins"].items():
                if q != p:
                    res += nets[d["net"]]
    return res


def main():
    ap = argparse.ArgumentParser(description="回路図のつながりから、よくある抜けを点検する")
    ap.add_argument("sch")
    ap.add_argument("--md")
    a = ap.parse_args()
    items, _ = lint(a.sch)
    lines = [f"- [{cat}] **{where}**: {msg}" for cat, where, msg in items]
    print(f"点検: 要確認 {len(items)} 件")
    print("\n".join(lines) or "なし")
    if a.md:
        Path(a.md).write_text("# 回路図の点検\n\n" + ("\n".join(lines) or "なし") + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
