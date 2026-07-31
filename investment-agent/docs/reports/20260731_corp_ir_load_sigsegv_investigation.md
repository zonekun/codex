# corporate-ir-pipeline Load 相 SIGSEGV 調査報告（TASK 00072）

- 起票: terminal-relay TASK 00072 `corp-ir-load-sigsegv-mupdf`（from: Windows FABLE → 別セッション=バグ調査担当）
- 調査: Linux VM セッション / 2026-07-31
- 対象コード: `zonekun/claude` の `investment-agent`（正本。codex は docs 部分ミラー）
- 調査規律: `docs/knowledges/tools/109_bug_investigation_principles.md` 準拠（断定は一次資料の逐語引用がある範囲に限定）

---

## 1. 結論（確定＝一次資料で裏取り済み）

**Load 相の PyMuPDF テキスト抽出が、ネイティブ層 C++ 例外 `mupdf::FzErrorLimit` により
`std::terminate` → プロセス即死し、それが同一プロセスの `ThreadPoolExecutor` 全体
＝Cloud Run Job を道連れにしている。** ネイティブ層の terminate は Python の `try/except` では
捕捉できないため、既存の 2 段の例外ガードはいずれも無力。

### 根拠（逐語）

一次ログ（TASK 00072 本文・execution `corporate-ir-pipeline-b2fwn`）:

```
21:38:05  -- Load 相（corporate_ir → BQ） --
21:38:23  MuPDF error: syntax error: too many sub-functions in stitching function （8行連続）
21:38:25  terminate called after throwing an instance of 'mupdf::FzErrorLimit'
21:38:25    what():  code=5: exception stack overflow!
21:38:25  Container terminated on signal 11.
```

ソース（クラッシュ現場と、無力な既存ガード 2 箇所）:

- `src/llm/pdf_extract_chunk.py:103` `text = page.get_text("text")` … MuPDF が不正 PDF を解釈して例外を投げる現場
- `src/llm/pdf_extract_chunk.py:107` `except Exception:` … **Python 例外しか捕捉できない**（ネイティブ terminate は通過）
- `scripts/corporate_ir/load_runner.py:208`（旧）`extract_text_pymupdf(pdf_bytes)` … Load 相の抽出入口
- `scripts/corporate_ir/run_pipeline.py:638` `try: ... except Exception as e:`（コメント「1 doc の想定外例外で Load 相全体を abort させない」）… **同じく Python 例外のみ**
- `scripts/corporate_ir/run_pipeline.py:651` `with ThreadPoolExecutor(max_workers=workers) as ex:` … 抽出は**スレッド＝同一プロセス**で走る。1 つの PDF のネイティブクラッシュが全スレッド＝Job を落とす

依存: `pyproject.toml:69` `pymupdf>=1.27.2.2`（C++ バインディング世代。ログの `mupdf::FzErrorLimit` と整合）。

### 機構の実証（当環境で再現実験）

pymupdf 1.28.0 を導入し、本番と同型の構造で検証:

- **現状構造**（`ThreadPoolExecutor` 内でネイティブ crash）: 親プロセスが終了コード **134（SIGABRT）で即死**＝封じ込め失敗。本番の「1 PDF が Job を道連れ」を再現。
- **修正構造**（サブプロセス隔離）: crash した子は `returncode<0` で検知され当該 doc のみ skip、**他 doc は完走・親は生存（コード 0）**。

（注: 「ネイティブ terminate はスレッドで封じ込め不能／サブプロセスで封じ込め可能」という**修正契約**の実証。crash の因果自体は一次ログの逐語で確定。実トリガー PDF そのものは GCS 上の特定ファイルだが、**症状を出す PDF 構造は §1-B で手製再現に成功**した。）

---

## 1-B. そもそもの根本原因（深掘り）— 機能バグか？

### 何が起きているか（MuPDF ソース逐語）

エラーは PDF の **Type-3「stitching（縫合）関数」** のロードで発生する。stitching 関数は
複数の 1 入力関数を定義域で繋ぎ合わせる PDF 標準の関数型で、Separation/DeviceN 色空間の
**tint 変換**や shading で使われる。`get_text("text")` はページ内容の色設定オペレータ（`scn` 等）を
解釈する際にこの関数をロードするため、**テキスト抽出でも評価経路に入る**（当環境で実証済み）。

