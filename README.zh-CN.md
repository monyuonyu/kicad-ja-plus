[English](README.md) | [日本語](README.ja.md) | **简体中文**

> 本文译自 [README.ja.md](README.ja.md)（日文版为原文），对应提交 `fab6b11`。

# kicad-ja-plus — 无需图形界面也能处理电路板的 KiCad 改进版

在 KiCad 的基础上增加了下列内容。共有两个版本。

| 版本 | 适合的用法 |
|---|---|
| **10.0.6**（推荐） | 基于最新的稳定版。还从上游的 MR 中引入了生产数据和 DRC 相关缺陷的修复。**在没有显示器的服务器、CI 以及 AI 中都能直接运行** |
| 8.0.9 | 希望修改后的文件继续在 KiCad 8 中使用时 |

增加的内容：

- 无需打开图形界面即可处理电路板和原理图的工具（布线、移动元件、DRC/ERC、3D 图像、生产数据比较、电路仿真）
- 来自 KiCad 9～11 以及上游 MR 的功能和修复

**保存格式与对应版本的上游 KiCad 相同**，因此修改后的文件可以直接用普通的 KiCad（10 或 8）打开。

## 功能

入口只有一个：`kicad-local`（`kicad-local help` 会列出命令）。

```
kicad-local drc 基板.kicad_pcb            # DRC 要点（按类型的数量和位置）
kicad-local erc 回路図.kicad_sch
kicad-local lint 回路図.kicad_sch          # 偏离设计惯例之处（外部线路的保护、基极电阻、电源电容……）
kicad-local render 基板.kicad_pcb iso      # 无图形界面生成 3D 图像
kicad-local stats 基板.kicad_pcb           # 元件数、孔径等
kicad-local drc 基板.kicad_pcb --strict    # 另外检查 DRC 即使“0 违规”也会漏掉的 3 项（无网络的焊盘、比网络类更细的走线、过期的铺铜）
kicad-local why 基板.kicad_pcb             # 对每一对未布线连接，按层说明“为什么布不通”（禁布区、其他网络的铜、板框）
kicad-local view 基板.kicad_pcb --drc --net GND   # 生成带注释的 2D 图（高亮网络、为 DRC 违规编号）
kicad-local autoroute 入力.kicad_pcb 出力.kicad_pcb   # 用 Freerouting 对整板自动布线，并重新填充铺铜、运行 DRC
kicad-local finish outline|zone|stitch|islands|widen 入力 出力 ...   # 板框、铺铜、缝合过孔、去除孤岛、按电流加宽走线

# 移动元件并布线（步骤也可从标准输入给出）
kicad-local route 基板.kicad_pcb 出力.kicad_pcb - --instructions 手順.md <<'EOF'
findspace R10 0.25                  # 可放置位置的候选
drag R10 1,0                        # 移动后，相连的走线会绕行重布，丝印文字也会避让
autoroute R10.2 C5.1                # 自动寻找最多 1 个过孔的路径
check                               # 当场运行 DRC
EOF

kicad-local sch 入力.kicad_sch 出力.kicad_sch 手順   # 无图形界面修改原理图（保存后检查连接和 BOM 的差异、ERC）
kicad-local pcbsync 回路図 基板 出力                # 将原理图的修改同步到电路板
kicad-local fabdiff 前.kicad_pcb 後.kicad_pcb 比較/  # 逐层比较生产数据，输出差异图像和位置列表
kicad-local sim run 手順.toml --out 結果/            # 从原理图自动生成 SPICE 并仿真（ngspice）
kicad-local review 新しい版 前の版 --out 確認/        # 一次完成 ERC、DRC、3D、差异和仿真 → 報告.md（报告）
```

加上 `--instructions` 后，还会输出一份操作说明，让人可以在 KiCad 图形界面中重现相同的操作。
**从 AI 或脚本中使用**：drc、erc、why、view、stats、autoroute、finish 可用 `--json` 输出机器可读的结果；drc、erc、why 在有需要确认的问题时
返回退出码 1（无需解析文字，就能根据结果决定下一步）。10.0.6 版在没有显示器（DISPLAY）时也能运行。
详细用法见 [docs/guide.md](docs/guide.md)，工具见 [tools/README.md](tools/README.md)，电路仿真见 [tools/ksim/README.md](tools/ksim/README.md)。

