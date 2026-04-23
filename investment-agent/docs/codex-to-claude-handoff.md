# Codex → Claude Code 引き継ぎメモ

Codex 側で行った変更や特例対応を Claude Code 側へ伝えるためのメモ。
Claude Code 側の `docs/handoff.md` とは目的が違うため、混同を避ける。

## ルール

- エントリは新しい順（上が最新）
- Claude Code 側が確認したら `status` を `done` に変更し、結果を追記
- 不要になったエントリは削除してよい

---

## 2026-04-23 JST

- **from**: Codex
- **to**: Claude Code
- **status**: pending
- **task**: ザラ場モニタツール改造の特例反映
- **context**:
  - Claude Code が操作できないため、今回に限り Codex 側のプログラム変更だけを Claude Code 側 `master` に直接反映した。
  - `docs/knowledges/` は Claude Code → Codex の一方通行同期対象のため、Codex 側での直接更新はリカバリ済み。今後も Codex から直接編集しない。
- **reflected_to_claude_master**:
  - repo/path: `C:\gdrive\claude\investment-agent`
  - branch: `master`
  - commit: `79e07bd Enhance zaraba prepare options`
  - files:
    - `scripts/zaraba_earnings.py`
    - `zara.py`
  - not included:
    - `docs/knowledges/tools/066_zaraba_tool.md`（Codex 追記分は戻した）
    - `C:\Users\zonekun\Dropbox\stock\script\claude-investment-agent.ps1`（repo 外。Codex 側で手元ファイルは更新済みだが Git 管理外）
- **codex_branch_history**:
  - `66d8c0e Enhance zaraba prepare options`
  - `992c51a Recover zaraba knowledge doc edit`
- **changes_summary**:
  - `prepare --target scheduled|all` を追加。既定は従来通り `scheduled`。
  - `prepare --data full|consensus` を追加。既定は従来通り `full`。
  - `--data consensus` はコンセンサスキャッシュだけ更新。
  - `--target all` は `STOCK_CODE_LIST` の全4桁銘柄を対象に `prior_data.json` を作成し、`watch` はその全銘柄キャッシュを使って評価。
  - `prepare_meta.json` を追加し、既存の予定銘柄キャッシュがある日に `--target all` を選んでも誤スキップしないようにした。
  - ザラバ決算モニター表の列幅を `scripts/zaraba_earnings.py` 先頭の `WATCH_TABLE_WIDTH_*` 定数に切り出し。
  - `zara.py` の対話ランチャーに「対象」「データ種別」の選択を追加。
- **verification**:
  - Claude Code 側で `uv run python -m py_compile scripts\zaraba_earnings.py zara.py` 済み。
  - Claude Code 側で `uv run python scripts\zaraba_earnings.py prepare --help` 済み。
- **linux_vm_note**:
  - Linux VM でユーザーが `WATCH_TABLE_WIDTH_*` を手編集済みの場合、その変更は VM 側で以下を実行して `master` に反映する:
    ```bash
    cd ~/project/claude/investment-agent
    git status --short -- scripts/zaraba_earnings.py
    git diff -- scripts/zaraba_earnings.py
    git add scripts/zaraba_earnings.py
    git commit -m "Tune zaraba monitor table widths"
    git push origin master
    ```
  - Claude Code 復旧後は `git pull origin master` で Linux 側の幅調整コミットも取り込むこと。
- **follow_up_for_claude_code**:
  - 必要なら Claude Code 側で `docs/knowledges/tools/066_zaraba_tool.md` を正規ルートとして更新する。
  - Codex 側の再発防止記録は `docs/codex-operation-knowledge.md` に追記済み。

---

## 2026-04-14 JST

- **from**: Windows
- **to**: Linux VM
- **status**: done
- **task**: ザラ場決算ツール 取りこぼし事例の調査・改善
- **result**:
  - **根本原因**: `cmd_prepare` fin_summary SQL のソート順バグで `fin.iloc[0]` が同FY最古の1Q行を拾っていた → `forecast_op` / `prev_cumulative_op` が1Q初期値となり、F2/F4/F13/QoQ/standalone_op 全てが誤値
  - **QoQ 3892/3177**: 修正後 standalone_op 正常化確認（3892: 750-503=247M）
  - **9601**: F4 を「翌期予想 vs 当期実績（cumulative_op）」に変更 → -45% 正表示（旧 +19% の構造バグ排除）
  - **1887**: F2 表示に修正率% 追加（例: `上方修正+7%`）、XBRL FDivAnn 取れない場合の `配当予想修正` related_titles 検知をフォールバック追加
  - **1430**: 同 iloc bug で `prev_cumulative_op` が 1Q（430M）→ 2Q（973M）に修正。閾値判定が改善するはず
  - **results.csv 上書き**: watch 起動時に既存 results.csv を `scored_results` へ初期ロード
  - **review サブコマンド**: `python scripts/zaraba_earnings.py review --date YYYYMMDD` で時系列全件表示
  - **テーブル刷新**: 列 `Score/Code/Name/Cap/Judge/Pos/Neg`、Judge 略称 `S-Buy/N-Buy/W-Buy/中立/W-Sell/Sell`、Cap 列は `YF_STOCK_INFO.MARKET_CAP` から億円右詰め
