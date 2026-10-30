"""ksim のモデルの出どころ: 黙って代用しない(台車基板の作業で、P6KE39A が値 D_TVS の対応で 6.8V 品になり、
値が Q_NPN_ECB のままで定格が当たらず「NG 0」に見えた)。使い方: python3 ksim_provenance_test.py <tools/ksim>"""
import sys
sys.path.insert(0, sys.argv[1])
import ksim


def comp(ref, lib, value, pins, **fields):
    return {"ref": ref, "lib": lib, "value": value, "fields": fields,
            "pins": {n: {"net": net, "fn": fn, "type": "passive"} for n, (net, fn) in pins.items()}}


comps = {
    "D4": comp("D4", "Device:D_TVS", "D_TVS", {"1": ("VIN", "K"), "2": ("GND", "A")}, MPN="P6KE39A"),
    "D5": comp("D5", "Device:D_TVS", "D_TVS", {"1": ("VIN", "K"), "2": ("GND", "A")}, MPN="P4KE6.8A"),
    "Q1": comp("Q1", "Device:Q_NPN_ECB", "Q_NPN_ECB", {"1": ("GND", "E"), "2": ("C", "C"), "3": ("B", "B")}),
    "Q2": comp("Q2", "Device:Q_NPN_ECB", "Q_NPN_ECB", {"1": ("GND", "E"), "2": ("C", "C"), "3": ("B", "B")}, MPN="2SC1815"),
    "SW1": comp("SW1", "Switch:SW_Push", "SW_Push", {"1": ("B", "1"), "2": ("GND", "2")}),
    "J1": comp("J1", "MyLib:CONN2", "CONN2", {"1": ("VIN", "1"), "2": ("GND", "2")}),
}
nets = {}
for c in comps.values():
    for p, d in c["pins"].items():
        nets.setdefault(d["net"], []).append((c["ref"], p, d["fn"]))
spec = {"_dir": ksim.HERE, "models": {"map": {"D_TVS": "P4KE6V8A", "Q_NPN_ECB": "2SC1815"}}}
b = ksim.Builder(comps, nets, spec)
prov = b.provenance_all()
ok = {r for r, p in prov.items() if p["ok"]}
assert "J1" not in prov, prov.get("J1")                      # a connector: no model needed
assert not prov["D4"]["ok"] and "P6KE39A" in prov["D4"]["why"], prov["D4"]  # the 6.8V TVS by its value: said
assert prov["D5"]["ok"] and "品番" in prov["D5"]["how"], prov["D5"]          # by its part number
assert not prov["Q1"]["ok"] and "Q_NPN_ECB" in prov["Q1"]["why"], prov["Q1"]
assert prov["Q2"]["ok"], prov["Q2"]
assert not prov["SW1"]["ok"] and "スイッチ" in prov["SW1"]["why"], prov["SW1"]
assert prov["D5"]["file"] == "base.lib", prov["D5"]
rt = {"rating": {"SA30A": {"v": 30}, "2SC1815": {"vceo": 50}}}
assert ksim.rating_match(comp("D9", "Device:D", "D", {}, MPN="SA30A"), rt) == "品番"
assert ksim.rating_match(comp("D9", "Device:D", "D", {}, MPN="1.5SA30A"), rt).startswith("品番の一部")
assert ksim.rating_match(comps["Q1"], rt) is None              # Q_NPN_ECB: not by its value
print("ok")
