# 受注高抽出ソルジャー spec 改修プラン（PyMuPDF経路一本化 + 既知問題是正） 改訂版

**作成日時**: 2026-05-18 22:55 JST（v1）/ 2026-05-18 23:15 JST（v2: 210レビュー全採用反映）
**ステータス**: 未着手（レビュー対応版）
**対象ファイル**: `skills/orders_soldier.md`（493行、commit a8515859 時点）
**対象読者**: code-reviewer サブエージェント / 次セッション担当
**目的**: 偽陽性銘柄を LLM トークンを消費せず除外するため、PDF テキスト抽出を **PyMuPDF 経路に一本化**（CHUNK_TEXT 経路は 167A 事例で PDF 全文をカバーしないため採用不可）、加えて 2026-05-18 resume 32件実走で観察された 5 件のソルジャー spec 違反を是正する。BQ クエリは **MAIN_CATEGORY include list ホワイトリスト方式**を採用してユーザー指定 4 カテゴリのみを対象とする。

> **分類**: (b) 継続改修型
> **フォーマット正本**: `skills/planning.md` §改修プラン MD フォーマット / `_template_refactor.md`

---

## 前提サマリ

- **過去修正**:
  - commit 354bcecc (2026-05-18): 初版 ソルジャー/コマンダー spec 新規追加（204/205 レビュー全採用）
  - commit 4ac4e7ff (2026-05-18): ソルジャー改善5点
  - commit 3042d8b2 (2026-05-18): heartbeat v1（206レビュー対応）
  - commit a8515859 (2026-05-18): heartbeat v2（207レビュー対応）
- **残存**: 本プランで扱う 8 項目（P0×3、P1×5）
- **実機検証**: 2026-05-18 build/resume 計 62件で観察済み。**本プロジェクトは個人用解析パイプラインで dev/prod 区別なし**
- **環境前提（実機照合済）**:
  - `pyproject.toml` に `pymupdf>=1.27.2.2` 登録済（210 重大#3 で確認）
  - `uv.lock` 記録済 / venv に fitz/pymupdf インストール済
  - `data_catalog/bq_tdnet_documents.md` 確認済（MAIN_CATEGORY='受注高/受注残高' は実存せず）
  - BQ 実機検証（210 重大#1 ): MAIN='受注高/受注残高' は 0 件、SUB に '受注高/受注残高' は 27,526 件
- **関連 incident**:
  - 167A 誤分類: CHUNK_TEXT が PDF 全文をカバーしない（決算短信「受注の実績」セクション欠落）
  - 166A 中断事例: 「並行 agent 競合」を理由に spec違反中断
  - 153A note 違反: spec ドメイン外キー独自付与
  - 166A docs_with_data=13 違反: spec 上 max=6

---

## 優先度の定義

- **P0**: 改修目的（token 削減）達成必須 / spec 仕様違反で誤データを生む
- **P1**: 削減効率を高める / 解釈ブレを抑える / 構造的バグ防止
- **P2**: なし

---

## 設計上の重要決定

1. **PyMuPDF 経路一本化の根拠**: 167A 事例で CHUNK_TEXT は PDF 全文をカバーしないと判明。ユーザー指示「トークン削減優先、GCS 費用・時間は無視」に合致
2. **キーワード正規表現はユーザー確定リスト**（前ターン採用済、約50ワード）。spec 内に直書きで一元管理
3. **スペース除去前処理**: PyMuPDF 抽出テキストの「受 注 の 実 績」スペース挟み対策。**ヒットページ判定時にのみ実施**（元ページ番号は維持）。後続セクションで重複言及せず本決定を参照
4. **BQ クエリは include list 方式**: ユーザー判断で明示的に「対象」と確定した 4 MAIN_CATEGORY のみ:
   - `決算短信` / `決算説明資料` / `その他（未分類）` / `受注高受注残高`（バグ表記、6324系四半期受注速報を救済）
   - 新カテゴリ出現時は自動除外 → 明示追加待ち
