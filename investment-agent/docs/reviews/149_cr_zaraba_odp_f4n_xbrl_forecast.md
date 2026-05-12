# コードレビュー: ザラ場ODP移行 + F4n新因子 + XBRL翌期予想

- 日時: 2026-05-12 22:30 JST
- 対象: scripts/zaraba_earnings.py, scripts/zaraba_tdnet_poller.py, scripts/xbrl_lookup.py, docs/knowledges/tools/066_zaraba_tool.md, docs/knowledges/tools/099_xbrl_lookup.md
- パターン: 1 (新規コード/変更のまっさらレビュー)
- レビュアー: Claude (code-reviewer runbook)

---

## 【サマリー】

- 変更の要約: ザラ場スコアリングエンジンの主要因子(F4/F7g/F12/F15/F4c)を営業利益→経常利益(ODP)ベースに統一し、F4n翌期コンセンサス乖離(純利益ベース)を新設。zaraba_tdnet_pollerに翌期予想4タグを追加。xbrl_lookupにFY時翌期予想行を追加。
- 品質評価: B — ODP→OPフォールバックパターンが全因子で一貫しており設計意図が明確。F4nのウェイトテーブルもF4cからの流用で整合性あり。ただし、エッジケース処理に1件の重大リスクと複数の改善余地あり。
- 主要リスク:
  1. F4c が FY 開示時に `ORD_PROFIT` コンセンサスを使うが、prepare サマリー表示(L1005)では FY 時 `OP_PROFIT` を使う不整合
  2. xbrl_lookup.py の `_xval` が `float(entry["value"])` を呼ぶが、`_parse_ixbrl` が `scaled_value` を `str()` 変換した文字列を格納するため、`"-inf"` や `"nan"` 文字列で予期しない float 値が生成される可能性
  3. F4n で `NxFNP` が None だが `consensus_next` に `NET_PROFIT` がある場合（会社予想非開示でコンセは存在）のサイレントスキップ

## 【重大な指摘】（即修正）

### #1 F4c の FY 時コンセンサス参照キーと prepare サマリーの不整合 **[修正済み]**

- 箇所: `scripts/zaraba_earnings.py:1850` vs `scripts/zaraba_earnings.py:1005`
- 事象: F4c は全 Q で `cons_q_data.get("ORD_PROFIT")` を使って経常利益ベースで比較する。しかし prepare サマリー表示（L1005）では `conse_key = "OP_PROFIT" if q_label == "FY" else "ORD_PROFIT"` と、FY 時は `OP_PROFIT` を表示する。ユーザーが prepare サマリーで見るコンセンサス値と、実際にスコアリングで使われるコンセンサス値が FY 開示時に異なる。
- トリガー: FY 決算銘柄で `OP_PROFIT` と `ORD_PROFIT` が異なる値を持つ場合（製造業等で営業外損益がある銘柄の大半）
- 影響: ユーザーが prepare サマリーの「コンセ」列を見てスコアリング結果を予測するが、実際の F4c は別の値を参照するため判断を誤る。スコアリング自体のバグではないが、表示と実処理の乖離は運用ミスの温床。
- 根拠: L1005 の `"OP_PROFIT" if q_label == "FY"` と L1850 の `cons_q_data.get("ORD_PROFIT")` の分岐条件が不一致
- 推奨対応: **[方向性]** L1005 のコンセンサス表示キーを F4c のスコアリングロジックと揃える（全 Q で `ORD_PROFIT` に統一）か、prepare サマリーに両方の値を表示するか、いずれかの方針を選択。FY 時に `OP_PROFIT` を使う理由があるなら（QUICK ソースの FY は OP しかないなど）コメントで明記すべき。
- **対応**: L1005 を全Q `ORD_PROFIT` に統一。コメント削除。

### #2 F4n のサイレントスキップ — 翌期NP非開示 × コンセンサス存在のケース **[却下: 設計意図通り]**

