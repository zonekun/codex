# 受注高抽出ソルジャー spec 改修プラン v2（B+C ハイブリッド方式 / PyMuPDF text + pdfplumber tables）

**作成日時**: 2026-05-19 00:10 JST / **改訂 v2**: 2026-05-19 (212 レビュー全採用、#2 除く)
**ステータス**: v2 実装完了（commit hash は §実装記録 参照）
**対象ファイル**: `skills/orders_soldier.md`（629 行、commit 5ea7ed39 時点）
**対象読者**: code-reviewer サブエージェント / 次セッション担当
**目的**: 2026-05-18/19 resume 実走で観察された「PyMuPDF 経路一本化版」のトークン削減限界（5 件平均 85,468 token、偽陽性ゼロのため skip 発火せず）を打破するため、**Read tool を実質スキップし、PyMuPDF が抽出したテキスト + pdfplumber が抽出した表 JSON を assistant に直接渡す B+C ハイブリッド方式**へ移行する。併せて、PyMuPDF 一本化を徹底するために spec 内の CHUNK_TEXT 関連記述を削除する（CHUNK_INDEX IS NOT NULL 罠回避記述は BQ 一般注意として残す）。

**v2 変更点 (212 レビュー反映)**:
- 表抽出ライブラリを **PyMuPDF `find_tables()` → pdfplumber `extract_tables()`** に切替（062 知見§4.1「PyMuPDF find_tables は pdfplumber より弱い」既知、月次開示 `scripts/extract_monthly_data.py` と仕組み統一）
- `tbl.extract()` 戻り値正規化規約 (結合セル None / 改行 / △ ハンドリング) 明文化
- silent skip 禁止: `table_extract_errors` 配列にエラー記録、assistant が ERRORS/HIGHLIGHTS に転記
- 複数表混在時の判定基準・表vs text 優先規約・閾値根拠・中間ファイル命名規約を spec に明示
- 不採用: pages_data サイズ上限ガード（データ取りこぼし回避を優先）

> **分類**: (b) 継続改修型
> **フォーマット正本**: `skills/planning.md` §改修プラン MD フォーマット / `_template_refactor.md`

---

## 前提サマリ

- **過去修正**:
  - commit 0d407011 (2026-05-18): PyMuPDF 経路一本化 + 既知問題是正（210 レビュー全採用）
  - commit 6e762b33 (2026-05-18): キーワード追加「受注.{0,8}推移」
- **残存**: 本プランで扱う 2 項目（P0×2）
- **実機検証**: 2026-05-18/19 resume 計 5 件で観察済み（平均 85,468 token、PyMuPDF 経路導入後も偽陽性ゼロのため skip 発火なし）
- **環境前提（実機照合済）**:
  - `pyproject.toml` に `pymupdf>=1.27.2.2` 登録済
  - venv `C:/venvs/investment-agent` に fitz/pymupdf インストール済
  - `page.find_tables()` は PyMuPDF 1.23+ で安定供給（インストール済バージョン 1.27.2.2 で利用可）
- **関連 incident**:
  - 167A 誤分類: CHUNK_TEXT が PDF 全文をカバーしないため PyMuPDF 一本化を決定済
  - 166A 中断事例: 「並行 agent 競合」を理由とした spec 違反中断
  - 月次開示パイプラインで PyMuPDF `find_tables()` + テキスト併用方式が既に運用実績あり（仕組み統一の動機）

---

## 優先度の定義

- **P0**: トークン削減目的（B+C ハイブリッドの本旨）達成 / spec 一貫性確保
- **P1**: なし
- **P2**: なし

---

## 設計上の重要決定

1. **B+C ハイブリッド方式の根拠**:
   - 純粋 C 案（表抽出のみ）だと、「受注残高は前年同期比で大幅に増加」等の文章記述が漏れる
   - 純粋 B 案（テキスト全文のみ）だと、表構造の行列対応が崩れ、assistant が数値ラベルを誤認するリスク
   - 両方を assistant に渡すことで取りこぼし防止 + 数値抽出精度向上
2. **Read tool の実質スキップ**:
   - assistant は `/c/tmp/tdnet_orders/{ticker}.pages.json` を Read tool で読む（PDF 全文ではなく JSON テキストのみ）
   - PyMuPDF が事前にヒットページを絞り込んだ上で、その text + pdfplumber 表のみを JSON に格納するため、JSON 自体が PDF 全文より桁違いに軽い
   - 画像 PDF 時のみ Read tool fallback を残す（`total_chars < 500` または `fitz.open()` 例外）
3. **既存実装との位置付け（v2 で事実修正）**: 月次開示パイプライン（`scripts/extract_monthly_data.py`）の主力表抽出は `pdfplumber.extract_tables()` であり、PyMuPDF `find_tables()` は補助的なセクション識別 bbox 取得のみに使われている（062 知見 §4.1 参照、L1717 pdfplumber.open / L1719 page.extract_tables 確認済）。本ソルジャーも **pdfplumber に統一** することで月次開示と仕組みを揃え、062 知見「PyMuPDF find_tables は pdfplumber より弱く、複雑表で崩れる」のリスクを排除する。**text 抽出のみ PyMuPDF（速度最速・ヒットページ判定で繰返し呼び出すため）、表抽出は pdfplumber** の役割分担とする
4. **CHUNK_TEXT 関連削除の徹底**:
   - 現 spec §Step 2 SQL コメント「CHUNK_TEXT 条件は付けない」を削除（PyMuPDF 一本化で CHUNK_TEXT 自体未使用 → 記述自体が混乱の元）
   - §注意事項の「CHUNK_INDEX IS NOT NULL を付けない（過去ロード罠）」は **BQ 一般注意として残す**（PyMuPDF とは独立した BQ クエリ設計上の罠）
5. **コマンダー spec は変更不要**: 表抽出はソルジャー内部実装で完結、コマンダー側に CHUNK_TEXT 関連記述なし（確認済）

### v2 追加決定（212 レビュー全採用、#2 除く）

6. **`tbl.extract()` 戻り値正規化規約**（重大#3）:
   - 結合セル `None` / `""` → 空文字 `""` (assistant 側で `null` 解釈)
   - 改行入りセル `"行A\n行B"` → `\n` を半角空白に置換
   - マイナス記号 `△` / `▲` → `-`（spec L409「マイナスは `-数値`、△ は使わない」と整合）
   - 数値表記 `1,234` のカンマ除去等は assistant 側 d[] 構築時に実施
7. **silent skip 禁止**（重大#4）:
   - Step 4-B の `try/except: pass` を廃止し、`table_extract_errors: list[str]` 配列にエラー文字列を記録
   - assistant は `pages.json` 読込時にこの配列を確認し、空でなければ ERRORS or HIGHLIGHTS に転記する
8. **表 vs text 値不一致時の優先規約**（改善#4）:
   - 表優先（既定）。表に明示数値あり、text に別値の場合は表を採用
   - 表に値なしで text のみある場合は text 採用
   - 両方値ありで矛盾する場合は表優先 + `note` に `value_conflict_table_priority` を追記
9. **画像 PDF fallback 閾値 500 文字の根拠**（改善#6）:
   - 典型的な 1 ページ決算短信本文 ≒ 1,500 文字、6 ページの最小決算短信でも 5,000 文字超
   - 500 文字未満 = ほぼ確実に画像のみ or 抽出失敗
10. **中間ファイル命名規約**（改善#7）:
    - `/c/tmp/tdnet_orders/{ticker}_{idx}.pdf`（連番、1 ticker = N ファイル、idx は 1-6）
    - `/c/tmp/tdnet_orders/{ticker}.pages.json`（単数、1 ticker = 1 ファイル）
11. **不採用: pages_data サイズ上限ガード**（重大#2 不採用）:
    - データ取りこぼし回避を優先するため、hit_pages 数や JSON バイト数の soft cap は導入しない
    - 結果として大型決算短信（30-50 ページ）でトークン削減効果が頭打ちになる可能性は受容する

---

## 指摘項目

### P0-1. B+C ハイブリッド方式（PyMuPDF text+table_extract）への移行 🚨

**症状**:
- 現 spec の PyMuPDF 経路一本化版でも、偽陽性ゼロの実走では 5 件平均 85,468 token を消費（Read tool が PDF 全文を LLM に投入）
- 偽陽性 skip が発火しない場合、トークン削減効果が限定的

**該当**:
- `skills/orders_soldier.md` §Step 4-B (PyMuPDF 抽出 + キーワード前処理フィルタ) L143-188
- `skills/orders_soldier.md` §Step 5 (PDF 読み取り) L190-243
- `skills/orders_soldier.md` §Step 6 (JSON 組み立て) L298-446

**根本原因**:
- ヒットページが見つかった PDF を Read tool（PDF 全文読み込み）に投入する設計のため、トークン消費は PDF サイズに比例
- PyMuPDF で既にテキスト抽出済なのに、結果を JSON 化せず Read tool に再投入する二重処理

**修正方針**:

#### 1) §Step 4-B 拡張 — ヒットページごとに pdfplumber で表抽出を追加（v2: PyMuPDF text + pdfplumber tables）

```python
# v2 after（B+C ハイブリッド / PyMuPDF text + pdfplumber tables）
import fitz, pdfplumber, json, re, sys
pattern = re.compile(r'受注高|受注残|...|受注.{0,8}推移')  # 既存キーワードリストそのまま

def normalize_table(tbl):
    """tbl.extract() 戻り値正規化 (設計決定 §6):
    結合セル None → 空文字、改行入りセル → スペース、△/▲ → - """
    out = []
    for row in tbl:
        out_row = []
        for cell in row:
            if cell is None:
                out_row.append('')
            else:
                s = str(cell).replace('\n', ' ').replace('△', '-').replace('▲', '-').strip()
                out_row.append(s)
        out.append(out_row)
    return out

result = {}
for pdf_path in sys.argv[1:]:
    try:
        doc = fitz.open(pdf_path)
    except Exception as e:
        result[pdf_path] = {'error': f'fitz.open failed: {e}', 'hit_pages': [], 'total_chars': 0, 'page_count': 0, 'pages_data': {}, 'table_extract_errors': []}
        continue
    page_texts = {}
    chars = 0
    hit_pages = []
    # Phase 1: PyMuPDF で text 抽出 + ヒット判定
    for i, page in enumerate(doc, 1):
        text = page.get_text("text")
        chars += len(text)
        page_texts[i] = text
        if pattern.search(re.sub(r'\s+', '', text)):
            hit_pages.append(i)
    # Phase 2: ヒットページのみ pdfplumber で表抽出（設計決定 §3: 062 知見§4.1 で PyMuPDF find_tables より pdfplumber が強い）
    pages_data = {}
    table_extract_errors = []
    if hit_pages:
        try:
            with pdfplumber.open(pdf_path) as plumber_pdf:
                for i in hit_pages:
                    page_data = {'text': page_texts[i], 'tables': []}
                    try:
                        tables = plumber_pdf.pages[i-1].extract_tables() or []
                        page_data['tables'] = [normalize_table(t) for t in tables]
                    except Exception as e:
                        # silent skip 禁止 (設計決定 §7): エラー記録して後段で参照
                        table_extract_errors.append(f'page={i} error={e}')
                    pages_data[i] = page_data
        except Exception as e:
            # pdfplumber 全体失敗時も text は残す
            for i in hit_pages:
                pages_data[i] = {'text': page_texts[i], 'tables': []}
            table_extract_errors.append(f'pdfplumber_open_failed={e}')
    result[pdf_path] = {
        'hit_pages': hit_pages,
        'total_chars': chars,
        'page_count': len(doc),
        'pages_data': pages_data,
        'table_extract_errors': table_extract_errors,
    }
print(json.dumps(result, ensure_ascii=False))
```

#### 2) §Step 5 抜本改訂 — Read tool を実質スキップ

| 条件 | 動作 | DOCS_READ カウント |
|------|------|-------------------|
| `hit_pages` 1 件以上 | `{ticker}.pages.json` の `pages_data` から該当ページの text + tables を assistant が直接解析 | カウントする |
| `hit_pages` 空 かつ `total_chars >= 500` | 偽陽性確定 → スキップ | カウントしない |
| `hit_pages` 空 かつ `total_chars < 500` | 画像 PDF fallback（Step 5-B、Read tool で PDF 直読） | カウントする |
| `error` キーあり（`fitz.open()` 例外） | 画像 PDF fallback（Step 5-B） | カウントする |

- assistant は `{ticker}.pages.json` を Read tool で読む（PDF 全文ではなく JSON テキストのみ、桁違いに軽い）
- 画像 PDF fallback のみ従来の Read tool 3 回試行ループを残す（pages 指定 1-5 / 6-10）
- ハートビート義務は維持（pages.json 読込前、画像 PDF fallback の各 Read 試行前）

#### 3) §Step 6 微修正 — pages_data の text + tables を見て d[] を構築

- 表 (`tables`) があれば優先採用（行/列の対応が確実）
- 表がない場合は `text` 全文から数値抽出
- §抽出対象 / §抽出対象外 / §「期待する数値がなかった」場合の判定 等の意味論はすべて維持

**呼び出し側への波及**:
- `skills/orders_commander.md`: 変更不要（CHUNK_TEXT 関連無し、表抽出はソルジャー内部実装、戻り値プロトコル不変）
- `.claude/commands/orders-soldier.md`: 変更不要（ラッパーのみ）
- BQ クエリ・GCS パス・status file / heartbeat / インデックス CSV: 全て変更なし

**検証**:
- smoke test: 1734, 1736, 1739 で 3 件試行（resume 実走で実測済の銘柄群）
- token 実測値（5 件平均 85,468 → 改修後の値）を比較し、削減効果を確認
- 各 ticker の `{ticker}.json` 内容（kind / 数値 / セグメント）が改修前後で同等以上であることを目視確認

**ロールバック**:
- commit 6e762b33 に `git revert` で 1 コミットで戻せる
- 中間生成物 `{ticker}.pages.json` は形式が変わるだけで out-of-band（処理後削除）、永続データへの影響なし

---

### P0-2. CHUNK_TEXT 関連記述の削除（PyMuPDF 一本化徹底） 🚨

**症状**:
- 現 spec §Step 2 SQL コメント (L104) に「CHUNK_TEXT 条件は付けない（受注情報の本体は CHUNK_TEXT が NULL のメタ行に記録される設計のため、本文は GCS PDF を直接読む）」とあるが、PyMuPDF 一本化方針では CHUNK_TEXT 自体を使わないため、このコメントが将来の読者を混乱させる
- §注意事項 (L603) にも「本ソルジャーは PyMuPDF 一本化方針のため CHUNK_TEXT を直接使わないが、将来 CHUNK_TEXT 補助検索を追加する場合に〜」という記述があり、CHUNK_TEXT 言及が残存

**該当**:
- `skills/orders_soldier.md` §Step 2 (L104)
- `skills/orders_soldier.md` §注意事項 (L603 CHUNK_INDEX の罠説明内の CHUNK_TEXT 言及部)

**根本原因**:
- 旧仕様（CHUNK_TEXT を活用する設計案）の名残が消去されずに残っている

**修正方針**:

```markdown
# before（§Step 2 L104）
- CHUNK_TEXT 条件は付けない（受注情報の本体は CHUNK_TEXT が NULL のメタ行に記録される設計のため、本文は GCS PDF を直接読む）
- **CHUNK_INDEX IS NOT NULL も付けない**（2026-05-18 以前のロードは CHUNK_INDEX が全件 NULL のため、付けると過去データ全件を誤って除外する罠）

# after
- **CHUNK_INDEX IS NOT NULL を付けない**（2026-05-18 以前のロードは CHUNK_INDEX が全件 NULL のため、付けると過去データ全件を誤って除外する罠。BQ クエリ一般の注意事項）
```

```markdown
# before（§注意事項 L603）
- **CHUNK_INDEX IS NOT NULL の罠**: BQ クエリの WHERE 句に `CHUNK_INDEX IS NOT NULL` を**絶対に付けない**。2026-05-18 以前のロードは CHUNK_INDEX が全件 NULL のため、付けると過去データ全件を誤って除外する。本ソルジャーは PyMuPDF 一本化方針のため CHUNK_TEXT を直接使わないが、将来 CHUNK_TEXT 補助検索を追加する場合に同条件を組み込まないよう注意

# after
- **CHUNK_INDEX IS NOT NULL の罠**: BQ クエリの WHERE 句に `CHUNK_INDEX IS NOT NULL` を**絶対に付けない**。2026-05-18 以前のロードは CHUNK_INDEX が全件 NULL のため、付けると過去データ全件を誤って除外する（BQ クエリ一般の注意事項）
```

**呼び出し側への波及**: 無し（spec 内コメント / 注意事項のみ）

**検証**:
- 改修後 spec を Read し、CHUNK_TEXT という文字列が grep で 0 件であることを確認（CHUNK_INDEX の罠記述は残存）

**ロールバック**: コメント削除のみのため取り消しは git revert で容易

---

## 対応アンチパターン

| plan ID | 004 | T-x | G-x |
|---|---|---|---|
| P0-1 | — | — | — |
| P0-2 | — | — | — |

> P0-1 はトークン削減を目的とした方式改良で既存アンチパターンに直接該当しない。P0-2 はドキュメント整合のクリーンアップで、コード/SQL アンチパターンには該当しない。

---

## 検証戦略

1. **smoke test (v2 強化)**: 1734, 1736, 1739 に加え **大型決算短信 1 件（6501/6502/7011 から選択）** を含めた 4 件で `{ticker}.pages.json` 生成 → assistant が JSON を Read → `{ticker}.json` 出力までの一連を実走し、以下 3 種の実測値を ntfy 報告 (改善#2/#3):
   - **token 実測値**（改修前 5 件平均 85,468 token との比較）
   - **wall time**（PyMuPDF text + pdfplumber tables の追加コスト評価）
   - **`{ticker}.pages.json` バイト数**（max/avg、ハイブリッド方式での JSON サイズ感を観測値として残す）
2. **dev 実機**: 本プロジェクトは個人用解析パイプラインで dev/prod 区別なし。smoke の 4 件結果が現実装の出力 JSON と同等以上の数値・kind 網羅率なら本番継続
3. **本番適用判断基準**:
   - 4 銘柄全てで `completed` / `completed_partial` STATUS を維持
   - 改修前の `{ticker}.json` 比較で `d[]` 要素数の減少が 0 または許容範囲内（数値・セグメント網羅性が落ちないこと）
   - 平均 token 消費が改修前 85,468 から有意に減少
   - **大型決算短信 1 件で wall time が hb 30 分上限を圧迫しない**こと
4. **回収手順**: 万一 smoke で抽出精度が落ちる場合は `git revert <new_commit>` で commit 5ea7ed39 に即時復帰。出力 JSON は ticker 単位で上書きされるため、再実行で完全復元可能（永続データ破壊リスクなし）

---

## 関連ドキュメント

- 関連スキル: `skills/orders_soldier.md`（本改修対象）, `skills/orders_commander.md`（変更不要）
- 関連 commit:
  - `5ea7ed39` — orders ソルジャー B+C ハイブリッド方式（v1: PyMuPDF text + PyMuPDF find_tables）
  - `6e762b33` — orders ソルジャー キーワード追加 受注*推移
  - `0d407011` — PyMuPDF 経路一本化 + 既知問題是正
- 関連 plan: `docs/plans/refactor_orders_soldier_pymupdf_20260518_225508.md`（直前の改修プラン）
- 関連 review: `docs/reviews/210_cr_orders_soldier_pymupdf_plan.md`, `docs/reviews/212_cr_orders_soldier_hybrid_table.md`
- フォーマット正本: `skills/planning.md` §改修プラン / バグ修正指示書 MD フォーマット

### 210 由来規約と本 v2 改修の累積影響表（改善#5）

| 210 で導入された規約 | v2 での扱い | 備考 |
|--------|--------|------|
| PyMuPDF 経路一本化 (text 抽出) | **維持** | text 抽出は引き続き PyMuPDF |
| ヒットページ判定 (キーワード regex + スペース除去) | **維持** | Step 4-B Phase 1 でそのまま実施 |
| 画像 PDF fallback (`total_chars < 500` または `fitz.open()` 例外で Read tool 直読) | **維持** | 閾値 500 の根拠を本 v2 で明文化 (設計決定 §9) |
| heartbeat 義務 (Step 4-B 直前、Step 5 Read 試行直前、Step 7 終了直前) | **維持** | pages.json 読込前 hb 追加 (v1 から継承) |
| Step 7 順序 (PDF削除→status→hb更新→hb削除) | **維持** | race 回避規約 |
| 中間ファイル `{ticker}_*.pdf` の glob 削除 | **拡張** | v1 で `{ticker}.pages.json` も同時削除を追加 |
| CHUNK_INDEX IS NOT NULL の罠記述 | **維持** | BQ クエリ一般注意として保持 |
| **新規 v2**: pdfplumber 表抽出 + 正規化規約 + silent skip 禁止 | — | 062 知見§4.1 に合わせて表抽出本体を pdfplumber に統一 |

---

## 提出前セルフチェック（必須）

- [x] 冒頭に基準 commit hash があるか（6e762b33）
- [x] 全項目が 7 フィールドを揃えているか
- [x] 修正方針に before/after の両方があるか
- [x] 呼び出し側への波及が行番号リストで明示されているか
- [x] 対応アンチパターン表が末尾にあるか
- [x] 検証戦略が smoke / dev / 本番適用判断基準 / 回収手順の 4 段を網羅しているか
- [x] ロールバック手順があるか
- [x] 「既に〜がある」系の前提を実コードで Read 確認したか（commit 6e762b33 の orders_soldier.md L104/L153-180/L603、orders_commander.md grep 結果を全て Read 済）

---

## 実装記録（実装後に記入）

**実装 commit**: `<hash>` — YYYY-MM-DD HH:MM JST
**検証結果**:
- smoke test: <PASS/FAIL — 実行コマンドと結果の1行要約>
- dev 実機: <PASS/FAIL — 結果の1行要約>
- 本番適用: <適用済み / 未適用>

**code-reviewer 推奨の採否**:
| # | 推奨内容 | 採否 | 理由 |
|---|---------|------|------|
| 1 | <推奨の要約> | 採用 / 不採用 / 次回対応 | <理由> |

---

## 実装後チェック（実装完了時に記入）

- [ ] 冒頭のステータスを「完了」に更新したか
- [ ] 実装 commit hash を記録したか
- [ ] 検証結果（smoke / dev）を記録したか
- [ ] code-reviewer 推奨の採否を記録したか
- [ ] 関連知見MDにこのプランの変更を反映したか
- [ ] 完了プランを `docs/plans/archive/202605/` に移動したか

---

## レビュー追記: 2026-05-19 00:55 JST — code-reviewer

→ `docs/reviews/212_cr_orders_soldier_hybrid_table.md` §「レビュー追記: 2026-05-19 00:55 JST — code-reviewer (パターン2)」

---

## レビュー対応記録 v2（212 全採用、#2 除く） — 2026-05-19 JST

**対応方針**: ユーザー判断により、212 レビューの重大 #1 / #3 / #4 + 改善 #1-#7 を**全採用**。重大 #2 (pages_data サイズ上限ガード) は**不採用**（データ取りこぼし回避を優先）。

### 採否一覧

| # | 区分 | 内容 | 採否 | 反映先 |
|---|------|------|------|--------|
| 重大#1 | 事実誤認 | 「月次開示で PyMuPDF find_tables 運用実績」は不正確 → pdfplumber に切替 | **採用** | 設計決定 §3 書換 + Step 4-B コード pdfplumber 化 |
| 重大#2 | 副作用 | pages_data サイズ上限ガード | **不採用** | 設計決定 §11 で不採用理由明記 (取りこぼし回避優先) |
| 重大#3 | 副作用 | tbl.extract 戻り値正規化規約 (結合セルNone/改行/△) | **採用** | 設計決定 §6 + Step 4-B `normalize_table` 関数 |
| 重大#4 | 新規リスク | silent skip 禁止 (table_extract_errors 配列記録) | **採用** | 設計決定 §7 + Step 4-B エラー記録ロジック |
| 改善#1 | 可読性 | 複数表混在時の判定基準 (見出し行キーワードマッチ) | **採用** | Spec Step 6 §3 具体化 |
| 改善#2 | 検証戦略 | wall time / pages.json サイズ計測追加 | **採用** | 検証戦略 §1 強化 |
| 改善#3 | 検証戦略 | smoke に大型決算短信 1 件追加 (6501/6502/7011) | **採用** | 検証戦略 §1 4 件化 |
| 改善#4 | 可読性 | 表 vs text 値不一致時の優先規約 (表優先 + note) | **採用** | 設計決定 §8 + Spec Step 6 |
| 改善#5 | 可読性 | 210 由来規約との累積影響表 | **採用** | §関連ドキュメント §累積影響表 |
| 改善#6 | 可読性 | 画像 PDF fallback 閾値 500 文字の根拠付記 | **採用** | 設計決定 §9 + Spec Step 5 |
| 改善#7 | 可読性 | 中間ファイル命名規約 (pages.json 単数 vs _{idx}.pdf 連番) | **採用** | 設計決定 §10 + Spec Step 5 |

### 変更ファイル
- `docs/plans/refactor_orders_soldier_table_extraction_20260519_001056.md` (本ファイル、v2 改訂)
- `skills/orders_soldier.md` (Step 4-B 書換 / Step 5 中間ファイル命名 + 閾値根拠 / Step 6 §1 §3 / 表vs text 規約 / silent skip 対策)
- `docs/reviews/212_cr_orders_soldier_hybrid_table.md` (末尾に対応記録セクション追加)

### コミット
- v2 実装 commit: `ae50de72` (feat: orders ソルジャー 212レビュー全採用（#2除く）- pdfplumber切替+正規化規約+silent skip対策)
