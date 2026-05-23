# コードレビュー: JQuantsBQClient / screen_vcp_jp.py 新規作成

- 日時: 2026-05-21 23:11 JST
- 対象: `scripts/tob_prediction/jquants_bq_client.py`, `scripts/tob_prediction/screen_vcp_jp.py`
- パターン: 1 (新規)
- レビュアー: Claude (code-reviewer runbook)

---

## 【サマリー】

- 変更の要約: FMPClient 互換の J-Quants/BQ ラッパー `JQuantsBQClient` と、VCP スクリーナーを TSE 向けに移植した `screen_vcp_jp.py` の新規作成。データ取得を BQ に差し替え、計算ロジックはオリジナルをそのまま利用する設計。
- 品質評価: B — インターフェース整合性・基本ロジックは概ね正しいが、CLAUDE.md 規約違反（datetime/print）が screen_vcp_jp.py に複数あり、`C:/tmp` ハードコードによる移植性の問題が残る
- 主要リスク:
  1. `screen_vcp_jp.py` の `datetime.now()` / `datetime.now().strftime()` がタイムゾーン非指定（CLAUDE.md §7 違反）。JST 以外の環境でレポートタイムスタンプがずれる
  2. `C:/tmp/claude-trading-skills` の絶対パスハードコード。他端末・CI では即 `sys.exit(1)` で動作不能
  3. `JQuantsBQClient` の OHLCV フェッチ期間 (`fetch_days=400` カレンダー日) が過去日指定バックテスト時に 52 週（260 取引日）を下回るケースがある

---

## 【重大な指摘】

### #1 `datetime.now()` タイムゾーン非指定 — CLAUDE.md §7 違反

- 箇所: `scripts/tob_prediction/screen_vcp_jp.py:782,789`
- 事象: `datetime.now().strftime(...)` がローカル naive datetime を返す。`metadata["generated_at"]` および出力ファイル名タイムスタンプに UTC+0 や JST 以外の時刻が入る
- トリガー: Windows のタイムゾーン設定が JST 以外の環境（CI/Cloud Run、WSL UTC 設定等）で実行した場合
- 影響: 出力 JSON/MD のタイムスタンプが実時刻と最大 ±数時間ずれる。ファイル名が意図しない日付になり上書き衝突を引き起こす可能性
- 根拠: CLAUDE.md §7「タイムゾーン非明示の日時取得・表示・出力は禁止（`datetime.now()` 等）。JST 指定必須」
- 推奨対応 [検証済み]: `JST = timezone(timedelta(hours=9), "JST")` は `jquants_bq_client.py` の L51 に定義済み。`screen_vcp_jp.py` でも同様に定義し `datetime.now(tz=JST)` を使用する。`from datetime import timezone, timedelta` は L46 で既にインポートしている

### #2 `C:/tmp/claude-trading-skills` 絶対パスハードコード — 移植性ゼロ

- 箇所: `scripts/tob_prediction/screen_vcp_jp.py:52`
- 事象: `_VCP_SKILL_DIR = Path("C:/tmp/claude-trading-skills/skills/vcp-screener/scripts")` がハードコード。パスが存在しない場合は L54-60 で `sys.exit(1)` する
- トリガー: 別端末・別ユーザー・Cloud Run / CI 環境で実行した場合
- 影響: スクリプトが即時終了。エラーメッセージで `git clone` コマンドを案内するが、CI パイプラインや別端末では自動対処不可
- 根拠: CLAUDE.md §6「venvパスはMD直書き禁止。`.claude.local.md` の参照を使う」の精神と同様、環境固有パスのハードコードはプロジェクト規約に反する
- 推奨対応 [方向性]: 環境変数 `VCP_SKILL_DIR`（フォールバックとして `C:/tmp/...`）、または `.claude.local.md` 的な設定ファイル経由でパスを注入する設計が望ましい。最低限 `README` / `docs/knowledges/` の知見 MD にセットアップ手順を明記すること

### #3 バックテスト時の 52 週データ不足リスク

- 箇所: `scripts/tob_prediction/jquants_bq_client.py:79,243`
- 事象: `fetch_days=400` カレンダー日 ≈ 280 取引日でフェッチ。`get_batch_quotes` 内で `grp.tail(YEAR_TRADING_DAYS)` (260 取引日) を使うため、銘柄数が多い日でも最低 20 取引日は切り捨てられる。問題は `date_to` を過去日（例: 6ヶ月前）に指定しつつ `fetch_days` をデフォルト 400 のまま使うと、フェッチ範囲全体が 400 カレンダー日（約 280 取引日）しかなく、52 週高値・安値が正確に計算できなくなること
- トリガー: `--date` オプションで 1 年以上前の日付を指定して `screen_vcp_jp.py` を実行した場合（バックテスト的利用）
- 影響: `yearHigh` / `yearLow` が過小評価され、pre-filter（52 週安値から 20% 以上 / 52 週高値から 30% 以内）の通過銘柄が意図せず変動する
- 根拠: `jquants_bq_client.py:79` `fetch_days: int = FETCH_DAYS` (= 400)、`jquants_bq_client.py:243` `grp.tail(YEAR_TRADING_DAYS)` (= 260 取引日)
- 推奨対応 [方向性]: `date_to` が今日から 1 年以上前の場合に `fetch_days` を自動的に増やすか、docstring に「バックテスト時は `fetch_days` を調整すること」と明記する