- **knowledge**: `docs/knowledges/tools/066_zaraba_tool.md` に落とし穴3件追記（iloc bug / F4 分母 / results.csv 上書き）
- **context**: Linuxで稼働中のザラ場ツール（`scripts/zaraba_earnings.py`）に当たり外れあり。Windows側で観測した取りこぼし事例を以下に集約するので、原因究明と改善を依頼。
- **cases**:
  - **1430** — 上方修正で上昇したが拾えていない
  - **QoQ計算バグ疑い** — QoQ%の値が明らかに過大。実際は±10%程度のはずが以下のように異常値:
    - **3892** QoQ +76%
    - **3177** QoQ +287%
  - **results.csv 上書きバグ疑い** — `/tmp/zaraba_cache/20260414/results.csv` は「全件保存・上書きしない」とLinux側で説明されていたが、`watch → 閉じる → watch → 閉じる` を繰り返すと**最後のwatchぶんしか残っていない**ように見える。append/merge処理が効いていない or 起動時に毎回truncateしている可能性
  - **1887 日本国土開発（SLIGHT_BUY）— 因子の表示漏れ・検知漏れ**
    - **上方修正の率（%）が表示されていない**。判定理由に「上方修正」とだけ出るが、修正幅が見えないため評価できない → 修正率を Pos 因子の表示に含めること
    - **増配を捉えていない**。同時開示されているはずだが Pos 因子に出ていない → 配当修正イベントの検知ロジックを確認
  - **9601 評価大失敗 — 来期ガイダンス半減を検知できず（計算バグ確定）**
    - 経常利益 今期 `6,345` → 次期 `3,500`（単位: 百万円）
    - 実質**約45%減益のガイダンス**
    - **ツールは「翌期 +19%」と表示**。符号も大きさも完全に誤り（実際は約 -45%）
    - 想定原因: 来期ガイダンスの YoY 計算ロジックの分子/分母取り違え or 別項目の値を拾っている可能性。スコアリング以前にデータパース or 計算式そのものが壊れている疑いが濃厚
- **investigation_hints**:
  - 1430: 当日のザラ場ツールログで候補に上がったか / スコア何点だったかを確認。拾えなかった原因の切り分け: ①開示検知漏れ ②スコアリング閾値で落ちた ③通知フィルタで落ちた ④TDnet取得遅延
  - QoQバグ: `scripts/zaraba_earnings.py` のQoQ計算ロジックを確認。累計値（YTD）から単独四半期を引く処理で、前期累計を引き忘れている等の単位ミス疑い。F13 QoQ OP 実装周辺（Step 1で追加）を重点確認
  - 必要に応じて `docs/knowledges/tools/066_zaraba_tool.md` に「落とし穴」追記
- **feature_requests**:
  - **全件表示機能（review/history モード）** — 指定日付（デフォルト今日）の watch済み結果を**時間順に全件表示**するサブコマンドを追加。現状 watch を閉じると見る手段がない。
    - 入力: `/tmp/zaraba_cache/YYYYMMDD/results.csv`（上記の上書きバグ修正が前提）
    - 出力: ターミナルに時系列ソート（開示時刻順）でテーブル表示。スコア・QoQ・主要因子列を含む
    - CLI例: `python scripts/zaraba_earnings.py review --date 20260414` / `--date today`
  - **テーブル表示の列幅圧縮（右側に列追加できる余白を作る）**
    - **列名を英語化して短縮**:
      - スコア → `Score`
      - 銘柄名 → `Name`
      - 判定 → `Judge`
    - **判定の略称化**（Judge列を短く）:
      - ユーザー指示の例: Strong Buy → `Strong` / Slightly Buy → `Slight`
      - **既存ラベル一覧（買い側・売り側・Neutral等すべて）を洗い出し、略称案をLinux側で作成してユーザーに提案すること**（Windows側では案を作らない方針）
    - **因子表示の分割**
      - 現状: プラス因子・マイナス因子が同一列に混在
      - 改善: **プラス要因列とマイナス要因列に分離**して表示
      - 列名: `Pos` / `Neg` で確定
    - **列追加: 時価総額（列名 `Cap` で確定）**
      - 位置: `Name` の直後
      - 単位: 億円ベース（「億」表記は付けない）
      - 書式: 数字のみ・カンマ区切りなし・**右詰め**
      - 例: `12345`（=1兆2345億円）, `87`（=87億円）
      - データソース: 既存の prepare 段で取得済みのマスタ（J-Quants 銘柄マスタ等）から流用

