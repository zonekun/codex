# 作業計画: TOB MLモデル改善 — TOP10株主TYPE付与 + オーナー色因子新設

**作成日時**: 2026-05-16 22:00 (JST)
**ステータス**: 完了（2026-05-18: screen_tob.py TODO残存なし確認済み、007本体整備も完了）
**分類**: (b) 継続改修型
**親知見 MD**: `docs/knowledges/analysis/007_tob_ml_prediction.md`
**関連アイディアID**: -

## 目的

TOP10_NAMES_JSON の各株主名に TYPE を付与し、信託口スキップ後の「実質筆頭株主」とTOP10内のオーナー色スコアを因子としてモデルに追加する。MBO/オーナー型TOBの捕捉力を改善する。

## 背景・動機

### 現状の問題
- モデルは `top_shareholder_is_public=1` + `other_corp_ratio`高 の「親子上場型TOB」を捕捉可能
- MBO/オーナー型TOB（久光製薬 rank 3345、マンダム rank 3052）は構造的に圏外
- 根本原因: `TOP_SHAREHOLDER_NAME` は信託銀行が名目1位になるケースが多く、実質筆頭を見分けられない

### ユニクロ(9983)の例で見る課題
```
#1 日本マスタートラスト信託銀行  21.8% ← 名目筆頭（信託口）
#2 柳井 正                     17.4% ← 実質筆頭（創業者）
#3 日本カストディ銀行            10.6% ← 信託口
#4 TTY Management B.V.          5.2% ← 柳井家海外持株会社
#5 柳井 康治                    4.7% ← 息子
#6 柳井 一海                    4.7% ← 息子
#7 有限会社Fight&Step            4.7% ← 柳井家資産管理会社
#8 STATE STREET BANK             3.6% ← 外国カストディ
#9 有限会社MASTERMIND             3.5% ← 柳井家資産管理会社
#10 JP MORGAN CHASE               2.8% ← 外国カストディ
```
→ 現モデル: `top_shareholder_is_public=0`（信託銀行は非上場）→ オーナー色ゼロ
→ 実態: TOP10中6/10が柳井家関連、合計35%超のオーナー企業

### データ規模（BQ実測）
| カテゴリ | ユニーク名 | 出現数 |
|---------|-----------|--------|
| 信託銀行系（信託銀行/カストディ/マスタートラスト） | 4,367 | 71,482 |
| 外国カストディ（STATE STREET/JP MORGAN等） | 2,619 | 10,482 |
| 法人接尾辞あり（㈱/株式会社/有限会社等） | 17,894 | 141,702 |
| 接尾辞なし（個人名・保険・銀行㈱表記等） | 31,280 | 151,684 |
| **合計** | **56,160** | **375,350** |

### 新因子候補
| 因子 | 型 | 意味 |
|------|---|------|
| `real_top_is_individual` | binary | 信託口スキップ後の実質筆頭が個人/資産管理会社か |
| `owner_count_in_top10` | int(0-10) | TOP10中の個人＋資産管理会社の数 |
| `owner_ratio_in_top10` | float | TOP10中の個人＋資産管理会社の合計持株比率 |
| `has_famous_investor` | binary | TOP10に有名個人投資家（複数銘柄TOP10に出現する個人）がいるか |

**有名個人投資家因子の仮説**: 五味大輔(118社)、内藤征吾(204社)等、複数銘柄のTOP10に出現する個人投資家は小型割安バリュー株を好む傾向があり、TOBターゲット属性と重なる。「N社以上のTOP10に出現するINDIVIDUAL」で自動定義（閾値はEDAで決定）。

### 関連ファイル
- 知見: `docs/knowledges/analysis/007_tob_ml_prediction.md`
- 株主構成知見: `docs/knowledges/tools/081_shareholder_composition.md`
- モデル: `scripts/tob_prediction/train_rf.py`
- 既存判定スクリプト（パターン参考）: `scripts/apply_shareholder_listing_flag.py`
- BQテーブル: `STOCK.SHAREHOLDER_COMPOSITION`

---

## 作業ステップ

### Phase 1: 株主名TYPE分類（最大工数）

