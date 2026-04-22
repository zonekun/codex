# 月次パイプライン一致率 65.3% → 90.3% 改善記録

**期間**: 2026-04-08〜09
**最終結果**: OK=1145, NG=123, **OK/(OK+NG) = 90.3%**（202社）
**最終CSV**: `buffett_compare_20260409_175131.csv`

---

## 一致率推移

| 段階 | 日時 | OK | NG | OK/(OK+NG) | CSV |
|------|------|-----|-----|-----------|-----|
| ベースライン | 04/05 15:32 | 779 | 414 | **65.3%** | `buffett_compare_20260405_153217.csv` |
| adapter修正54社 | 04/08 21:36 | 769 | 373 | 67.3% | `buffett_compare_20260408_213600.csv` |
| +Gemini 3 Flash化29社 | 04/09 03:02 | 897 | 297 | 75.1% | `buffett_compare_20260409_030227.csv` |
| +NG12修正バッチ | 04/09 08:12 | 910 | 284 | 76.2% | `buffett_compare_20260409_081055.csv` |
| +NG21修正バッチ | 04/09 10:02 | 1016 | 199 | 83.6% | `buffett_compare_20260409_100226.csv` |
| +62社全修正 | 04/09 15:00 | 1071 | 197 | 84.5% | `buffett_compare_20260409_150055.csv` |
| +auto-detection誤り修正 | 04/09 15:50 | 1143 | 125 | **90.1%** | `buffett_compare_20260409_155028.csv` |
| +PDF構造特化prompt+8705リバート | 04/09 17:53 | 1145 | 123 | **90.3%** | `buffett_compare_20260409_175131.csv` |

---

## 実施した施策と効果

### 1. adapter修正54社（+2.0pt: 65.3→67.3%）
- unit_scale追加（3690, 6238, 8739）
- regex修正（138A, 2705, 3191, 8142等）
- overwrite_past_months（2750, 3608, 9223, 9941）
- manual_override=true設定

### 2. コード修正必要29社をGemini 3 Flash化（+7.8pt: 67.3→75.1%）
- extraction_method を "gemini" に変更
- google-genai + 個人APIキー + gemini-3-flash-preview
- response_schema でOBJECT型を明示
- プロンプト構造化（タスク/文書情報/フィールド/ルール分離）
- 29社中21社は新規でregex→gemini、8社は既にgeminiで再抽出

### 3. NG12社個別調査+修正（+1.1pt: 75.1→76.2%）
- custom_prompt: 2670, 2674, 4666, 6627, 8798, 9616
- yoy_offset=100: 2670, 6627, 8798
- unit_scale=1000000: 9252
- overwrite_past_months: 3077, 3195
- bc_ignore: 7606（BCマッピング不規則）
- bc_floor削除: 4776

### 4. NG21社一括修正（+7.4pt: 76.2→83.6%）
- ベースプロンプト改善（ルール7-10追加: 空欄=null/売上高vs前年比区別/ストック値指示/2段構成セル）
- 21社にcustom_prompt設定
- yoy_offset/unit_scale修正
- overwrite_past_months設定

### 5. 残り40社custom_prompt追加（+0.9pt: 83.6→84.5%）
- 調査結果JSONから各社の原因に応じたcustom_promptを生成
- 9245, 9973: overwrite_past_months追加

### 6. auto-detection誤り30件修正（+5.6pt: 84.5→90.1%）★最大効果
- yoy_offset誤設定削除: 141A, 2294, 3175, 7422, 9251（Gemini抽出が既にインデックス形式）
- unit_scale誤設定削除: 6238, 8705, 2705, 2997
- **教訓**: Gemini method銘柄にyoy_offset/unit_scaleを設定すると、compare_monthly_buffett.pyのauto-detectionが誤判定する

### 7. PDF構造特化custom_prompt 26社（+0.2pt: 90.1→90.3%）★効果なし
- 26社にPDFテーブル構造に合わせた詳細custom_prompt設定
- 8705は逆効果（-13件悪化）のためリバート
- **結論: Gemini 3 Flashの読み取り精度限界はcustom_promptでは改善しない**

