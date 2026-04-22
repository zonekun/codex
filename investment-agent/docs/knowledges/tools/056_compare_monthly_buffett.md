# バフェットコード月次データ突合ツール

**カテゴリ**: tools
**作成日**: 2026-03-25
**ステータス**: 有効
**関連ファイル**: `scripts/compare_monthly_buffett.py`

## 概要

GCS に保存された月次抽出データ（`monthly_records.json`）とバフェットコードの KPI ページを突合し、抽出精度を検証するツール。定期的に実行して extract_adapter.json の品質管理に使う。

## 実行コマンド

```bash
# 全銘柄（月次データありのもの全件）
PYTHONUTF8=1 python scripts/compare_monthly_buffett.py

# 件数制限
PYTHONUTF8=1 python scripts/compare_monthly_buffett.py --limit 20

# 特定ティッカーのみ
PYTHONUTF8=1 python scripts/compare_monthly_buffett.py --tickers 3097 3046 2670

# バックグラウンド実行（全件 204社で約1.5〜2時間）
PYTHONUTF8=1 python scripts/compare_monthly_buffett.py \
  > data/logs/buffett_compare_YYYYMMDD.log 2>&1 &
```

## 出力

| ファイル | 内容 |
|---------|------|
| `C:\tmp\buffett_compare_YYYYMMDD_HHMMSS.csv` | 突合結果（全フィールド） |
| ログ（stdout） | 進捗・一致/不一致の詳細 |

### CSV カラム

| カラム | 内容 |
|--------|------|
| `ticker` | 銘柄コード |
| `year_month` | 対象年月（YYYY-MM） |
| `our_field` | GCS 抽出フィールド名（adapter.fields[*].key）|
| `our_value` | GCS 抽出値 |
| `yoy_offset` | unit_scale / yoy_offset / floor 等の調整ラベル（適用時のみ）|
| `bc_field` | BC 側でマッチしたフィールド名（`adapter.bc_key` または暗黙 `key`）|
| `bc_value` | BC 側の値 |
| `diff` | 差分（0.0〜、round/floor/ceil いずれかの最小値）|
| `match` | `OK` / `NG` / `BC_NODATA` / `BC_NON_NUMERIC` |
| `match_score` | 常に 100（直接引き当て成功）または 0 (BC_NODATA) |

### ログ記号

| 記号 | 意味 |
|------|------|
| ✅ | 一致（diff ≤ 0.5、または round/floor/ceil で完全一致）|
| ❌ | 不一致（diff > 0.5） |
| ⚠️ | BC 側に対応フィールドなし（`BC_NODATA`）or BC 値が非数値（`BC_NON_NUMERIC`）|

## マッチングロジック（2026-04-18 改修版）

### 基本方針: 決定論的・drift 許容

BC 突合は **adapter.fields[*].bc_key** で明示指定された BC 名への**直接引き当て**のみ。正規化一致・部分一致・値近似 tiebreaker は**使わない**（過去に存在した `_match_score` とそれらのフォールバック経路は廃止）。

### 照合順序

```
1) adapter.fields[*].bc_key が明示 → その名前で BC scrape 結果の dict を直接 lookup
2) bc_key が省略 → our_key を暗黙の bc_key として lookup（移行期の後方互換）
3) 上記いずれも不在 → BC_NODATA
```

### BC 表示精度の自動吸収

BC は小数点 N 桁表示、our は N+1 桁以上取りうる。`bc_val` を文字列化して表示桁数を検出し、`our_val` を round/floor/ceil の 3 候補に変換して、いずれかが BC 値と完全一致すれば diff=0 で OK。

```python
bc_str = f"{bc_val:.10g}"
decimals = len(bc_str.split(".")[1]) if "." in bc_str else 0
unit = 10 ** decimals
candidates = {
    round(adj_val * unit) / unit,
    math.floor(adj_val * unit) / unit,
    math.ceil(adj_val * unit) / unit,
}
if any(abs(bc_val - c) < 1e-9 for c in candidates):
    diff = 0.0
else:
    diff = min(abs(bc_val - c) for c in candidates)
```

