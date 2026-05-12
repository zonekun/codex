# コードレビュー: structure.json metrics[] に source 属性追加 計画

- 日時: 2026-05-07 22:41 JST
- 対象: `docs/plans/tools-042_monthly_adapter_field_source_20260507_222800.md`
- パターン: 4 (新規開発・設計計画)
- レビュアー: Claude (code-reviewer runbook)

---

## 【サマリー】

- 変更の要約: structure.json の `metrics[]` に `"source": "bc" | "original"` を追加し、メトリクスの作成基準を追跡可能にする計画。全470ファイルのマイグレーション + `compare_monthly_buffett.py` 改修を含む
- 品質評価: **B** — 目的・設計は明確だが、影響スクリプト分析に重大な漏れが2件ある
- 主要リスク:
  1. `extract_monthly_data.py` の structure-adapter 突合ロジックが `source: "original"` メトリクスで偽警告を出す（計画で未検出）
  2. `build_monthly_extractor.py` が `source: "original"` メトリクスを Gemini プロンプトに渡し、不正な adapter fields を生成する（計画で未検出）
  3. GCS↔ローカル差分チェック（Step 1）の具体的な手順・ツールが未定義

---

## 【パターン4: 新規計画評価】

### 技術選定の妥当性

適切。structure.json はメトリクス定義の正本であり、メトリクスの作成基準を `source` フィールドとしてここに持たせるのは自然な設計。extract_adapter.json の `fields[].source` との関係も整理されている（042 知見 MD で既に fields レベルの `source` が定義済み）。省略時デフォルト `"bc"` による後方互換も正しい。

**1点確認**: structure.json の `metrics[].source` と extract_adapter.json の `fields[].source` は意味が異なる。structure 側は「メトリクス定義の出自」、adapter 側は「フィールドの作成基準」。現時点では同値だが将来分岐し得る。計画 MD またはスキーマ定義で両者の関係を明文化すべき。

### 既存システムとの統合

#### 計画が正しく「要」と判定したスクリプト

- [x] `compare_monthly_buffett.py`: `load_structure_bc_names()` (L108-123) が全メトリクス名を集合化 → `source: "original"` を除外する改修は必須。計画の判定は正しい

#### 計画が「不要」と判定したが実際は「要」のスクリプト（重大漏れ）

- [ ] **`extract_monthly_data.py`** (L3336-3362): `_structure_metric_names` を全メトリクスから構築し、adapter fields との差分で `adapter_fields_incomplete` 警告を出す。`source: "original"` メトリクスが structure に追加された場合、対応する adapter field が無いと偽警告が発生する。将来 `source: "original"` メトリクスを追加した瞬間に全該当銘柄で警告ログが出る
- [ ] **`build_monthly_extractor.py`** (L214-218, L301-305): `parse_structure_metrics()` が全メトリクス名を Gemini プロンプトに渡す。`source: "original"` メトリクスは BC 定義とは異なるため、Gemini が誤った抽出ルールを生成するリスクがある。`--rebuild` 実行時にアダプター破壊の可能性

#### 計画が正しく「不要」と判定したスクリプト（検証済み）

以下は検証の結果、計画の「不要」判定が妥当であることを確認した:

| スクリプト | 理由 |
|-----------|------|
| `buffett_monthly_scrape.py` | BC データから structure を生成。`manual_add` マージは passthrough で `source` フィールドを保持。生成側なので影響なし |
| `buffett_monthly_local.py` | structure を取得するのみ（pass-through） |
| `monthly_data_load.py` | structure の `monthly_items` と `extraction_method` のみ参照。metrics を名前集合化する処理なし |
| `download_monthly.py` | structure.json を参照しない |
| `download_bc_kpi.py` / `_v2.py` | structure.json の存在チェックのみ（ticker 一覧取得用） |
| `migrate_gcs_monthly_paths.py` | ファイル名マッチでコピーするのみ |
| `sync_adapters.py` | GCS→ローカル同期でファイルをそのままコピー |
| `extract_order_backlog.py` | quarterly 用の別スキーマ（versions/breakdown_dimensions）。月次 structure とは無関係 |
| `cleanup_completed_metrics.py` | quarterly 用 |
| `generate_backlog_structure.py` | quarterly 用 |
| `validate_backlog_structure.py` | quarterly 用 |

### リスク・コスト

- **GCP課金**: マイグレーションは GCS read + write × 470 = 約940 API コール。無視可能なコスト
- **処理時間**: 1.5 時間の見積もりは妥当（大半は GCS 差分チェックとマイグレーション実行）
- **撤退基準**: 未定義。マイグレーション失敗時のロールバック手順がない。Step 4 → Step 6 の間で一部だけマイグレーション済みの中間状態が残る可能性。GCS バックアップスナップショットの取得タイミングを計画に含めるべき

### 抜け漏れ

