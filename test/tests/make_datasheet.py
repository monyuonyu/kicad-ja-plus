"""bom の試験の材料: FOO123A(5.0mm)と FOO123B(2.5mm)のリード間隔が書かれたデータシートの PDF
使い方: python3 make_datasheet.py <出力.pdf>"""
import sys
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
fig = plt.figure(figsize=(8.27, 11.7))
fig.text(0.1, 0.9, "Series FOO: dimensions", fontsize=12)
fig.text(0.1, 0.85, "FOO123A   Lead spacing 5.0 mm   Height 7 mm", fontsize=10)
fig.text(0.1, 0.82, "FOO123B   Lead spacing 2.5 mm   Height 7 mm", fontsize=10)
fig.savefig(sys.argv[1])
