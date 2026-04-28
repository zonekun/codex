# IFIS直取得コンセンサス追加 Codex実装プラン

**作成日時**: 2026-04-27 22:06 JST  
**作成者**: Codex  
**対象リポジトリ**: `C:\Users\zonekun\Documents\codex\investment-agent`  
**対象ブランチ**: `codex/integration`  

---

## 目的

IFIS株予報ページを直接スクレイピングし、経常利益コンセンサスを `STOCK.CONSENSUS` に保存する新規スクリプトを作成する。

既存の楽天証券経由コンセンサス取得とは別レコードとして保存し、`SOURCE='IFIS'` で識別する。

---

## 作業分担

### Codexが実装する範囲

- 新規 `scripts/update_conse_ifis.py` の作成のみ
- Codex側 Git 作業ツリーへ反映
  - repository: `C:\Users\zonekun\Documents\codex\investment-agent`
  - branch: `codex/integration`

### Codexが触らない範囲

- `scripts/update_conse_rakuten.py`
  - Claude Code側で修正中のため、Codexは変更しない
  - 今後のinsertに `SOURCE='RAKU'` を入れる必要があることだけ伝える
- `scripts/zaraba_earnings.py`
- `scripts/earnings_model/batch_rerun_predict.py`
- `scripts/export_consensus_csv.py`
- BigQueryテーブル事前更新
- `data_catalog.md` / 知見MD / Claude Code側プランMD更新

### Claude Code側に任せる範囲

- `STOCK.CONSENSUS` への `SOURCE` カラム追加
- 既存レコードの `SOURCE='RAKU'` 埋め戻し
- `update_conse_rakuten.py` の `SOURCE='RAKU'` 対応
- 下流クエリの `SOURCE` 混入リスクへの対応方針決定と実装
- ドキュメント更新
- 実行・本番反映判断

---

## BigQuery前提

Codex実装は、以下のテーブル更新がClaude Code側で事前に完了している前提で書く。

```sql
ALTER TABLE `gmailpj-357912.STOCK.CONSENSUS`
ADD COLUMN SOURCE STRING;
```

既存楽天版レコードの埋め戻しはClaude Code側で実施する。

```sql
UPDATE `gmailpj-357912.STOCK.CONSENSUS`
SET SOURCE = 'RAKU'
WHERE SOURCE IS NULL;
```

`SOURCE` 値:

- `RAKU`: 既存楽天証券経由
- `IFIS`: 新規IFIS直取得

`RAW_SOURCE_URL` / `SCRAPED_AT` は追加しない。取得日は既存 `DATAAT` で足りる。

---

## 新規スクリプト

```text
scripts/update_conse_ifis.py
```

既存 `scripts/update_conse_rakuten.py` の運用形に寄せる。

### 実装方針

- IFISページ取得は `requests + BeautifulSoup`
- Seleniumは使わない
- BQ書き込みは既存RAKUTEN版と同じく `insert_rows_json`
- 冪等性、staging table、MERGE/DELETE+INSERT は実装しない
- `SOURCE='IFIS'` を必ずinsert行に含める
- `SOURCE='RAKU'` レコードと混同しない
- 再開仕様はRAKUTEN版に合わせる
- CSV出力あり

### URL

```text
https://kabuyoho.ifis.co.jp/index.php?action=tp1&sa=report&bcode={ticker}
```

---

## CLI案

RAKUTEN版と同じ雰囲気で、個別確認用の `--ticker` と新規実行用 `--fresh` を持つ。

```bash
PYTHONUTF8=1 python scripts/update_conse_ifis.py --ticker 6723 --dry-run
PYTHONUTF8=1 python scripts/update_conse_ifis.py --ticker 6723
PYTHONUTF8=1 python scripts/update_conse_ifis.py --fresh
PYTHONUTF8=1 python scripts/update_conse_ifis.py
```

`--dry-run` はBQ insertもCSV追記もしない。

---

## 再開仕様

RAKUTEN版に合わせる。ただしファイル名は衝突させない。

状態ファイル:

```text
data/logs/conse_ifis_resume.json
```

形式:

```json
{"dataat": "2026-04-27", "last_ticker": "7203"}
```

仕様:

- 起動時に状態ファイルがあれば `last_ticker` の次から再開
- `DATAAT` は最初の実行日で固定
- `--fresh` で状態ファイルを無視して新規実行
- 完了時に状態ファイルを削除

---

## CSV出力

IFIS版もCSVを作る。RAKUTEN版とは別ファイルにする。

```text
C:\Users\zonekun\Dropbox\stock\py\conse_ifis.csv
```

列はRAKUTEN版CSVに合わせる。

```text
TICKER,1Q_CURRENT,2Q_CURRENT,3Q_CURRENT,FY_CURRENT,FY_NEXT
```

IFIS直版は当面 `TARGET='CURRENT'` のみなので、`FY_NEXT` は空欄。

---

## IFIS抽出仕様

### FY

IFISページ `.prog_quarter` 内の「今期 YYYYMM」から取得する。

例:

```html
<th class="none_left em_red">
  今期 202612<br>
  進ちょく率
</th>
```

抽出方針:

- `.prog_quarter` 配下の `th` を走査
- テキストを `unicodedata.normalize("NFKC", text)` で正規化
- `今期\s*(\d{6})` で抽出
- 月が `01`〜`12` の場合のみ採用
- 取得できない場合は `FY='000000'` を使わず、その銘柄をスキップ

### コンセンサス

VBA `Sub setValueB` は `.prog_quarter` の `rn=2` をコンセンサスとして読んでいた。

Python版では `rows[2]` 固定にせず、行ヘッダ `th` の正規化テキストに `コンセンサス予想` を含む `tr` を探す。

取得対象:

- `1Q`
- `2Q`
- `3Q`
- `FY`

保存値:

- `PROFIT` は既存 `STOCK.CONSENSUS` と同じく累計値のまま保存
- 単独Q変換はしない

---

## BQ insert行

```python
{
    "DATAAT": dataat,
    "TICKER": code,
    "FY": fy,
    "QUARTER": quarter,
    "PROFIT": profit,
    "TARGET": "CURRENT",
    "SOURCE": "IFIS",
}
```

IFIS版では `FY='000000'` を使わない。

---

## skip / ログ

以下はinsertせずスキップする。

- HTTP取得失敗
- `.prog_quarter` が見つからない
- `FY` が抽出できない
- `コンセンサス予想` 行が見つからない
- 数値セルが全て空

ログには ticker と skip reason を出す。

---

## Claude Code側への注意ポイント

### テーブル更新

Codex実装は `SOURCE` カラムが存在する前提。未追加の場合、BQ insertは失敗する。

### RAKUTEN版

`scripts/update_conse_rakuten.py` はCodexでは触らない。Claude Code側で修正中。

今後のinsertには `SOURCE='RAKU'` が必要。

### 下流クエリ

同じ `STOCK.CONSENSUS` に `RAKU` と `IFIS` が共存するため、`SOURCE` を見ない既存クエリでは混入リスクがある。

注意対象としてClaude Code側へ伝える。

- `scripts/zaraba_earnings.py`
- `scripts/earnings_model/batch_rerun_predict.py`
- `scripts/export_consensus_csv.py`

具体的な取り込み方針・修正範囲・実装判断はClaude Code側に任せる。

### requests不発リスク

IFIS直ページはまず `requests + BeautifulSoup` で実装する。

ただし以下の可能性がある。

- User-Agent制限
- bot対策
- 銘柄ごとのHTML構造差分
- JS描画への変更

不発の場合はClaude Code側で Selenium fallback の要否を判断する。

---

## 実装後にCodexが伝言メモへ残す内容

- Codex側リポジトリ・ブランチ
- 新規作成ファイルパス
- Claude Code側ファイルを直編集していないこと
- BQテーブル事前更新は未実施であること
- `update_conse_rakuten.py` は未変更であること
- 下流クエリは未変更で、混入リスク注意をClaude Codeへ渡すこと
- smoke結果
  - `--ticker 6723 --dry-run`
  - 可能なら `requests` 取得可否