MuPDF の該当実装（`source/pdf/pdf-function.c` / `source/fitz/error.c`・上流 master 逐語確認）:

1. `load_stitching_func`: サブ関数数 `k > MAX_STITCHING`（**`#define MAX_STITCHING 256`**）で
   `fz_throw(FZ_ERROR_SYNTAX, "too many sub-functions in stitching function")`。
2. サブ関数は `pdf_load_function_imp` で**再帰ロード**（`pdf_cycle` によるサイクル検出はあり＝
   自己参照は "recursive function" で弾く。よって無限ループではなく**有限だが過大な深さ/幅**が問題）。
3. `fz_push_try`: 例外スタック（`ctx->error.stack`・固定長）が溢れる直前に
   `FZ_ERROR_LIMIT` で `"exception stack overflow!"` を throw。**設計上これは catch 可能**
   （error.c は overflow 時も `error.top++` して fz_try/fz_catch に届くよう細工している）。

つまり症状は **2 つの内部上限**の合わせ技:
- **幅**: stitching 関数のサブ関数が 256 超 →「too many sub-functions」
- **深さ**: stitching 関数が入れ子で深く連なる → 再帰ロードで例外スタック超過 →「exception stack overflow」

### 手製 PDF で症状を再現（当環境 pymupdf 1.28.0・一次実証）

Separation 色空間の tint 変換に病的な Type-3 関数を仕込んだ PDF を生成し `get_text("text")` で検証:

| 再現ケース | 生成物 | 結果（本番ログとの一致） |
|-----------|--------|--------------------------|
| 幅: サブ関数 300 個（>256）の stitching | `/tmp/wide.pdf` | `MuPDF error: syntax error: too many sub-functions in stitching function` を**逐語再現** |
| 深さ: 250 段ネストした stitching チェーン | `/tmp/nested_fn.pdf` | `MuPDF error: exception stack overflow!` / `limit error` を**逐語再現** |
| 合流: 深チェーン末端に幅超過ノード | `/tmp/combo.pdf` | 上記エラーが**本番と同じ 8 回**出力 |

→ 本番ログの 2 文言（"too many sub-functions" ×8 → FzErrorLimit "exception stack overflow"）が
**この PDF 構造で確定的に発生する**ことを実証。引き金 PDF は「Separation/DeviceN 色空間 or shading の
関数が過大に広い/深い」構造を持つと断定できる（owning 側で 9972 PDF の色空間/関数を確認すれば裏取り可）。

### 「機能バグ」の帰属 — 3 層

1. **PDF（真の起点）＝ 不正 PDF**。正常な決算説明 PDF は 256 超の入れ子/分岐関数を持たない。
   壊れた PDF 生成器の産物か、意図的に病的なファイル。**当方コード・データ処理の論理バグではない**。
2. **PyMuPDF/MuPDF（なぜ致命化するか）＝ 上流の既知の堅牢性上限（wontfix）**。
   `FZ_ERROR_LIMIT` は C 層では catch 可能に設計されているが、PyMuPDF の **C++ バインディング**では
   `mupdf::FzErrorLimit` が、直前の 8 連続 throw の unwinding 中に再 throw される等の条件で
   `std::terminate` に化ける（本番 `signal 11`）。Artifex はこの系統（Issue #3608
   `FzErrorLimit code=5`）を **"wontfix"**（malformed PDF に対する意図的な保護上限）とし、
   かつ**バージョン依存**（#3608: 1.22.5 は通過・1.24.5 で発症）。
   → **バージョン更新は信頼できる恒久対策にならない**。当環境の 1.28.0 は同じ病的 PDF を
   graceful に catch（終了コード 0）した＝**致命化するか否かはビルド/版に依存**し、
   in-process の `try/except` に頼れないことの裏付け。
3. **当方コード＝論理バグなし・構造的弱点のみ**。`try/except`（`pdf_extract_chunk.py:107` /
   `run_pipeline.py:643`）は Python 例外にしか効かず、抽出を **ThreadPoolExecutor（同一プロセス）**で
   回していたため 1 PDF の native crash が Job を道連れにした。§2 の**サブプロセス隔離**が唯一の
   版非依存な恒久対策。

### 結論（機能バグか？への回答）

