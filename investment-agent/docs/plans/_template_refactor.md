# <改修の短いタイトル>

**作成日時**: YYYY-MM-DD HH:MM JST
**対象ファイル**: `scripts/foo/bar.py`（N 行、commit <hash> 時点）
**対象読者**: code-reviewer サブエージェント / 次セッション担当
**目的**: <1-2文で改修の主旨。スコープと非スコープを明示すると尚良い>

> **使い方**: このファイルをコピーして `docs/plans/<category>-<number>_<slug>_YYYYMMDD_HHMMSS.md` にリネームし、`<...>` を埋める。テンプレ本体（このファイル）は編集しない。
> **フォーマット正本**: `skills/planning.md` §改修プラン / バグ修正指示書 MD フォーマット
> **分類ルール**: 同 §プラン分類 ((a) 恒久知見型 / (b) 継続改修型 / (c) 一過性型)

---

## 前提サマリ

- 過去修正: <commit hash> で N 件修正済み（<内訳。例: A-1/A-3/B-1 系>）
- 残存: <このプランで扱う件数>
- 実機検証の有無: <dev / prod / 未検証>
- 関連 incident: <あれば 1 行要約 + ログ/PR リンク>

---

## 優先度の定義

- **P0**: <ブロッカー条件。例: scheduler RESUME 前必須、データ破壊・セキュリティ・SLO 違反>
- **P1**: <次のリリース/バックフィル前に消化>
- **P2**: <余力で、ブロッカーではない>

---

## 指摘項目

### P0-1. <短いタイトル> 🚨

**症状**: <何が起きるか。観測事実で書く。「〜の可能性」ではなく「〜している」>

**該当**: `path/to/file.py:L123-L140` / `function_name()`

```python:L123-L140
<実コードの抜粋（3-10 行）>
```

**根本原因**: <なぜ発生するか。`docs/knowledges/tools/004_coding_conventions.md` §バッチジョブ・ETL アンチパターン集 の A-1〜F-1、`013_tdnet_load.md` §T-x、`078_gemma4_operation.md` §G-x のどれに該当するか>

**修正方針**: <変更の骨子。関数シグネチャ変更あれば明示>

```python
# before
<現行コード>

# after
<修正後コード>
```

**呼び出し側への波及**:
- `path/to/caller1.py:L100` — <どう変わるか>
- `path/to/caller2.py:L250` — <どう変わるか>
- （無ければ「無し」と明示）

**検証**: <単体で挙動確認する手順。smoke test の入力条件も>

**ロールバック**: <失敗時の戻し方。コミット revert で済むか、データ補正が必要か。無い場合は「取り消し不可」と明記>

---

### P0-2. <次の項目> 🚨

（同じフォーマットで繰り返し）

---

### P1-1. <P1 項目> ⚠️

（同じフォーマット）

---

## 対応アンチパターン

| plan ID | 004 | T-x | G-x |
|---|---|---|---|
| P0-1 | B-1, B-3 | T-7 | G-2 |
| P0-2 | A-5 | — | — |
| P1-1 | — | T-6 | — |

> 参照: `docs/knowledges/tools/004_coding_conventions.md` §バッチジョブ・ETL アンチパターン集 / `013_tdnet_load.md` §T-x / `078_gemma4_operation.md` §G-x

---

## 検証戦略

1. **smoke test**: <最小入力・期待結果。例: `--limit 1` で 1 件処理して logger 出力と BQ row 1 件を確認>
2. **dev 実機**: <スケール・期間・コストガード。例: DOCS_LIMIT=50、dev プロジェクトで 1 日分、想定 BQ スキャン量 < 1GB>
3. **本番適用判断基準**: <smoke + dev 両方 PASS で初めて prod。どの指標を何閾値で判定するか>
4. **回収手順**: <本番で問題発覚時の被害最小化手順。DELETE → 再実行が可能か、partial failure 時の resume 手順>

---

## 関連ドキュメント

- 知見 MD: `docs/knowledges/<category>/<number>_<slug>.md`（親知見）
- 関連 commit: `<hash>` — <1 行要約>
- 関連 incident / 実機ログ: <GCS パス・Workflow execution ID・ntfy 通知 ID 等>
- フォーマット正本: `skills/planning.md` §改修プラン / バグ修正指示書 MD フォーマット