- [ ] **extract_monthly_data.py の structure-adapter 突合ロジック**: 上記「既存システムとの統合」参照。「将来要」ではなく「同時改修要」。source フィールドの追加と同時に対応しないと、将来 `source: "original"` メトリクスを追加した際に偽警告が出る仕組みが残る
- [ ] **build_monthly_extractor.py の Gemini プロンプト**: 同上。`--rebuild` 時に `source: "original"` メトリクスが Gemini に渡される
- [ ] **042 知見 MD の structure.json スキーマ定義テーブル**: 計画 Step 2 で「フラグテーブルに追加」としているが、042 知見 MD の §手動修正の上書き防止フラグ > structure.json テーブル（現在 5 フラグ: `manual_override`, `manual_add`, `collection_excluded`, `_bc_value_invalid`, `name`）への追記が必要。計画では「source を追加」としか書かれておらず、既存テーブルのどの位置にどう追記するかが曖昧
- [ ] **GCS バックアップ**: マイグレーション前のスナップショット取得が計画に含まれていない。042 知見 MD に `monthly/_backup/` パスが定義済み（例: `20260503_1527_bc_historical/`）。同パターンでバックアップすべき
- [ ] **Step 1 GCS↔ローカル差分チェックの具体手段**: `sync_adapters.py` を使うのか、新規スクリプトを書くのか、手動 `gsutil` か。判定基準（「どちらが新しいか」）も updated_at ベースか blob 更新日時ベースか不明

### 目的・スコープの明確性

良好。目的は「source 属性追加によるメトリクス出自追跡」で明確。非スコープ（「original メトリクスの実際の作成ワークフロー設計」）も明示されている。

### 段階的検証計画

Step 4 で「dry-run → 10件目視確認 → 全件実行」、Step 7 で smoke test。基本的な段階は踏んでいるが、以下が不足:

- compare 以外の影響スクリプト（extract_monthly_data, build_monthly_extractor）に対する検証ステップがない
- GCS 同期後の整合性検証（ローカル全470 = GCS 全470）がない

### 完了条件の検証可能性

完了条件 1-4 は具体的で検証可能。ただし条件 4「`load_structure_bc_names()` が `source: "original"` のメトリクスを除外する」は、現時点で `source: "original"` のメトリクスが存在しないため、テストデータの作成手順が必要。

### データカタログ整合

該当なし。BQ テーブルへの変更はなく、GCS パスも既存の `monthly/meta/{ticker}/structure.json` のまま。

---

## 【重大な指摘】（即修正）

### #1 extract_monthly_data.py の structure-adapter 突合で偽警告が発生する

- 箇所: `scripts/extract_monthly_data.py:3336-3362`
- 事象: `_structure_metric_names` が全メトリクス名を含むため、`source: "original"` メトリクスが structure に追加されると、adapter に対応 field がない場合に `adapter_fields_incomplete` 警告が出る
- トリガー: 将来 `source: "original"` メトリクスを structure.json に追加した時
- 影響: 全該当銘柄で偽の `adapter_fields_incomplete` 警告ログ + error_entries CSV 汚染
- 根拠: L3336-3337 で `m.get("name")` のみでフィルタし、`source` を考慮していない。L3350-3351 で差分を取る際も `source` フィルタなし
- 推奨対応: 計画 Step 5 に `extract_monthly_data.py` の改修を追加。`_structure_metric_names` 構築時に `source: "original"` を除外するか、`collection_excluded` と同様の除外ロジックを追加。今回のマイグレーションでは全て `source: "bc"` なので即座の問題は起きないが、source フィールド導入と同時にフィルタを入れないと将来の利用時に罠になる

### #2 build_monthly_extractor.py の Gemini プロンプトに不正なメトリクスが混入する [却下: 2026-05-07 — 運用停止スクリプトへの改修はスコープ外。ユーザー判断]

- 箇所: `scripts/build_monthly_extractor.py:214-218, 301-305`
- 事象: `parse_structure_metrics()` が全メトリクス名を返し、Gemini プロンプトの「抽出したいメトリクス」に含まれる。`source: "original"` メトリクスは BC 由来ではないため、Gemini に渡すと不正確な抽出ルールが生成される
- トリガー: `source: "original"` メトリクスが structure に存在する状態で `--rebuild` を実行した時
- 影響: 生成される extract_adapter.json が不正なフィールド定義を含む。既存の正常アダプターが破壊される可能性
- 根拠: `parse_structure_metrics()` は `structure.get("metrics")` の全要素から `name` を取得するのみ（L218）。source フィルタなし
- 推奨対応: ~~計画に `build_monthly_extractor.py` の改修を追加~~ 却下。運用停止スクリプトへの改修は本プランのスコープ外

### #3 マイグレーション前のGCSバックアップ手順が欠落

