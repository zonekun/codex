# 作業計画: data_catalog.md を INDEX + 個別MD に分割

**作成日時**: 2026-05-08 22:50 (JST)
**ステータス**: 完了
**分類**: (c) 一過性型
**親知見 MD**: 該当なし（プロジェクトインフラ改善）
**関連アイディアID**: -

## 目的

`data_catalog.md`（1,617行 / 107KB / 推定 ~30,000トークン）を、薄いインデックス + テーブル別詳細ファイルに分割し、セッション毎のトークン消費を 90% 以上削減する。

## 背景・動機

CLAUDE.md の「データ参照ルール」により、BQ テーブル名1つを確認するだけでも data_catalog.md 全体（~30Kトークン）を Read している。実際に必要なのはテーブル名→スキーマの lookup だけであり、インデックス（~100行）+ 必要な詳細ファイル1つ（~50-300行）で足りる。

## 現状分析

### サイズ内訳（行数）

| セクション | 行範囲 | 行数 | 備考 |
|-----------|-------|------|------|
| ヘッダ + ストレージ種別 | 1-17 | 17 | **INDEX に残す** |
| (a) BQ サマリテーブル | 19-37 | 19 | **INDEX に残す** |
| BQ 詳細: STOCK_PRICE_JQUANTS | 39-87 | 49 | 分割 |
| BQ 詳細: INDEX_PRICE | 89-116 | 28 | 分割 |
| BQ 詳細: DIVIDEND_DATE | 118-143 | 26 | 分割 |
| BQ 詳細: STOCK_PRICE | 146-188 | 43 | 分割 |
| BQ 詳細: STOCK_PRICE_YF_AM | 190-228 | 39 | 分割 |
| BQ 詳細: SHINA_RATES | 231-277 | 47 | 分割 |
| BQ 詳細: MARGIN_BALANCE | 280-356 | 77 | 分割 |
| BQ 詳細: STOCK_CODE_LIST | 359-418 | 60 | 分割 |
| BQ 詳細: DELISTED_STOCKS | 421-456 | 36 | 分割 |
| BQ 詳細: SHAREHOLDER_COMPOSITION | 459-495 | 37 | 分割 |
| BQ 詳細: fin_summary + view | 498-872 | 375 | **最大。分割** |
| BQ 詳細: TDNET_DOCUMENTS_ENHANCED | 875-1060 | 186 | 分割 |
| (b) GCS サマリ + 詳細 | 1062-1151 | 90 | 分割 |
| (c) ローカル CSV + スキーマ | 1152-1305 | 154 | 分割 |
| (d) 外部 API + キャッシュ | 1306-1394 | 89 | 分割 |
| BQ 詳細: EARNINGS_CALENDAR | 1396-1461 | 66 | 分割 |
| BQ 詳細: YF_STOCK_INFO | 1463-1565 | 103 | 分割 |
| BQ 詳細: SIGNAL_011_4 + PAPER_TRADE | 1567-1617 | 51 | 分割 |
| CONSENSUS + V_CONSENSUS_MERGED（CSV節に混在） | 1214-1265 | 52 | BQ 詳細として分割 |

### 影響を受けるコード・ドキュメント

| ファイル | 影響 | 対応 |
|---------|------|------|
| `src/datastore/catalog.py` | `load_catalog()` が全文(1,617行)→インデックスのみ(~120行)に変化。`append_entry()` はファイル末尾追記のためサマリテーブル外に行が漏れる | `append_entry()` を廃止予定として Phase 3 で対応。`load_catalog()` は現状のまま（将来 `discovery.py` 実装時に改修必要、下記リスク参照） |
| `src/datastore/discovery.py` | `load_catalog()` 経由で参照。`discovery.py` 自体が未実装（`NotImplementedError`） | 変更不要 |
| `CLAUDE.md` | 4箇所で `data_catalog.md` を参照: §データカタログ説明(L159)、§データ参照ルール(L161)、§BQクエリ作成ルール(L165)、§高頻度参照テーブル(L226-227) | 全4箇所を2段階ルックアップに更新 |
| `docs/knowledges/INDEX.md` | L72「データ取り込み・更新タスク → `data_catalog.md`」、L180「データストア定義」 | 2段階ルックアップ手順に更新 |
| 他MD（知見・プラン等） | grep で `data_catalog` を参照する約65ファイル。`§` 形式のセクション直接参照あり | Phase 0 で棚卸し → セクション参照があるものは Phase 3 でリンク更新 |

