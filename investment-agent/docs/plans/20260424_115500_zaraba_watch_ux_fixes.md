# ザラバツール UX 改善（列幅変更・進捗表示・NaN バグ修正）

**作成日時**: 2026-04-24 11:55 JST
**対象ファイル**: `scripts/zaraba_earnings.py`（1673 行、commit `e633ed2` 時点）
**対象読者**: code-reviewer サブエージェント / 次セッション担当
**目的**: ザラバ決算モニターの表示改善 4 件。スコアリングロジック変更なし。

> **分類**: (c) 一過性型 — 4 件すべて UI/UX 改善のみ。完了後アーカイブ可。

---

## 前提サマリ

- 過去修正: `ee41e6d` で F16 観察用フィールド追加済み
- 残存: 本プランで扱う 4 件
- 実機検証: Linux VM (`~/project/claude/investment-agent/`) でバグ再現済み
- 関連 incident: watch 起動時に `TypeError: 'float' object is not subscriptable` でクラッシュ

---

## 優先度の定義

- **P0**: watch が起動できない（クラッシュ）
- **P1**: 表示・操作性の改善（ブロッカーではない）

---

## 指摘項目

### P0-1. watch の `_build_table` で name が float (NaN) のとき TypeError 🚨

**症状**: `results.csv` から reload した行で `name` が NaN (float) → `[:WATCH_TABLE_WIDTH_NAME]` スライスで `TypeError: 'float' object is not subscriptable` → watch クラッシュ。

**該当**: `scripts/zaraba_earnings.py:L1157` / `_build_table()`

```python:L1157
(r.get("name", "") or "")[:WATCH_TABLE_WIDTH_NAME],
```

**根本原因**: `pd.read_csv` で reload すると、空文字列 `""` が `NaN` (float) に変換される。`or ""` は `NaN` を falsy 扱いしないため float のままスライスに渡る。

**修正方針**: `str()` で明示的に文字列化してから NaN を除去する。

```python
# before
(r.get("name", "") or "")[:WATCH_TABLE_WIDTH_NAME],

# after
_s(r.get("name"))[:WATCH_TABLE_WIDTH_NAME],
```

既存ヘルパー `_s()` を確認。無ければ以下を追加:

```python
def _s(v) -> str:
    if v is None or (isinstance(v, float) and pd.isna(v)):
        return ""
    return str(v)
```

**呼び出し側への波及**: 無し（`_build_table` 内の表示コードのみ）

**検証**: 
1. `results.csv` の `name` 列に NaN を含む行を作成
2. `watch` 起動 → クラッシュしないこと
3. NaN 行が空文字で表示されること

**ロールバック**: コミット revert で済む。データ影響なし。

---

### P1-1. 列幅定数をファイル先頭に移動 ⚠️

**症状**: 列幅定数 (`WATCH_TABLE_WIDTH_*`) がファイル中盤の定数ブロック（L61-68）にあり、エディタで探しにくい。

**該当**: `scripts/zaraba_earnings.py:L61-68`

```python:L61-68
WATCH_TABLE_WIDTH_SCORE = 5
WATCH_TABLE_WIDTH_CODE = 5
WATCH_TABLE_WIDTH_NAME = 12
WATCH_TABLE_WIDTH_CAP = 6
WATCH_TABLE_WIDTH_JUDGE = 6
WATCH_TABLE_WIDTH_POS = 28
WATCH_TABLE_WIDTH_NEG = 22
```

**修正方針**: import 文直後、他の定数よりも前（L48 `JST = ...` の前）に移動。ユーザーが最初に見える位置にする。値も以下に変更:

```python
WATCH_TABLE_WIDTH_SCORE = 8
WATCH_TABLE_WIDTH_CODE = 8
WATCH_TABLE_WIDTH_NAME = 12
WATCH_TABLE_WIDTH_CAP = 8
WATCH_TABLE_WIDTH_JUDGE = 6
WATCH_TABLE_WIDTH_POS = 28
WATCH_TABLE_WIDTH_NEG = 22
```

変更値: SCORE 5→8、CODE 5→8、CAP 6→8。NAME/JUDGE/POS/NEG は据え置き。

