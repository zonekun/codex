# 月次開示エラー全自動解決スキル 設計リファレンス

**カテゴリ**: tools | **作成日**: 2026-05-05 | **ステータス**: 実装完了
**親MD**: [`042-1_monthly_error_fix_patterns.md`](042-1_monthly_error_fix_patterns.md)
**関連**: `skills/monthly-error-autofix.md`（正本）, `.claude/commands/monthly-error-autofix.md`（ラッパー）

> 元プランMD `docs/plans/20260505_193700_monthly_error_autofix_skill.md` から移動（2026-05-12）。
> スキル実装完了済み。設計判断の経緯を残すためのリファレンス。

---

## 背景（2026-05-05 セッションの実績）

今回の手動対応フロー:
1. Cloud Run Job ログから DL エラー 1社 / Extract エラー 6社を検出
2. 各社の失敗原因を個別診断:
   - **8515**: ドメイン変更 (aiful.co.jp → muninova.co.jp) → url_adapter 修正
   - **3086**: regex不一致 (▲と数字の間にスペース) → extract_adapter regex修正
   - **9519/7177/8267**: regex では抽出困難 → Gemini extraction_method に変更
   - **7455**: 空文字regex キーがデフォルト値を無効化 → キー削除
3. アダプター修正をGCSにアップロード
4. `--tickers` で対象会社のみ Cloud Run Job 再実行
5. ログ確認で成功を検証

**所要時間**: 約2時間（人間→AI指示のラウンドトリップ込み）
**目標**: エージェント起動1回で自律完了（時間制限なし。品質優先）

---

## エージェントの自律的判断フロー

```
[START]
    │
    ▼
[1. ログ取得] 直近の extract-monthly-data / download-monthly 実行ログを取得
    │
    ▼
[2. エラー分類] 各tickerのエラーを以下のカテゴリに分類
    │
    ├── DL系エラー
    │   ├── (D1) 404/ドメイン変更 → url_adapter の ir_page_url / link_href_pattern 修正
    │   ├── (D2) CSS selector 不一致 → url_adapter の css_selector 修正  
    │   ├── (D3) DNS 解決不可 → skip（企業サイト停止、対応外）
    │   └── (D4) その他ネットワーク → retry 指示のみ
    │
    └── Extract系エラー
        ├── (E1) regex不一致 → PDF/HTMLを読み、extract_adapter regex修正
        ├── (E2) 年月検出失敗 → title regex / ヒューリスティック修正
        ├── (E3) regex限界 → extraction_method: "gemini" に変更
        ├── (E4) Gemini応答パースエラー → プロンプト/response_schema修正
        ├── (E5) 空文字キー問題 → 不要キー削除（コード側は修正済み）
        └── (E6) structure.jsonとのフィールド不一致 → fields alignment
    │
    ▼
[3. 修正適用] 分類に応じた修正を実施
    │  - ローカル adapter JSON を修正
    │
    ▼
[3.5 ローカル検証] 修正adapterで1社テスト実行（★社訓）
    │  python extract_monthly_data.py --tickers <ticker> --no-batch
    │  → 正しい値が抽出されたか確認（Gemini使用あり）
    │  → NG なら修正に戻る
    │
    ▼
[4. 本番投入] GCSアップロード → Cloud Run Job 再実行
    │  gsutil cp → gcloud run jobs execute --args="--tickers,<t1>,<t2>,..."
    │  git commit（修正内容をローカルにも記録）
    │
    ▼
[5. 監視] 30秒間隔でジョブ完了をポーリング（最大10分）
    │
    ▼
[6. 検証] ログから成功/失敗を確認
    │  - 全成功 → 完了報告
    │  - 一部失敗 → 2回目の診断ループ（最大2回）
    │  - 全失敗 → エスカレーション（人間に報告）
    │
    ▼
[7. 報告] LINE通知 + 知見MD更新（新しいパターンがあれば）
```

---

## 各診断ステップの詳細

### Step 1: ログ取得

```bash
# 直近の execution を特定
gcloud run jobs executions list --job=extract-monthly-data --region=us-west1 --limit=1
gcloud run jobs executions list --job=download-monthly --region=us-west1 --limit=1

# ログ読み取り
gcloud logging read "resource.type=cloud_run_job AND ..." --limit=100
```

出力から「失敗=N」「ERROR」「スキップ」を抽出。

### Step 2: エラー分類の判断基準

| ログパターン | 分類 | 対応 |
|---|---|---|
| `HTTP 404` / `status_code=404` | D1 | ドメイン調査→url_adapter修正 |
| `CSS selector` / `リンクなし` | D2 | ページ構造変更対応 |
| `DNS resolution failed` | D3 | skip記録 |
| `regex.*一致なし` / `抽出結果0件` | E1 | PDFテキスト確認→regex修正 |
| `年月.*None` / `ym.*skip` | E2 | title regex修正 |
| `バッチリクエスト: 0 件` | E2/E5 | adapter内の空文字キー確認 |
| `Gemini.*parse.*error` | E4 | プロンプト修正 |

### Step 3: 修正パターン集