#### Step 1. ユニーク名抽出 + ルールベース分類 ✅ (2026-05-16)
- [x] BQ から TOP10_NAMES_JSON 内の全ユニーク名を抽出 → `C:\tmp\tob_prediction\all_unique_names.csv` (56,160名)
- [x] ルール分類スクリプト `scripts/tob_prediction/classify_shareholder_names.py` を作成
  - **TRUST_BANK**: 信託銀行/カストディ/マスタートラスト/信託口（本体名に含む場合のみ、常任代理人注釈は除外） → **2,128名 (3.8%)**
  - **FOREIGN_CUSTODIAN**: STATE STREET/JP MORGAN等の外国カストディ銀行（本体名が既知銀行の場合のみ） → **4,894名 (8.7%)**
  - **INSTITUTION**: 保険/銀行/証券/信金/常任代理人付き実投資家（ファンド/政府等） → **13,301名 (23.7%)**
  - **INDIVIDUAL**: 日本人名パターン（漢字+スペース+漢字、カタカナ名、持株会・○○会除外） → **14,353名 (25.6%)**
  - **UNCLASSIFIED**: 上記に該当しない残り → **21,484名 (38.3%)** → Step 2 へ
- [x] dry-run でカテゴリ別件数確認 + 個別テスト10件で分類精度検証済み
  - 伊予銀行(常任代理人カストディ) → INSTITUTION ✓、柳井正 → INDIVIDUAL ✓、日本生命 → INSTITUTION ✓
- [x] 出力: `C:\tmp\tob_prediction\shareholder_name_types.csv` (全56,160名), `C:\tmp\tob_prediction\unclassified_names.csv` (21,484名)

#### Step 2. Sonnet 判定（UNCLASSIFIED分）✅ (2026-05-16)
- [x] Step 1 の UNCLASSIFIED 21,484名 → `unclassified_names.csv` 出力済み
- [x] ルール拡張（外国カストディ銀行追加・PRIVATE_CORP修正・常任代理人ロジック修正等）で 21,484 → 393 に削減
- [x] 残り393名を Sonnet 4.6 で手動分類 → `shareholder_name_types_sonnet.csv` (393行)
  - INDIVIDUAL 332 / INSTITUTION 22 / PRIVATE_CORP 20 / FOREIGN_CUSTODIAN 11 / ASSET_MGMT 8
- [x] `--merge-sonnet` で統合 → `shareholder_name_types.csv` (56,160行、UNCLASSIFIED=0)
  - 最終分布: INDIVIDUAL 19,583 / INSTITUTION 13,186 / PRIVATE_CORP 10,855 / FOREIGN_CUSTODIAN 9,224 / TRUST_BANK 2,128 / ASSET_MGMT 1,184

#### Step 3. 信頼度検証 ✅ (2026-05-16)
- [x] 各TYPEからサンプル目視確認 — 分類精度良好
- [x] ASSET_MGMT / PRIVATE_CORP 境界: おおむね適切（創業家資産管理会社はPRIVATE_CORPに寄せる方針で許容）
- [x] バグ修正: 常任代理人ハンドラ内の `　` スペース正規化漏れを修正（`_ALL_SPACES_RE.sub`適用）
- [ ] 久光製薬(4530)・マンダム(4917)・ユニクロ(9983)の期待値確認 → Step 4以降のBQ反映後に実施

### Phase 2: BQデータ更新

#### Step 4. マッピングテーブル `SHAREHOLDER_COMPOSITION_EXTEND` 作成 ✅ (2026-05-16)
- [x] BQ に新テーブル `STOCK.SHAREHOLDER_COMPOSITION_EXTEND` を作成 (`scripts/tob_prediction/load_shareholder_name_types.py`)
- [x] 56,160行を BQ にロード（RULE 53,767 / SONNET 393）
- [x] dry-run → 10件確認 → 全件TRUNCATE+INSERT 完了
  - TYPE分布: INDIVIDUAL 19,583 / INSTITUTION 13,186 / PRIVATE_CORP 10,855 / FOREIGN_CUSTODIAN 9,224 / TRUST_BANK 2,128 / ASSET_MGMT 1,184
- [x] **既存 `fetch_shareholder_composition.py` は改修不要**（TOP10_NAMES_JSON は `{name, ratio}` のまま維持）

