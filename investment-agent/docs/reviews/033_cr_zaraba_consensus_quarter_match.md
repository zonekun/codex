# コードレビュー: ザラ場 F4c コンセ乖離 四半期/FY 不一致バグ修正プラン

- 日時: 2026-04-30 16:10 JST
- 対象:
  - プラン MD: `docs/plans/tools-066_zaraba_consensus_quarter_match_20260430_153500.md`
  - 対象コード: `scripts/zaraba_earnings.py`（commit `135d5b8` 時点、1792 行）
  - 親知見: `docs/knowledges/tools/066_zaraba_tool.md`
- パターン: 2 (改修 / バグ修正レビュー)
- レビュアー: Claude (code-reviewer runbook、Agent サブエージェント起動)

---

## 【サマリー】

- 変更の要約: F4c コンセンサス乖離因子で `iloc[0]` が CSV 並び順の先頭（1Q）を拾い、FY 開示なのに 1Q コンセと比較して +320.5% 等の誤差が出ているバグを修正。CURRENT を四半期別 dict (`consensus_profit_by_q`) に保存し、scoring 時に `CurPerType` で引く構造に変更。NEXT は FY 降順で「最大 FY」を採用。`_consensus_to_prior_fields` ヘルパーに切り出して `_build_prior_data` と `_refresh_prior_consensus` の両経路で再利用。
- 品質評価: **A** — 真因に正面対処し、過去同型バグ（2026-04-14 fin.iloc[0] 修正）の知見を踏まえた構造（QUARTER 別 dict）も整合的。ただしいくつかの抜け（catchup の `master_lookup` 未使用銘柄での p={} 救済路、`_print_prepare_summary` 表示破綻、obs 観察列の表示スキーマ追従）と P1-1 後方互換のさらなる注意点があるので、本プラン適用前に下記【重大な指摘】を取り込むこと。
- 主要リスク（上位3件）:
  1. P1-1 の旧キー互換 path で `cons_profit = None` 早期 return しても旧キャッシュ全銘柄で F4c 一斉沈黙する。catchup 不可日が混在すると誤値より検知漏れの方が広がる懸念。
  2. プラン記載の「scoring 側 1 箇所（L1615）波及」は事実だが、`_print_prepare_summary` の `conse_s` 表示（L849）と `_guidance_vs_consensus` の意味整合は明示確認必要。前者はキー名変更で必ず壊れる。
  3. catchup の prior 未登録銘柄経路（L1422-1428 で `p = {"ticker": code_4, "name": name}` を作る）に `consensus_profit_by_q` キー無し → F4c 不発は意図通りだが旧キャッシュ判定との整合は要確認。

## 【改修プラン評価】

### 妥当性

- **真因対処は適切**: `iloc[0]` で集約キー（QUARTER/FY）を無視して先頭行を取る構造そのものを廃止し、scoring 時に rec の `CurPerType` でルックアップする方針は、過去 fin_summary 側修正（2026-04-14、`066_zaraba_tool.md §落とし穴: fin.iloc[0]...`）と同じ「集約キー一致を取得側で確定する」原則に準拠。対症療法ではなく構造的修正。
- **横展開漏れの認識も正確**: `_build_prior_data` と `_refresh_prior_consensus` の二重経路バグを両方識別し、`_consensus_to_prior_fields` ヘルパーに切り出す構造提案は妥当。dict comprehension で同 TICKER 複数行を silent overwrite する `_refresh_prior_consensus` 側の構造的問題（L523-532）も見落とさず P0-3 として独立項目化。
- **数値再計算の妥当性**: 観測事実テーブル（プラン L26-32）の `actual_odp = (3.205 + 1) × 33.1B ≈ 139.2B`、真の `cd = (139.2B - 138.85B) / 138.85B ≈ +0.3%` は四則演算上正確。数値の食い違いはなし。
- **過去事故との横ぐし**: `066_zaraba_tool.md` L254-271 の同型バグ（fin.iloc[0]）への参照と、関連 commit の特定指示（`git log -- scripts/zaraba_earnings.py` で 2026-04-14 横展開漏れ起点）は適切。

### 副作用・デグレードチェック

