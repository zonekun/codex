# コードレビュー: update_conse_quick.py CSV出力パリティ・BQ INSERT・クラッシュ安全性

- 日時: 2026-05-05 14:30 JST
- 対象: `scripts/update_conse_quick.py`
- パターン: 1 (新規レビュー)
- レビュアー: Claude (code-reviewer runbook)
- 比較基準: `scripts/update_conse_ifis.py`, `scripts/update_conse_rakuten.py`

---

## 【サマリー】

- 変更の要約: QUICK コンセンサス取得スクリプトの CSV 出力機能パリティ、BQ INSERT の新スキーマ適合性、クラッシュ安全性を IFIS/RAKU と比較レビュー
- 品質評価: **B** — 新スキーマ対応の BQ INSERT は正しく実装されているが、CSV 出力のアーキテクチャが IFIS/RAKU と異なり最終状態 CSV が欠落、flush 安全性に改善余地あり
- 主要リスク:
  1. `export_consensus_csv()` 未呼び出し — 他スクリプトが前提とする merged CSV が QUICK 実行後に更新されない
  2. `append_csv()` が open/close を毎銘柄繰り返すため中断時に最終行が破損する可能性がある（buffered write の途中 kill）
  3. `_to_int()` が想定外文字列を受け取ると `ValueError` で銘柄ごと無言欠落

---

## 【重大な指摘】（即修正）

### #1 `export_consensus_csv()` 未呼び出し — 最終状態 CSV の欠落

- 箇所: `scripts/update_conse_quick.py` (全体構造 — `run()` 関数)
- 事象: IFIS（L380）と RAKU（L836）は処理完了後に `lib_conse_csv_from_view.export_consensus_csv(bq_client, CSV_PATH)` を呼び出し、BQ `V_CONSENSUS_MERGED` VIEW から最新状態を取得して Dropbox 上の merged CSV を上書きする。QUICK はこの呼び出しを一切行っていない。
- トリガー: QUICK スクリプトを単独実行した場合、QUICK 分のデータが BQ に入っても merged CSV は古いまま残る
- 影響: merged CSV を参照する下流ツール（株スクリーニング等）が QUICK コンセンサスを反映しない。ただし次に IFIS か RAKU を実行すれば解消される。
- 根拠: IFIS は `from scripts.lib_conse_csv_from_view import export_consensus_csv` をインポートし L380 で呼び出す。RAKU も L836 で呼び出す。QUICK にはインポートも呼び出しも存在しない。
- 推奨対応: `run()` 関数末尾（`clear_state()` 後）に `export_consensus_csv(bq_client, CSV_PATH_MERGED)` を追加する。ただし現行 `V_CONSENSUS_MERGED` VIEW は `PROFIT` カラムを参照しており新スキーマに未対応（後述 #3）。VIEW 更新が先行タスク。

**[見送り: VIEW新スキーマ対応（P1）が先行タスク。P1完了後に追加する]**

### #2 QUICK 専用 CSV (`conse_quick.csv`) と merged CSV の役割分離が未定義

- 箇所: `scripts/update_conse_quick.py:54` (`CSV_PATH = r"C:\Users\zonekun\Dropbox\stock\py\conse_quick.csv"`)
- 事象: QUICK は `conse_quick.csv` に銘柄ごとに append する独自 CSV を持つ。一方 IFIS/RAKU は自身の独自累積 CSV を持たず、最終的に `export_consensus_csv()` で merged CSV を VIEW から一括出力する構造。QUICK だけが「逐次 append CSV」+「merged CSV なし」という異なるアーキテクチャ。
- トリガー: 再開実行時（`--fresh` なし）。前回データの末尾に新規データが追記される。
- 影響: (a) 同じ DATAAT + TICKER + FY の行が `conse_quick.csv` に重複蓄積される可能性がある（同日に途中中断→再開した場合、`resume_from` の銘柄は skip されるが CSV への重複チェックは無い）。(b) merged CSV 未更新のため下流ツールへの伝搬が途切れる。
- 根拠: `init_csv()` は `fresh=True` 時のみヘッダ付き新規作成（L218-223）。再開時は前回データの末尾に追記する設計。重複排除ロジックなし。
- 推奨対応: 設計意図を明確にする。(A) IFIS/RAKU と同様に `conse_quick.csv` を「実行ログ的な累積ファイル」と割り切り、merged CSV（`export_consensus_csv()`）を正式出力にする。(B) あるいは逐次 CSV を正とするなら重複排除を入れる。

**[採用]** — 逐次append CSV を廃止し、IFIS/RAKU と同じ構造（BQ + resume.json のみ）に統一した。

### #3 `V_CONSENSUS_MERGED` VIEW の新スキーマ非対応（QUICK 追加の前提条件）

- 箇所: `scripts/lib_conse_csv_from_view.py:33` (`SELECT TICKER, QUARTER, PROFIT, TARGET`)
- 事象: 新スキーマでは `PROFIT` カラムが存在しない（`REVENUE`, `OP_PROFIT`, `ORD_PROFIT`, `NET_PROFIT`, `EPS` に分割）。`export_consensus_csv()` が参照する `V_CONSENSUS_MERGED` VIEW は旧スキーマの `PROFIT` カラムを前提。
- トリガー: `create_consensus_table.sql` の DROP + CREATE 実行後に IFIS/RAKU が `export_consensus_csv()` を呼ぶと VIEW クエリ失敗
- 影響: IFIS/RAKU の全体実行が CSV 出力段階で失敗する。BQ データは既に insert 済みなので致命的データ損失にはならないが、merged CSV が更新されない。
- 根拠: `create_consensus_table.sql` に `PROFIT` カラムは未定義。`lib_conse_csv_from_view.py` L33 は `PROFIT` を参照。
- 推奨対応: テーブル再構成と同時に VIEW と `lib_conse_csv_from_view.py` を新スキーマに合わせて更新する。QUICK の `ORD_PROFIT` が IFIS/RAKU の旧 `PROFIT`（経常利益）に相当するため、VIEW の統合ロジック設計が必要。

