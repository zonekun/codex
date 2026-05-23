# コードレビュー: tdnet_download.py 前日分取りこぼし改修

- 日時: 2026-05-15 21:44 JST
- 対象: `scripts/tdnet_download.py`（`_resolve_dates()` の変更）、`docs/knowledges/tools/003_tdnet_download.md`（知見MD更新）
- パターン: 1 (新規レビュー)
- レビュアー: Claude (code-reviewer runbook)

---

## 【サマリー】

- 変更の要約: `DATE_MODE="t"` を当日のみ `(today, today)` から前日〜当日 `(yesterday, today)` に変更し、20:03以降開示や土日開示の取りこぼしを防止。GCS既存ファイルスキップにより冪等性を担保。知見MD・コメントも同期更新。
- 品質評価: **B** — 修正方針は正しく冪等性も確保されているが、Cloud Run環境でのタイムゾーン問題（`date.today()` がUTCを返す）が未対処のまま残っている
- 主要リスク:
  1. `date.today()` がCloud Run（UTC環境）でJST日付と最大9時間ずれ、23:50 JST実行時に「翌日」を返す
  2. 知見MD内のコードブロック例が旧記述のままで実コードと不整合
  3. `004_coding_conventions.md` の DATE_MODE 実例も旧記述のまま

## 【重大な指摘】（即修正）

### #1 `date.today()` がCloud Run環境でUTC日付を返す

- 箇所: `scripts/tdnet_download.py:688`
- 事象: Cloud Run JobsはデフォルトでシステムタイムゾーンがUTC。`date.today()` はシステムローカル時刻の日付を返すため、Cloud Run上では UTC日付が返る。23:50 JST（= 14:50 UTC）の実行では `date.today()` は UTC の当日日付を返すので問題ないが、**日本時間の 00:00〜08:59 に手動実行した場合**、UTCではまだ前日であるため `date.today()` が JST基準で1日ずれる
- トリガー: Cloud Run Job を JST 00:00〜08:59 の間に手動実行（`gcloud run jobs execute tdnet-download`）した場合
- 影響: 取得対象日が意図より1日ずれ、当日分を取りこぼす可能性がある。また `yesterday` が JST基準の前日ではなくUTC基準の前日になるため、意図した2日間とずれる
- 根拠: `docker/Dockerfile.tdnet` に `ENV TZ=Asia/Tokyo` が設定されていない。`date.today()` はタイムゾーン引数を取らず、システムローカル時刻に依存する。CLAUDE.md §7 および `004_coding_conventions.md` §日付時刻ルール は `datetime.now()` を禁止し `datetime.now(tz=ZoneInfo('Asia/Tokyo'))` を義務付けているが、`date.today()` も同様にタイムゾーン非対応であり同じ問題を持つ
- 推奨対応: **[方向性]** `date.today()` を `datetime.now(tz=JST).date()` に置き換える。既にファイル冒頭で `JST = timezone(timedelta(hours=+9), "JST")` が定義されているのでそのまま使える。あるいは Dockerfile に `ENV TZ=Asia/Tokyo` を追加する方法もあるが、コード側での明示的なJST指定の方がプロジェクト規約に沿う

### #2 知見MD内コードブロックの DATE_MODE コメントが旧記述のまま

- 箇所: `docs/knowledges/tools/003_tdnet_download.md:45`
- 事象: テーブル（L56）は `前日〜当日の2日分を取得` に更新されているが、直上のコードブロック例（L45）は `# "t"=今日` のまま。知見MDを参照してコードを書き換える際、コードブロックとテーブルの記述が矛盾している
- トリガー: 知見MDのコードブロックを参考にして設定を行う場合
- 影響: AI・人間の双方が混乱し、DATE_MODE の意味を誤解する可能性がある
- 根拠: diff で確認。L45 の `# "t"=今日` は変更されていない
- 推奨対応: **[検証済み]** `# "t"=今日` を `# "t"=前日〜今日` に修正する

