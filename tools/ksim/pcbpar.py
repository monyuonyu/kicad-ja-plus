#!/usr/bin/env python3
"""
pcbpar: 基板の配線から、ネットの中の「起点の端子 → 各端子」の寄生インダクタンスと抵抗を概算する(ksim 用)

  kicad-python pcbpar.py <基板.kicad_pcb> <ネット名> <起点 部品.端子> [--json]

計算のしかた(概算。相互インダクタンスは無視):
  配線   : 長方形断面の導体の自己インダクタンス L = 0.2·l·(ln(2l/(w+t)) + 0.5 + 0.2235·(w+t)/l) [nH, mm]
           抵抗 R = ρ·l/(w·t)(銅 ρ = 1.72e-5 Ω·mm)
  ビア   : L = 0.2·h·(ln(4h/d) + 1) [nH](h = 板厚、d = 穴径)
  ベタ   : 同じネットのベタの上の 2 点は、幅 = 距離の板とみなす(広がって流れる分を大まかに入れる)
           L = 0.2·d·(ln 2 + 0.5 + 0.2235) ≈ 0.28 nH/mm × 距離
  経路   : 起点から各端子まで、インダクタンスが最小の経路(Dijkstra)
  容量   : 反対の層に GND ベタがある配線は、マイクロストリップの式で 1mm あたりの容量を出す(同じ層の横のベタは
           入れない → 容量は少なめ=尖頭電圧は高め)。ネット全体の容量を、起点からの経路長の比で各端子に配る
"""
import heapq, json, math, sys
import pcbnew

RHO = 1.72e-5      # 銅 Ω·mm
MM = pcbnew.ToMM


def l_bar(l, w, t):
    if l <= 1e-6:
        return 0.0
    v = 0.2 * l * (math.log(2 * l / (w + t)) + 0.5 + 0.2235 * (w + t) / l)
    return max(v, 0.0)


def l_via(h, d):
    return 0.2 * h * (math.log(4 * h / d) + 1)


def c_microstrip(w, h, er, t=0.035):
    """1mm あたりの容量 [pF](Hammerstad の近似)"""
    eeff = (er + 1) / 2 + (er - 1) / 2 / math.sqrt(1 + 12 * h / w)
    z0 = 87 / math.sqrt(er + 1.41) * math.log(5.98 * h / (0.8 * w + t)) if w / h < 1 else \
        120 * math.pi / math.sqrt(eeff) / (w / h + 1.393 + 0.667 * math.log(w / h + 1.444))
    return math.sqrt(eeff) / (3e11 * z0) * 1e12   # F/mm → pF/mm


def l_plane(d):
    return 0.2 * d * (math.log(2) + 0.5 + 0.2235) if d > 0 else 0.0


