# CR-040: Phase 5 受注残高アダプタ生成 評価レポート

- **日時**: 2026-04-30 22:15 JST
- **レビュアー**: Claude Code
- **対象**: `scripts/build_backlog_adapter.py`（Codex `codex/integration` ブランチ）
- **依頼元**: ユーザー（regex 1社/1,314社 の妥当性調査）

---

## 1. 調査背景

Phase 5 で生成された 1,314社の extract_adapter のうち、`extraction_method=regex` は **6269 三井海洋の1社のみ**（0.08%）。残り1,313社はすべて `gemini_vision`。この偏りが妥当かを調査した。

---

## 2. regex/gemini_vision 分岐ロジック（3段ゲート）

### ゲート1: pdfplumber complexity 分類（`analyze_complexity()`）

`simple_table` になるには**すべて**クリアが必要:

| 条件 | 閾値 | 超えた場合 |
|------|------|-----------|
| キーワードページあり | 1ページ以上 | `no_relevant_pages` |
| テーブルあり | 1テーブル以上 | `no_table` |
| 1列目ラベル結合なし | `broken_col0=False` | `complex_table` |
| 単位混在なし | `unit_mixed=False` | `complex_table` |
| テーブル数 | ≤3 | `complex_table` |
| 列数 | ≤12 | `complex_table` |

### ゲート2: process_candidate 条件分岐（L684）

```python
if structure_body.get("data_available") and complexity.complexity == "simple_table":
    adapter_body = call_gemini_json(...)  # Gemini に regex adapter 設計を依頼
else:
    adapter_body = {"extraction_method": "gemini_vision", ...}  # 無条件 gemini_vision
```

### ゲート3: versioned_extract_adapter 強制上書き（L574）

```python
if complexity.complexity != "simple_table":
    method = "gemini_vision"  # Gemini が regex を返しても上書き
```

→ **regex になるには「pdfplumber が simple_table と判定」かつ「Gemini が data_available=true と判定」かつ「Gemini が regex を提案」の3条件が必要。**

---

## 3. complexity 分布（チェックポイント実績）

| complexity | data_available=true | data_available=false | 合計 |
|-----------|--------------------:|---------------------:|-----:|
| complex_table | 425 | 31 | 456 |
| no_relevant_pages | 0 | 678 | 678 |
| no_table | 148 | 25 | 173 |
| simple_table | 2 | 5 | 7 |
| **合計** | **575** | **739** | **1,314** |

> ※ 引継ぎメモの structure_summary（Gemini判定ベース）と数値が異なるのは、チェックポイントが pdfplumber 判定を記録しているため。

---

## 4. 発見: `_detect_unit_mixed` の部分文字列バグ

### バグの内容

```python
def _detect_unit_mixed(text: str) -> bool:
    units = [u for u in ("百万円", "千円", "億円", "円") if u in text]
    return len(set(units)) >= 2
```

`"百万円"` を含むテキストでは `"円" in text` も `True` になるため、**単一単位のページでも必ず `unit_mixed=True`** を返す。

### 再現確認

```
"受注高 1,500百万円" → matched=['百万円', '円'] → unit_mixed=True  ← 誤検知
"受注残高 23,456千円" → matched=['千円', '円']   → unit_mixed=True  ← 誤検知
"売上 5億円"          → matched=['億円', '円']   → unit_mixed=True  ← 誤検知
```

### 修正方法（参考）

```python
# 案1: 長い単位から順にマッチし、マッチ済み部分を除去
# 案2: 正規表現で境界を考慮
import re
def _detect_unit_mixed(text: str) -> bool:
    found = set()
    for unit in ("百万円", "千円", "億円"):
        if unit in text:
            found.add(unit)
    # 「円」単独は「百万円」「千円」「億円」のいずれにも属さない「円」がある場合のみ
    stripped = text
    for unit in ("百万円", "千円", "億円"):
        stripped = stripped.replace(unit, "")
    if "円" in stripped:
        found.add("円")
    return len(found) >= 2
```

---

## 5. バグの影響範囲

### complex_table（data_available=true, 425社）の判定理由内訳

| 判定理由 | 社数 | バグ影響 |
|---------|-----:|---------|
| `unit_mixed` のみ | **237** | **全社誤分類の可能性大** |
| `many_tables(>3)` + `unit_mixed` | 150 | unit_mixed は誤検知だが many_tables で complex は妥当 |
| `many_tables(>3)` + `unit_mixed` + `wide_cols(>12)` | 17 | 同上 |
| `unit_mixed` + `wide_cols(>12)` | 7 | unit_mixed は誤検知だが wide_cols で complex は妥当 |
| `broken_col0` + `many_tables(>3)` + `unit_mixed` | 5 | 同上 |
| `broken_col0` + `many_tables(>3)` + `unit_mixed` + `wide_cols(>12)` | 3 | 同上 |
| `broken_col0` + `unit_mixed` | 2 | broken_col0 で complex は妥当 |
| `broken_col0` + `unit_mixed` + `wide_cols(>12)` | 2 | 同上 |
| `broken_col0` + `many_tables(>3)` | 1 | バグ無関係 |
| `many_tables(>3)` のみ | 1 | バグ無関係 |

→ **237社（全体の18%）が `unit_mixed` のみで complex_table にされた。バグ修正で simple_table に再分類される候補。**

