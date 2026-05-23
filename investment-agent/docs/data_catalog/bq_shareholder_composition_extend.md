# STOCK.SHAREHOLDER_COMPOSITION_EXTEND
> 親: [`data_catalog.md`](../../data_catalog.md)

| テーブル名 | 説明 | 更新頻度 | 備考 |
|-----------|------|---------|------|
| `gmailpj-357912.STOCK.SHAREHOLDER_COMPOSITION_EXTEND` | 株主名→TYPE マッピングテーブル（56,160名） | 年次（有報シーズン後に手動更新） | `scripts/tob_prediction/classify_shareholder_names.py` + Sonnet手動判定 → `load_shareholder_name_types.py` でBQ投入 |

**`STOCK.SHAREHOLDER_COMPOSITION_EXTEND` スキーマ:**

| カラム | 型 | モード | 説明 |
|---|---|---|---|
| NAME | STRING | REQUIRED | 株主名（TOP10_NAMES_JSON内の原文）★PK |
| NAME_NORMALIZED | STRING | NULLABLE | 正規化後の名前（fullwidth→ASCII、スペース統一、upper）JOIN補助用 |
| TYPE | STRING | REQUIRED | INDIVIDUAL / ASSET_MGMT / INSTITUTION / TRUST_BANK / FOREIGN_CUSTODIAN / PRIVATE_CORP / LISTED_CORP |
| CONFIDENCE | STRING | REQUIRED | RULE（ルール分類）/ SONNET（Sonnet手動判定）/ MANUAL |
| TICKER | STRING | NULLABLE | 上場企業の場合のticker（現状NULLのみ） |
| UPDATED_AT | TIMESTAMP | REQUIRED | 最終更新日時 |

**主キー:** `NAME` — NOT ENFORCED

**TYPE定義:**

| TYPE | 意味 | 例 |
|------|------|-----|
| TRUST_BANK | 信託銀行・カストディバンク（信託口名義） | 日本マスタートラスト信託銀行、日本カストディ銀行 |
| FOREIGN_CUSTODIAN | 外国カストディ銀行（常任代理人の本体） | STATE STREET BANK, JP MORGAN CHASE, BNY GCM |
| INSTITUTION | 銀行・保険・年金・政府系・財団法人・持株会等 | 日本生命保険(相), 公益財団法人西村奨学財団 |
| INDIVIDUAL | 個人名（創業者・個人投資家） | 柳井　正, 吉田　知広 |
| ASSET_MGMT | 資産管理・投資組合・ファンド | 日本美容・ヘルスケア成長投資１号組合 |
| LISTED_CORP | 上場事業法人（STOCK_CODE_LIST.STOCK_NAME に存在する法人） | 株式会社キーエンス, 株式会社桧家ホールディングス |
| PRIVATE_CORP | 上記以外の法人（創業家資産管理会社等。LISTED_CORP 昇格対象を除く） | 有限会社Fight&Step, ㈱ティ・ケー・ワイ |

**注意事項:**
- `INDIVIDUAL` + `ASSET_MGMT` の合計がオーナー色因子（`OWNER_COUNT/RATIO_IN_TOP10`）に使用される
- `LISTED_CORP` は `real_top` CTE に残る（`REAL_TOP_TYPE=LISTED_CORP` として記録）。`OWNER_COUNT/RATIO_IN_TOP10` にはカウントされない（INDIVIDUAL/ASSET_MGMT のみ）
- `LISTED_CORP` 再分類後の再実行フロー: `classify_shareholder_names.py --listed-names-csv` → `load_shareholder_name_types.py --mode full` → `compute_owner_features.py --mode full`
- 創業家資産管理会社（`有限会社○○`等）は名前だけでは判別不能なため `PRIVATE_CORP` に分類されるものが多い
- 常任代理人付き名前（`X（常任代理人 Y銀行）`）: Xが外国カストディ銀行なら FOREIGN_CUSTODIAN、それ以外は INSTITUTION
- 年次更新手順: `docs/knowledges/analysis/007_tob_ml_prediction.md` §年次更新手順を参照

**件数 (2026-05-16):**
- 合計: 56,160名
- INDIVIDUAL: 19,583 / INSTITUTION: 12,624 / PRIVATE_CORP: 10,393 / FOREIGN_CUSTODIAN: 10,250 / TRUST_BANK: 2,128 / ASSET_MGMT: 1,182
- RULE分類: 53,767 / SONNET判定: 393