- [x] **既存正常系で破壊する箇所**:
  - `scripts/zaraba_earnings.py:849` — `_print_prepare_summary` の表示列「コンセ」が `item.get("consensus_profit")` から取得しているため、新スキーマ (`consensus_profit_by_q`) に切り替えると常に `None`／"-" となる。プランの修正方針（L107）で「表示列を変える」と方針提示はあるが具体修正案が無い。**追加指摘 #1**（後述）。
  - `_print_prepare_summary` は `target == PREPARE_TARGET_ALL` の場合は早期 return（L835-838）するため、`PREPARE_TARGET_SCHEDULED` モードで prepare した場合のみ表示が壊れる。実運用は scheduled モードが既定（`p_prep.add_argument(... default=PREPARE_TARGET_SCHEDULED)`、L1755）なので毎日影響する。
- [x] **過去の緩和策剥がし**:
  - 直近の関連修正（2026-04-14 fin.iloc[0] バグ）は scoring 側の挙動を `latest_q_quarter` 比較から `cur_per` 比較に切り替えていない（プラン touch 範囲外）。F3 YoY 計算（L1492-1500）は既に `cur_per` ベースに移行済みなので、F4c も同じ思想に揃える本プランの方針は整合的。緩和策の剥がしは無い。
  - `_refresh_prior_consensus` の updated カウンタ（L534-540）は CURRENT ヒット銘柄数を返す現行仕様。プラン L250 の `updated += 1  # CURRENT 側ヒットだけカウントしたい場合は条件追加` のコメントは曖昧 → **追加指摘 #4**。
- [x] **既存データの互換性**:
  - 既存 `prior_data.json`（旧 `consensus_profit` キー）に対する移行戦略 P1-1 は妥当だが、「旧キーを検出したら `cons_profit = None` で F4c をスキップ」する方針は誤値より無加点が安全という判断は同意できる。ただし**旧キャッシュの場合 F4c が全銘柄で一斉に沈黙**する。catchup を実行しない（または `--data consensus --force` を実行しない）日が混在すると、その日は F4c 列が完全空欄になる。本来 1Q コンセが偶然正解（1Q 開示）の銘柄も巻き添えで沈黙する。**追加指摘 #2**。

### 抜け漏れ（類似観点での横展開含む）

- [x] **同種 `iloc[0]` の他箇所の点検**: `iloc[0]` 全 13 箇所（L313/L472/L633/L640/L652/L703/L746/L767/L781/L786/L798）のうち、集約キーを持つ DataFrame に対する `iloc[0]` で**かつソートが暗黙**なのは:
  - L781/L786 = 本プランの修正対象（CURRENT/NEXT）
  - L703 (`latest_q = qoq.iloc[0]`) は SQL `ORDER BY ... CURRENT_PERIOD_END_DATE DESC`（L344）に依存。**SQL の ORDER BY は LIMIT/QUALIFY を伴わないため、pandas に渡るときソート順は維持されるが、Python 側で改めて sort せず `iloc[0]` 取得は脆弱**。同関数内 L730 の `fy_rows` は明示 sort_values 済みで対比的に安全。BQ 経由なので現状は壊れていないが、同型リスクとして親知見に追記候補（**追加指摘 #5**、軽微）。
  - L633 (`row = cal.iloc[0]`)、L640 (`mr = m.iloc[0]`) は SELECT DISTINCT で 1 行を期待する箇所（cal は TICKER ユニーク、master は TICKER ユニーク）。ユニーク前提が崩れた場合の保護は無いが現状仕様上 OK。
  - L746 (`pr.iloc[0]`)、L767 (`mgr = mg.iloc[0]`)、L798 (`mc_rows.iloc[0]`) も SQL 側で「最新 1 行」を保証する作りだが、ユニーク保証はパーティション関数任せ。リスク低。
  - L652 (`latest = fin.iloc[0]`) は **直前に `sort_values("DISCLOSED_DATE", ascending=False)` 済み**で安全。2026-04-14 修正の現状形。
  - 結論: P0-1/2/3 以外で**実害がある同型バグは検出できず**。プランの横展開判断は妥当。