**呼び出し側への波及**: 無し（定数の参照箇所は変わらない。移動のみ）

**検証**: `watch` / `review` で表示幅が変わっていること。

**ロールバック**: コミット revert。

---

### P1-2. `_build_prior_data` に進捗ログ追加 ⚠️

**症状**: `_build_prior_data` は `target=all` で 4000+ 銘柄をループするが、ログ出力がゼロ。beta_20d_loaded → 数分の沈黙 → master_all 取得、となり「固まった？」と見える。

**該当**: `scripts/zaraba_earnings.py:L624-803` / `_build_prior_data()`

```python:L624-626
prior: dict = {}
for ticker in tickers:
    info: dict = {"ticker": ticker}
```

**修正方針**: ループ内で N 件ごと（例: 500 件ごと）に進捗ログを出す。

```python
# after
total = len(tickers)
for i, ticker in enumerate(tickers):
    info: dict = {"ticker": ticker}
    if (i + 1) % 500 == 0 or (i + 1) == total:
        log.info("build_prior_progress", done=i + 1, total=total)
    ...
```

**呼び出し側への波及**: 無し

**検証**: `prepare --target all` 実行 → 500 件ごとに `build_prior_progress done=500 total=4159` 等が出力されること。

**ロールバック**: コミット revert。

---

### P1-3. `_print_prepare_summary` の銘柄一覧を件数のみに変更 ⚠️

**症状**: `_print_prepare_summary` が `target=all` 時に最大 200 銘柄を一覧表示する。4000+ 銘柄中 200 件を表示しても有用性が低く、端末を埋め尽くす。

**該当**: `scripts/zaraba_earnings.py:L818-854` / `_print_prepare_summary()`

```python:L822-854
display_limit = 200
display_items = items[:display_limit]
print()
print(f"=== {d} ザラバ決算 事前サマリー（{len(items)}銘柄） ===")
...
for item in display_items:
    ...
    print(f"{t:<7} | {code:<5} | ...")
```

**修正方針**: `target=all` 時は銘柄一覧ループを省略し、件数のみ表示する。`target=scheduled` 時は従来通り一覧表示（予定銘柄は数十件なので有用）。

判定方法: 引数に `target` を追加するか、`len(items)` が閾値（例: 100）を超えたら件数のみにする。後者のほうがシンプル。

```python
# after
SUMMARY_DETAIL_LIMIT = 100

if len(items) <= SUMMARY_DETAIL_LIMIT:
    # 従来通り一覧表示
    for item in items:
        ...
else:
    print(f"（{len(items)}銘柄のキャッシュを作成。一覧表示は{SUMMARY_DETAIL_LIMIT}銘柄以下の場合のみ）")
```

**呼び出し側への波及**: `_print_prepare_summary` を呼ぶ箇所のシグネチャが変わる場合は要確認。閾値方式なら不要。

**検証**:
1. `prepare --target all` → 件数のみ表示
2. `prepare --target scheduled` → 従来通り一覧表示

**ロールバック**: コミット revert。

---

## 対応アンチパターン

| plan ID | 004 | T-x | G-x |
|---|---|---|---|
| P0-1 | — | — | — |
| P1-1 | — | — | — |
| P1-2 | — | — | — |
| P1-3 | — | — | — |

> 該当アンチパターンなし（UI/UX 改善のため）

---

## 検証戦略

1. **smoke test**: `py_compile scripts/zaraba_earnings.py` → 構文 OK
2. **dev 実機**: Linux VM で `prepare --target all --force` → 進捗ログ確認 → `watch` 起動 → NaN 行でクラッシュしないこと
3. **本番適用判断基準**: smoke + dev 両方 PASS
4. **回収手順**: コミット revert で即復旧。データ・スコアへの影響なし

---

## 関連ドキュメント

- 知見 MD: `docs/knowledges/tools/066_zaraba_tool.md`
- 関連 commit: `ee41e6d` — F16 観察用フィールド追加
- Linux VM バグ再現ログ: ユーザー報告（本プラン冒頭の Traceback）
