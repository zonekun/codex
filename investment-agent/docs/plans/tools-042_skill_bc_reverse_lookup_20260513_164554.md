# monthly-error-autofix スキル拡充: BC値逆引き診断技法の追加

**作成日時**: 2026-05-13 16:45 JST
**ステータス**: 完了（2026-05-13）
**対象ファイル**: `skills/monthly-error-autofix.md`（273行、commit 11dd9d9 時点）
**対象読者**: code-reviewer サブエージェント / 次セッション担当
**目的**: 抽出値とBC値のアンマッチ時に「数値から列を逆引き特定する」診断技法をスキルに追加する。スキルの設計思想（AIの全知能で自律リカバリ）を維持しつつ、逆引きという強力な武器を「知っている状態」にする。
**非スコープ**: スキルの既存フロー（Step 0〜7）の構造変更、パターンDB（042-1）の改修、スクリプト本体の修正

---

## 前提サマリ

- 現状のスキルはStep 3A修復サイクルで「現物を見て直す」アプローチのみ。アンマッチ時に「BC側の数値から正解列を特定する」手段がない
- 042マスターMD §bc_ignore判定基準 に5ステップの逆引き手順が存在するが、bc_ignore判定専用でありスキルから未参照
- `reconcile_bc_key_from_compare.py` が全field×誤差調整マトリクスの自動逆引き機能を持つが、スキルフローに未統合
- `monthly_bc_repair/` にRUNBOOK・inspect_ticker.py・reextract_and_compare.py 等のツール群があるが、スキルから参照されていない

---

## 優先度の定義

- **P0**: スキル実行時にアンマッチ原因を特定できず修復不能になる問題の解消
- **P1**: 既存ツール群との導線確保

---

## 指摘項目

### P0-1. BC値逆引き診断技法の追加

**症状**: 抽出値がBC値と不一致の場合、エージェントには「regexを修正してローカル検証」のループしか手段がない。抽出された数値がどのBC列に対応するのか（客数？単価？前年比？）を判断する方法がスキルに記載されておらず、field誤認（E4-2/E4-3相当）の修復が困難。

**該当**: `skills/monthly-error-autofix.md` Step 3A「判断の指針」〜「修復サイクル」

**改修方針**:

Step 3A の「判断の指針」セクションの直後に**「診断の武器庫（修復サイクルで行き詰まったとき）」**セクションを新設する。以下の3点を「修復に行き詰まったとき・field対応が不明なとき、必要に応じて使う技法」として記載:

#### (a) BC値直接照合（手動・少数field向け）

```
bc_monthly_kpi.csv → 当該ticker・直近年月のBC値一覧を取得
→ ソース文書（PDF/HTML）内でその数値を検索（単位変換 ×100, ÷1000 等を考慮）
→ 見つかった位置からfield対応を特定
```

- 042マスターMD §bc_ignore判定基準 の手順1-5を参照先として示す
- ツールレシピ: ローカルCSVまたはBQ `monthly_kpi` からの取得ワンライナー
- **Step 3Cとの棲み分け**: この技法はfield特定が目的。データ自体が存在しないと判明した場合はStep 3Cへ（CR#165-1）

#### (b) reconcile自動逆引き（多数field・系統的ズレ向け）

```
reconcile_bc_key_from_compare.py: NG/BC_NODATA行に対し全BC field × 誤差調整
（identity/yoy±100/×N/÷N/符号反転等）で値一致率を計算し、bc_key候補を提案
```

- 「抽出はできているが、どのBC列に対応するか分からない」場合の決定打
- **前提条件**: compare CSV（`compare_monthly_buffett.py`の出力）が手元にある場合に使える。なければ先に`compare_monthly_buffett.py`を実行するか、(a)の手動照合を使う（CR#165-2）
- コマンド例と出力フォーマットの要約（詳細はスクリプトdocstring参照）

#### (c) inspect_ticker.py（事実確認の起点）

```
monthly_bc_repair/inspect_ticker.py: adapter・records・BC値・PDF構造を一括表示
→ 何が取れていて何が取れていないかの全景把握
```

### P0-2. 記載トーンの方針

**重要**: スキルの設計思想は「チェックリスト消化」ではなく「AIの全知能による自律リカバリ」。追加セクションは以下のトーンで書く:

- **「やるべきこと」リストではなく「使える武器」リスト**。いつ使うかはエージェントが判断する
- 「数値がどのfieldか分からないとき」「修復サイクルで2回以上失敗したとき」等、使いどころの指針は示すが、必須ステップにはしない
- 既存の「要するに: 現物を見て、スキーマを知っていて、直す」の精神と矛盾しない位置づけ

### P1-1. Step 3Cツールレシピの拡充

**症状**: Step 3Cの「BC structure.json逆引き」レシピがメトリクス名の一覧表示のみで、BC**値**の取得レシピがない。

**該当**: `skills/monthly-error-autofix.md` L188

**改修方針**: 既存レシピの隣にBC値取得レシピを追加:

```bash
# BC値取得（直近年月）
PYTHONUTF8=1 <python> -c "
import csv, sys
ticker = sys.argv[1]
with open('data/csv/bc_monthly_kpi.csv', encoding='utf-8') as f:
    rows = [r for r in csv.DictReader(f) if r['ticker'] == ticker]
    for r in sorted(rows, key=lambda x: x['year_month'], reverse=True)[:10]:
        print(f\"{r['year_month']} {r['field']:30s} {r['value']}\")
" <ticker>
```

---

## 実装手順

### Step 1: スキル改修（P0-1 + P0-2）

1. `skills/monthly-error-autofix.md` のStep 3A「判断の指針」の直後、「修復サイクル」の直前に**「診断の武器庫」セクション**を挿入
2. (a)(b)(c) の3技法を、P0-2のトーン方針に従い記載
3. 各技法にツールレシピ（コマンド例）を添える。ただし最小限（詳細はスクリプトdocstring/042マスターMD参照）

### Step 2: Step 3Cツールレシピ拡充（P1-1）

1. 既存の「BC structure.json逆引き」レシピの直後にBC値取得レシピを追加

### Step 3: 検証

1. スキル全体を通読し、追加セクションが既存フローと矛盾しないこと確認
2. 特に「やらないこと」セクション（L136-141）との整合性確認 → 逆引きは「パターンDB事前ロード」とは異なるため矛盾しない

---

## チェック項目

- [ ] 追加セクションが「必須ステップ」ではなく「利用可能な技法」として記載されていること
- [ ] 042マスターMD §bc_ignore判定基準 への参照が含まれていること
- [ ] reconcile_bc_key_from_compare.py のコマンド例が含まれていること
- [ ] inspect_ticker.py への導線が含まれていること
- [ ] Step 3Cツールレシピにbc_monthly_kpi.csv からの値取得レシピが追加されていること
- [ ] 既存の「やらないこと」セクションと矛盾しないこと
- [ ] スキル全体の行数が過度に増加しないこと（目安: +30行以内）

---

## レビュー追記: 2026-05-13 17:10 JST -- code-reviewer

→ `docs/reviews/165_cr_skill_bc_reverse_lookup.md`
