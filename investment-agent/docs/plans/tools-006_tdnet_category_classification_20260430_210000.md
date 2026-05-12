# TDnet カテゴリ分類定義の一元化・乖離解消

**作成日時**: 2026-04-30 21:00 JST
**分類**: (b) 継続改修型（既存知見MD 006 への追記 + コード同期）
**基準 commit**: `7f9db84`
**対象読者**: 次セッション担当 / code-reviewer サブエージェント
**ステータス**: Step 1,2,4,6,8a,8b 完了（2026-04-30）。Step 3,5,8c,8d は P1/P2 で未着手。
**目的**: TDnet カテゴリ名の定義が5+箇所に散在し、006 MD（正本のつもり）がコード実態と乖離している問題を解消する。

---

## 1. 現状の問題

### 1.1 定義箇所の全洗い出し結果

TDnet カテゴリ名が定義・参照されている箇所は以下の **7 ファイル**（+ 1 データカタログ）。

| # | ファイル | 定義形式 | 役割 | カテゴリ数 |
|---|---------|---------|------|-----------|
| A | `docs/knowledges/tools/006_tdnet_category_classification.md` | Markdown テーブル | 「正本のつもり」の一覧（62カテゴリ） | 62 |
| B | `scripts/tdnet_download.py` | `classify()` 正規表現 + コメントテーブル | DL 時のタイトル分類（正規表現 → カテゴリ名） | 65（コメントテーブル）/ 64（classify 関数の return 値） |
| C | `scripts/irbank_tdnet_download.py` | `classify()` 正規表現 | IRBank 経由 DL 時のタイトル分類（B のコピー） | 64 |
| D | `scripts/tdnet_load_parallel.py` | `VALID_CATEGORIES` リスト + セット定義 | Gemini/Gemma AI判定で使うカテゴリ制約 | 46 |
| E | `scripts/gemma_tpu_worker.py` | `VALID_CATEGORIES` リスト | Gemma TPU 推論プロンプト | 46 |
| F | `scripts/tdnet_load_recovery.py` | `VALID_CATEGORIES` リスト + セット定義 | リカバリ処理（D のコピー） | 46 |
| G | ~~`scripts/tdnet_gemma3_benchmark/main.py`~~ | `VALID_CATEGORIES` リスト | ベンチマーク（D のコピー）— **削除済み** | 46 |
| H | `data_catalog.md` | Markdown テーブル（§TDNET_DOCUMENTS_ENHANCED） | BQ スキーマ定義としての正本 | 46 |

**003 MD** (`docs/knowledges/tools/003_tdnet_download.md`) は改訂履歴でカテゴリ変更を記録するが、カテゴリの完全一覧は持たない。
**013 MD** (`docs/knowledges/tools/013_tdnet_load.md`) は `VALID_CATEGORIES` はカタログと同期させることと注記しているのみ。

### 1.2 カテゴリの2系統問題

現行アーキテクチャでは、カテゴリは **2つの異なるフェーズ** で使われる:

1. **DL フェーズ（62+カテゴリ）**: `tdnet_download.py` の `classify()` がタイトル正規表現で判定。DL 要否を決定するだけで、BQ には書かない。C 優先度のカテゴリも含む全カテゴリを返す
2. **AI 判定フェーズ（46カテゴリ）**: `tdnet_load_parallel.py` の `VALID_CATEGORIES` が Gemini/Gemma のプロンプトに渡され、MAIN_CATEGORY / SUB_CATEGORIES として BQ に格納される

006 MD は DL フェーズの 62 カテゴリを記載しているが、AI 判定フェーズの 46 カテゴリとの対応関係を一切記述していない。

---

## 2. 具体的な乖離一覧

### パターン (A): コードに存在するが 006 MD に記載なし