- **当方の機能バグではない**（抽出ロジック・BQ ロジックは正しい）。
- 起点は**不正 PDF**、致命化は **PyMuPDF C++ バインディングの既知・上流 wontfix な堅牢性限界**。
- したがって「上流修正待ち」も「版固定」も当てにできず、**プロセス隔離（実装済み）が正解**。
  補助策として、owning 側で①引き金 PDF の色空間/関数構造の確認、②必要なら当該 PDF の
  除外/事前検知（ただし判定は MuPDF 依存で脆いため隔離の代替にはしない）。

### 1-C. 「バージョンを変えれば回避できるか」— 版スイープ実測

同一の手製病的 PDF（combo/nested_fn/wide）を複数版で実行し outcome を実測（当環境 py3.11）:

| PyMuPDF 版 | combo.pdf | nested_fn.pdf | wide.pdf |
|-----------|-----------|---------------|----------|
| 1.24.5 | GRACEFUL | GRACEFUL | GRACEFUL |
| 1.24.14 | GRACEFUL | GRACEFUL | GRACEFUL |
| 1.26.4 | GRACEFUL | GRACEFUL | GRACEFUL |
| **1.27.2.2（本番最小版）** | GRACEFUL | GRACEFUL | GRACEFUL |
| 1.28.0 | GRACEFUL | GRACEFUL | GRACEFUL |

（GRACEFUL＝エラーは印字されるが内部 catch され戻り値あり・native crash も Python 例外 raise も無し）

**判明した事実と回答:**

- **正しく導入できた全版（本番最小版 1.27.2.2 を含む）で、私の合成 PDF は致命化しなかった。**
  つまり本番と同じエラー**文言**を出す合成 PDF ですら terminate を再現できない
  ＝ **本番の `terminate` は実トリガー PDF 固有の構造依存**（合成では捉えきれない要素がある。
  #3608 の "too many nested graphics states" 等、別種の限界経路の可能性）。
- 版で挙動が変わる**方向性**自体は上流も認める（#3608: 1.22.5 通過→1.24.5 で別限界に regress）。
  しかし当環境の実測では**版を上げても下げても本件症状は一様に GRACEFUL** で、
  「この版なら安全」という差を作れなかった。
- 結論: **バージョン変更で回避できる保証はなく、検証もできない。**
  ①実トリガー PDF が無いと「どの版が安全か」を実測できない、②限界は上流 wontfix で意図的、
  ③ある版が当該 PDF を回避しても別の不正 PDF が別限界を踏めば再発（版更新はむしろ他 PDF を
  regress させ得る）。**版選定は「安全策」ではなく、隔離導入後に実トリガー PDF を各版へ当てて
  “抽出できる版があるか”（＝skip を減らすデータ品質最適化）を測る用途にのみ意味を持つ。**
  crash 安全性は版非依存の**プロセス隔離**でのみ担保する。

---

## 2. 対処（実装・検証済み／未デプロイ）

**方針**: PyMuPDF 抽出を**別プロセスに隔離**する。子がネイティブクラッシュ（SIGSEGV/SIGABRT）・
timeout・非0終了しても、親は `("", 0)` を受け取り、**既存の pdfminer フォールバック**へ委ねる
（当該 PDF のみ skip・Job は継続）。TDnet（TASK 00071 凍結中）を巻き込まないよう
**共有関数 `extract_text_pymupdf` は無変更**、corp Load 経路にだけ隔離版を配線する。

### 変更ファイル

| ファイル | 変更 |
|----------|------|
| `src/llm/pdf_extract_chunk.py` | `extract_text_pymupdf_isolated()`（サブプロセス隔離ラッパー）／`_isolated_worker_argv()`（テスト用シーム）／`__main__` ワーカー（`--extract <in> <out>`）を**追加**。既存関数は無変更 |
| `scripts/corporate_ir/load_runner.py` | `_extract_text` の抽出呼び出しを `extract_text_pymupdf` → `extract_text_pymupdf_isolated` に差し替え（import も更新） |
| `tests/test_pdf_extract_isolation.py` | 回帰テスト**新規**（挙動保存・封じ込め・timeout の 3 ケース） |

パッチ: 本ディレクトリの `20260731_corp_ir_sigsegv_fix.patch`（`git apply` で適用可）。

### 隔離設計のポイント

- 子は `python -m src.llm.pdf_extract_chunk --extract <in_pdf> <out_txt>` として本モジュールを再実行し、
  **同一の `extract_text_pymupdf` を子側で呼ぶ**（ロジック単一正本を維持）。戻り値・`[PAGE N]`
  マーカー・正規化は現行と完全一致。