- 箇所: 計画 MD Step 3-4 間
- 事象: 470 ファイルを一括変更する前のバックアップ取得手順が計画にない
- トリガー: マイグレーションスクリプトのバグで structure.json が破損した場合
- 影響: 470 ファイルの復旧手段がない（git 管理のローカルからは復元可能だが GCS 側は不可）
- 根拠: 042 知見 MD に `monthly/_backup/` パスが定義されており、過去にも `20260503_1527_bc_historical/` でスナップショットを取った実績がある
- 推奨対応: Step 3 の前に「GCS `monthly/meta/` 配下の全 structure.json を `monthly/_backup/YYYYMMDD_HHMM_pre_source_migration/` にコピー」ステップを追加

---

## 【改善提案】（可読性・保守性）

### #1 影響分析テーブルの粒度を上げる

- 箇所: 計画 MD §影響スクリプト分析テーブル
- 現状: 6 スクリプトのみ列挙。残り 9 スクリプトは「その他 BC 突合系」で一括。grep で 15 本検出されている
- 提案: 全 15 スクリプトを個別に列挙し、各スクリプトが structure.json のどの部分をどう使っているか（存在チェックのみ / name 一覧取得 / メトリクス詳細参照 / 書き込み）を明記する。本レビューの「既存システムとの統合」節のテーブルを参考にできる

### #2 structure.json と extract_adapter.json の source フィールドの関係を明文化

- 箇所: 計画 MD §設計
- 現状: structure.json の `metrics[].source` のみ定義。extract_adapter.json の `fields[].source` との関係が未記述
- 提案: 両者の意味の違い（structure 側: メトリクス定義の出自、adapter 側: フィールドの作成基準）と、現時点での対応関係（同値）、将来の分岐可能性を計画 MD に1段落追記する。§設計の末尾が適切

### #3 Step 1 の GCS↔ローカル差分チェック手順を具体化

- 箇所: 計画 MD Step 1
- 現状: 「GCS上の全 structure.json をDLし、ローカル470ファイルとハッシュ比較」のみ
- 提案: 使用するコマンド/スクリプト（`sync_adapters.py` の `--dry-run` or 新規ワンライナー）、判定基準（blob 更新日時 vs updated_at フィールド vs content hash）、差分があった場合のマージ方針（GCS 優先 / ローカル優先 / 手動判断）を明記

---

## 【修正例】（必要な箇所のみ）

#### #1 に対する修正案（extract_monthly_data.py の structure metric 集合構築）

```python
# before: scripts/extract_monthly_data.py:3336-3338
_structure_metric_names = {
    m.get("name") for m in (_structure or {}).get("metrics", []) if isinstance(m, dict) and m.get("name")
}

# after: source: "original" を除外し、BC メトリクスのみで adapter 突合
_structure_metric_names = {
    m.get("name") for m in (_structure or {}).get("metrics", [])
    if isinstance(m, dict) and m.get("name") and m.get("source", "bc") != "original"
}
```

#### #2 に対する修正案（build_monthly_extractor.py の parse_structure_metrics）

```python
# before: scripts/build_monthly_extractor.py:214-218
def parse_structure_metrics(structure: dict) -> list[str]:
    """structure.json からメトリクス名一覧を返す（metrics / monthly_items 両対応）"""
    metrics: list[str] = []
    seen: set[str] = set()
    items = structure.get("metrics") or structure.get("monthly_items") or []

# after: source フィルタ追加
def parse_structure_metrics(structure: dict, source_filter: str | None = "bc") -> list[str]:
    """structure.json からメトリクス名一覧を返す（metrics / monthly_items 両対応）.

    Args:
        structure: structure.json dict
        source_filter: "bc" で BC 由来のみ、None で全件。default="bc"
    """
    metrics: list[str] = []
    seen: set[str] = set()
    items = structure.get("metrics") or structure.get("monthly_items") or []
    # source_filter が指定されている場合、該当 source のみ通す
    if source_filter:
        items = [it for it in items if isinstance(it, dict) and it.get("source", "bc") == source_filter]
```

---

## 【確認できなかった事項】

- `monthly_data_load.py` の `extract_monthly_data()` 関数（L521）に渡される `monthly_items` が内部でどう使われるか。`monthly_items` は structure の配列を直接渡しているが、呼び出し先関数の実装が大きく全体を閲読できなかった。`source` フィールドを参照するロジックがあるかは不明
- `compare_monthly_buffett.py` の `check_adapter_definitions()` (L126-175) も `load_structure_bc_names()` を使っている。計画の Step 5 改修で `load_structure_bc_names()` に source フィルタを入れれば自動的にこちらも修正されるが、`check_adapter_definitions()` の呼び出し側で「original メトリクスが structure にあるが adapter にない」を正常として扱うべきか、別の警告を出すべきかの設計判断が必要
- 既存の 470 structure.json に `metrics[]` ではなく `monthly_items[]` キーを使っているファイルが混在しているか。マイグレーションスクリプトが両方のキーに対応する必要があるかは実データを確認しないと判定不能
