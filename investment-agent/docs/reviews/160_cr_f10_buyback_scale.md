# 160_cr_f10_buyback_scale

## レビュー対象

| 項目 | 値 |
|------|-----|
| パターン | 2（既存コード改修） |
| プランMD | `docs/plans/tools-066_zaraba_tool_20260513_005500.md` |
| コミット | `3e84109` |

### 対象ファイル

- `scripts/zaraba_earnings.py` — F10自社株買いスコアリング改修（キーワード判定・TN3判定・スケール化）
- `scripts/zaraba_tdnet_poller.py` — PDF解析（parse_buyback_pdf）・正規表現・BuybackInfo
- `tests/test_buyback_parser.py` — PDF正規表現の訓練/テスト分割検証（77件）
- `tests/test_scoring_integration.py` — キーワード判定・スコアリングロジック・PDFラウンドトリップ

### 知見MD

- `docs/knowledges/tools/066_zaraba_tool.md` — F10行・P2 TODO更新

## 背景

F10（自社株買い）ファクターを固定+2点から規模連動スケール（0〜+4）に改修。
TDnet PDFから発行済株式数比率を抽出し、<3%→+1, 3-5%→+3, >=5%→+4。
ToSTNeT-3のみの場合はweight=0（タグ表示のみ）。解析失敗時はフォールバック+2。
キーワード判定のバグ修正（訂正・中止・終了等の除外、月次報告の偽陽性排除）も含む。

---

# コードレビュー: F10 自社株買いスケール化

- 日時: 2026-05-13 03:15 JST
- 対象: commit 3e84109 / プラン `docs/plans/tools-066_zaraba_tool_20260513_005500.md`
- パターン: 2 (改修)
- レビュアー: Claude (code-reviewer runbook)

---

## 【サマリー】

- 変更の要約: F10自社株買い因子を一律+2からPDF解析ベースの段階スコアリング（0〜+4）に改修。キーワード判定関数を新規追加し、BQ 43,941件との突合で偽陰性0.8%を確認。テストは訓練39件+テスト38件+結合テスト18ケース。
- 品質評価: A — BQ大量検証・訓練/テスト分割・結合テストの3層検証が堅実。重大なロジック欠陥はなく、実運用リスクも低い。軽微な設計上の改善余地あり。
- 主要リスク:
  1. watch並列処理中のPDF fetch（F10）がメインスレッドでブロックI/Oを発生させ、ポーリング遅延を招く可能性
  2. `poller._session` へのアクセスがprivate属性参照で、ポーラー実装変更時に破損する
  3. `_parse_comma_int` が小数点を含む株数文字列を受けると誤変換（`1.5` → `.` 除去 → `15`）

## 【パターン2: 改修プラン評価】

### 妥当性

プランはF10因子の3つの課題（キーワード漏れ/ノイズ、一律+2の粗さ、TN3未区別）を正しく特定し、それぞれに対処している。根本原因分析（BQ全件タイトル分析 → キーワード設計 → PDF解析 → スコア段階化）は体系的で、方向性は適切。独自推定した根本原因とも一致する。

### 副作用・デグレードチェック

- [x] `related_titles_by_code` → `related_discs_by_code` のリネーム: catchup(L1168-1170)とwatch(L1442,L1515)の両方で変更済み。`_xbrl_to_jquants_rec` は `related_titles` 引数も維持しており後方互換性あり
- [x] F8b（記念配当）の `related_titles` 参照: L1901で `rec.get("_related_titles")` から取得しており、`_xbrl_to_jquants_rec` で `titles` を構築済み。影響なし
- [x] F14（株式分割）の `_all_related` 参照: diffに含まれないが、`_related_titles` を使用しており同様に互換維持
- [x] `_split_factors` のPos/Neg分類: `自社株N.N%`, `自社株(TN3)`, `自社株買い` はいずれも NEG_MARKERS に該当せず Pos 側に分類される。docstring も更新済み。正常

### 抜け漏れ（類似観点での横展開含む）

- [x] `_is_buyback_title` のexclude/includeキーワードの衝突: テストケースL35で `"自己株式取得に係る事項の決定及び自己株式の消却に関するお知らせ"` が `False` を返すことを確認。`_EXCLUDE` が `_INCLUDE` より先に判定されるため、消却を含む複合タイトルは正しく除外される
- [x] `pdfplumber` の依存: プランに記載の通り、既存16スクリプトで使用実績あり。追加依存なし
- [ ] PSメニュー依存パッケージ（`claude-investment-agent.ps1` の `$REQUIRED_PACKAGES`）: `pdfplumber` が新たにwatch/catchup実行パスで必要になるが、既に別スクリプト経由で追加済みか未確認。066知見MD §PSメニュー依存パッケージチェック のリストに `pdfplumber` がない

### 新規リスク

- watch中のPDF fetchはTDnetへのHTTPリクエスト（timeout=10秒）を決算スコアリングのメインパスに挿入する。決算集中日に自社株買い開示が複数あると、ポーリングの1サイクルが10秒×N件分延びる可能性がある。ただし自社株買い開示と決算短信が同時刻に集中するケースは稀であり、実運用リスクは低い
- `_amount_to_oku` の `千円` 変換: `value / 100000.0` だが、億円への変換は `value / 100_000` が正しい。100,000千円 = 1億円なので計算は正しい

## 【重大な指摘】（即修正）

### #1 watch/catchup並列処理中のPDF fetchがThreadPoolExecutor外で実行される

