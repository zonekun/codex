# monthly-error-autofix スキル改修: Geminiローカル検証回数ガード追加

**作成日時**: 2026-05-13 18:22 JST
**ステータス**: 完了（2026-05-13）
**対象ファイル**: `skills/monthly-error-autofix.md`（305行、commit eb4e5f6 時点）
**対象読者**: code-reviewer サブエージェント / 次セッション担当
**目的**: Gemini adapterのローカル検証ループでAPI呼び出しが際限なく増加する問題を防ぐため、同一tickerのローカル検証を最大3回に制限するガードレールを追加する。
**非スコープ**: スキルの既存フロー（Step 0〜7）の構造変更、パターンDB（042-1）の改修、スクリプト本体の修正

---

## 前提サマリ

- 6040プランでローカルGemini検証が76回タイムアウト（Vertex AI経由）した事例が発生
- 現状のスキルでは修復サイクル（Step 3A→Step 4）のループ反復回数に制限がない
- Gemini multi_month + overwrite_past_months の銘柄は1回のテスト実行で複数PDF×複数月分のAPI呼び出しが走る
- ガードレール#6「2回失敗でエスカレーション」はStep 2起点の大ループ（本番投入後の再診断）であり、Step 3A内の修復サイクル反復は対象外

---

## 指摘項目

### P0-1. ローカル検証回数上限の追加

**症状**: Step 3A修復サイクル（L135-166）でNGが出るとStep 3→4のループを繰り返すが、回数上限がない。Gemini adapterの銘柄は毎回API呼び出しが発生し、コスト・時間が爆発する。

**該当箇所**:
- `skills/monthly-error-autofix.md` L246-248（Step 4のNG分岐）
- `skills/monthly-error-autofix.md` L286-293（ガードレールセクション）

**改修方針**:

#### (a) ガードレールセクション（L286-293）に新項目追加

既存の項目8として以下を追加:

```
8. **ローカル検証回数上限**: 同一tickerのローカル検証（Step 4）は最大3回。3回NGでエスカレーション。Gemini adapter銘柄は特にAPI呼び出しコストが大きいため厳守
```

#### (b) Step 4のNG分岐（L247）を更新

現状:
```
- NG → Step 3に戻り修正
```

改修後:
```
- NG → Step 3に戻り修正（同一ticker最大3回まで。3回失敗→エスカレーション）
```

### P0-2. Gemini adapter銘柄の `--since` 絞り込み推奨

**症状**: Gemini multi_month銘柄のローカル検証で、全期間分のPDFにGemini呼び出しが走る。検証目的なら直近1-2件で十分。

**該当箇所**: `skills/monthly-error-autofix.md` L242-244（Step 4のテスト実行コマンド）

**改修方針**: Step 4のテスト実行コマンドの直後に、Gemini adapter向けの補足を追加:

```
Gemini adapterの銘柄は `--since YYYY` で直近年に絞り、API呼び出しを最小化する:
```bash
PYTHONUTF8=1 C:/venvs/investment-agent/Scripts/python.exe scripts/extract_monthly_data.py --tickers <ticker> --no-batch --since <直近年>
```
```

---

## 実装手順

### Step 1: ガードレール追加（P0-1a）

1. L293（ガードレール#7の後）に項目8を追加

### Step 2: Step 4 NG分岐更新（P0-1b）

1. L247の `NG → Step 3に戻り修正` を回数上限付きに変更

### Step 3: Step 4 Gemini絞り込み追記（P0-2）

1. L244のテスト実行コマンド直後にGemini adapter向け `--since` ガイダンスを追加

### Step 4: 検証

1. スキル全体を通読し、追加内容が既存フローと矛盾しないこと確認
2. ガードレール#6（2回失敗でエスカレーション=本番投入後の大ループ）との棲み分けが明確であること確認

---

## チェック項目

- [ ] ガードレール#8として「同一ticker最大3回」が追加されていること
- [ ] Step 4のNG分岐に回数上限への言及があること
- [ ] Gemini adapter向け `--since` 絞り込みガイダンスがあること
- [ ] ガードレール#6（本番2回失敗）との混同が起きない記載であること
- [ ] スキル全体の行数増加が最小限であること（目安: +10行以内）

---

## レビュー追記: 2026-05-13 18:35 JST — code-reviewer

→ `docs/reviews/166_cr_skill_gemini_guard.md`