この仕組みで旧 `bc_floor` フラグ（手動で「BC 側は切り捨て表示」と指定）は実質不要化された（floor 候補が自動で含まれる）。旧 adapter の `bc_floor` 設定は後方互換で残しているが、新規設定は不要。

### unit_scale / yoy_offset の扱い

- `adj_val = our_val * unit_scale + field_yoy_offset`
- 適用結果は `yoy_offset` カラムに表示（例: `×100` / `+100` / `×100+100(floor)`）
- **自動 YoY+100 検出は廃止**（2026-04-18 改修以前は、`diff_yoy = |bc - (our+100)|` を自動試行して近い方を採用する補正があったが、偽 OK の温床だったため削除）

### pre-check banner

`compare_monthly_buffett.py` 実行時、まず全対象銘柄の adapter / structure.json 定義整合性をチェックしてバナー表示:

- 全 fields / bc_ignore / bc_key 明示 / bc_key 省略（暗黙照合）の件数
- **定義不備リスト**: adapter.fields[*].bc_key（または省略時の key）が structure.json.monthly_items[*].name に存在しない field を検出
- 不備詳細は `C:\tmp\bc_key_precheck.csv` に出力

banner のシグナルで、compare を走らせる前に adapter の bc_key 設定漏れを発見できる。

## 仕組み

### BC スクレイピング（`scrape_buffett_kpi`）

バフェットコード `https://www.buffett-code.com/company/{code}/kpi` の HTML テーブルを Selenium で直接パース。

**ページ構造:**
```
<thead>
  <tr><th>2026年</th><th>1月</th><th>2月</th>...<th>12月</th></tr>
</thead>
<tbody>
  <tr>
    <th>メトリクス名</th><td>値1</td><td>値2</td>...
  </tr>
  ...
</tbody>
```
- `<thead>` が年ごとに存在（複数）
- データ行の `<th>` がメトリクス名、`<td>` が月別値
- `kpi__table-spacer` クラスのセルはスキップ

### フィールドマッチング（`_keyword_score`）

GCS の English フィールド名と BC の日本語フィールド名を **キーワードスコア** でセマンティックマッチング。

`_FIELD_KEYWORDS` にフィールド名 → キーワードリストを定義し、BC フィールド名に含まれるキーワード数でスコアを計算。最高スコアの BC フィールドを対応として選択。

### 突合ロジック（`compare_records`）

1. GCS データの最新月（直近 3ヶ月）をスキャン
2. 対応する BC 値をキーワードスコアで探索
3. 数値差が `TOLERANCE=0.5` 以内 → OK、超過 → NG

## 初回実行結果（2026-03-25）

- **対象**: 204社
- **総フィールド数**: 1,601
- **✅ 一致 (diff≤0.5)**: 269件 (16.8%)
- **❌ 不一致**: 959件
- **⚠️ BC未取得**: 373件

### 完全一致率100%の銘柄

245A, 2685, 2305, 3181, 7359, 7674, 9759, 8255, 4177, 7422

### 主な不一致原因

| 原因 | 件数 | 説明 |
|------|-----|------|
| 年を値として抽出 | 273件 | `our_value=2026.0`（extract_adapter の regex が年テキストにマッチ） |
| 複数フィールドが同値 | 212組 | 1つの regex が複数フィールドにヒット → 全部同じ値になる |
| 単位スケール違い | 多数 | GCS:百万円 vs BC:億円 等 |
| フィールドマッチング誤り | 189件 | diff 0.5〜3.0（別指標を参照） |

## 改良ポイント（将来）

- `TOLERANCE` をフィールド種別で変える（整数値は 0、%は 0.5 等）
- `_FIELD_KEYWORDS` を拡充してマッチング精度向上
- `BC_NODATA` の件数を減らすためのキーワード追加
- GCS の extract_adapter バグ修正後に再実行して改善確認
- 定期実行化（月1回など）とCSV結果のBQ保存