### 237社の内訳（Gemini 側の判定）

Gemini が画像を見た結果の complexity 判定:

| Gemini complexity | 社数 | 解釈 |
|------------------|-----:|------|
| complex_table | 217 | Gemini も complex と判定（妥当な場合あり） |
| ppt_chart | 12 | チャート形式 → gemini_vision が妥当 |
| **simple_table** | **8** | **Gemini も simple と判定 → regex 候補** |

Gemini の presentation_format:

| format | 社数 |
|--------|-----:|
| table | 135 |
| mixed | 96 |
| chart | 4 |
| text | 2 |

### 最有力 regex 候補: Gemini も simple_table と判定した8社

pdfplumber が `unit_mixed` バグのみで complex 化 & Gemini も `simple_table` と判定した企業。バグ修正だけで regex adapter 生成の対象になる:

| ticker | 社名 | テーブル数 | 列数 | 行数 | 関連ページ |
|--------|------|----------:|-----:|-----:|-----------|
| 1793 | 大本組 | 1 | 4 | 33 | [7] |
| 1808 | 長谷工 | 1 | 4 | 34 | [8] |
| 1975 | 朝日工 | 2 | 7 | 5 | [3, 4, 9] |
| 6840 | AKIBA | 3 | 4 | 3 | [5] |
| 6999 | KOA | 1 | 5 | 3 | [16] |
| 7354 | DmMiX | 1 | 5 | 9 | [10] |
| 8056 | BIPROGY | 1 | 6 | 5 | [6] |
| 9663 | ナガワ | 2 | 7 | 4 | [4] |

これら8社の extract_adapter は現在すべて `gemini_vision / fields=[] / notes="Vision extraction selected because structure is complex or data is unavailable."` であり、regex adapter は未生成。

---

## 6. simple_table 判定企業の検証結果

チェックポイント上の simple_table は7社（引継ぎメモの9社と差異あり）:

| ticker | 社名 | data_available | method | 備考 |
|--------|------|:-:|--------|------|
| 6269 | 三井海洋 | true | **regex** | 唯一の regex。決算短信本文からラベルマッチ |
| 3670 | 協立情報通信 | true | gemini_vision | pdfplumber は simple_table だが Gemini が棒グラフと判定。**妥当** |
| 3137 | ファンデリー | false | gemini_vision | ゲート2（data_available）でブロック |
| 3964 | オークネット | false | gemini_vision | 同上 |
| 4387 | ZUU | false | gemini_vision | 同上 |
| 5570 | ジェノバ | false | gemini_vision | 同上 |
| 7362 | T.S.I | false | gemini_vision | 同上 |

---

## 7. 総合評価

### 良い点

- **パイプライン全体の設計**: checkpoint/resume、逐次DL+削除、GCS保存、バージョン管理は堅実
- **Gemini の判断品質**: structure.json の data_available 判定、complexity 再判定は概ね妥当
- **安全側に倒す設計方針**: 迷ったら gemini_vision は運用上は正しい判断

### 問題点

1. **`_detect_unit_mixed` のバグ（重大）**: 部分文字列マッチにより、円建て単位があるページはすべて `unit_mixed=True` を返す。237社が誤分類
2. **regex adapter 生成パスが実質的に死んでいる**: 3段ゲートの設計上、regex になる条件が厳しすぎて事実上 gemini_vision 一択
3. **引継ぎメモの数値不一致**: `simple_table: 9` と報告されたが、チェックポイント上は7社。structure_summary が Gemini 判定ベースなのか pdfplumber ベースなのか不明確

### リスク評価

- **運用リスク: 低〜中**。gemini_vision フォールバックがあるため、抽出自体は動作する。ただし Gemini API コスト（毎回画像送信）が regex より高い
- **品質リスク: 低**。regex adapter の品質は Gemini のプロンプト設計に依存するため、gemini_vision でも抽出精度は同等以上の可能性あり
- **修正の緊急度: Phase 6 開始前に修正すべき**。Phase 6 で実抽出を回す際、237社分の不要な Gemini API コールが発生する

---

## 8. 推奨アクション（Codex 向け）

### 必須（Phase 6 前）

1. **`_detect_unit_mixed` のバグ修正**: §4 の修正案を参照。修正後に unit_mixed-only の237社を再分類
2. **再分類後の simple_table 企業に対して regex adapter を再生成**: 少なくとも上記8社（Gemini も simple_table 判定）は優先
3. **引継ぎメモの数値基準を明記**: structure_summary の complexity が pdfplumber 由来か Gemini 由来かを明示

### 推奨（品質向上）

4. `no_table`（148社 data_available=true）の中にも、テーブルではなく本文テキストに数値が含まれる企業がある可能性。6269 三井海洋と同じパターン（本文ラベルマッチ regex）が適用できるか調査
5. `complex_table` の `many_tables(>3)` 判定企業（150社）は、受注関連ページのテーブル数ではなく全関連ページのテーブル合計で判定している。ページ単位で見れば simple な場合がある

### 禁止

- **バグ修正を理由に全1,314社を再実行しない**。再実行対象は `unit_mixed` のみで complex 化された237社に限定すること
- **regex adapter の `row_label_regex` を汎用化しすぎない**。6269 の `"受注高は"` のようにドキュメント固有のラベルでよい。汎用正規表現は誤マッチのリスクが高い