## PCB 编辑器中的 AI 聊天（10.0.6 版）

在 PCB 编辑器中选择 视图（“表示”）→ 面板（“パネル”）→ AI Chat，右侧会出现 AI 聊天面板。可以针对当前打开的电路板请它查询或修改。

- AI 会读取并修改当前打开的电路板（10.0.6 通过 KiCad 的 IPC API，8.0.9 通过 KiCad 内置的 Python）。**默认情况下，每次运行会修改电路板的代码之前，都会先展示内容并请你确认**。
  勾选面板下方的“AI のコードをたずねずに実行する”（不经询问直接运行 AI 的代码）即可跳过确认（勾选时会显示警告。该设置不会保存，关闭 KiCad 后会恢复为每次确认。运行过的代码会保留在聊天中）。
  已运行的修改可以通过一次撤销（“元に戻す”）恢复，出错那一次的修改会被回滚。
- DRC 和图像是先保存电路板的副本，再交给 kicad-cli 处理（不会改动当前打开的电路板及其文件）。
- 使用的是**你自己的 Anthropic API 密钥**。首次使用时会询问，并保存在操作系统的密钥存储中（如 Windows 的凭据管理器）。
- **Windows 版的安装程序中已附带 AI 所需的 Python 和 Claude Agent SDK**（`<KiCad>/ai-runtime`）。无需准备，也无需下载
  （与 AI 的对话本身需要联网）。
- 从源码构建时，首次打开会询问是否创建专用的 Python 环境（需要 Python 3.10 或更高版本以及网络连接）。
  如需手动准备：`python3 <KiCad の scripting>/kicad_ai/setup_ai.py`（`<KiCad の scripting>` 指 KiCad 的 scripting 目录）。
- 如果 KiCad 的 API 服务器未启动，会询问是否启用（与 偏好设置（“設定”）→ 插件（“プラグイン”）中的设置相同）。

工作原理：面板（`pcbnew/widgets/panel_ai_chat.cpp`）只负责显示对话和处理批准，与 AI 的通信由另一个进程中的中介
（`scripting/kicad_ai/bridge.py`）完成。工具共 4 个：`get_board`（概要和选中内容）、`run_python`（kipy 代码，需批准）、`check`（DRC）、`render`（图像）。

## 引入的修改

每个版本都记录了来源（上游的提交或 MR）、为该版本所做的调整以及验证过的内容。

- [10.0.6](patches/10.0.6/README.md)：本仓库的改进（无图形界面的布线命令、DRC 的 JSON 中包含位置和层、移动元件时的绕行、Gerber 比较等），以及**上游的 11 个 MR**（ODB++、IPC-2581 生产数据中的错误，原理图一致性检查对变体的支持，`--theme` 可指定文件等）
- [8.0.9](patches/8.0.9/README.md)：00～08（布线器的 19 处修复、从 KiCad 9～11 引入的功能等）

没有加入任何改变文件格式的功能。

## 安装（Linux，预编译版）

Releases 中提供了面向 Ubuntu 24.04（x86_64）的 tar.gz。

1. 从 PPA 安装上游的 KiCad 10。元件库以及 wxWidgets、OpenCASCADE 等公共组件都使用它提供的。

   ```
   sudo add-apt-repository ppa:kicad/kicad-10.0-releases
   sudo apt install kicad python3-numpy python3-matplotlib python3-pil
   ```

2. 解压到任意位置，并把 `bin/` 加入 PATH

   ```
   tar xzf kicad-ja-plus-10.0.6-linux-x86_64.tar.gz
   export PATH="$PWD/kicad-ja-plus-10.0.6-linux-x86_64/bin:$PATH"
   kicad-local help
   kicad-local test        # 回归测试。全部 OK 即说明安装正确
   ```