---

## 残りNG 123件の分類（04/09 17:53時点）

| 分類 | 件数 | 対策 |
|------|------|------|
| **overwrite_past_months未実装** | ~50件 | test_gemini3_29.pyに全月抽出ロジック実装 |
| **Gemini 3 Flash精度限界** | ~40件 | モデル変更 or pdfplumber前処理 |
| **Geminiの非決定性** | ~20件 | 複数回実行の多数決 or 許容閾値緩和 |
| **BC側の問題** | ~10件 | bc_ignore |

---

## 得られた教訓

### adapter設計
1. **yoy_offset=100は原則追加不要**: `_extract_pdf_by_column`もGeminiもインデックス形式で抽出する。追加するとauto-detection誤判定で大幅悪化
2. **unit_scaleもGemini method銘柄には原則不要**: Geminiは値をそのまま返す。compare_monthly_buffett.pyで二重適用される
3. **custom_promptはadapterに格納**: スクリプトにハードコードせず、銘柄固有の指示はadapter JSONのcustom_promptフィールドに

### Gemini抽出
4. **response_schemaでOBJECT型を明示**: list返却を防止
5. **プロンプトは構造化**: タスク/ルール/出力形式を箇条書きで分離
6. **Gemini応答不安定時はプロンプト修正が先**: try/exceptで吸収しない
7. **Gemini 3 Flashの精度限界**: 分割テーブル判定/行列識別/空欄認識でcustom_promptの工夫では改善しないケースがある

### 調査手法
8. **数字逆引き必須**: BC値・抽出値をPDFテキストから検索し、どのテーブル・行・列から来たか特定
9. **pdfplumber+画像+Gemini分析の3段階**: 1つの情報源だけでは不正確
10. **1社ずつ調査**: バッチ推定禁止。PDF構造は銘柄ごとに異なる

### overwrite_past_months
11. **速報→確報差は広く存在**: 多くの企業で月次速報値が後日修正される
12. **test_gemini3_29.pyにはoverwrite_past_months未実装**: 各BQ文書を個別処理するだけで、最新PDFから全月分を一括取得するロジックがない
13. **Cloud Run版extract_monthly_data.pyにはoverwrite_past_months実装済み**: ローカルテストスクリプトとの機能差

---

## 追加検証: 26社モデル・入力形式比較（04/09 20:50）

Gemini精度限界と判定された26社に対し、3パターンを検証。

| モデル | 入力形式 | 26社OK | 26社NG | 26社一致率 |
|--------|---------|--------|--------|-----------|
| **Gemini 3 Flash** | PDFバイナリ | 116 | 93 | **55.5%** |
| Gemini 3 Flash | pdfplumberテキスト | 111 | 98 | 53.1% |
| **Gemini 3.1 Pro** | PDFバイナリ | 116 | 93 | **55.5%** |
| **Gemini 3.1 Pro** | pdfplumberテキスト | 88 | 86 | **50.6%** |

**結論:**
- 4パターン全て誤差範囲。モデル・入力形式を変えても改善しない
- pdfplumberテキスト化は逆効果（-1.1pt）。テーブル構造情報がPDFバイナリより劣化
- 3.1 ProはFlashと同等。Proにしても改善しない
- 裏どり: NG93件のうち速報→確報差（overwrite_past_months）は18件のみ。残り73件はGemini読み取りミス（大差）で、モデル変更でも改善不可

---

## 次ステップ

1. **overwrite_past_months実装**（test_gemini3_29.pyまたはCloud Run再実行）: ~50件解決見込み → 93%+
2. **Gemini精度限界対策**: pdfplumberでテーブルをテキスト化 → テキストモードでGeminiに渡す（画像依存をやめる）
3. **BCデータ最新化**: 3月度月次が出揃い中。BC側データ更新後に再突合
4. **Cloud Run化**: ローカルテスト成功分をCloud Runイメージに反映