| カテゴリ名 | 存在するファイル | 優先度 | 備考 |
|-----------|----------------|--------|------|
| `受注高/受注残高` | B,C,D,E,F,G,H | B ✅ | 2026-03-01改訂で新規追加（003 MD に履歴あり）。006 MD への反映漏れ |
| `業績の重要な先行指標` | B,C,D,E,F,G,H | B ✅ | 同上。006 MD への反映漏れ |
| `特別利益` | B,C,D,E,F,G,H | A ✅ | `特別損益計上` を分離。006 MD の #21 は旧名 `特別損益計上` のまま |
| `特別損失` | B,C,D,E,F,G,H | A ✅ | 同上 |
| `有価証券報告書` | B,C | C ❌ | DL フェーズのみで使用。AI 判定の `VALID_CATEGORIES` にも 006 MD にも不在 |

### パターン (B): 006 MD に記載あるがコード実態と不整合

| カテゴリ名 | 006 MD の記載 | コード実態 | 備考 |
|-----------|-------------|-----------|------|
| `特別損益計上` | #21 A として記載 | `classify()` は `特別利益`/`特別損失` に分離して返す。`VALID_CATEGORIES` には残存（Gemini が返す可能性のため） | 006 MD では分離を反映していない |
| `受注・契約` | #46 B として記載 | `classify()` は `大型受注・契約` を返す。`受注・契約` は `_AMBIGUOUS_SUBCATEGORY` セットに残存するのみ | 006 MD の名称が旧名 |

### パターン (C): 名称の揺れ

| 006 MD の名称 | コードの名称 | 差異 |
|--------------|------------|------|
| `受注・契約`（#46） | `大型受注・契約`（classify 関数・VALID_CATEGORIES） | 2026-03-01改訂で変更済みだが 006 MD 未反映 |

### パターン (D): MAIN_CATEGORY vs SUB_CATEGORIES の区別が 006 MD で不明確

006 MD は DL フェーズの 62 カテゴリ一覧を記載しているが、以下の情報が欠落:

- AI 判定フェーズの `VALID_CATEGORIES`（46カテゴリ）のどれが BQ の MAIN_CATEGORY/SUB_CATEGORIES に使われるかの対応
- DL フェーズ限定カテゴリ（`有価証券報告書` 等の C カテゴリ）と AI 判定フェーズカテゴリの境界
- `_AMBIGUOUS_OVERWRITE`、`_MONTHLY_SUB_CATEGORIES`、`_NEEDS_SUB_CATEGORIES`、`_NEEDS_GEMINI_ANALYSIS` 等のセット定義の意味
- `_AMBIGUOUS_SUBCATEGORY` に残存する `受注・契約` の位置付け（旧名残留か意図的か）

### 追加発見: `受注・契約` の残留問題

`tdnet_load_parallel.py` L241 と `tdnet_load_recovery.py` L446 の `_AMBIGUOUS_SUBCATEGORY` セットに `"受注・契約"` が含まれている。しかし:
- `VALID_CATEGORIES` には `"受注・契約"` は存在しない（`"大型受注・契約"` のみ）
- `classify()` も `"大型受注・契約"` を返す
- `_AMBIGUOUS_SUBCATEGORY` は `_MONTHLY_SUB_CATEGORIES` と `_NEEDS_GEMINI_ANALYSIS` の基盤セットなので、`"受注・契約"` がここに残っていると月次判定ロジックに影響する可能性がある

**リスク**: `main_category == "受注・契約"` になる文書が存在する場合（GCS ファイル名に旧カテゴリ名が埋め込まれているバックフィルデータ等）、`_NEEDS_GEMINI_ANALYSIS` にヒットして AI 分析は走るが、`VALID_CATEGORIES` に無い値なので Gemma/Gemini の出力バリデーションで弾かれる。ただし `main_category` はファイル名から parse → `_correct_category_by_title` を経由するので、実際に `"受注・契約"` が `main_category` になるケースがあるかは要調査。

---

## 3. 正本の一元化方針

### 3.1 現状の正本関係

```
data_catalog.md（BQ スキーマ定義）
    ↓ 46カテゴリ（AI判定用）
    ↓ 正本と宣言されている（003 MD L197, 013 MD L576）
    ↓
    ├── VALID_CATEGORIES（D,E,F,G） ← カタログと同期義務あり
    │
006 MD（62カテゴリ）
    ↓ DL要否判定用
    ↓ 同期対象: tdnet_download.py のコメントテーブル（L26-29）
    ↓
    ├── classify()（B,C） ← 64カテゴリを返す
```