只有整板自动布线（`kicad-local autoroute`）需要 Java 21 或更高版本以及
[Freerouting](https://github.com/freerouting/freerouting/releases) 的 jar（jar 的位置用 `--jar` 或 `FREEROUTING_JAR` 指定）。

## 构建（Linux）

1. 安装构建所需的软件包（Ubuntu/Debian。列表见 [patches/10.0.6/builddeps.txt](patches/10.0.6/builddeps.txt)）

   ```
   sudo apt install $(cat patches/10.0.6/builddeps.txt)     # 8.0.9 则使用 patches/8.0.9/notes/builddeps.txt
   ```

2. 获取 KiCad 源码并打上改进补丁（默认位置为 `~/src/kicad-<版本>`）

   ```
   ./apply.sh 10.0.6        # 或 ./apply.sh 8.0.9
   ```

3. 构建（在 Ubuntu 24.04 上验证过的设置。8.0.9 请将 10.0.6 替换为 8.0.9）

   ```
   cmake -S ~/src/kicad-10.0.6 -B ~/src/build-10.0.6 -G Ninja -DCMAKE_BUILD_TYPE=Release \
         -DKICAD_BUILD_PNS_DEBUG_TOOL=ON -DKICAD_SCRIPTING_WXPYTHON=OFF -DKICAD_BUILD_I18N=OFF
   ninja -C ~/src/build-10.0.6 -j4 kicad-cli pcbnew_kiface eeschema_kiface cvpcb_kiface kicad_route kicad_gerber \
         pcbnew/_pcbnew.so s3d_plugin_vrml s3d_plugin_oce s3d_plugin_idf
   ```

   构建 kicad_route（`qa/tools/pns/`）需要 `KICAD_BUILD_PNS_DEBUG_TOOL=ON`。
   内存 7GB 时最多可用 `-j5`，完整构建约需 20～30 分钟。不进行安装，直接从构建目录运行
   （`bin/` 中的入口会设置 `KICAD_RUN_FROM_BUILD_DIR` 等环境变量）。

4. 把 `bin/` 加入 PATH（或把 `bin/` 中的内容放到 `~/.local/bin`）

   ```
   export PATH="$PWD/bin:$PATH"
   kicad-local help
   ```

   构建目录用 `KICAD_LOCAL_BUILD` 指定（默认为 `~/src/build-10.0.6`；8.0.9 则为 `export KICAD_LOCAL_BUILD=~/src/build-8.0.9`）。
   如果改变了源码的位置，请设置 `KICAD_LOCAL_SRC`。
   元件库（封装、符号、3D 模型）使用系统中安装的上游 KiCad（同一系列）的库（`/usr/share/kicad`）。

## 测试

```
kicad-local test        # = test/regress.sh（几十秒）
```

使用 KiCad 8.0.9 自带的演示电路板（pic_programmer）和原理图（interf_u）运行布线步骤，将结果与基准（`test/baseline/`）比较，
并逐个检查各工具的运行情况。

## 目录

| 位置 | 内容 |
|---|---|
| `patches/10.0.6/` | 针对 KiCad 10.0.6 的改进（按编号排列的补丁；上游的 MR 带有作者姓名） |
| `patches/8.0.9/` | 针对 KiCad 8.0.9 的改进。`ALL-combined.patch` 是全部合并后的补丁，带编号的补丁是历史记录 |
| `bin/` | 入口（`kicad-local`、`kicad-route`、`kicad-gerber`、`kicad-cli-local`、`kicad-python`） |
| `tools/` | Python 工具（review、lint、sch、sim、pcbsync、silkfix 等） |
| `test/` | 回归测试 |
| `package/` | 制作发行包的脚本（`linux.sh`：从构建目录生成 tar.gz） |
| `installer/` | Windows 安装程序（NSIS） |
| `docs/guide.md` | 指南（使用流程、从最新版本引入功能的方法、常见陷阱） |

## 许可证

GPL-3.0-or-later（与 KiCad 相同）。KiCad 本体由 KiCad 开发者编写，从上游引入的修改的作者记录在各个补丁中。