---

## 【改善提案】

### #1 `get_sp500_constituents` で `iterrows` が二重実行

- 箇所: `scripts/tob_prediction/jquants_bq_client.py:168,196`
- 現状: `_name_sector_maps()` でキャッシュ済みの `name_map/sector_map` を構築した後、`get_sp500_constituents` でも `self._master.iterrows()` を再走して `result` を構築している。`name_map.get(t, t)` / `sector_map.get(t, "Unknown")` の値は `_name_sector_maps` で既に計算済み
- 提案: `get_sp500_constituents` の `for _, row in self._master.iterrows()` ループを `name_map, sector_map = self._name_sector_maps()` の結果から構築するよう整理。TSE 銘柄数（約 3,800）では実害は小さいが、読みやすさの観点で冗長

### #2 `_name_sector_maps` のキャッシュキーが `"_name_sector"` で公開 API と区別がつかない

- 箇所: `scripts/tob_prediction/jquants_bq_client.py:162`
- 現状: `self._cache["_name_sector"]` と `self._cache["tse_constituents"]` が同一 dict に格納される。`get_api_stats` の `cache_entries` が混在カウントになる
- 提案: `_internal_cache` と `_public_cache` を分離するか、内部用キーに `_` プレフィックスを付ける（`"_internal_name_sector"` 等）。現行でも動作に影響はないが意図の明確化に有効

### #3 `screen_vcp_jp.py` の `print()` 多用

- 箇所: `scripts/tob_prediction/screen_vcp_jp.py` 全体（L544, L552, L554, L560-611 等、数十箇所）
- 現状: CLAUDE.md §7「print 禁止。structlog を使用」に違反。ただし docstring に「オリジナルをそのまま利用する」と明記されており、意図的な移植維持の可能性がある
- 提案: 「移植ファイルは元の `print` を維持する」旨を docstring または `# noqa` コメントで明示し、レビュアー・後任開発者に意図を伝える。将来的に structlog に統一するなら計画 MD を起票すること

### #4 `fetch_ohlcv` への `force_reload` 引数が `JQuantsBQClient` から渡せない

- 箇所: `scripts/tob_prediction/jquants_bq_client.py:103`
- 現状: `fetch_ohlcv(self._date_from, self._date_to)` — `force_reload=False` 固定。キャッシュが古いデータを返しても `JQuantsBQClient` ユーザーはリフレッシュできない
- 提案: `JQuantsBQClient.__init__` に `force_reload: bool = False` を追加し `fetch_ohlcv` / `fetch_topix` に引き渡す。`screen_vcp_jp.py` 側から `--force-reload` フラグを追加すると `screen_tob_insider.py` の操作感と統一できる

### #5 `get_historical_prices` のキャッシュキーが `days` 単位で分かれる

- 箇所: `scripts/tob_prediction/jquants_bq_client.py:286`
- 現状: `cache_key = f"hist_{symbol}_{days}"` — `get_historical_prices("8141", days=260)` と `get_historical_prices("8141", days=100)` が別エントリになる。`days=100` は `days=260` の tail にすぎないのにデータを二重保持する
- 提案: キャッシュキーを `symbol` のみにして全履歴を保持し、`get_historical_prices` 内で `.tail(days)` するとメモリ効率が良い。現行の screen_vcp_jp.py では `days=260` 固定なので実害はなし（提案のみ）

---

## 【修正例】

#### #1 に対する修正案（screen_vcp_jp.py の datetime.now()）

```python
# before: screen_vcp_jp.py:782
timestamp = datetime.now().strftime("%Y-%m-%d_%H%M%S")

# after
from datetime import timezone, timedelta
_JST = timezone(timedelta(hours=9), "JST")
timestamp = datetime.now(tz=_JST).strftime("%Y-%m-%d_%H%M%S")
```

```python
# before: screen_vcp_jp.py:789
"generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),

# after
"generated_at": datetime.now(tz=_JST).strftime("%Y-%m-%d %H:%M:%S JST"),
```

---

## 【確認できなかった事項】

1. `C:/tmp/claude-trading-skills/skills/vcp-screener/scripts/` 内の各 `calculators/*.py` / `scorer.py` / `report_generator.py` の `generate_json_report` / `generate_markdown_report` シグネチャ。`screen_vcp_jp.py:817-818` の `all_results=results` キーワード引数が互換元と一致するかは本レビューでは未検証
2. `_get_bq()` が singleton であるため、`screen_tob_insider` を先に `import` した後に `JQuantsBQClient` を初期化すると同一 `bigquery.Client` インスタンスを共有する。マルチスレッド利用時のスレッド安全性は `bigquery.Client` 公式ドキュメント依存で未検証（単一スレッドの CLI 用途なら問題なし）
3. `fetch_ohlcv` の parquet キャッシュパスが `C:/tmp/tob_insider_screener/ohlcv_{date_from}_{date_to}.parquet` — `date_to` が異なるたびに別ファイルが生成され C:/tmp を圧迫する可能性。クリーンアップ方針の記述が知見 MD に存在するか未確認