- 箇所: `scripts/zaraba_earnings.py:1880-1911`
- 事象: F4n は `nx_np = _to_num(rec.get("NxFNP"))` が None の場合、コンセンサスが存在しても何も出力せずスキップする。F4 では翌期予想非開示時に `score -= 1` + `"翌期予想非開示"` を付与するが、F4n にはこの救済ロジックがない。
- トリガー: FY 決算で翌期純利益予想を非開示にしている銘柄（通期予想を出すが純利益だけ非開示にする企業は少数だが存在）
- 影響: F4 が「翌期予想非開示」ペナルティを付けるのに対し、F4n はペナルティもなく完全に沈黙する。情報の非対称性。ただし F4n は「翌期NP」と「翌期コンセNP」の比較因子であり、片方が無い場合にペナルティを付けるべきかは設計判断。
- 根拠: F4（L1762-1765）の `翌期予想非開示` 分岐と F4n の分岐構造を比較
- 推奨対応: **[方向性]** F4n で NxFNP が None の場合の振る舞いを意図的にスキップとするなら、コメントで「NxFNP 非開示時はペナルティ不要（F4 でカバー済み）」等と明記。F4 の「翌期予想非開示」は NxFODP/NxFOP が None の場合に発火するため、NxFNP だけ非開示の稀少ケースでは F4 も F4n も無反応になる。
- **却下理由**: F4は「会社が翌期予想を出すか」自体がシグナル（大型株非開示=ネガティブ）でペナルティは合理的。F4nは「会社予想 vs コンセンサス」の乖離因子であり、比較対象が欠損すれば評価不能=スキップが正しい。コンセンサス非カバー銘柄も多く、それ自体はネガティブではない。NxFNPだけ非開示のケースはF4の「翌期予想非開示」でカバー済み（NxFODP/NxFOPも通常同時に非開示）。二重減点回避の設計。

### #3 xbrl_lookup.py の `print` 使用 — structlog ルール違反 **[不採用: 重大ではない]**

- 箇所: `scripts/xbrl_lookup.py:350-358`
- 事象: 3箇所で `print()` を使用。CLAUDE.md §7 のコーディング規約は「print禁止。structlogを使用」。
- トリガー: 常時
- 影響: 規約違反。ただし zaraba_earnings.py も CLI ユーザー向け出力に `print()` を多用しており、プロジェクト全体で CLI 向け出力に限り print を許容する暗黙の慣行がある。一貫性の観点からは xbrl_lookup.py だけ指摘するのは不公平だが、規約上は違反。
- 根拠: CLAUDE.md:L7 「ロギング: print禁止。structlogを使用」
- 推奨対応: **[方向性]** CLI ユーザー向けの進捗表示を structlog に切り替えるか、004 規約に「CLI ユーザー向け出力は print 許容」の例外を明記するか。zaraba_earnings.py と同じ慣行に揃えるなら、規約側を更新する方が現実的。
- **不採用理由**: CLIユーザー向け表示出力でありプロジェクト全体の慣行。重大な指摘には該当しない。

### #4 xbrl_lookup.py の SQL インジェクション経路 **[不採用: 重大ではない]**

- 箇所: `scripts/xbrl_lookup.py:87`
- 事象: `WHERE LOCAL_CODE LIKE '{ticker[:4]}%'` で f-string による SQL 組立。ticker は argparse 経由の CLI 入力。
- トリガー: `python scripts/xbrl_lookup.py "'; DROP TABLE--"` のような入力
- 影響: ローカル専用ツールであり、BQ のパラメータ付きクエリが推奨される場面。サービスアカウント権限で BQ テーブル操作が可能な場合にリスクあり。ただし zaraba_earnings.py にも同様のパターンが多数存在しており（プロジェクト全体の課題）、本レビュースコープ固有の問題ではない。
- 根拠: L87 の f-string SQL
- 推奨対応: **[方向性]** パラメータ化クエリへの移行が望ましいが、プロジェクト全体で同パターンが蔓延しているため、個別修正より横断的な対応を検討。
- **不採用理由**: ローカルCLI専用ツールかつプロジェクト横断の既存課題。本スコープで重大指摘とするほどではない。

## 【改善提案】（可読性・保守性）

### #1 ODP→OP フォールバックパターンの共通化

- 箇所: `scripts/zaraba_earnings.py:1744-1752`, `1934-1942`, `1961-1968`
- 現状: F4, F7g, F12 の3因子で同一の「NxFODP があれば ODP で、なければ NxFOP で OP フォールバック」パターンが個別にインライン実装されている。同じ条件分岐が3回コピーされ、いずれかの修正漏れがロジック分離を招く。
- 提案: ヘルパー関数 `_nx_growth_rate(rec, cumulative_op) -> float | None` を抽出し、3因子から共通呼び出し。

### #2 F4c/F4n のウェイトテーブル重複

- 箇所: `scripts/zaraba_earnings.py:1855-1878` と `1888-1911`
- 現状: F4c と F4n で if/elif の8段階ウェイトテーブルがまったく同一構造でコピーされている。ウェイト変更時に片方の更新を忘れるリスク。
- 提案: ウェイト判定を関数化（`_consensus_weight(deviation: float) -> int`）して共通呼び出し。

### #3 xbrl_lookup.py の `dateutil.relativedelta` 二重 import

- 箇所: `scripts/xbrl_lookup.py:390` と `scripts/xbrl_lookup.py:413`
- 現状: 同一関数内で `from dateutil.relativedelta import relativedelta` を2回インポートしている（L390 は `relativedelta`、L413 は `relativedelta as _rd`）。
- 提案: 関数先頭で1回だけ import する。