### 3.2 推奨方針: 006 MD を「全カテゴリの統合正本」に昇格

**理由**:
- `data_catalog.md` はBQスキーマの定義として AI 判定用 46 カテゴリの正本だが、DL フェーズのカテゴリは管轄外
- 006 MD は「TDnet カテゴリ分類ロジック」と銘打っており、本来全カテゴリを網羅すべき位置にある
- 006 MD がカバーすべき範囲: DL フェーズ全カテゴリ + AI 判定フェーズのメタ情報（どのカテゴリが VALID_CATEGORIES に含まれるか）

**正本ヒエラルキー**:
```
006 MD（統合正本: DL + AI 判定の全カテゴリ一覧）
    ├── tdnet_download.py コメントテーブル ← 同期（既存ルール L26-29 を維持）
    ├── VALID_CATEGORIES（D,E,F,G） ← 同期（新規ルール追加）
    └── data_catalog.md § MAIN_CATEGORY/SUB_CATEGORIES 値一覧 ← 同期（新規ルール追加）
```

---

## 4. 修正ステップ

### Step 1: 006 MD のカテゴリテーブルを更新（P0）

**作業内容**:

1. テーブルヘッダに `AI判定` 列を追加（`VALID_CATEGORIES` に含まれるか否か: ✅ / --）
2. 以下のカテゴリを追記:
   - `受注高/受注残高`（B ✅ 要、AI判定 ✅）
   - `業績の重要な先行指標`（B ✅ 要、AI判定 ✅）
3. `#21 特別損益計上` の行を修正:
   - `特別損益計上` は「AI判定の VALID_CATEGORIES にのみ残存（互換用）」と注記
   - `特別利益`（A ✅ 要）と `特別損失`（A ✅ 要）を独立行として追加
4. `#46 受注・契約` を `大型受注・契約` にリネーム
5. `有価証券報告書`（C ❌ 不要）を追記（DL フェーズ限定）
6. タイトル「62カテゴリ」の数値を実態に合わせて更新
7. `_SKIP_B_CATEGORIES` セクションが最新であることを確認

**テーブル列の案**:
```
| # | P | カテゴリ | 件数/週 | DL要否 | AI判定 | 理由 |
```

### Step 2: 006 MD の同期ルールを拡張（P0）

**現行**（L26-29）:
> このファイルと `scripts/tdnet_download.py` のコメントテーブルは常に同期を保つこと。

**拡張案**:
```markdown
## 同期ルール

> **このファイルは TDnet カテゴリ定義の統合正本である。** 以下の4箇所と常に同期を保つこと。
> カテゴリの追加・変更・要否変更を行った場合は**5箇所すべて**を更新する。
>
> 1. `scripts/tdnet_download.py` — `classify()` 関数 + コメントテーブル（DL フェーズ）
> 2. `scripts/irbank_tdnet_download.py` — `classify()` 関数（B のコピー、1 と同期）
> 3. `scripts/tdnet_load_parallel.py` — `VALID_CATEGORIES` リスト（AI 判定フェーズ）
> 4. `data_catalog.md` — §TDNET_DOCUMENTS_ENHANCED の MAIN_CATEGORY/SUB_CATEGORIES 値一覧
>
> ※ `scripts/gemma_tpu_worker.py`、`scripts/tdnet_load_recovery.py`、
>    `scripts/tdnet_gemma3_benchmark/main.py` は 3 のコピー。3 を更新したら連動更新。
```

### Step 3: 006 MD に AI 判定フェーズの解説セクションを追加（P1）

**追加セクション案**: 「§ AI 判定フェーズのカテゴリ制約」

内容:
- `VALID_CATEGORIES`（46カテゴリ）の目的と DL フェーズとの違い
- `_AMBIGUOUS_OVERWRITE` / `_MONTHLY_SUB_CATEGORIES` / `_NEEDS_SUB_CATEGORIES` / `_NEEDS_GEMINI_ANALYSIS` の各セットの定義と相互関係図
- MAIN_CATEGORY vs SUB_CATEGORIES の適用ルール（現在 `data_catalog.md` に記載あり、006 MD にはなし）