## 【改善提案】（可読性・保守性）

### #1 `004_coding_conventions.md` の DATE_MODE 実例も更新すべき

- 箇所: `docs/knowledges/tools/004_coding_conventions.md:31`
- 現状: `DATE_MODE = "t"        # "t"=今日 / "1"=特定の1日 / "r"=期間` と記載されており、改修後の実コード（L64: `# "t"=前日〜今日`）と不整合
- 提案: `# "t"=前日〜今日 / "1"=特定の1日 / "r"=期間` に更新する

### #2 index CSV・再開ログのファイル名が日付範囲依存で毎日変わる

- 箇所: `scripts/tdnet_download.py:899`, `scripts/tdnet_download.py:628`
- 現状: `index_{date_from}_{date_to}.csv` と `_resume_{date_from}_{date_to}.txt` は `date_from`/`date_to` を含むため、2日分取得に変更したことで毎日異なるファイル名（例: `index_20260514_20260515.csv`）になる。これは従来の1日分モード（`index_20260515_20260515.csv`）とは命名パターンが異なる
- 提案: 機能上は問題ない（各実行が独立した index を出力するため）。ただし、GCS上に日付ペアが異なる index が蓄積される点は認識しておくべき。下流で index CSV を日付パターンで glob する処理があれば影響する可能性がある

### #3 金曜23:50実行で土日2日分が取れない可能性

- 箇所: `scripts/tdnet_download.py:691-692`
- 現状: 金曜23:50 JST実行時は `(木曜, 金曜)` の2日分を取得する。土曜・日曜の開示は翌月曜23:50実行時に `(日曜, 月曜)` で取得されるため、**土曜の開示は月曜実行ではカバーされない**。ただしTDnetは休日にページを公開しないため、実際に土曜に開示が存在するケースは極めて稀（例外: 緊急開示）。既存のGCSスキップにより、仮にバックフィルで補完しても重複は発生しない
- 提案: 現状の2日分で実用上は十分と判断する。万全を期すなら月曜実行時のみ3日分（金〜月）にする案もあるが、費用対効果を考えると現実装で問題ない

## 【修正例】（必要な箇所のみ）

#### #1 に対する修正案

```python
# before: scripts/tdnet_download.py:688
    today = date.today()

# after
    today = datetime.now(JST).date()
```

`datetime` と `JST` は既にインポート・定義済み（L42, L44）のため追加のインポートは不要。

#### #2 に対する修正案

```markdown
# before: docs/knowledges/tools/003_tdnet_download.md:45
DATE_MODE   = "t"          # "t"=今日 / "1"=特定の1日 / "r"=期間

# after
DATE_MODE   = "t"          # "t"=前日〜今日 / "1"=特定の1日 / "r"=期間
```

## 【確認できなかった事項】

- Cloud Run Job `tdnet-download` の環境変数設定（`TZ` が設定されているか否か）。`gcloud run jobs describe` で確認が必要。Dockerfile には `TZ` 設定がないが、Job 作成時に `--set-env-vars TZ=Asia/Tokyo` で設定されている可能性は排除できない
- 下流パイプライン（tdnet-load 等）が index CSV のファイル名パターンに依存しているかどうか。依存している場合、2日分命名への変更で不具合が出る可能性がある
- `date.today()` を使っている他のCloud Run Jobスクリプト（`is_holiday.py`, `edinet_xbrl_extractor.py` 等）でも同じTZ問題が潜在しているか。本改修のスコープ外だが、横展開の検討は必要

---

## 返却 2026-05-15

- 重大#1: [採用] date.today() → datetime.now(JST).date() に修正
- 改善#1: [採用] 003 MD コードブロック修正
- 改善#2: [採用] 004 MD DATE_MODE 実例修正
- 改善#3(index CSV命名): [見送り: 機能上問題なし。認識のみ]
- 改善#3(金曜土日カバー): [見送り: 実害ほぼなし。費用対効果低]