#### Step 5. 派生カラム追加（SHAREHOLDER_COMPOSITION側）✅ (2026-05-16)
- [x] SHAREHOLDER_COMPOSITION に6カラムを ALTER TABLE ADD (`scripts/tob_prediction/compute_owner_features.py`)
- [x] Step 5a: EDA → 閾値=20社 → **19名**が有名投資家（吉田知広93社・内藤征吾70社等）→ `C:\tmp\tob_prediction\famous_investors.csv`
- [x] BQ UPDATE 37,607行完了
- [x] 検証:
  - ユニクロ(9983): REAL_TOP=柳井正/INDIVIDUAL, OWNER_COUNT=3-4, OWNER_RATIO≈0.27-0.34 ✓
  - 久光(4530): REAL_TOP=日本生命保険(相)/INSTITUTION, OWNER_COUNT=0 （TOP10に服部家不在 — 既知限界）
  - マンダム(4917): REAL_TOP=公益財団法人西村奨学財団/INSTITUTION, OWNER_COUNT=1 ✓
- [x] バグ修正: `公益財団法人`等フルフォーム → INSTITUTION（以前はPRIVATE_CORP）、再分類・再投入済み

### Phase 3: モデル改修 + 再評価

#### Step 6. train_rf.py 改修 ✅ (2026-05-16)
- [x] `_load_shareholders()` に新4カラム追加 (REAL_TOP_TYPE, OWNER_COUNT_IN_TOP10, OWNER_RATIO_IN_TOP10, HAS_FAMOUS_INVESTOR)
- [x] `build_feature_matrix()` に新因子4つ追加:
  - `real_top_is_individual` → BINARY_FEATURES
  - `owner_count_in_top10` → CONTINUOUS_FEATURES
  - `owner_ratio_in_top10` → CONTINUOUS_FEATURES
  - `has_famous_investor` → BINARY_FEATURES
- [x] 特徴量: 22 → 26変数

#### Step 7. 効果検証 ✅ (2026-05-16)
- [x] Walk-Forward再評価 (ROC-AUC): 2022=0.813 / 2023=0.746 / 2024=0.714 / 2025=0.748 → **平均0.7551** (旧0.752 ✓維持)
- [x] SHAP分析: owner_count rank13(0.0075) / owner_ratio rank15(0.0070) / real_top_is_individual rank16(0.0060) / has_famous_investor rank21(0.0026) — 全因子が寄与
- [x] 久光(4530)・マンダム(4917): Top15%未達（久光91%/マンダム66%）— 久光はTOP10に服部家不在のためOWNER_COUNT=0。**完了条件#4は未達成**
- [x] Top5%ヒット: 2022=35%/2023=18%/2024=21%/2025=26% recall

### Phase 4: ドキュメント + 運用整備

#### Step 8. 知見・カタログ・スクリーニング更新 ✅ (2026-05-16)
- [x] `007_tob_ml_prediction.md` に新因子・評価結果・SHAP追記
- [x] `docs/data_catalog/bq_shareholder_composition.md` に派生6カラム追記
- [x] `docs/data_catalog/bq_shareholder_composition_extend.md` 新規作成
- [ ] `screen_tob.py` の根拠表示にオーナー色因子を追加 → 次セッションで対応
- [x] memory `project_tob_ml_feature_cleanup.md` を更新

#### Step 9. 年次更新運用の定義 ✅ (2026-05-16)
- [x] 年次更新フロー（`fetch_shareholder_composition.py` 実行後）を `007_tob_ml_prediction.md` に「年次更新手順」節として追記

---

## 必要データ

| データ | ストレージ層 | パス/テーブル |
|--------|------------|--------------|
| 株主構成（TOP10_NAMES_JSON含む） | (a) BQ | `STOCK.SHAREHOLDER_COMPOSITION` |
| 上場企業マスタ | (a) BQ | `STOCK.STOCK_CODE_LIST` |
| 株主名→TYPE マッピング（新規） | (a) BQ | `STOCK.SHAREHOLDER_COMPOSITION_EXTEND` |
| 中間CSV（ルール分類済み） | (c) ローカル | `C:\tmp\tob_prediction\shareholder_name_types.csv` |
| 中間CSV（Sonnet判定分） | (c) ローカル | `C:\tmp\tob_prediction\shareholder_name_types_sonnet.csv` |