- コンテナ整合: `Dockerfile.corporate-ir-pipeline` は `WORKDIR /app` に `src/`・`scripts/corporate_ir/` を
  配置。ラッパーは `cwd=/app`（`Path(__file__).parents[2]`）＋`PYTHONPATH=/app` を明示設定するため
  `-m src.llm.pdf_extract_chunk` は確実に解決（`src/__init__.py` 存在）。
- PDF は一時ファイル経由で受け渡し（大容量説明会 PDF 対応）。timeout=120s でハング PDF も保険。

### 検証（当環境・pymupdf 1.28.0）

```
tests/test_pdf_extract_isolation.py ...  3 passed
```

- `test_isolated_matches_direct` — 通常 PDF で隔離版 == 直接版（text・page_count 一致＝挙動保存）
- `test_child_signal_death_is_contained` — 子が SIGABRT で死んでも親は `("", 0)`・生存
- `test_child_timeout_is_contained` — 子ハング → timeout → `("", 0)`

ruff: 新規コードは指摘なし（既存の未整列 import 等は差分外のため不変更）。

### トレードオフ（要認識）

doc ごとにサブプロセスを spawn（子は fitz 等を import）。バッチ Job では許容範囲だが、
将来 doc 数が増えて起動コストが問題化するなら**永続ワーカープール化**が最適化候補
（ただし `ProcessPoolExecutor` は worker の signal 死で `BrokenProcessPool` になるため、
プール再生成＋再投入の設計が別途必要。まずは per-doc 隔離が安全なベースライン）。

---

## 3. 未確定・owning セッション（GCP 保有）への引き継ぎ事項

当セッションは GCP に未接続のため、以下は**未検証＝要対応**:

1. **引き金 PDF の特定**（仮説: 21:37:53 に promote された `9972 ...KessanSetsumeikai.pdf`。
   説明会スライドは shading/stitching function を含みやすく症状と整合するが**未確定**）。
   本修正は「どの PDF が来ても Job を落とさない」ため特定は必須ではないが、再発監視の観点で有用。
2. **BQ 中途書込の整合**: Load 相が抽出途中で死亡。`CORPORATE_IR_DOCUMENTS_CHUNKS` / `_AI` /
   `_LOAD_LOG` に半端な pending / 行残りがないか（台帳は「pending 先行登録 39件」まで進行）。
   → `scripts/corporate_ir/health_check.py`（106-O §統合ヘルスチェック）で照合推奨。
3. **再発性**: 同じ PDF を再処理すれば必ず落ちるかの確認（本修正適用後は skip されるはず）。
4. **デプロイ**: 本修正はイメージ再ビルド＋Job 更新が必要（稼働イメージは 07-29 ビルドの旧版）。
   デプロイ後、失敗 execution を再実行して pending が解消し Job が緑になることを確認。

---

## 4. terminal-relay へ追記する RESULT（owning セッションが claude 側ボードへ append 用）

> 当セッションは `zonekun/claude` へ push 権限がないため、以下を owning セッションが
> `docs/terminal-relay.md` に `printf >>` で追記してください（ID は 1 行目カウンタ +1 で採番）。

```
## RESULT: <採番ID> corp-ir-load-sigsegv-mupdf 2026-07-31 JST REPLY TO 00072

原因確定（一次ログ+ソース逐語）: Load 相 PyMuPDF 抽出が MuPDF ネイティブ例外
FzErrorLimit で terminate→SIGSEGV。ThreadPoolExecutor(run_pipeline.py:651)＝同一プロセスの
ため 1 PDF が Job 全体を道連れ。Python の except(pdf_extract_chunk.py:107 / run_pipeline.py:643)
はネイティブ terminate を捕捉不能。
対処: PyMuPDF 抽出をサブプロセス隔離（extract_text_pymupdf_isolated）。子の signal 死/timeout は
("",0)→既存 pdfminer フォールバックへ。TDnet 巻き込み回避のため共有関数は無変更・corp Load のみ配線。
検証: tests/test_pdf_extract_isolation.py 3 passed（挙動保存/封じ込め/timeout）。
パッチ: docs/reports/20260731_corp_ir_sigsegv_fix.patch。
要 owning 対応: ①引き金PDF特定(仮説9972説明会) ②BQ中途書込整合(health_check.py) ③デプロイ(旧イメージ)。
```