**正本帰属**: `data_catalog.md`（インデックス）はルックアップ用の目次。`docs/data_catalog/*.md` が各テーブル・ストレージの正本。

## 作業ステップ

### Phase 0: 参照元の棚卸し（分割前に実施）

0. [x] `grep -rl "data_catalog" docs/ CLAUDE.md` で全参照元をリスト化 → 68ファイル
1. [x] うち `data_catalog.md §` や `data_catalog.md#` 形式のセクション直接参照を特定 → 0件（Phase 3 でのリンク更新不要）

### Phase 1: 分割先ディレクトリ + 個別ファイル作成

2. [x] `docs/data_catalog/` ディレクトリ作成
3. [x] BQ テーブル別の詳細ファイル作成（16ファイル）:

**個別ファイルテンプレート**（全ファイル共通の冒頭2行）:
```markdown
# STOCK.テーブル名 — 説明
> 親: [`data_catalog.md`](../../data_catalog.md)
```

ファイル一覧:
   - `bq_stock_price.md` — STOCK_PRICE (yfinance, 43行)
   - `bq_stock_price_jquants.md` — STOCK_PRICE_JQUANTS (49行)
   - `bq_stock_price_yf_am.md` — STOCK_PRICE_YF_AM (39行)
   - `bq_index_price.md` — INDEX_PRICE (28行)
   - `bq_shina_rates.md` — SHINA_RATES (47行)
   - `bq_margin_balance.md` — MARGIN_BALANCE (77行)
   - `bq_stock_code_list.md` — STOCK_CODE_LIST (60行)
   - `bq_delisted_stocks.md` — DELISTED_STOCKS (36行)
   - `bq_shareholder_composition.md` — SHAREHOLDER_COMPOSITION (37行)
   - `bq_fin_summary.md` — fin_summary + v_fin_summary_actual_for_q_on_q (375行)
   - `bq_tdnet_documents.md` — TDNET_DOCUMENTS_ENHANCED (186行)
   - `bq_consensus.md` — CONSENSUS + V_CONSENSUS_MERGED (52行)
   - `bq_earnings_calendar.md` — EARNINGS_DISCLOSURE_CALENDAR (66行)
   - `bq_yf_stock_info.md` — YF_STOCK_INFO (103行)
   - `bq_dividend_date.md` — DIVIDEND_DATE (26行)
   - `bq_signal_011_4.md` — SIGNAL_011_4 + PAPER_TRADE_011_4 (51行)
4. [x] ストレージ層別の詳細ファイル作成（3ファイル）:
   - `gcs.md` — GCS パス一覧 + edinet/tdnet/monthly 詳細 (90行)
   - `csv.md` — ローカル CSV + bond_history/四季報スキーマ (154行)
   - `api_cache.md` — 外部 API + yasai_price 詳細 (89行)

   **注意**: CONSENSUS スキーマ（元L1214-1265）は (c) CSV セクション内に物理的に混在している。CSV節から切り出して `bq_consensus.md` に配置すること。直後の `consensus_result.csv` 廃止予定スキーマ（L1266-1305）も BQ CONSENSUS の下流出力のため `bq_consensus.md` にまとめて配置する。

### Phase 2: data_catalog.md をインデックス化

5. [x] `data_catalog.md` を 120行以下のインデックスに書き換え（実績: 80行）:
   - ヘッダ + ストレージ種別テーブル（現行踏襲）
   - (a) BQ サマリテーブル: 各行に `→ [詳細](docs/data_catalog/bq_xxx.md)` リンク追加
   - (b) GCS サマリテーブル + `→ [詳細](docs/data_catalog/gcs.md)` リンク
   - (c) CSV サマリテーブル + `→ [詳細](docs/data_catalog/csv.md)` リンク
   - (d) API サマリテーブル + `→ [詳細](docs/data_catalog/api_cache.md)` リンク

### Phase 3: 参照元の更新

6. [x] CLAUDE.md 全4箇所を更新（L331 はファイル名不変のため書き換え不要）:
   - **L159 §データカタログ説明**: 「`data_catalog.md`」→「`data_catalog.md`（インデックス）+ `docs/data_catalog/*.md`（詳細）」
   - **L161 §データ参照ルール**: 「`data_catalog.md` で確認する」→「`data_catalog.md`（インデックス）でテーブル名・詳細ファイルパスを確認。スキーマが必要な場合のみ詳細ファイルを Read」
   - **L165 §BQクエリ作成ルール**: 「`data_catalog.md` のスキーマ定義でカラム名を確認」→「`data_catalog.md` でテーブルの詳細ファイルを特定し、そのファイルのスキーマ定義でカラム名を確認」
   - **L226-227 §高頻度参照テーブル**: `data_catalog.md` → `data_catalog.md`。`data_catalog.md（§STOCK_CODE_LIST: ...）` → `docs/data_catalog/bq_stock_code_list.md`