### Step 4: `受注・契約` 残留問題の解消（P1）

`_AMBIGUOUS_SUBCATEGORY` セット内の `"受注・契約"` を以下のいずれかで対処:

- **案A**: `"大型受注・契約"` にリネーム（`classify()` の出力と一致させる）
- **案B**: 削除（`_AMBIGUOUS_SUBCATEGORY` の目的に照らして不要であれば）
- **案C**: そのまま残す + コメントで旧名残留である理由を明記

**判断材料**: GCS 上のバックフィルデータ（2017-2022）のファイル名に `受注・契約` が含まれるものが存在するか確認が必要。存在する場合、`parse_tdnet_filename()` がこの旧名を返す可能性がある。

### Step 5: VALID_CATEGORIES の重複定義をコード的に解消（P2）

現在 `VALID_CATEGORIES` が 4 ファイル（D,E,F,G）にコピペされている。

- **案A**: `src/tdnet/categories.py` に一元定義し、全スクリプトから import
- **案B**: 現状維持（Cloud Run Job の `gemma_tpu_worker.py` は別デプロイ単位のため import 不可）

Cloud Run Job のデプロイ単位が異なるため、完全な一元化は困難。少なくとも以下は実施:
- `tdnet_load_parallel.py` と `tdnet_load_recovery.py` は同一デプロイ → 共通 import 可能
- `gemma_tpu_worker.py` は TPU VM 上で独立実行 → コピペ維持が現実的
- `tdnet_gemma3_benchmark/main.py` はベンチマーク用 → 更新頻度低いのでコピペ許容

### Step 6: tdnet_download.py コメントテーブルの最終同期確認（P1）

`tdnet_download.py` のコメントテーブル（L123-192）は 006 MD との同期が既存ルールで義務化されている。
Step 1 の 006 MD 更新後、コメントテーブルも以下を反映:
- `#46` の名称を `大型受注・契約` に（既に classify() は `大型受注・契約` を返している。コメントテーブル L174 は反映済み）
- `受注高/受注残高` と `業績の重要な先行指標` はコメントテーブルにはあるが番号なし（L176-177）。正式な番号を付与

---

## 5. 検証戦略

### 5.1 diff による整合確認

006 MD 更新後、以下のチェックスクリプトで全ファイル間の一貫性を確認:

```python
# 確認事項
# 1. 006 MD のカテゴリテーブルから全カテゴリ名を抽出
# 2. classify() が返す全カテゴリ名を抽出（正規表現の return 値）
# 3. VALID_CATEGORIES の全値を抽出
# 4. data_catalog.md の値一覧を抽出
# 5. 4つの集合の差分を計算・表示
```

### 5.2 BQ 実データとの照合

```sql
-- BQ 上の MAIN_CATEGORY に存在する値が VALID_CATEGORIES と一致するか確認
SELECT DISTINCT MAIN_CATEGORY, COUNT(*) as cnt
FROM `project.dataset.TDNET_DOCUMENTS_ENHANCED`
GROUP BY MAIN_CATEGORY
ORDER BY cnt DESC
```

---

## 6. リスク評価

| リスク | 影響 | 軽減策 |
|-------|------|--------|
| 006 MD 更新時に classify() 側を変更してしまう | DL 判定ロジック変更 → 意図しないスキップ/取り込み | Step 1 は MD のみ変更。コード変更は Step 4-6 で個別に |
| `受注・契約` 削除で旧データのパイプラインが壊れる | バックフィルデータの MAIN_CATEGORY が不正値になる | Step 4 の前に GCS ファイル名を grep して影響範囲を確認 |
| VALID_CATEGORIES 統合 import で Cloud Run Job のビルドが壊れる | デプロイ失敗 | Step 5 はデプロイ単位内のみ。cross-deploy は現状維持 |

---

## 7. 実施順序とブロッカー

