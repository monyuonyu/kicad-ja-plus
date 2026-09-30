# KiCad 8.0.9 ローカル修正パッチ

ソース: ~/src/kicad-8.0.9（GitHub 公式ミラーの 8.0.9 タグ）／ビルド: $KICAD_LOCAL_BUILD
ファイル形式は 8.0 のまま（KiCad 8 と互換）。取り込むたびに ~/src/route-test/regress.sh と
kicad-cli-local の DRC/ERC をシステム版と突き合わせて確認する。

| 番号 | 内容 | 出どころ | 確認 |
|---|---|---|---|
| 00 | kicad_route（画面なし配線コマンド）＋ SWIG の thisown 参照数バグ修正 | 独自 | 回帰試験・valgrind |
| 01 | ルーター不具合修正 19 件（8.0.9 向けに 3 件を調整） | master の 4403c9b62 6078bc52e fd502efff 663afac3d a6e111ec0 18c17111b e4ef64294 55d598843 7b6a344f4 bf64e7418 bfb3875a6 078703111 78fc95fd6 04b9fc76d 0256ccb6a f745f61d0 3b1c8e7ba 2927760c8 42cc8baa6 | 回帰試験で結果同一 |
| 02 | pcb drc --refill-zones / --save-board | KiCad 10 | 保存結果が元とバイト一致 |
| 03 | DRC の JSON に marker の位置と層 | MR !2780 | JSON 出力を確認 |
| 04 | 部品を動かすとき、つながった線を迂回させて引き直す（C++17 と 8.0.9 の WALKAROUND::Route に合わせて調整） | MR !2188 | 回帰試験で結果同一。174 通りの移動の比較で、部品を右 1mm が「不可」→「可」（DRC 違反なし） |
| 05 | `kicad-cli pcb export stats`（基板の統計。テキスト/JSON） | KiCad 10（f827982ca の版を移植。PRESSFIT 行は除外、ビア種別は BLIND_BURIED に統合、子要素の走査を明示化） | 部品・パッド・ビア・穴径ごとの数をシステムの pcbnew と突き合わせて一致 |
| 06 | `kicad-cli pcb render`（画面なしの 3D レイトレース画像。PNG/JPEG、視点・回転・ズーム・高画質・床と影） | KiCad 9（f6f0b9a66 の job/CLI）＋ master a9ce9da55（`--rotate -45,0,45` の前処理） | 試験用の基板で上面・斜め・底面を描画。3D モデル表示には s3d_plugin_vrml/oce/idf のビルドが必要 |
| 07 | kicad-route の使い勝手: help / info / check(その場で DRC、未配線と違反を分けて表示) / silk / 標準入力 / .kicad_pro の自動コピー / 指示書(--instructions、Markdown) / 部品を動かしたら部品番号の文字を自動で逃がし、押し出された周りの文字も逃がす（移動前からの重なりには触らない） | 独自（ユーザーの提案: シルクも押しのけ配線のように） | 部品移動の通し試験で DRC が移動前と同一、3D で文字が見えることを確認 |
| 08 | `kicad-gerber`（info / diff / dirdiff）。差分画像は 灰=共通・赤=消えた・緑=増えた・青枠と番号=差のある場所、場所の座標一覧つき。`kicad-local fabdiff`（2 つの基板の製造データを層ごとに比較）と `renderdiff`（3D 画像の差分） | KiCad 11 の gerber_diff.cpp / gerber_to_polyset.cpp（4852b8485 以降）を移植。PNG は 8.0.9 に PNG プロッタが無いため Cairo で自前描画。gerbview の job の仕組みは移植していない | 同じ基板の 2 つの版の比較で、全層の差がすべて意図した変更で説明できることを確認。副産物として出力設定の穴の印(drillshape 1)を発見 |
| 09 | 基板エディタの AI チャットの枠（Claude Agent SDK。10.0.6 の 0032・0033 と同じ画面と使い方）。8.0.9 には IPC API が無いので、承認したコードは KiCad の中の Python（pcbnew）で実行し、アクションプラグインと同じ仕組みで「元に戻す」の 1 段にする（変わらなかった回は段を作らない、誤りの回は取り消す）。基板を変える実行は、画面で承認した番号のものだけを受け付ける | 独自 | 変えたファイルのコンパイル |
| 10 | AI チャット: たずねずに実行する切り替え（10.0.6 の 0034 と同じ） | 利用者の希望 | 変えたファイルのコンパイル |
| 11 | AI チャットの直し: run_python の 1 回目で落ちた（基板を文字列にする時に、SaveBoard と同じ準備をしていなかった）。ビルドした場所から動かす時は ../kicad/kicad-cli を使う | 開発環境整備のセッション（WSL）の報告 | 変えたファイルのコンパイル |
| 12 | Windows（MinGW）でビルドできるように: 3D 画像の書き出しの TRANSPARENT・OPAQUE が Windows のヘッダーの定数とぶつかる、新しい SWIG に Python 2 の名前（PyInt_FromLong など）が無い | 8.0.9 の Windows 版のビルドで見つけた | Windows のビルド |
| 13 | AI チャット: get_board が毎回失敗していた（8.0.9 の Python からは LIB_ID::Format() を呼べないので GetFPIDAsString に） | 開発環境整備のセッションの自動試験 | 同じ試験 |
| 14 | AI チャット: AI のコードの実行の間は、pcbnew.LoadBoard・NewBoard を使えなくする（別の基板のプロジェクトを読み込み、後で落ちた）。SaveBoard は写しの保存だけ（開いている基板のファイルは上書きしない。設定も保存すると、開いているプロジェクトの場所が写し先に変わるので基板だけ） | 開発環境整備のセッションの自動試験 | 偽の pcbnew での試験 |
| 15 | AI チャット: 「たずねずに実行」の確認の窓のボタンを、環境の言葉によらず「有効にする／やめる」に | 開発環境整備のセッションの試験 | コンパイル |
| 16 | AI チャット: 64KB を超える出力で固まった（仲介役へ書き切るまで繰り返す。run_python の出力は後ろの 20,000 文字に切り詰める）。ビルドの場所の kicad-cli の時は KICAD_RUN_FROM_BUILD_DIR を外さない（check・render が失敗した）。出力の UTF-8 として正しくない文字は置き換える | 開発環境整備のセッションの本物の AI での試験 | コンパイル・判定の試験 |
| 17 | AI チャット: run_python の出力の NUL を「\0」と見せる（後ろが切れていた） | 開発環境整備のセッションの試験 | 偽の pcbnew での試験 |
| 18 | AI チャット: 設計の設定（ルール・ネットクラス。プロジェクトの側）の変更を見分け、誤りの時は戻す（「元に戻す」では戻らないと知らせる）。check の写しに保存前の設定も書き出す。やり取りの回数の上限（80）で止まったら知らせる。斜めの画像を少し引く。切り詰めの印を仲介役で消さない | 開発環境整備のセッションの本物の AI での試験 | コンパイル |
| 19 | AI チャット: 18 の設定の比較が効いていなかった（入れ子の設計のルール・ネットの設定を、比べる前に親の JSON へ書き戻す） | 開発環境整備のセッションの試験 | コンパイル |
| 20 | AI チャット: 元に戻すにグループとネットの追加・削除も入れる。誤りの時は図枠の文字・層の名前も戻す（変えた時は元に戻すで戻らないと知らせる）。120 秒で終わらないコードは止める。最後の式の値を表示できなくても変更は取り消さない。長い出力は頭と尻を残す。上書きの防止をシンボリックリンクでも効かせる。回路図がある時だけ照合し、写しに回路図も写す。check の 1 行 1 件、角度は小数 3 桁 | 開発環境整備のセッションの本物の AI での試験 | コンパイル・偽の pcbnew での試験・kicad-cli の照合 |
| (00内) | kicad_route の optimize 命令 | KiCad 11 の Optimize Route (654f0f473) | DRC 違反なし |

8.0.9 向けの調整:
- 663afac3d: PNS_LAYER_RANGE（9 で導入）→ LAYER_RANGE
- 04b9fc76d: std::set::contains（C++20）→ count()
- 4403c9b62: 8.0.9 に無いメンバ m_lastFixNode の初期化は除外
- bfb3875a6: ルーター部は BOX2 の改名だけで 8.0.9 では同等（ClosestPointTo のまま）
- 0256ccb6a: NODE::AssembleLine / followLine に aAllowSegmentSizeMismatch を移植（master と同じ判定）

見送り: 001f22914 と d1191971d（8.0.9 には前提の制約・不具合が無い）

06 の移植方針: 9 ではレイトレーサを base/GL/RAM の 3 クラスに分割したが、その前提に RGBA 背景などの改修が
連鎖しているため、8.0.9 の RENDER_3D_RAYTRACE に HeadlessPrepare/HeadlessRender を足すだけにした（画面側は無変更）。
視点切り替え（CAMERA::ViewCommand_T1）は共通ライブラリを変えないよう job 側に置いた。背景の透過は未対応（警告を出して不透明で描く）。

## 入口

`kicad-local help`（~/.local/bin。控えは bin/）。drc / erc / render / stats / route / cli / python / patches / rebuild / test。