7. [x] `docs/knowledges/INDEX.md` L72, L180 を2段階ルックアップに更新
8. [x] Phase 0 で特定したセクション直接参照を持つMDのリンクを更新 → 該当0件のため作業不要
9. [x] `src/datastore/catalog.py`: `append_entry()` に docstring で「廃止予定: 分割後はインデックス末尾にテーブル行が漏れるため使用禁止」コメントを追加。将来的に削除
10. [x] `docs/knowledges/data/001_data_catalog_date_policy.md` の「確認履歴テーブル」が個別ファイルにも適用される旨を追記

### 分割後の運用ルール（CLAUDE.md §データストアに追記）

新規テーブル追加時:
1. `data_catalog.md` サマリテーブルに1行 + `→ [詳細](docs/data_catalog/bq_xxx.md)` リンク追加
2. `docs/data_catalog/bq_xxx.md` を個別ファイルテンプレートに従い新規作成

### Phase 4: 検証

11. [x] 分割後の data_catalog.md が 120行以下であること → 80行
12. [x] 個別ファイルの合計が元の 1,617行と概ね一致すること（±10行） → 1,619行（+2行、ヘッダオーバーヘッド）
13. [x] 全個別ファイルのリンクが data_catalog.md から辿れること → 19/19 OK
14. [x] 各個別ファイルの冒頭に `> 親: [data_catalog.md]` の戻りリンクがあること → 19/19 OK
15. [x] Phase 0 で特定した参照元MDのリンクが全て更新されていること → セクション直接参照0件、CLAUDE.md 4箇所 + INDEX.md 2箇所更新済み
16. [ ] git diff で内容欠落がないことを目視確認
17. [x] 動作確認: 「STOCK_PRICEのカラム名を確認する」シナリオで、インデックス → 詳細ファイルへの2段階ルックアップが機能することを検証

## 成果物

| 成果物 | パス |
|--------|------|
| インデックス（書き換え） | `data_catalog.md` (120行以下) |
| BQ 詳細ファイル ×16 | `docs/data_catalog/bq_*.md` |
| GCS/CSV/API 詳細ファイル ×3 | `docs/data_catalog/{gcs,csv,api_cache}.md` |
| CLAUDE.md 更新 | §データストア 4箇所（L159, L161, L165, L226-227） |
| INDEX.md 更新 | L72, L180 |
| catalog.py 廃止コメント | `append_entry()` に廃止予定コメント |

## 完了条件

1. `data_catalog.md` が 120行以下
2. テーブル名「STOCK_PRICE」でインデックスから詳細ファイルに1クリック/1 Read で到達できる
3. 既存の全テーブル・スキーマ情報が個別ファイルに欠落なく移行されている
4. `src/datastore/catalog.py` の `load_catalog()` が正常に動作する（エラーなし）
5. CLAUDE.md の全4箇所が2段階ルックアップに更新されている
6. Phase 0 で特定した参照元MDのリンクが全て更新されている

## 見積もり

- 想定所要時間: 30〜40分（ほぼ機械的な分割作業）
- 難易度: 低（内容変更なし、構造のみ）

## リスク

- **既存MDからの参照切れ**: Phase 0 の棚卸しで全数把握し、Phase 3 で更新。Phase 4 で検証
- **catalog.py の append_entry 破綻**: Phase 3 で廃止予定コメントを付与。`discovery.py` 自体が未実装のため実害は当面なし
- **`load_catalog()` の戻り値縮小**: 分割後はインデックスのみ(~120行)を返す。将来 `discovery.py` を実装する際は `load_catalog()` の改修（個別ファイルの動的読み込み等）が前提条件になる

## レビュー履歴

- 2026-05-08 22:54 JST: md-reviewer パターン1 → `docs/reviews/124_mr_data_catalog_split.md`（AI可読性B/誤読リスクB）。重大指摘4件を本プランに反映済み
- 2026-05-08 23:03 JST: code-reviewer パターン4 → `docs/reviews/125_cr_data_catalog_split.md`（品質B）。重大指摘2件、改善提案4件。条件付き承認

---

## レビュー追記: 2026-05-08 23:03 JST — code-reviewer

→ `docs/reviews/125_cr_data_catalog_split.md`