**[見送り: P1（VIEW/下流スクリプト改修）で対応予定]**

---

## 【改善提案】（可読性・保守性）

### #1 `append_csv()` の flush 安全性向上

- 箇所: `scripts/update_conse_quick.py:226-233`
- 現状: `open("a")` で開いて `writer.writerow()` で書き込み、`with` ブロック終了時に close。1 銘柄分の records をまとめて書くため通常は問題ない。しかし OS レベルの kill（SIGKILL、電源断等）では Python の `__exit__` が呼ばれず、OS write buffer に残った分が失われる可能性がある。
- 提案: `f.flush(); os.fsync(f.fileno())` を write 後に呼ぶ。append 方式のため atomic replace は適用困難だが、fsync で OS バッファを確実にディスクに書き出せる。

### #2 BQ insert 失敗時のリトライ・エラー伝搬

- 箇所: `scripts/update_conse_quick.py:274-282`
- 現状: `insert_bq()` で streaming insert エラーが出た場合、`log.warning` で先頭 3 件のエラーを出力するだけで処理を続行する。失敗した行は CSV には書かれているが BQ には入らない。再実行時に BQ 側の欠落を検知・補完する仕組みがない。
- 提案: IFIS の `insert_to_bq()` は `bool` を返し、失敗時にカウンタに計上して最終的に `sys.exit(1)` する設計（IFIS L382-385）。QUICK も同様に失敗カウントを追跡し、終了コードに反映するとオペレーション上の気付きが得られる。

### #3 `_to_int` / `_to_float` のエラーハンドリング

- 箇所: `scripts/update_conse_quick.py:241-251`
- 現状: 空文字以外の入力に対して `int()` / `float()` を直接呼ぶ。`extract_con_data()` → `clean_value()` が事前にカンマ除去・`-` 除去を行うため正常系では問題ない。しかし想定外のHTMLパターン（例: "N/A"、"未定"、全角数字残留）が `clean_value` を通過した場合に `ValueError` が発生し、`build_bq_rows()` → `process_codes()` の呼び出し元 `process_ticker()` の except で捕捉されてデータが無言で欠落する。
- 提案: `try/except ValueError` で None を返し、ログに警告を出す。これにより部分的に取得できたフィールドは BQ に保存され、異常値のみが NULL になる。

### #4 CSV と BQ の書き込み順序 — 冪等性の欠如

- 箇所: `scripts/update_conse_quick.py:480-484`
- 現状: `append_csv(records)` → `insert_bq(bq_client, records)` の順。CSV 成功後に BQ 失敗で不整合。再実行時に BQ streaming insert は重複チェックしないため二重挿入のリスクがある。
- 提案: streaming insert は at-least-once セマンティクスのため、完全な冪等性確保には `insertId` の付与か、定期的な重複排除 DML が必要。最低限、`insertId` として `f"{dataat}_{ticker}_{fy}"` を設定すれば BQ 側の best-effort dedup が効く。

---

## 【パリティ比較表】

| 機能 | IFIS | RAKU | QUICK | 判定 |
|------|------|------|-------|------|
| CSV初期化（ヘッダ付き新規作成） | `export_consensus_csv()` で毎回上書き | 同左 | `init_csv()` で fresh 時のみ | 設計差異あり |
| 再開機能（state.json） | あり | あり | あり | OK |
| 逐次append CSV | なし（VIEW一括出力） | なし（VIEW一括出力） | あり（銘柄ごと追記） | 設計差異 |
| merged CSV出力（VIEW経由） | あり（L380） | あり（L836） | **なし** | **欠落** |
| BQ insert エラー伝搬 | bool返却 + exit(1) | print警告のみ | log.warning のみ | 改善余地 |
| CSV flush安全性 | VIEW一括上書きで実質安全 | 同左 | append方式（fsyncなし） | 改善余地 |
| エラー時の部分書き込み保護 | CSV=VIEW出力で影響なし | 同左 | CSV先行書き込み→BQ失敗で不整合 | 改善余地 |
| dry-run モード | なし | なし | あり | QUICK が優位 |
| --ticker 個別指定 | あり | なし | あり | OK |
| 重複排除 | BQ streaming dedup任せ | 同左 | なし（CSV・BQとも） | 改善余地 |

---

## 【確認できなかった事項】

- `V_CONSENSUS_MERGED` VIEW の BQ 上の現在の定義。新スキーマへの移行が既に完了しているか、旧 `PROFIT` カラムが互換用に残されているかは実行して確認する必要がある
- `conse_quick.csv` を参照する下流ツールの有無。Dropbox 共有フォルダ上のファイルとして他プロセスが読んでいる可能性
- Streaming insert の 1 リクエストあたりの行数上限（通常 1 銘柄 = 1-3 行程度なので問題ないが、将来の銘柄あたり複数 FY 展開時に 500 行上限に当たる可能性）
- `CON_PATTERN` 正規表現の網羅性（テスト済みとコメントにあるが、`test_conse_quick.py` の内容は未確認）
- IFIS/RAKU が新スキーマに移行済みかどうか（現在のコード上は旧スキーマ `PROFIT` / `TARGET` を insert しており、新テーブル構造と不整合）