```
Step 1 (P0) ─→ Step 2 (P0) ─→ Step 6 (P1) ── 006 MD + download.py 同期完了
                                    │
Step 3 (P1) ←──────────────────────┘
                                    │
Step 4 (P1) ←──── GCS 旧名調査が前提 ──── 調査タスク（別途）
                                    │
Step 5 (P2) ←──────────────────────┘
```

Step 1-2 は即時着手可能。Step 4 は GCS 上の旧カテゴリ名ファイル調査がブロッカー。

---

## 8. 006 MD の同期を自動的に保証する仕組み（追記: 2026-04-30）

### 8.1 課題

Step 2 で同期ルールの文言を拡張しても、同期を実行するのは人間（Claude Code セッション）の注意力頼み。今回の 5 件の乖離がまさに「文言はあるが仕組みがない」ことで発生した。

### 8.2 案の比較

#### 案A: CI パイプラインでのチェック

**概要**: GitHub Actions 等の CI で、PR / push 時に 006 MD・`classify()`・`VALID_CATEGORIES`・`data_catalog.md` の4集合を比較するスクリプトを走らせる。差分があれば CI を fail させる。

**実現可能性**: **低**。現状このプロジェクトには CI パイプラインが存在しない（`.github/workflows/` なし、`.pre-commit-config.yaml` なし）。CI を新設するオーバーヘッドがプロジェクト規模（個人運用）に対して過大。

**利点**: 最も確実。コード変更が main にマージされる前に検知できる。
**欠点**: CI 基盤の構築・維持コストが高い。個人プロジェクトには過剰。

#### 案B: git pre-commit hook でのチェック

**概要**: `.git/hooks/pre-commit` にチェックスクリプトを設置し、commit 時に整合性を検証する。

**実現可能性**: **中**。git hook 自体は設置可能だが、以下の問題がある:
- `.git/hooks/` は git 管理外のため、端末間（Windows PC / GCE VM）で自動同期されない
- `pre-commit` パッケージを導入すれば `.pre-commit-config.yaml` で git 管理可能だが、依存追加が必要
- commit は Claude Code セッション外（手動 git commit）でも発生するため、Python 環境依存のスクリプトが動かない可能性がある

**利点**: commit の瞬間に検知できる。
**欠点**: hook の端末間同期問題。Python 依存。

#### 案C: Claude Code hook（PostToolUse）での自動検知（推奨）

**概要**: `.claude/settings.local.json` の `PostToolUse` hook に、対象ファイル変更時のチェックスクリプトを追加する。`Edit` や `Write` で対象ファイルが変更されたときに自動で整合チェックを走らせ、差分があれば Claude Code の出力に警告を表示する。

**実現可能性**: **高**。以下の根拠:
- 既に `PostToolUse` hook が 2 つ稼働中（`sync_colab_notebooks.py` と `claude_logger.py`）。仕組みが確立済み
- カテゴリ変更は事実上すべて Claude Code セッション内で行われる（手動エディタでの変更は稀）
- `matcher` で対象ツール（`Edit|Write`）を絞れるため、無関係な操作への影響なし

**構成案**:

```jsonc
// .claude/settings.local.json の PostToolUse に追加
{
  "matcher": "Edit|Write",
  "hooks": [
    {
      "type": "command",
      "command": "PATH=$HOME/.local/bin:$PATH PYTHONUTF8=1 uv run python scripts/check_category_drift.py",
      "timeout": 15
    }
  ]
}
```

**チェックスクリプト `scripts/check_category_drift.py` の設計**:

1. **起動判定**: 変更されたファイルが以下のいずれかに該当する場合のみチェック実行（該当しなければ即終了、0 コストを保証）:
   - `docs/knowledges/tools/006_tdnet_category_classification.md`
   - `scripts/tdnet_download.py`
   - `scripts/irbank_tdnet_download.py`
   - `scripts/tdnet_load_parallel.py`
   - `scripts/gemma_tpu_worker.py`
   - `scripts/tdnet_load_recovery.py`
   - `scripts/tdnet_gemma3_benchmark/main.py`
   - `data_catalog.md`