- [x] **scoring 側の他のキー lookup**: `prior.get("consensus_profit_next")` は `_guidance_vs_consensus` の 1 箇所（L1391）だけ。プラン P0-2 の修正で「FY 降順の最大 FY 行」を採用する方針は obs 観察用としても妥当。ただし**複数 broker から複数 NEXT 行が混在**する銘柄（IFIS/RAKU 並走など、1878 大東建託のケース）で「FY=同値・broker=複数」の場合、`sort_values("FY", ascending=False).iloc[0]` は **broker 順（pandas stable sort）が不定**になる。本プラン提示のヘルパーで `iloc[0]` を `mean()` 等にする検討は無し。**追加指摘 #3**。
- [x] **関連ファイルへの必要変更**:
  - `docs/knowledges/tools/066_zaraba_tool.md` の §スコアリング因子 F4c コンセ乖離（L134-140 周辺）と §落とし穴 への追記が未提案。新規アンチパターン候補も提案だけで親知見への反映方針は無い。本プラン適用後に必須。
  - results.csv のスキーマ変更は無いが、`obs_guidance_vs_consensus` の意味は変わる（NEXT が「正しい翌期 FY」になる）ため predict notebook 側の答え合わせ解釈が変わる。プラン P1-2 で「2026-04-30 以前の F4c は信頼しない」と方針提示はある。
  - prior_data.json 内の旧キー削除は P1-1 で「skip + warning」で当面残置。**完全削除タイミングがプランに無い**ため、長期的には旧キー（バグ値）が混入したまま運用される。
- [x] **テスト**: プラン L322 で「`_score_record` 単体テスト」を提案しているが既存テストの場所は不明。`scripts/zaraba_earnings.py` に対する pytest スイートは現コードベースに見当たらず、プラン適用時の test 追加コストが見えていない。
- [x] **移行期の互換**: 旧スキーマと新スキーマの**両方を持つ prior_data.json**（部分 migrate 状態）は発生しうる（人為操作・部分 import）。P1-1 の `cons_by_q else legacy` 分岐は新キー優先なので OK だが、ロギング上「旧キー残存件数」を `prior_consensus_refreshed` ログに含めると運用観察に役立つ（軽微提案）。

### 新規リスク

- **F4c 沈黙による検知漏れ**: P1-1 旧キー検出時の F4c skip は誤値防止としては正しい。ただし、初回適用日（旧キャッシュ）には F4c 由来の +3 加点が全銘柄で消え、本来 STRONG_BUY 判定だったポジティブ事例も SLIGHT_BUY 等に降格する。誤値防止のメリット > 検知漏れデメリットだが、運用初日にユーザーへ「初日 F4c 沈黙正常」を周知しないと「F4c 完全に壊れた」と誤認しうる。プランの検証戦略 L329 「`prepare --data consensus --force` を必ず実行」が運用必須条件。
- **新ヘルパー `_consensus_to_prior_fields` の責務肥大化リスク**: 戻り値 dict が 4 キーで mypy/typing 上 `dict[str, object]` のため、呼び出し側で `cur_map: dict[str, float|None] | None` の型情報が失われる。型ヒントは `TypedDict` で固めるか、戻り値を `dataclass` にする方が保守性高い。軽微な改善提案。
- **`_refresh_prior_consensus` のループ単位 BQ filter**: 提案コード（プラン L233-250）は `for ticker, info in prior.items():` の中で `df_conse[df_conse["TICKER"] == tk]` を毎回 boolean filter する。prior 銘柄数 N × df_conse 全件 M の O(N*M) 走査。1500 銘柄 × 数千行で 1〜2 秒程度の追加コスト見込み。実用上問題ないが事前に group_by indexing したほうが綺麗（軽微）。

### 改修プラン MD フォーマット適合性チェック

- [x] 冒頭に対象ファイルの基準 commit hash が書かれている（L4 に `commit 135d5b8`）
- [x] 前提サマリで過去修正と残件数が明示されている（L14-21）
- [x] 優先度の定義（P0/P1/P2 昇格基準）が冒頭にある（L37-41）
- [x] 各項目が「症状 / 該当 / 根本原因 / 修正方針 / 呼び出し側波及 / 検証 / ロールバック」7 フィールドを揃えている（P0-1/P0-2/P0-3/P1-1 すべて確認）
- [x] 修正方針に before/after の両方（P0-1 の `# before` / `# after` ブロック等）
- [x] 呼び出し側への波及が**該当行リスト**で明示（P0-1 で L1614-1637, L849, L538-547 等の行番号付き）
- [x] 「既に〜がある」系の前提記述を実コードと照合（cur_per 取得の L1444 / L1514 確認 → 実コード L1444 の `cur_per = rec.get("CurPerType", "")` 一致、L1514 で再代入も一致）
- [x] アンチパターン対応表（plan ID → 004/T/G）が末尾にある（L303-312）
- [x] 検証戦略が smoke / dev / prod / 回収手順の 4 段を網羅（L316-336）
- [x] ロールバック手順が書かれている（各 P0/P1 内 + L333-336）
- [x] 読みづらさ・デッドコードだけで P0 に置かれている項目は無い（P0 はすべて運用判定の壊れ）
- [x] 関連 commit・知見 MD・incident ログへのリンクがある（L342-347）

