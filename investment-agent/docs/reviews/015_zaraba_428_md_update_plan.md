# MD更新計画レビュー依頼: ザラ場ツール 4/28 バグ修正後のMD反映

- 提出日: 2026-04-28 JST
- 提出者: メインエージェント
- パターン: 1（作成・更新MDのまっさらレビュー）

---

## 背景

2026-04-28 のザラ場決算ツール反省会で発見された5つのバグを修正し、コミット・プッシュ済み。
修正内容は `scripts/zaraba_tdnet_poller.py` と `scripts/zaraba_earnings.py` に反映済み。

### 修正済みバグ一覧

| ID | 概要 | コード修正内容 |
|----|------|---------------|
| P0-1 | IFRS/米国基準の連結OP取り違え | TDNET_TAG_MAP タグ順を IFRS→US-GAAP→J-GAAP に変更。全タグ横断で連結エントリ優先抽出に刷新 |
| P0-2 | 米国基準 OP タグ不足 | `OperatingIncomeUS` を TDNET_TAG_MAP の全OPキーに追加 |
| P1-1 | FY配当の ForecastMember 制約 | 配当タグ(`is_div`)に限り `ResultMember` も許可 |
| P1-3 | F12 ForEPS 恒久不発 | `FORECAST_EPS` を TDNET_TAG_MAP に追加、`_xbrl_to_jquants_rec` にマッピング追加 |
| P2-1 | 翌期非開示ペナルティの時価総額フィルタ | `market_cap_oku >= 3000` ガード追加 |

---

## MD更新計画（5項目 × 3ファイル）

### 更新1: 066 翌期予想非開示ルール更新

- **対象**: `docs/knowledges/tools/066_zaraba_tool.md` line 381付近
- **現状**: `翌期予想非開示: FY 発表で NxFOP が取れない場合のみ付与` — 時価総額フィルタの記載なし
- **修正内容**: `market_cap_oku >= 3000`（3,000億円以上のみ発火）の条件を追記
- **根拠**: P2-1 修正（zaraba_earnings.py L1522-1526）で実装済み

### 更新2: 066 XBRLタグセクション更新

- **対象**: `docs/knowledges/tools/066_zaraba_tool.md` lines 326-339（XBRL予想値タグ修正セクション）
- **現状**: 既存のタグ修正記録はあるが、以下が未反映:
  - TDNET_TAG_MAP のタグ候補順が IFRS→US-GAAP→J-GAAP であること
  - `OperatingIncomeUS` の追加
  - `FORECAST_EPS` キーの追加
  - 連結優先（cross-tag consolidated-first）の抽出ロジック
- **修正内容**: P0-1/P0-2/P1-3 の修正を反映するセクション追加（「4/28 修正済み」セクションは既にあるが、技術仕様セクションへの反映が必要）

### 更新3: 066/071 配当タグの ResultMember 許可

- **対象**: 
  - `docs/knowledges/tools/066_zaraba_tool.md` — 注意事項セクション付近
  - `docs/knowledges/tools/071_xbrl_to_jquants.md` line 188付近（予想値タグ表）
- **現状**: 両ファイルとも配当コンテキストは `ForecastMember` のみ前提で記載
- **修正内容**: 配当（`DividendPerShare`）のみ `ResultMember` も有効であることを明記。FY確定配当は `ResultMember` で格納される
- **根拠**: P1-1 修正（zaraba_tdnet_poller.py の `if is_div:` ガード変更）

### 更新4: 071 FORECAST_EPS・IFRS/US-GAAPタグ優先の追記

- **対象**: `docs/knowledges/tools/071_xbrl_to_jquants.md` lines 184-189（予想値タグ表）
- **現状**: FOP/NxFOP/FDivAnn のみ記載。FORECAST_EPS なし。IFRS/US-GAAPタグ優先の記載なし
- **修正内容**: 
  - FORECAST_EPS 行を追加（タグ: `EarningsPerShare`, context: `*ForecastMember`, 用途: F12 PEG計算）
  - 注記: タグ候補順は IFRS (`OperatingIncomeIFRS`) → US-GAAP (`OperatingIncomeUS`) → J-GAAP (`OperatingIncome`) の優先順序

### 更新5: 059 因子表の最終更新日・F12データソース補足

- **対象**: `docs/knowledges/tools/059_earnings_model_eda.md` lines 48-64（因子定義表）
- **現状**: 最終更新 2026-04-13。F12 のデータソースが `株価÷ForEPS÷(翌期OP成長率×100)` のみで、ForEPS のソースが明記されていない
- **修正内容**: 
  - 最終更新日を 2026-04-28 に更新
  - F5 の発火条件に時価総額フィルタ注記を追加（翌期非開示ペナルティは cap >= 3000億のみ）
  - F12 のデータソースに `ForEPS: TDnet iXBRL FORECAST_EPS` を明記

---

## レビュー観点

以下の観点でAI可読性レビューをお願いします:

1. 上記5項目で漏れている更新箇所がないか（修正コードの影響範囲を網羅しているか）
2. 各更新項目の記述が、将来のAIが066/071/059を読んだ時に誤読を生まないか
3. 066の「4/28 修正済み」セクション（既存）と技術仕様セクションの整合性
4. 071と066で同一情報（XBRLタグ仕様）を二重管理するリスクと正本帰属

## 関連ファイル

- `scripts/zaraba_tdnet_poller.py` — XBRL抽出ロジック本体
- `scripts/zaraba_earnings.py` — スコアリングロジック本体
- `docs/plans/20260428_zaraba_scoring_bugs.md` — バグ修正計画書
- `docs/knowledges/tools/066_zaraba_tool.md` — ザラ場ツール知見
- `docs/knowledges/tools/071_xbrl_to_jquants.md` — XBRL→J-Quants変換知見
- `docs/knowledges/tools/059_earnings_model_eda.md` — 決算反応モデル因子定義