2. **3集合の抽出と比較**:
   - **集合1**: 006 MD のカテゴリテーブルから全カテゴリ名を正規表現で抽出
   - **集合2**: `tdnet_download.py` の `classify()` が返す全 return 値を AST / 正規表現で抽出
   - **集合3**: `tdnet_load_parallel.py` の `VALID_CATEGORIES` リストを AST / 正規表現で抽出

3. **出力**:
   - 3 集合が整合していれば何も出力しない（サイレント成功）
   - 差分があれば `stderr` に警告を出力。Claude Code は hook の stderr を表示するため、セッション中に即座に気づける

   ```
   ⚠ TDnet カテゴリ同期チェック: 差分検出
     006 MD にあって classify() にない: {'有価証券報告書'}
     classify() にあって VALID_CATEGORIES にない: {'特別利益', '特別損失'}
     → 006 MD の同期ルール（§同期ルール）に従い全箇所を更新してください
   ```

4. **注意点**:
   - hook は `PostToolUse` のため Edit/Write の**後**に走る。変更を止めるのではなく「気づかせる」仕組み
   - `timeout: 15` 秒でファイル読み込み + 正規表現抽出のみ。外部 API / BQ 呼び出しなし
   - hook のエラー（スクリプト自体のバグ等）は通常のワークフローをブロックしない

#### 案D: CLAUDE.md の知見ファイル更新義務を強化

**概要**: CLAUDE.md の §高頻度参照テーブルに「TDnet カテゴリを変更する」行を追加し、変更時に 006 MD + 全同期先を読むよう義務化する。

**実現可能性**: **高**だが効果は限定的。CLAUDE.md の INDEX 参照は「タスク開始時」に読む仕組みであり、カテゴリ変更がタスクの主目的でない場合（例: バグ修正の副作用でカテゴリ名を変える場合）は参照されない。

### 8.3 推奨案: 案C（Claude Code hook）

**理由**:

1. **プロジェクト規模に適合**: 個人運用のプロジェクトに CI を新設するのは過剰。Claude Code hook は既存の仕組みに 1 エントリ追加するだけ
2. **変更の発生源をカバー**: カテゴリ変更は事実上 Claude Code セッション内で行われるため、Claude Code hook で十分な検知率を確保できる
3. **即時フィードバック**: 変更直後に警告が出るため、同一セッション内で修正できる。CI のように PR を出してから気づくのではなく、コード変更の瞬間に検知
4. **既存パターンの踏襲**: `sync_colab_notebooks.py`（NotebookEdit|Write 時にColab同期）と同じパターンであり、運用実績がある
5. **フォールバック**: 手動エディタでの変更や hook 未設置端末への対策として、案D（CLAUDE.md 強化）を併用する

### 8.4 実施タスク

| # | 内容 | 依存 | 状態 |
|---|------|------|------|
| 8a | `scripts/check_category_drift.py` を実装 | Step 1 完了後 | **完了**（2026-04-30） |
| 8b | `.claude/settings.local.json` に PostToolUse hook エントリを追加 | 8a | **完了**（2026-04-30） |
| 8c | CLAUDE.md §高頻度参照に「TDnet カテゴリ変更」行を追加（案D 併用） | なし | 未着手 |
| 8d | GCS 同期で `settings.local.json` の hook 定義を端末間共有 | 8b | 未着手 |

**8d の補足**: `settings.local.json` は現在 `.claude/` ディレクトリ内にあり git 管理可能。ただし `.gitignore` で除外されている場合は、hook 定義部分だけプロジェクト設定（`.claude/settings.json`）に移すことも検討。プロジェクト設定なら git push で端末間同期が自動的に保証される。

### 8.5 §7 実施順序への統合

```
Step 1 (P0) ─→ Step 2 (P0) ─→ Step 6 (P1) ── 006 MD + download.py 同期完了
                                    │
Step 3 (P1) ←──────────────────────┘
                                    │
Step 4 (P1) ←──── GCS 旧名調査が前提 ──── 調査タスク（別途）
                                    │
Step 5 (P2) ←──────────────────────┘
                                    │
Step 8a-8b (P1) ←── Step 1 完了後 ──── チェックスクリプト + hook 設置 ✅完了
Step 8c (P0) ──────────────────────── CLAUDE.md 追記（即時着手可能）
```