5. **画像PDF / 抽出失敗 fallback**: PyMuPDF テキスト抽出文字数が極端に少ない（例: 全PDF合計500文字未満）場合は Read tool fallback を必須化（210 重大#2 対策）
6. **コマンダー連動改訂は別プラン**: 本プランはソルジャー側のみ
7. **CHUNK_INDEX 罠回避**: BQ クエリの WHERE 句に `CHUNK_INDEX IS NOT NULL` を**絶対に付けない**。2026-05-18 以前のロードは CHUNK_INDEX が全件 NULL のため、付けると過去データ全件を誤って除外する。本プランは PyMuPDF 一本化方針のため CHUNK_TEXT を直接使わないが、将来 CHUNK_TEXT 補助検索を追加する場合に同条件を組み込まないよう spec §注意事項にも明記する

---

## 指摘項目

### P0-1. PDF テキスト抽出を PyMuPDF 経路に一本化 + 画像PDF fallback 🚨

**症状**:
- 現行 spec §Step 4-5 では「LLM 直読」方式。偽陽性銘柄（40%）も全文 Read してから failed_no_data 判定 → 1 件あたり数万〜十万トークン浪費
- 60 件 build で総トークン消費は数百万〜千万トークン規模
- CHUNK_TEXT 代替案も検証したが、167A 事例で **CHUNK_TEXT は PDF 全文を保証しない**ことが判明、不採用

**該当**: `skills/orders_soldier.md` § Step 4 (PDF ダウンロード) / § Step 5 (PDF 読み取り)

**根本原因**:
- spec が PDF 全体を LLM に渡す前提で書かれている
- 偽陽性銘柄の事前判定がない

**修正方針**:

新 Step 4 (PDF ダウンロード + PyMuPDF 抽出 + キーワード検索を **1 回の python サブプロセス**で実施、改善#1 取り込み):

```bash
# 1. PDF DL (既存)
gsutil cp "gs://stock_data_1930932/tdnet/{ticker}/{FILE_NAME}" "/c/tmp/tdnet_orders/{ticker}_{idx}.pdf"

# 2. ハートビート更新 (PyMuPDF処理直前、改善#10/207重大#2再発防止)
date -Iseconds > "/c/gdrive/claude/work/_heartbeat/{ticker}.hb"

# 3. PyMuPDF 抽出 + キーワード検索を 1 回でまとめて実施
PYTHONUTF8=1 python <<'PYEOF'
import fitz, json, re, sys, glob
# キーワード正規表現（spec §キーワードリスト で一元定義）
pattern = re.compile(r'受注高|受注残|受注金額|受注工事|受注件数|受注棟数|受注戸数|受注社数|受注組数|受注機数|受注案件|受注実績|受注の実績|受注済|受注額|受注予想|受注予定|受注契約|受注ライセンス|受注LT|受注平均単価|新規受注|当期受注|繰越|手持工事|手持高|契約獲得|契約残高|仕入棟|仕入件|仕入区画|管理戸|管理棟|管理件|棚卸資産件|棚卸資産残|販売件|販売数|販売棟|販売戸|販売区画|販売台|売上件|売上棟|売上戸|売上数|売上数量|受入数|受入純増|パイプライン|生産高|生産実績')
result = {}
total_chars = 0
for pdf_path in sys.argv[1:]:
    try:
        doc = fitz.open(pdf_path)
    except Exception as e:
        result[pdf_path] = {'error': str(e), 'hit_pages': [], 'total_chars': 0}
        continue
    hits = []
    chars = 0
    for i, page in enumerate(doc, 1):
        text = page.get_text("text")
        chars += len(text)
        # スペース除去後に検索（設計決定 §3）
        if pattern.search(re.sub(r'\s+', '', text)):
            hits.append(i)
    result[pdf_path] = {'hit_pages': hits, 'total_chars': chars, 'page_count': len(doc)}
    total_chars += chars
print(json.dumps(result, ensure_ascii=False))
PYEOF
# 引数で対象PDFを渡す: $(ls /c/tmp/tdnet_orders/{ticker}_*.pdf)
```

新 Step 5 (ヒットページのみ Read tool + 画像PDF fallback):

```
各 PDF について:
- 該当PDFの hit_pages が空 かつ total_chars >= 500: 偽陽性として Read スキップ、DOCS_READ にカウントしない
- 該当PDFの hit_pages が空 かつ total_chars < 500: 画像PDF/抽出失敗の可能性 → 旧来の Read tool fallback 起動（spec §画像PDF fallback）
- 該当PDFの hit_pages があれば: Read tool で pages=<該当ページ> を指定して LLM 読み込み（hb 更新を Read 直前に実施）
```

**画像PDF fallback の詳細（spec §Step 5 §抽出失敗時の fallback として新設）**:
- 判定閾値: PDF 全ページ合計テキスト 500 文字未満
- 動作: Read tool に pages 指定なしで PDF 全文を投げる（旧 spec の挙動）
- 結果: ヒット 0 でも DOCS_READ にカウント（画像PDFを「処理した」と扱う）
- fitz.open() 例外時も同じ fallback パスに合流

**呼び出し側への波及**:
- なし（ソルジャー内部実装変更、戻り値フォーマット不変）

**検証**:
- 1301 (極洋、failed_no_data 確定): 全 PDF ヒットゼロ かつ total_chars >= 500 → Read スキップ + DOCS_WITH_DATA=0 + failed_no_data
- 167A (リョーサン菱洋、completed_partial): PyMuPDF テキスト + スペース除去後検索で「受注の実績」セクションがヒット → Read で抽出
- 1414 (ショーボンド、completed): 全 PDF ヒットあり → 該当ページ Read で全データ抽出

**ロールバック**: spec の Step 4-5 を旧版 (commit a8515859) に revert + `rm -f /c/tmp/tdnet_orders/*.pages.json` で残骸削除（改善#6 取り込み）

---

### P0-2. BQ クエリを include list ホワイトリスト方式に変更 🚨

**症状**:
- 現行 spec §Step 2 BQ クエリ: `(MAIN = '受注高/受注残高' OR EXISTS(SUB ...))` だが MAIN 値として「受注高/受注残高」は実存せず、実質 SUB のみで拾っている
- WHERE 末尾に `MAIN NOT IN (...)` を後付けすると SUB 経由で正当な受注情報を持つ文書を巻き添えで除外する集合論バグ
- ユーザー判断: 明示的に「対象」と確定した 4 MAIN_CATEGORY のみ対象とする ホワイトリスト厳格運用

**該当**: `skills/orders_soldier.md` § Step 2 BQ SQL

**根本原因**:
- spec 設計時に MAIN_CATEGORY 別の特性と Gemini 分類仕様を未照合
- 「受注高/受注残高」が MAIN/SUB で表記揺れ（スラッシュ有無）している実機データを確認しなかった

**修正方針**:

```sql
SELECT DISTINCT
  TICKER, FILER_NAME, DOC_ID, FILE_NAME,
  SUBMISSION_DATE, DOC_TITLE,
  MAIN_CATEGORY, SUB_CATEGORIES
FROM `gmailpj-357912.STOCK.TDNET_DOCUMENTS_ENHANCED`
WHERE TICKER = '{ticker}'
  AND EXISTS (SELECT 1 FROM UNNEST(SUB_CATEGORIES) AS sc WHERE sc = '受注高/受注残高')
  AND MAIN_CATEGORY IN ('決算短信', '決算説明資料', 'その他（未分類）', '受注高受注残高')
  AND AI_STATUS = 'completed'
  AND SUBMISSION_DATE BETWEEN '2022-01-01' AND CURRENT_DATE("Asia/Tokyo")
ORDER BY SUBMISSION_DATE DESC
```

**ポイント**:
- include list で 4 MAIN_CATEGORY のみ対象（決算短信/決算説明資料/その他（未分類）/受注高受注残高）
- 「受注高受注残高」(/なし、6324系四半期受注速報 25件) を含めて救済
- 新カテゴリ出現時は自動除外、明示追加待ち（ユーザー指示）

**呼び出し側への波及**:
- **コマンダー spec の build モード BQ クエリにも同じ MAIN_CATEGORY include list を追加必須**
- 別プラン対応だが、本プラン適用とコマンダー spec 適用を**同時 commit すること**を §検証戦略 §smoke test ゲートに明記

**検証**:
- BQ で 60件 build 対象を実機集計し、改修前後の docs_found 数を比較
- 1431 (リブワーク): 月次開示・業績修正系は除外、決算短信3+決算説明資料2 = 5件残ることを確認
- 6324 (ハーモニック・ドライブ): 「受注高受注残高」MAIN 文書 24件が含まれることを確認

**ロールバック**: WHERE 句を元の `(MAIN = '受注高/受注残高' OR EXISTS(SUB ...))` に戻す。

---

### P0-3. DOCS_WITH_DATA の cap と計算アルゴリズム明示 🚨

**症状**:
- 166A ソルジャーが DOCS_WITH_DATA=13 を返却（spec 上 max=6）
- ソルジャーが「JSON 内の d[] 要素数」と「数値抽出できた PDF 数」を混同

**該当**: `skills/orders_soldier.md` § Step 8 §戻り値フォーマット

**根本原因**: spec の DOCS_WITH_DATA 定義「PDF 数」が弱く、ソルジャーが d[] 要素数で誤代入

**修正方針**:

§ Step 8 §戻り値フォーマットを以下に強化（改善#9 アルゴリズム式提示 取り込み）:

```
DOCS_WITH_DATA: <数値抽出できた PDF 数。0 ≤ DOCS_WITH_DATA ≤ DOCS_READ ≤ DOCS_FOUND ≤ 6 を必ず満たすこと>

**計算式（必須）**:
  DOCS_WITH_DATA = len(set(d_elem['doc_id'] for d_elem in d))
  （JSON の d[] 要素数 len(d) ではなく、d_elem['doc_id'] のユニーク数）
```

§ 禁止事項に追加:

```
- DOCS_WITH_DATA に len(d) を返さない。必ず len(set(d_elem['doc_id'] for d_elem in d)) で計算する
```

**呼び出し側への波及**:
- コマンダー §Step 4B 数値パース時に「DOCS_WITH_DATA > DOCS_READ なら DOCS_READ にキャップ + ログ警告」を追加（防御的二重キャップ、別プラン）

**検証**: 166A 再投入で DOCS_WITH_DATA=6 が返ることを確認

**ロールバック**: 強化文・計算式を削除。

---

### P1-1. note ドメイン外キーの徹底排除（既知違反: 153A） ⚠️

**症状**: 153A が note に独自キー `saas_subscription_business` 付与

**該当**: `skills/orders_soldier.md` § Step 6 §note ドメイン規約 / §禁止事項

**根本原因**: 現行禁止記述が「禁止」と書くだけで違反例を示していない

**修正方針**:

§ note ドメイン規約に**ソルジャー側ルール強化**:

```
許可キー: partial / reason= / unit_mixed / pages_retry_used のみ
**それ以外の独自キー（saas_subscription_business 等）は禁止**
（コマンダー側のパース仕様は orders_commander.md に分離 - 単一責任原則）
```

§ 禁止事項を強化:

```
- note に許可キー (partial / reason= / unit_mixed / pages_retry_used) 以外を書かない
  例: saas_subscription_business, metric_is_contract_acquisition_not_orders 等の独自タグ全て禁止
```

**呼び出し側への波及**: なし

**検証**: 153A 再投入で note が許可キー形に正規化されることを確認

**ロールバック**: 追記削除。

---

### P1-2. 並行 agent 競合心配 + タスク中断全般の徹底排除（既知違反: 166A 初回） ⚠️

**症状**: 166A 初回ソルジャーが「並行 agent 競合」で処理中断（spec違反、prompt 強化で復帰）

**該当**: `skills/orders_soldier.md` § Step 1 / § 禁止事項

**根本原因**: 既存記述が「ファイル単位の競合」のみ排除、「処理進行の競合」「自己判断中断」を排除する文言がない

**修正方針**:

§ Step 1 「並行 agent との競合は心配無用」を強化:

```
- 出力ファイル名は ticker 単位で完全分離、ファイル競合は構造的に発生しない
- task-notification や JSONL ログで他 ticker の処理進行が見える場合があるが、それは他 ticker の話で自分の処理に一切影響しない
- 「他に複数 BG agent が稼働中」「重複起動回避」「既存ファイル尊重」「並行作業を妨げない」等の理由で処理中断・上書きスキップしてはならない
```

§ 禁止事項にメタルール追加（改善#8 取り込み）:

```
- 他 ticker を処理中の並行 agent の存在を理由に、自分の ticker 処理を中断・スキップ・既存ファイル尊重しない
- **タスク開始後の自己判断による処理中断は spec 違反として禁止**
  （spec が明示的に中断条件として定義しているケース＝ハートビート 30 分超 / context 枯渇接近 等を除く）
```

**呼び出し側への波及**: なし

**検証**: 166A 再投入で中断なく完了

**ロールバック**: 強化文削除。

---

### P1-3. HIGHLIGHTS 上限の徹底（既知傾向: 5行超散発） ⚠️

**症状**: 1417 / 166A / 150A で HIGHLIGHTS が 12-15 行に達した

**該当**: `skills/orders_soldier.md` § Step 8 §戻り値フォーマット §容量制約

**修正方針**:

容量制約を再強調（改善#7 根拠リンク追加 取り込み）:

```
**容量制約（厳守）**:
- HIGHLIGHTS: 最大 5 行 / 1 行 100 文字以内
  （コマンダーは HIGHLIGHTS をインデックス・ログ共に記録しない仕様
   - 根拠: skills/orders_commander.md L530「ソルジャー戻り値の文言（HIGHLIGHTS 等）はインデックスには記録しない（ログにも記録しない。容量肥大防止）」
  - ただし HIGHLIGHTS は debug 用に残す価値あり、空にせず要点を簡潔に絞る）
- ERRORS: 該当なしは `なし` 1 行のみ
```

§ 禁止事項に追加:

```
- HIGHLIGHTS を 5 行超で返却しない（要点を絞ること、空にはしない）
```

**呼び出し側への波及**: なし

**検証**: 1417 級複雑銘柄でも 5行以内

**ロールバック**: 強調文削除。

---

### P1-4. キーワードリストを spec 内に明示（DRY、設計決定 §3 と統合） ⚠️

**症状**: 新 PyMuPDF 経路の「キーワードヒットページ特定」がコア処理。Python コード片に直書きすると変更時に複数箇所修正

**該当**: `skills/orders_soldier.md` § Step 5 §抽出対象

**修正方針**:

§ Step 5 に新セクション「キーワードリスト（前処理フィルタ用）」を追加。

```markdown
#### キーワードリスト（前処理フィルタ用）

PyMuPDF 経由のテキストに対し以下の正規表現でヒットするページのみを Read tool で LLM 読み込みする。0 ヒットの PDF は失敗判定（画像 PDF fallback 経路を除く）。

```regex
受注高|受注残|受注金額|受注工事|受注件数|受注棟数|受注戸数|受注社数|受注組数|受注機数|受注案件|受注実績|受注の実績|受注済|受注額|受注予想|受注予定|受注契約|受注ライセンス|受注LT|受注平均単価|新規受注|当期受注|繰越|手持工事|手持高|契約獲得|契約残高|仕入棟|仕入件|仕入区画|管理戸|管理棟|管理件|棚卸資産件|棚卸資産残|販売件|販売数|販売棟|販売戸|販売区画|販売台|売上件|売上棟|売上戸|売上数|売上数量|受入数|受入純増|パイプライン|生産高|生産実績
```

**スペース除去前処理**: 設計上の重要決定 §3 のとおり、各ページテキストに `re.sub(r'\s+', '', text)` を適用してから検索する。元のページ番号は維持。

**抽出対象（LLM 読み込み時の指示用、既存セクション）と本キーワードリストの整合義務**:
本リストは PyMuPDF 前処理フィルタ、抽出対象セクション (§Step 5 §抽出対象) は LLM 抽出指示。両者は目的が違うが、新キーワード追加時は両方を更新すること。
```

**呼び出し側への波及**: なし

**検証**: 167A 決算短信でスペース除去後に「受注の実績」がヒット

**ロールバック**: セクション削除。

---

### P1-5. PyMuPDF 依存の spec 注意事項明記（既登録済の事実反映） ⚠️

**症状（210重大#3 修正版）**:
- `pyproject.toml` に `pymupdf>=1.27.2.2` 既登録、venv インストール済（2026-05-18 確認）
- 旧プラン記述「インストール済か不明」「ImportError 対策必要」「uv add 同時実施」は全て事実誤認
- spec MD には PyMuPDF 依存が明記されていない → 注意事項に明記して再現性担保のみ必要

**該当**: `skills/orders_soldier.md` § 注意事項

**根本原因**: 旧プラン作成時に pyproject.toml を Read していなかった

**修正方針**:

§ 注意事項に追加（依存追加処理は不要、明記のみ）:

```
- **PyMuPDF (`pymupdf` / `import fitz`) は本ソルジャーの必須依存**: pyproject.toml に `pymupdf>=1.27.2.2` 登録済、venv `C:/venvs/investment-agent` にインストール済（2026-05-18 確認）。再現性担保のため uv sync で venv 再構築すれば自動的に入る
- **PyMuPDF AGPL ライセンス**: 個人用解析パイプラインのため AGPL OK（非配布）
```

**呼び出し側への波及**: なし

**検証**: `python -c "import fitz; print(fitz.__version__)"` で 1.27.2.2 確認

**ロールバック**: 注意事項記載削除（依存自体は他用途で使用中の可能性があり残す）。

---

## 対応アンチパターン

| plan ID | 004 | T-x | G-x |
|---|---|---|---|
| P0-1 | LLM全文直読の無条件適用 | T-該当なし | — |
| P0-2 | OR条件の集合論誤用 | T-該当なし | — |
| P0-3 | 戻り値計算アルゴリズム不在 | — | — |
| P1-1 | 自由記述ノイズ | — | — |
| P1-2 | 並行処理誤判断 / 自己中断 | — | — |
| P1-3 | 戻り値容量 | — | — |
| P1-4 | DRY違反 / 一元管理不在 | — | — |
| P1-5 | 既存環境前提未確認 | — | — |

> 参照: `docs/knowledges/tools/004_coding_conventions.md` §確認優先 §効率化

---

## 検証戦略

> **個人用パイプラインで dev=prod**（プラン §前提サマリ §実機検証 通り）。dev/prod 区別なし。smoke test のみ別段の検証層。

1. **smoke test (5件)**:
   - 1301 (極洋、真の偽陽性): PyMuPDF ヒットゼロ + total_chars>=500 → Read スキップ → DOCS_WITH_DATA=0 → failed_no_data。所要 token < 1000
   - 167A (リョーサン菱洋、completed_partial): スペース除去後検索で「受注の実績」ヒット → Read で抽出 → completed_partial
   - 1414 (ショーボンド、completed): 全 PDF ヒットあり → 該当ページ Read → completed
   - 148A (パーキング SaaS、failed_no_data): 同 1301 パターン
   - 1719 (安藤ハザマ、completed): 大規模ticker、PyMuPDF + 該当ページ Read で 6件抽出
2. **dev=prod 実機**: 残 5 件 pending (1734/1736/1737/1739/1743) で改修後 spec を実走 → トークン消費を測定。現状の 1/8 程度を期待
3. **本番適用判断基準（dev=prod のため一体）**: smoke 5件 PASS かつ 1301 級真陽性がゼロ token で正しく判定、1414 級が従来通り抽出
4. **回収手順**:
   - 改修後最初の5件で誤判定が出た場合、ticker 単位で旧 spec (commit a8515859) revert で個別救済
   - 画像 PDF fallback が機能していれば真陽性銘柄の誤skipは起きないはず
5. **同時 commit ゲート**: P0-2 BQ クエリ改修は **コマンダー側 build モード SQL の同期改訂と必ず同時 commit する**こと（時間差で大量 failed_no_bq_records 副作用防止）

---

## 関連ドキュメント

- ソルジャー spec: `skills/orders_soldier.md`
- ラッパー: `.claude/commands/orders-soldier.md`
- コマンダー spec: `skills/orders_commander.md`（P0-2 / P0-3 連動改訂対象、別プラン）
- 過去レビュー: 204 / 206 / 207 / 210
- 実機データ: `C:/gdrive/claude/work/_index/orders_index.csv` + `orders_log.tsv`
- 167A検証PDF: `C:/Users/zonekun/Dropbox/stock/temp/167A_verification/`

---

## スコープ外

- コマンダー spec 連動改訂（P0-2 / P0-3 連動）→ 別プラン（ただし§検証戦略 §5 で同時 commit ゲート明記）
- PyMuPDF OCR 機能（画像化 PDF の OCR 抽出）→ 別プラン。本プランは画像PDF を Read tool fallback で吸収
- LLM 直読 vs PyMuPDF テキスト品質比較 → smoke test で 5件サンプル確認
- 失敗時自動再投入（resume 自動化）→ コマンダー側課題
- キーワードリストの別ファイル化 (improvement #2) → spec内一元で十分、別ファイル化はオーバーキル

---

## レビュー対応記録（v2 改訂、210 全採用）

**対応日**: 2026-05-18
**ユーザー判断**: 「他で異論ないものは取り込み」+「include list ホワイトリスト厳格運用、4 MAIN_CATEGORY 確定」

### 重大3件
- **#1 BQ 集合論バグ** → [採用] include list 方式に書換、対象 4 MAIN_CATEGORY 明示
- **#2 画像PDF誤skip構造** → [採用] PyMuPDF 抽出文字数閾値 (500文字) + Read tool fallback を P0-1 §修正方針に追加
- **#3 PyMuPDF 依存事実誤認** → [採用] P1-5 を全面書換、pyproject.toml 既登録の事実反映

### 改善提案10件
- #1 サブプロセス1回まとめ → [採用] P0-1 §修正方針の python ヒアドキュメント1回呼び化
- #2 キーワード外部JSON化 → [異論あり 不採用] spec内一元で十分、別ファイル化はオーバーキル
- #3 スペース除去前処理位置 → [採用] §設計上の重要決定 §3 で1回触れて参照統一
- #4 アンチパターン表 T-? → [採用] 「T-該当なし」明示
- #5 dev=prod 明示 → [採用] §検証戦略 冒頭で明示
- #6 .pages.json cleanup → [採用] P0-1 §ロールバックに追加
- #7 P1-3 「コマンダー破棄」根拠 → [採用] L530 リンク追加
- #8 並行 agent メタルール → [採用] §禁止事項に「タスク開始後の自己判断による処理中断は禁止」追加
- #9 P0-3 アルゴリズム式 → [採用] `len(set(d_elem['doc_id'] for d_elem in d))` 計算式追加
- #10 hb 更新タイミング統合 → [採用] §修正方針本体に hb 更新コード明示

### 確認できなかった事項5件
- MAIN_CATEGORY × SUB 実機分布 → [実機検証済] 27,526件のSUB分布確認、MAIN='受注高/受注残高' 0件
- 1431/1719 MAIN内訳 → [実機検証済]
- 画像PDF実存件数 → [smoke test §1 に組み込み済]
- PyMuPDF サブプロセスオーバーヘッド実測 → [改善#1 で 1回まとめ化により大幅削減、別途実測不要]
- コマンダー連動改訂タイミング → [§検証戦略 §5 同時 commit ゲート明記で対応]