## 成果物

1. `scripts/tob_prediction/classify_shareholder_names.py` — 株主名TYPE分類スクリプト（ルールベース）
2. `scripts/tob_prediction/compute_owner_features.py` — 派生カラム算出スクリプト
3. BQ `STOCK.SHAREHOLDER_COMPOSITION_EXTEND` — 56,160名のTYPEマッピングテーブル
4. BQ `STOCK.SHAREHOLDER_COMPOSITION` — 派生カラム6つ追加
5. `C:\tmp\tob_prediction\famous_investors.csv` — 有名個人投資家リスト
6. `scripts/tob_prediction/train_rf.py` — 26変数モデル
7. Walk-Forward再評価結果 + SHAP分析

## 完了条件

1. 56,160名の全ユニーク株主名にTYPEが付与されている
2. ユニクロ(9983) / 久光(4530) / マンダム(4917) で期待されるオーナー色因子値が得られる
3. Walk-Forward評価でROC-AUC が旧モデル (0.752) 以上を維持
4. 久光(4530)・マンダム(4917)のランキングが改善（目標: Top15%以内）
5. 知見MD・memory が更新済み

## 見積もり

- Phase 1 (TYPE分類): 2-3時間（ルール詰め + Claude Code判定）
- Phase 2 (BQ更新): 1-2時間
- Phase 3 (モデル改修): 1-2時間（学習自体は ~30分/年度）
- Phase 4 (ドキュメント): 30分
- **合計: 5-8時間**（複数セッション想定）
- 難易度: 中（ルール精度の詰めが鍵）

## リスク・注意事項

- **ASSET_MGMT vs PRIVATE_CORP 境界**: 「有限会社○○」が創業家持株か無関係法人かは名前だけでは判別困難な場合あり。ただし因子としてはグラデーション（0-10, 0-1.0）で吸収されるため、多少の誤分類は許容範囲
- **表記ゆれ**: 同一株主の全角/半角・㈱/株式会社・スペース有無で別名義扱いになるリスク。正規化関数で対処
- **既存精度への影響**: 新因子追加でノイズ増→精度低下の可能性。旧モデル比でROC-AUC低下した場合は因子選択を再検討
- **TOP10_NAMES_JSON NULL**: JSONがNULLの行（少数）は新因子も欠損値扱い → median imputation で処理
- **有名個人投資家因子**: ワークするか不明。効果なければ26→25変数に戻す判断もあり

## 振り返り
- **実際の所要時間**: 約3時間（2026-05-16 22:00-翌01:00頃、複数セッション）
- **うまくいった点**:
  - ルール拡張+Sonnet手動分類で21,484 UNCLASSIFIED → 0 を完全に解消
  - `　`スペース正規化・常任代理人ハンドラ修正等で精度向上
  - BQマッピングテーブル方式（アプローチB）が既存パイプライン無改修で実現
  - ROC-AUC 0.7551 で旧モデル維持
  - `owner_ratio_in_top10` が2025年SHAPで実際に寄与
- **改善点/未達成**:
  - 久光(4530)/マンダム(4917)のTop15%達成ならず（服部家/西村家が有限会社経由のためINDIVIDUAL不出現）
  - `公益財団法人`フルフォームのINSTITUTION漏れバグを後から発見（修正済み）
  - screen_tob.py根拠表示更新は未実施
- **得られた知見**:
  - オーナー型TOBは創業家が個人名でなく法人（有限会社）経由で持つケースが多く、PRIVATE_CORPに分類される→因子としての効果が限定的
  - 完全にオーナー色を捉えるには `PRIVATE_CORP` かつ「代表者＝創業家」の判定が必要（将来課題）
  - `公益財団法人`等の法人格フルフォームはCORP_SUFFIXESより先にINSTITUTIONパターンで捕捉すべき

---

## レビュー追記: 2026-05-16 23:00 JST — code-reviewer

→ `docs/reviews/188_cr_tob_ml_owner_factor.md`