- 箇所: `scripts/zaraba_earnings.py:1880-1883`
- 事象: `_score_record` 内で `fetch_and_parse_buyback` を呼び出すが、この関数はHTTPリクエスト（timeout=10秒）を含む。`_score_record` は `ThreadPoolExecutor.as_completed` のイテレーション内（L1218-1229, L1539-1552）で呼ばれるが、`_process_one` が返した後のメインスレッドでの処理。XBRLダウンロードは並列化されているが、PDF fetchは直列化されている
- トリガー: 決算集中日に同一時刻で複数銘柄が自社株買い開示を出した場合。1銘柄あたりPDF fetch+parseで最大10秒のブロック
- 影響: watchのポーリング間隔が延び、決算発表の検知が遅延する。致命的ではないが、ザラ場ツールの即時性が損なわれる
- 根拠: L1539-1543でfut.result()取得後に_score_record→fetch_and_parse_buybackが同期呼出し
- 推奨対応: **[方向性]** `_process_one` の中でXBRLダウンロード+PDF fetchを両方並列実行するか、PDF fetchを非同期化する。ただし現時点では自社株買い開示と決算短信の同時刻集中は稀なため、優先度は低い。P2 TODOとして記録し、実運用で遅延が観測されてから対処でよい

### #2 `poller._session` へのprivate属性アクセス

- 箇所: `scripts/zaraba_earnings.py:1223`, `scripts/zaraba_earnings.py:1543`
- 事象: `poller._session` でポーラーの内部HTTPセッションに直接アクセスしている。`_session` はPythonの慣習としてprivate属性であり、ポーラー実装の変更（セッション名変更、セッション管理方式変更等）で破損する
- トリガー: `zaraba_tdnet_poller.py` のポーラークラスをリファクタリングした場合
- 影響: AttributeError で `_score_record` 内のF10処理が失敗する。ただしフォールバック+2が適用されるため、スコアリング全体は継続する
- 根拠: `TdnetHtmlPoller`/`YanoshinPoller` の `_session` は `__init__` 内で設定されるprivate属性（L211-222, L279-290）
- 推奨対応: **[方向性]** ポーラーにpublicな `session` プロパティを追加するか、`fetch_and_parse_buyback` がセッションを自前で構築するように変更する。現時点では呼び出し元が限定されているため緊急性は低い

## 【改善提案】（可読性・保守性）

### #1 `_xbrl_to_jquants_rec` の `related_titles` 引数が冗長

- 箇所: `scripts/zaraba_earnings.py:1569-1570`
- 現状: `related_discs` と `related_titles` の2引数を持つが、`related_titles` は後方互換のために残されたもので、実際の呼び出し元（catchup L1222, watch L1542）はすべて `related_discs` のみを渡している
- 提案: `related_titles` 引数を廃止し、内部で `[d.title for d in discs]` のみで構築する。現在もデフォルト動作がそうなっているため、引数削除だけで済む

### #2 `_is_tostnet_title` のdocstringとロジックの乖離

- 箇所: `scripts/zaraba_earnings.py:1081-1083`
- 現状: docstringに「ToSTNeT-3 / N-NET3（立会外取引）のタイトルか判定」とあるが、ロジックは `"立会外買付" in title` のみ。N-NET3の検出は「立会外買付」がタイトルに含まれているケースでのみ機能する。一部の古い開示では `"Ｎ－ＮＥＴ３"` のみでタイトルに「立会外買付」が含まれない可能性がある
- 提案: テストケース（test_scoring_integration.py L26）で `"自己株式の取得及び自己株式立会外買付取引（Ｎ－ＮＥＴ３）"` を検証しており、実際には「立会外買付」が含まれるため現時点では問題ない。ただし、N-NET3のタイトルで「立会外買付」を含まないバリエーションが出現した場合のフォールバックとして、PDF側の `_RE_TN3` が捕捉するため実害なし。docstringを「立会外買付取引をタイトルから判定」に修正する程度でよい

### #3 `fetch_and_parse_buyback` のimportが3箇所で重複

- 箇所: `scripts/zaraba_earnings.py:1124`, `1376`, `1882`
- 現状: catchup冒頭（L1124）、watch冒頭（L1376）、`_score_record`内（L1882）の3箇所で `from zaraba_tdnet_poller import fetch_and_parse_buyback` をしている。L1882は条件付きimport（http_session is not None の場合のみ）だが、L1124/L1376で既にimport済みのためモジュールスコープでは利用可能
- 提案: L1882のローカルimportは `_score_record` を独立して呼び出すケースへの防御だが、実際にはcatchup/watchからしか呼ばれない。ファイル冒頭でのトップレベルimportに統一するか、少なくともL1882のimportにコメントで理由を記載する

## 【確認できなかった事項】

- `_RE_PCT` 正規表現の `re.DOTALL` 有効時に、複数ページにまたがるPDFで「発行済株式総数」と「割合N%」が異なるページにある場合の挙動。`.*?` の lazy match で最短マッチするため、異なるセクションの数値を誤キャプチャする可能性がゼロではないが、TDnet PDFの定型フォーマットでは同一ブロックに記載されるため実害は低い（訓練/テスト77件で全PASSが傍証）
- watch実行中にTDnetサーバーがPDFダウンロードに遅延応答（10秒タイムアウトぎりぎり）を返した場合のユーザー体験。rich Live表示が10秒間フリーズするが、Ctrl+Cでの終了は可能
- `_parse_comma_int` で全角ピリオド `．` を含む入力（例: `１．５株`）を処理した場合、`_ZEN2HAN` で `.` に変換後 `.replace(".", "")` で除去されるため `15` になる。株数フィールドで小数が出現するケースは極めて稀（端株処理等）だが、0件とは断定できない