**フォーマット違反は無し**。改修プラン MD としては高品質。1 点だけ軽微改善: 関連 commit `7e3e7f1` が「推定、要 git log で特定」となっており、本プラン適用前に確定させるのが望ましい。

---

## 【重大な指摘】（即修正）

### #1 `_print_prepare_summary` のコンセ表示が新スキーマで必ず壊れる
- 箇所: `scripts/zaraba_earnings.py:849-850`
- 事象: `conse = item.get("consensus_profit")` を新スキーマでは取れず、毎回 `None`（"-" 表示）になる。事前サマリの「コンセ」列が常に空欄。
- トリガー: P0-1 適用後の `cmd_prepare(target="scheduled")` 実行時、毎日 1 回。
- 影響: 運用上「コンセ列が見えない」だけで判定ロジックには影響しないが、ユーザーが「コンセ取得自体が壊れた」と誤認するリスクあり。プラン L106-107 で言及はあるが**修正コード案が提示されていない**。
- 根拠: L849 `conse = item.get("consensus_profit")`。新キーは `consensus_profit_by_q` で dict 型。
- 推奨対応: `_print_prepare_summary` で disc_quarter（item.get("quarter")）を引いて該当 Q を表示。例:
  ```python
  by_q = item.get("consensus_profit_by_q") or {}
  q_label = item.get("quarter", "FY") or "FY"
  conse = by_q.get(q_label) if isinstance(by_q, dict) else None
  conse_s = _fmt_yen(conse * 1e6) if conse else "-"
  ```
  prepare 段階で `quarter` が空（カレンダー未登録）の銘柄では FY を fallback。

### #2 P1-1 旧キー検出時の F4c skip は「初回適用日に全銘柄で F4c 沈黙」する
- 箇所: `scripts/zaraba_earnings.py:1614-1637` （プラン L270-279 の提案コード）
- 事象: 旧 `prior_data.json` で `watch` を起動した場合、F4c が全銘柄で発火しない（プラン L267 の方針）。本来 +3 加点で STRONG_BUY 判定すべき銘柄が SLIGHT_BUY 以下に降格する。
- トリガー: 修正コミット直後の **`prepare --data consensus --force` 実行前** の `watch` 起動（運用ミスで起こりうる）
- 影響: 検知漏れ（false negative）。1Q 開示銘柄であれば旧キャッシュの `consensus_profit` は偶然正しい値だが、それも skip される。
- 根拠: プラン L271-279 のコード `elif p.get("consensus_profit") is not None: cons_profit = None`
- 推奨対応:
  - (A) スキーマバージョンキー（`prior["_schema_version"] = 2` 等）を `_build_prior_data` 出力に含め、ロード時に旧スキーマなら起動時 abort + ユーザーに `prepare --data consensus --force` 実行を促す。
  - (B) もしくは `cmd_watch` / `cmd_catchup` の prior ロード直後に旧スキーマ検出 → 自動 migrate（ただし旧 `consensus_profit` は 1Q バグ値の可能性が高いため**自動 migrate は誤値拡散のリスク**。運用上は (A) の起動時 abort が安全）。
  - 単純な per-ticker skip + warning ログだと初回ユーザーが「F4c 列空欄」を見ても気付かないリスクが高い。少なくとも prior ロード関数（`cmd_watch` / `cmd_catchup` の冒頭）で旧スキーマ件数を集計し、N 件以上なら警告 + abort 要否判定。

### #3 NEXT で同 FY・複数 broker の場合 `sort_values("FY", ascending=False).iloc[0]` の broker 不定
- 箇所: プラン L100-101, L219-221（提案 `nxt_sorted = nxt.sort_values("FY", ascending=False); nxt_sorted.iloc[0]`）
- 事象: NEXT 行が同じ FY で複数 broker から取れている場合（IFIS/RAKU/QUICK 等の並走）、FY 降順だけだと**broker 順序が不定**で `iloc[0]` が pandas の stable sort + 元並び依存になる。同じ ticker でも DATAAT が変わると別 broker の値が選ばれる可能性。
- トリガー: コンセンサスソース複数で同 ticker 同 FY を持つ銘柄。`V_CONSENSUS_MERGED` で SOURCE_USED が異なる行が並走している銘柄全般。
- 影響: `obs_guidance_vs_consensus` (NEXT) の値がデータ更新ごとに揺れる。観察用カラムだが、predict notebook 側の答え合わせで日付横断の比較が壊れる。
- 根拠: プラン提示コード `nxt.sort_values("FY", ascending=False)` のみで tie-breaker 無し。`_load_or_fetch_consensus` SQL（L480-483）も SOURCE_USED の優先順位指定無し。
- 推奨対応:
  - 最大 FY 行が複数あれば `mean()` で集約する（既存 V_CONSENSUS_MERGED の SOURCE_USED 優先順位を尊重するなら別途 priority map を入れる）。
  - 最低限 `sort_values(["FY", "SOURCE_USED"], ascending=[False, True])` のように tie-breaker を明示するだけでも揺らぎ防止になる。プラン提示コードに反映必要。

