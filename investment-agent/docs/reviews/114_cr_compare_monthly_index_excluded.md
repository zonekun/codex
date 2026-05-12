# コードレビュー: compare_monthly_buffett.py — monthly_adapter_index excluded フィルタ追加

- 日時: 2026-05-08 17:46 JST
- 対象: `docs/plans/20260508_174200_compare_monthly_index_excluded.md`
- パターン: 2（_template_refactor.md フォーマット）+ 4（新規計画の内容妥当性）
- レビュアー: Claude (code-reviewer runbook)
- 対象コード: `scripts/compare_monthly_buffett.py`（commit d91989c）

---

## 【サマリー】

- 変更の要約: `compare_monthly_buffett.py` の全社モードで `monthly_adapter_index.csv` の `category=excluded` 銘柄を突合対象から除外するフィルタを追加する予防的改修
- 品質評価: **A** — 小規模・明確な改修。042知見MDの絶対ルールとの整合を取る正当な修正。軽微な改善点あり
- 主要リスク:
  1. `_load_excluded_tickers()` の `except Exception: return set()` がサイレント失敗し、CSVの列名変更や破損時にフィルタが効かなくなる
  2. `--tickers` 指定時にフィルタが適用されない設計判断は妥当だが、`--tickers` とINDEX excludedの意味的衝突がドキュメントされていない
  3. `pandas` の遅延importが2箇所に散在する構造が固定化される

## 【パターン2: 改修プラン評価】

### フォーマット適合性チェック

- [x] 冒頭に基準 commit hash（d91989c）が記載
- [x] 前提サマリで過去修正（`_excluded` L602-612、`bc_ignore` L444-445）と残件が明示
- [x] 優先度の定義（P0のみ）が冒頭にある
- [x] P0-1が7フィールド（症状/該当/根本原因/修正方針/呼び出し側波及/検証/ロールバック）を揃えている
- [x] 修正方針に before/after の両方が記載
- [x] 呼び出し側への波及が「無し」と明示（新規ヘルパーのため妥当）
- [x] アンチパターン対応表が末尾にある（該当なし明示）
- [x] 検証戦略が4段（smoke/dev/本番判断/回収）を網羅
- [x] ロールバック手順あり
- [ ] **軽微**: 関連commitリンク（d91989c）の説明「structure.json metrics[] に source 属性追加」は本プランの修正内容と直接関係がない。基準commit hashとしては正しいが、関連commitとしての記載は誤解を招く

**フォーマット違反**: なし

### 妥当性

**独立仮説**: `list_tickers_with_records()` はGCSに `monthly_records.json` が存在する全銘柄を返す。excluded銘柄のrecordがGCSに残存している場合、突合対象に含まれてしまう。これは042知見MD §INDEXによる除外の「全工程で一切処理しない」絶対ルールに違反する。

**方向性照合**: プランの修正方針は、`list_tickers_with_records()` の結果からINDEX excludedを除外するフィルタを挟むもの。根本原因に対して正しい方向の対処。`list_tickers_with_records()` 自体を変更せず呼び出し側でフィルタする設計も、他の呼び出し元への影響を避ける観点で妥当。

**対症療法チェック**: 該当しない。根本原因（INDEXフィルタの欠如）を直接補完する修正。

### 副作用・デグレードチェック

- [x] `_load_excluded_tickers()` は新規追加のモジュール内ヘルパーで、既存関数のシグネチャ変更なし。副作用リスクは極小
- [x] `--tickers` 指定時はフィルタ非適用。明示指定のユーザー意図を尊重する設計は `extract_monthly_data.py` L3291 の挙動（明示tickers時はINDEXフィルタ非適用）と一貫
- [x] `--limit` の適用順序: excluded除外後に `--limit` がかかる。これは正しい（excludedを数えてlimitを消費するのは無駄）
- [x] `print_precheck_banner(gcs, tickers)` はexcluded除外後の `tickers` リストを受け取る。excludedが除外された状態でprecheckが走るのは正常系（excludedのadapter定義を検証しても無意味）

### 抜け漏れ（類似観点での横展開含む）

- [ ] **`--local-records` モードとの組み合わせ**: `--local-records` はGCSでなくローカルからrecordsを読むが、ティッカーリスト決定は同一ロジック（L550-558）を通る。修正はこのパスにも適用されるため問題なし — 確認済み
- [ ] **`bc_csv_cache` のフィルタ**: L560-582でオフラインCSVを全ティッカー分読み込むが、excluded銘柄のデータも読み込まれる。ただしメインループ（L599以降）がexcluded除外後の `tickers` を回すため、読み込んだデータが使われることはない。メモリ効率は若干低下するが実害なし
- [ ] **042知見MDの「孤立データとして物理削除する」**: excluded銘柄のrecordがGCSに残存している場合、本修正は突合から除外するだけで物理削除は行わない。これはcompare_monthly_buffett.pyの責務外（削除は別途の運用タスク）なのでスコープ外で妥当

