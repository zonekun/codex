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

（注: MuPDF `FzErrorLimit` を誘発する**実トリガー PDF は GCS 上の特定ファイル固有**で、当環境からは正確再現できていない。上記は「ネイティブ terminate はスレッドで封じ込め不能／サブプロセスで封じ込め可能」という**修正契約**の実証。crash の因果自体は一次ログの逐語で確定。）

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