### #4 `_refresh_prior_consensus` の `updated` カウンタ意味が変わる
- 箇所: プラン L246-250（提案コード `for k, v in fields.items(): if v is None: ... else: info[k] = v; updated += 1`）
- 事象: 既存実装では `updated` は CURRENT ヒット銘柄数（ユニーク TICKER 数）。プラン提案ではループ内で `consensus_profit_by_q` / `unit` / `next` / `next_fy` の 4 キー × 銘柄数 で `updated += 1` するため、最大 4 倍の値になる。プラン L250 のコメント `# CURRENT 側ヒットだけカウントしたい場合は条件追加` で曖昧。
- トリガー: `prepare --data consensus` のログ・コンソール表示。
- 影響: 運用上 `prior_consensus_refreshed updated=N` の N の意味が変わり、過去ログとの比較ができなくなる。表示文言「prior_data.json の consensus_profit 更新: {updated}銘柄」（L265）が「件数」表示で銘柄数として読まれる。
- 根拠: プラン L246-250 の擬似コードで条件分岐が未確定。
- 推奨対応: `updated` は **CURRENT ヒット銘柄数** で固定（`if fields["consensus_profit_by_q"] is not None: updated += 1`）。 ヘルパー外で集計するならフラグだけ返す形にする。

---

## 【改善提案】（可読性・保守性）

### #1 (軽微) `latest_q.iloc[0]` 系の暗黙ソート依存の補強
- 箇所: `scripts/zaraba_earnings.py:703`
- 現状: SQL `ORDER BY ... CURRENT_PERIOD_END_DATE DESC`（L344）に依存して `qoq.iloc[0]` を「最新 Q」と扱う。BQ → pandas DataFrame 変換でソート順は通常維持されるが、保証は無い。
- 提案: `qoq.sort_values("CURRENT_PERIOD_END_DATE", ascending=False).iloc[0]` を明示。本バグの再発防止策として。
- 対応するアンチパターン候補: プラン L312 で言及の「集約キーを持つ DataFrame からの `iloc[0]` 前にソート確認」を 004 コーディング規約に明文化する際に同時に取り込む。

### #2 (軽微) `_consensus_to_prior_fields` の戻り値型を TypedDict 化
- 箇所: プラン L188-222（新規ヘルパー）
- 現状: `dict[str, object]` で型情報が散逸。
- 提案:
  ```python
  class ConsensusFields(TypedDict, total=False):
      consensus_profit_by_q: dict[str, float | None]
      consensus_profit_unit: str
      consensus_profit_next: float | None
      consensus_profit_next_fy: str
  ```
  CLAUDE.md コーディング規約「型ヒント必須」に準拠。

### #3 (情報) `_guidance_vs_consensus` の単位整合性の確認
- 箇所: `scripts/zaraba_earnings.py:1388-1395`
- 現状: `cons_yen = cons_next * 1_000_000` で百万円→円換算。NEXT スキーマ変更後も意味同じ（百万円単位の数値が「正しい翌期 FY」になるだけ）。
- 提案: 変更不要。プラン P0-2 の波及記述（L135）通り。確認の上問題なし。

### #4 (軽微) アンチパターン候補の 004 反映
- 箇所: プラン L312 で「集約キー無視の `iloc[0]` / first 前にソートと一致確認」を 004 への新規アンチパターン候補として提案
- 提案: 本レビュー後、プラン適用と同時に `docs/knowledges/tools/004_coding_conventions.md` の §破壊的操作の事故事例 もしくは別節で「DataFrame からの代表値抽出時の集約キー確認」原則として追加。004-1 タグカタログに `bug:aggregation-key-ignored` の追加候補。