---

## 2026-04-13 JST

- **from**: Windows
- **to**: Linux VM
- **status**: done
- **task**: ザラ場ツール因子同期 Step 2-3 実装
- **plan**: `docs/plans/20260413_zaraba_factor_sync.md`
- **context**: 059 EDA で新設された因子をザラ場ツール（`scripts/zaraba_earnings.py`）に取り込む。Step 1（F6非対称化・F10自社株買い・F8b記念配当・F13 QoQ OP）はWindows側で完了済み。
- **remaining**:
  - **Step 2-a**: F4 コンセンサス乖離 ±1~±3 — consensus は prepare で取得済みだがスコアリング未使用。段階的判定を `_score_record()` に追加
  - **Step 2-b**: F7 成長加速/減速 ±1（FYのみ）— BQ から過去FY YoY OP median を prepare に追加
  - **Step 2-c**: F12 PER割安度(PEG) ±1~±2（FYのみ）— 株価÷ForEPS÷成長率
  - **Step 3-a**: F9 テーマブースト +1 — GCS beta_20d.csv + 当日TOPIX
- **command**:
  ```bash
  cd ~/project/claude/investment-agent && git pull origin master
  # 計画の詳細を確認
  cat docs/plans/20260413_zaraba_factor_sync.md
  # Step 1 のコード変更を確認
  git show --stat HEAD
  ```
- **notes**:
  - EDA側（059）のスコアリング因子テーブルが正。閾値・ウェイトはそちらに合わせる
  - Step 2-a のコンセンサス乖離は、累計 vs 単独の単位合わせに注意。EDA predict ノートブックの `compute_score()` 実装を参考にすること
  - Step 3-a のテーマブーストは、ザラ場中の TOPIX リアルタイム取得方法が課題（J-Quants は遅延あり）

---

## 2026-04-10 22:10 JST

- **from**: Linux VM
- **to**: Windows
- **status**: done
- **task**: BC月次KPIダウンロード残り307社
- **reason**: GCP IPレンジ（AS396982）がbuffett-code.comのAWS WAFにブロックされており、Linux VMからは実行不可。トップページすらHTTP 202 + "Human Verification"。
- **command**:
  ```bash
  cd /c/gdrive/claude/investment-agent
  git pull origin master

  # Step 1: GCSからログファイルとCSVを同期（resumeに必要）
  PYTHONUTF8=1 python -c "
  from google.cloud import storage
  from google.oauth2 import service_account
  from pathlib import Path
  creds = service_account.Credentials.from_service_account_file('keys/gcp-service-account.json')
  gcs = storage.Client(project='gmailpj-357912', credentials=creds)
  bucket = gcs.bucket('stock_data_1930932')
  for gcs_key, local_path in [
      ('logs/bc_kpi_download.log', 'data/logs/bc_kpi_download.log'),
      ('csv/bc_monthly_kpi.csv', 'data/csv/bc_monthly_kpi.csv'),
  ]:
      blob = bucket.blob(gcs_key)
      if blob.exists():
          Path(local_path).parent.mkdir(parents=True, exist_ok=True)
          blob.download_to_filename(local_path)
          print(f'OK: {local_path}')
      else:
          print(f'SKIP: {gcs_key} not found')
  "

  # Step 2: v2スクリプトはSelenium版（Windows用はv1を使う）
  # v2はnodriver（Linux用）なので、Windowsではv1を使用
  PYTHONUTF8=1 python scripts/download_bc_kpi.py --resume
  ```
- **notes**:
  - 待機間隔は10秒ベースに変更済み（push済み）。ただしv1の `WAIT_MIN`/`WAIT_MAX` も同様に変更が必要（v1は現在5〜10秒）
  - ログベースresume: `data/logs/bc_kpi_download.log` に200社記録済み（GCS `gs://stock_data_1930932/logs/bc_kpi_download.log` にアップロード済み）
  - CSV: `data/csv/bc_monthly_kpi.csv` も GCS `gs://stock_data_1930932/csv/bc_monthly_kpi.csv` にアップロード済み
  - **v1はCSVベースresume、v2はログベースresume**。v1で実行する場合はCSVがあればOK
  - 知見: `docs/knowledges/tools/069_nodriver_chrome_linux.md` にGCP IPブロックの詳細追記済み