def analyze(board_path, net, hub):
    b = pcbnew.LoadBoard(board_path)
    h = MM(b.GetDesignSettings().GetBoardThickness())
    cu_t = 0.035
    G = {}   # 節点 → [(相手, L nH, R mΩ, 長さ mm)]

    def edge(a, c, L, R, ln):
        G.setdefault(a, []).append((c, L, R, ln))
        G.setdefault(c, []).append((a, L, R, ln))

    pads = [p for p in b.GetPads() if p.GetNetname() == net]
    if not pads:
        raise SystemExit(f"ネット {net} の端子が基板に無い")
    names = [f"{p.GetParentFootprint().GetReference()}.{p.GetNumber()}" for p in pads]
    pads = list(zip(names, pads))
    if hub not in names:
        raise SystemExit(f"起点 {hub} がネット {net} に無い(端子: {', '.join(sorted(names))})")
    cu_layers = [l for l in b.GetEnabledLayers().CuStack()]

    def pad_on(p, layer):
        return p.IsOnLayer(layer)

    tracks = [t for t in b.GetTracks() if t.GetNetname() == net]
    vias = [t for t in tracks if t.Type() == pcbnew.PCB_VIA_T]
    segs = [t for t in tracks if t.Type() == pcbnew.PCB_TRACE_T]

    def node_pt(pt, layer):
        return ("T", round(MM(pt.x), 3), round(MM(pt.y), 3), layer)

    # 配線
    for s in segs:
        a, c = s.GetStart(), s.GetEnd()
        l = MM((c - a).EuclideanNorm())
        w = MM(s.GetWidth())
        edge(node_pt(a, s.GetLayer()), node_pt(c, s.GetLayer()), l_bar(l, w, cu_t), RHO * l / (w * cu_t) * 1e3, l)
    # 端子と配線の端
    for s in segs:
        for pt in (s.GetStart(), s.GetEnd()):
            for nm, p in pads:
                if pad_on(p, s.GetLayer()) and p.HitTest(pt):
                    edge(("P", nm), node_pt(pt, s.GetLayer()), 0.0, 0.0, 0.0)
    # ビア
    for v in vias:
        pos = v.GetPosition()
        d = MM(v.GetDrillValue())
        ls = [l for l in cu_layers if v.IsOnLayer(l)]
        vn = {l: ("V", round(MM(pos.x), 3), round(MM(pos.y), 3), l) for l in ls}
        for i in range(len(ls) - 1):
            edge(vn[ls[i]], vn[ls[i + 1]], l_via(h, d), 0.0, h)
        for s in segs:
            for pt in (s.GetStart(), s.GetEnd()):
                if s.GetLayer() in vn and MM((pt - pos).EuclideanNorm()) <= MM(v.GetWidth()) / 2 + 1e-3:
                    edge(vn[s.GetLayer()], node_pt(pt, s.GetLayer()), 0.0, 0.0, 0.0)
    # 端子(スルーホール)の層間: 同じ端子の各層は 1 つの節点(穴の中はつながっている)とみなす → 端子の節点は層なし
    # ベタ
    for z in b.Zones():
        if z.GetNetname() != net or not z.IsFilled():
            continue
        for layer in z.GetLayerSet().Seq():
            poly = z.GetFilledPolysList(layer)
            members = []
            # 端子の周りはベタが抜けている(熱の逃げ道でつながる)ので、端子の縁から少し外までにベタが触れていればつながりとみなす
            for nm, p in pads:
                reach = max(p.GetSize().x, p.GetSize().y) // 2 + pcbnew.FromMM(0.6)
                if pad_on(p, layer) and poly.Collide(p.GetPosition(), reach):
                    members.append((("P", nm), p.GetPosition()))
            for v in vias:
                if v.IsOnLayer(layer) and poly.Collide(v.GetPosition(), v.GetWidth() // 2 + pcbnew.FromMM(0.6)):
                    pos = v.GetPosition()
                    members.append((("V", round(MM(pos.x), 3), round(MM(pos.y), 3), layer), pos))
            for i in range(len(members)):
                for j in range(i + 1, len(members)):
                    d = MM((members[i][1] - members[j][1]).EuclideanNorm())
                    edge(members[i][0], members[j][0], l_plane(d), 0.0, d)

    # 配線の容量(反対の層に GND ベタがあるところ)
    er, hd = 4.5, h - 2 * cu_t
    try:
        for it in b.GetDesignSettings().GetStackupDescriptor().GetList():
            if it.GetType() == pcbnew.BS_ITEM_TYPE_DIELECTRIC:
                er, hd = it.GetEpsilonR(), MM(it.GetThickness())
                break
    except Exception:
        pass
    gnd_z = [z for z in b.Zones() if z.GetNetname() in ("GND", "/GND") and z.IsFilled()]
    c_total = 0.0
    for sg in segs:
        mid = (sg.GetStart() + sg.GetEnd()) / 2
        other = [l for l in (pcbnew.F_Cu, pcbnew.B_Cu) if l != sg.GetLayer()]
        if net not in ("GND", "/GND") and any(z.IsOnLayer(l) and z.GetFilledPolysList(l).Contains(mid) for z in gnd_z for l in other):
            c_total += c_microstrip(MM(sg.GetWidth()), hd, er) * MM((sg.GetEnd() - sg.GetStart()).EuclideanNorm())

    # 起点から最短(インダクタンス最小)
    start = ("P", hub)
    dist = {start: (0.0, 0.0, 0.0)}
    q = [(0.0, 0.0, 0.0, start)]
    while q:
        L, R, ln, u = heapq.heappop(q)
        if dist.get(u, (1e18,))[0] < L - 1e-12:
            continue
        for v2, dl, dr, dln in G.get(u, []):
            nl = L + dl
            if nl < dist.get(v2, (1e18,))[0] - 1e-12:
                dist[v2] = (nl, R + dr, ln + dln)
                heapq.heappush(q, (nl, R + dr, ln + dln, v2))
    out = {}
    lens = {nm: (dist.get(("P", nm)) or (0, 0, 0))[2] for nm, p in pads}
    tot = sum(lens.values()) or 1.0
    for nm, p in pads:
        d = dist.get(("P", nm))
        out[nm] = (d[0], d[1], d[2], c_total * lens[nm] / tot) if d else None
    return out


def main():
    a = sys.argv[1:]
    if len(a) < 3:
        print(__doc__)
        return 2
    res = analyze(a[0], a[1], a[2])
    if "--json" in a:
        print(json.dumps({k: v for k, v in res.items()}))
        return 0
    print(f"ネット {a[1]}、起点 {a[2]}")
    for k, v in sorted(res.items(), key=lambda kv: (kv[1] is None, kv[1][0] if kv[1] else 0)):
        print(f"  {k:10s} " + (f"L {v[0]:6.2f} nH  R {v[1]:6.2f} mΩ  経路 {v[2]:6.1f} mm  C {v[3]:5.2f} pF" if v else "つながっていない(配線もベタも無い)"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