### #5 (情報) 既存 results.csv の遡及修正方針
- 箇所: プラン P1-2 (L289-298)
- 現状: 「append-only 監査記録、過去行は触らない」「2026-04-30 以前の F4c は信頼しない」方針で OK。
- 提案: predict notebook 側で `disc_date < '2026-04-30'` の F4c 関連カラム（factors の "コンセ乖離"）を NaN 化するフィルタコード片を 066_zaraba_tool.md §落とし穴 に併記しておくと再現性高い。

---

## 【修正例】（必要な箇所のみ）

#### #1 (重大指摘) に対する `_print_prepare_summary` 修正案

```python
# before: scripts/zaraba_earnings.py:849-850
        conse = item.get("consensus_profit")
        conse_s = _fmt_yen(conse * 1e6) if conse else "-"

# after
        by_q = item.get("consensus_profit_by_q") or {}
        q_label = item.get("quarter") or "FY"
        # quarter が "1Q"/"2Q"/"3Q"/"FY" でない場合は FY フォールバック
        if isinstance(by_q, dict):
            conse = by_q.get(q_label) or by_q.get("FY")
        else:
            conse = None
        conse_s = _fmt_yen(conse * 1e6) if conse else "-"
```

#### #2 (重大指摘) に対する prior ロード時のスキーマチェック案

```python
# scripts/zaraba_earnings.py の cmd_watch / cmd_catchup の prior ロード直後

def _check_prior_schema(prior: dict, target_date: str) -> None:
    """旧スキーマ (consensus_profit only) の銘柄数を集計し、N>0 ならログ警告."""
    legacy_cnt = sum(
        1 for v in prior.values()
        if v.get("consensus_profit") is not None
        and v.get("consensus_profit_by_q") is None
    )
    if legacy_cnt > 0:
        log.warning(
            "prior_legacy_consensus_schema",
            count=legacy_cnt,
            total=len(prior),
            hint="prepare --data consensus --force を実行して再生成してください",
        )
        # 業務判断: 一定割合超えたら abort
        if legacy_cnt / max(len(prior), 1) > 0.5:
            raise SystemExit(
                f"旧コンセスキーマ {legacy_cnt}/{len(prior)} 件検出。"
                f"`prepare --data consensus --force` を実行してから再起動してください。"
            )
```

#### #3 (重大指摘) に対する NEXT tie-breaker 修正案

```python
# プラン L99-102 の after コードの修正
nxt = conse[conse["TARGET"] == "NEXT"]
if not nxt.empty:
    # FY 降順 + SOURCE_USED 優先順位（IFIS > RAKU > QUICK 等の慣習があれば反映）
    nxt_sorted = nxt.sort_values(
        ["FY", "SOURCE_USED"], ascending=[False, True], kind="stable"
    )
    # 同 FY が複数 broker にある場合は mean を取って揺らぎ防止
    max_fy = nxt_sorted["FY"].max()
    nxt_max = nxt_sorted[nxt_sorted["FY"] == max_fy]
    profits = nxt_max["PROFIT"].apply(_to_num).dropna()
    if not profits.empty:
        info["consensus_profit_next"] = float(profits.mean())
        info["consensus_profit_next_fy"] = str(max_fy)
```

---

## 【確認できなかった事項】

- `V_CONSENSUS_MERGED` の SOURCE_USED の意味と broker 優先順位（IFIS / RAKU / QUICK 等の優劣）— BQ ビュー定義を確認していない。複数 broker 同 FY 並走時のあるべき集約方針（mean / 特定 broker 優先 / median）はビュー設計に依存する。
- `scripts/zaraba_earnings.py` に対する pytest スイートの存在有無 — 現コードベースを Glob/Read で確認していないため、プラン L322 の「`_score_record` 単体テスト追加」の既存テスト土台があるかは不明。
- 関連 commit `7e3e7f1`（プラン L345 で「2026-04-14 推定」） — `git log -- scripts/zaraba_earnings.py` で確定すべき。本レビューでは推測のままで pass。
- catchup の旧 prior 経路（`p = {"ticker": code_4, "name": name}` で空 prior を作る L1422-1428）の F4c 動作 — `consensus_profit_by_q` は無いので新スキーマでも無音。挙動変化なし。
- 1878 大東建託の実機 `consensus_*.csv` の SOURCE_USED 並び — プラン L29 で「`FY,138850,CURRENT,IFIS`」と例示はあるが、実機 CSV の broker 並びと NEXT 行の broker 多重度は未確認。プラン提示コードでは IFIS / RAKU が混在している例あり（プラン L121「`FY=202603, 147,193 RAKU`／`FY=202703, 141,842 RAKU`」両方 RAKU だが、他銘柄では IFIS 混在の可能性あり）。