#### D1: ドメイン変更対応
1. 現在のurl_adapterから旧ドメイン特定
2. 企業名で新ドメインを推定（WebSearch or Geminiで確認）
3. 新ドメインのIRページ構造を確認
4. url_adapter更新（ir_page_url, link_href_pattern）

#### E1: regex修正
1. GCSからPDFダウンロード（1ファイルのみ）
2. pdfplumberでテキスト抽出
3. 現行regexがマッチしない箇所を特定
4. 正しいregexを生成
5. extract_adapter更新

#### E3: Gemini変換
1. PDFの構造を確認（テーブル/テキスト混在、複雑レイアウト等）
2. structure.jsonからフィールド名取得
3. extraction_method: "gemini" + 適切なプロンプト生成
4. gemini_multi_month / overwrite_past_months の判定

### Step 4-5: 再実行と監視

```bash
gcloud run jobs execute extract-monthly-data --region us-west1 \
  --args="--tickers,<修正済みticker一覧>" --async

# 30秒ポーリング
gcloud run jobs executions describe <exec-name> --region us-west1 \
  --format="value(status.completionTime,status.succeededCount)"
```

---

## 社訓

1. **1社ずつ丁寧に**: 時間制限なし。品質を最優先。バッチ的な推定・一括処理禁止
2. **修正→ローカル検証→本番の3段階は不可侵**: adapter修正後、必ずローカルで1社テスト実行して結果を確認してからGCSアップロード・本番投入

---

## 安全策（ガードレール）

1. **ローカル検証必須**: adapter修正後、`--tickers <t> --no-batch` で1社テスト。Gemini使用はこの検証に限り許可
2. **修正上限**: 1回のスキル実行で修正するのは最大10社。超過時はエスカレーション
3. **Gemini API使用範囲**: (a) ローカル検証実行時 (b) extraction_methodをgeminiに設定する判断のためのPDF構造確認。直接API呼び出しはこの2用途のみ
4. **regex→Gemini変換判断**: regex 2パターン以上試して失敗した場合のみ
5. **既存データ保護**: overwrite_past_months は累積型PDFのみ。個別月PDF銘柄には絶対に付けない
6. **adapter backup**: 修正前に旧アダプターを `_backup` サフィックスで保存（GCS上）
7. **2回失敗でエスカレーション**: 同一tickerが2ループ失敗したら人間に報告して停止
8. **コード変更は含まない**: extract_monthly_data.py 本体の修正が必要な場合はエスカレーション

---

## 3層の知識活用アーキテクチャ

```
エラー検出
  │
  ▼
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
【Layer 1: パターンDB即答】所要 ~1分
  042-1 パターンDB検索 → 既知パターンに一致？
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
  │                         │
  YES → 即適用             NO
  │                         │
  │                         ▼
  │  ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
  │  【Layer 2: 調査戦略に基づく探索】所要 ~5分
  │    コードベース・既存adapter・git履歴から解法を探す
  │  ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
  │    │                         │
  │    解法発見 → 適用           見つからない
  │    │                         │
  │    │                         ▼
  │    │  ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
  │    │  【Layer 3: 全力新規調査】所要 ~10分
  │    │    PDF実物確認・Webページ構造解析・
  │    │    類似企業の公開情報・仮説検証ループ
  │    │  ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
  │    │    │                         │
  │    │    解決 → 適用               解決不能
  │    │    │                         │
  │    │    │                         ▼
  │    │    │                    エスカレーション
  │    │    │                    （人間に報告）
  │    │    │
  └────┴────┴──→ 成功した解法をパターンDBに追記
                          │
                          ▼
                    再実行・検証
```

### Layer 2: 調査戦略ランブック

| 分類コード | 調査戦略 |
|---|---|
| D1 (404/ドメイン変更) | `git log --all --grep='<ticker>'` で過去修正履歴 + WebSearchで企業IR最新URL調査 |
| D2 (CSS selector不一致) | WebFetchでIRページ取得 → 現在のHTML構造を確認 → selector修正 |
| D3 (DNS解決不可) | 企業サイト生存確認。閉鎖なら `_excluded: true` で論理削除 |
| D4 (その他ネットワーク) | 一時障害の可能性 → 翌日再実行で解消するか確認。解消しなければD1-D3再判定 |
| E1 (regex不一致) | (a) GCSからPDF 1件DL→pdfplumberテキスト確認 (b) 同業種他社adapter参照 (c) regex修正 |
| E2 (年月検出失敗) | extract_monthly_data.py の年月判定ロジック(L880-920, L3660-3670)をread + adapter内の空文字キー確認 |
| E3 (regex限界→Gemini) | PDFの複雑度を確認。regex 2パターン失敗で初めてGemini変換を検討 |
| E4 (Gemini応答パース) | tdnet_load_parallel.py のGemini応答処理を参照 + プロンプト/response_schema見直し |
| E5 (空文字キー問題) | adapter内のregexキーに空文字がないか確認（コード側は修正済みだが念のため削除） |
| E6 (フィールド不一致) | structure.json をread → adapter.fields との差分を特定 → fields追加/修正 |