### #4 xbrl_lookup.py の翌期予想 FY 判定 — 変則決算期リスク

- 箇所: `scripts/xbrl_lookup.py:414`
- 現状: `next_fy_dt = datetime.strptime(fy_end, "%Y-%m-%d") + _rd(years=1)` で翌期FY終了日を算出。決算期変更企業では +1年 が正しくない場合がある（例: 3月決算→12月決算変更時、翌期は+9ヶ月）。
- 提案: 099_xbrl_lookup.md に「変則決算期の企業は FY判定がずれる場合がある」の記述があり認識済み。リスクが顕在化した場合の参考として記録。

### #5 _split_factors の YoY 判定パターン

- 箇所: `scripts/zaraba_earnings.py:1085-1086`
- 現状: `f.startswith("YoY")` で判定後 `"-" in f` で正負を判断している。しかし `YoY+100%` のような値には `-` が含まれないため正しく判定される一方、`YoY-30%` は `"-"` を含むので NEG に振られる。これ自体は正しい。ただし `YoY` の生成元（L1736-1739）を見ると、正の場合は `f"YoY+{yoy:.0%}"` で負の場合は `f"YoY{yoy:.0%}"` であり、負の場合は `+` がなく `-` が `yoy` のフォーマットから自動挿入される。この暗黙の挙動に依存しているため、フォーマット変更時に判定が壊れるリスクがある。
- 提案: ラベル生成時に明示的に `YoY-` プレフィックスを付ける（`f"YoY-{abs(yoy):.0%}"`）か、_split_factors 側のコメントで「YoY の符号は format 結果の `-` 文字に依存」と明記。

### #6 066_zaraba_tool.md のF4c行 — FY時の注記不足 **[解消: #1修正で不整合解消]**

- 箇所: `docs/knowledges/tools/066_zaraba_tool.md:140`
- 現状: F4c の説明は「全Q経常利益ベース」としているが、prepare サマリー表示（#1で指摘）では FY 時に `OP_PROFIT` を使っている。MD 側では F4c の実装に合わせて「全Q で ORD_PROFIT」と記載しており、F4c のスコアリングロジックとは整合している。ただし prepare サマリーとの不整合は注記すべき。
- 提案: 066 MD の F4c 行に「（※ prepare サマリー表示では FY 時 OP_PROFIT を表示。スコアリングは常に ORD_PROFIT）」等の注記追加。
- **解消**: 重大指摘#1でprepareサマリー表示を全Q `ORD_PROFIT` に統一済み。不整合が解消されたため注記不要。

## 【修正例】（必要な箇所のみ）

#### #1 に対する修正案（prepare サマリーの FY コンセンサスキー統一）

```python
# before: scripts/zaraba_earnings.py:1005
        conse_key = "OP_PROFIT" if q_label == "FY" else "ORD_PROFIT"

# after（F4c のスコアリングと一致させる）
        conse_key = "ORD_PROFIT"  # F4c は全Q経常利益ベース。表示も統一
```

#### 改善提案 #2 に対する修正案（ウェイトテーブル共通化）

```python
# after: 共通ウェイト判定関数
def _consensus_deviation_weight(cd: float) -> int:
    """コンセンサス乖離率 → スコアウェイト（F4c/F4n 共通）."""
    if cd > 0.10:
        return 3
    elif cd > 0.05:
        return 2
    elif cd > 0:
        return 1
    elif cd < -0.30:
        return -5
    elif cd < -0.20:
        return -4
    elif cd < -0.10:
        return -3
    elif cd < -0.05:
        return -2
    elif cd < 0:
        return -1
    return 0
```

## 【確認できなかった事項】

- `V_CONSENSUS_MERGED` VIEW の ORD_PROFIT が 1Q/2Q/3Q で累計値か standalone 値かの確認。F4c が `rec.get("OrdinaryProfit")` (XBRL累計値) と比較しているため、コンセンサスも累計値であれば正しいが、standalone 値であれば F4c の比較が不正。BQ VIEW の定義を実行して確認する必要がある。
- TDNET_TAG_MAP の `NEXT_YEAR_FORECAST_ODP` に `["OrdinaryIncome"]` の1タグのみ登録されているが、IFRS 企業で翌期経常利益予想がない場合に代替タグが必要かの確認。IFRS には経常利益の概念がないため、IFRS 企業では NxFODP は常に None → NxFOP フォールバックが作動する設計と思われるが、TDnet iXBRL のサンプルで検証していない。
- F4n の `NxFNP` が XBRL 上で取得できる頻度。会社予想の純利益を開示しない FY 決算がどの程度あるか（大半の企業は開示するはず）。