### 新規リスク

- 修正自体によるregressionリスクは極めて低い。`_load_excluded_tickers()` がCSV読み取り失敗時に `set()` を返すため、フィルタが効かないだけで既存動作にフォールバックする（安全側）

## 【重大な指摘】（即修正）

### #1 `_load_excluded_tickers()` の例外握り潰しでフィルタ無効化が検知不能

- 箇所: プランMD L93-95（提案コード `_load_excluded_tickers()` の `except Exception: return set()`）
- 事象: CSV列名変更（`category` → 別名）、CSVエンコーディング破損、pandas未インストール等の障害時に、例外を握り潰して空setを返す。結果としてexcludedフィルタが完全に無効化されるが、ログに何も出力されず検知不能
- トリガー: `monthly_adapter_index.csv` のスキーマ変更、ファイル破損、依存ライブラリ欠落
- 影響: 042知見MDの絶対ルール違反状態がサイレントに復活する
- 根拠: L602-612の既存 `_excluded` チェックも `except Exception: pass` で同じパターンだが、あちらは1銘柄単位の個別フォールバック。`_load_excluded_tickers()` は全銘柄に影響するため重大度が異なる
- 推奨対応: `except Exception` のブロック内で `log()` による警告出力を追加する。例: `log("⚠️ monthly_adapter_index.csv 読み込み失敗 — excluded フィルタ無効")`。`import structlog` が本スクリプトにない（独自 `log()` 関数を使用）点を考慮すると、既存の `log()` で出力するのが最小修正

## 【改善提案】（可読性・保守性）

### #1 `pandas` 遅延importの散在

- 箇所: `scripts/compare_monthly_buffett.py:L567`（既存）、プランMD L91（提案コード）
- 現状: 既存コードはL567で `import pandas as pd` を遅延importしている。プラン提案の `_load_excluded_tickers()` 内でもL91で `import pandas as pd` を遅延importする。同一モジュール内に遅延importが2箇所に散在する
- 提案: `_load_excluded_tickers()` はモジュールレベルの関数として定義されるため、モジュール先頭でのimportも選択肢。ただしオフラインモード以外（Seleniumモード）ではpandasが不要な可能性があるため、遅延importの判断自体は妥当。現状維持でも許容

### #2 `excluded_tickers` を `set` で保持する場合のログ出力

- 箇所: プランMD L74（提案コード `tickers = [t for t in tickers if t not in excluded_tickers]`）
- 現状: フィルタで除外された具体的な銘柄名がログに出ない（件数のみ）
- 提案: デバッグ時に「どの銘柄がexcluded扱いで除外されたか」を追跡できると有用。`log(f"  → index excluded: {sorted(excluded_set & set(tickers))}")` のような1行を追加すると運用時の透明性が向上する。ただし銘柄数が多い場合は冗長になるため、10社以下の場合のみ表示する条件付き出力が適切

## 【修正例】（必要な箇所のみ）

#### #1 に対する修正案

```python
# before: プランMD提案の _load_excluded_tickers()
def _load_excluded_tickers() -> set[str]:
    """monthly_adapter_index.csv から category=excluded のティッカー集合を返す。"""
    if not INDEX_CSV.exists():
        return set()
    try:
        import pandas as pd
        df = pd.read_csv(INDEX_CSV, dtype=str, encoding="utf-8-sig")
        return set(df.loc[df["category"] == "excluded", "ticker"].tolist())
    except Exception:
        return set()

# after
def _load_excluded_tickers() -> set[str]:
    """monthly_adapter_index.csv から category=excluded のティッカー集合を返す。"""
    if not INDEX_CSV.exists():
        log(f"⚠️ {INDEX_CSV} が存在しません — index excluded フィルタ無効")
        return set()
    try:
        import pandas as pd
        df = pd.read_csv(INDEX_CSV, dtype=str, encoding="utf-8-sig")
        return set(df.loc[df["category"] == "excluded", "ticker"].tolist())
    except Exception as e:
        log(f"⚠️ monthly_adapter_index.csv 読み込み失敗 — index excluded フィルタ無効: {e}")
        return set()
```

## 【確認できなかった事項】

- `monthly_adapter_index.csv` にexcluded銘柄が何社含まれているか（実データ件数）。実行して確認する必要がある
- excluded銘柄のGCS record残存状況（物理削除が完了しているなら本修正の実効果はゼロだが、防御コードとしての価値は変わらない）
