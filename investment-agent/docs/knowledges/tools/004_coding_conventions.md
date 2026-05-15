# コーディング規約・設計方針

**カテゴリ**: tools
**作成日**: 2026-02-25
**ステータス**: 有効
**適用範囲**: プロジェクト全体（すべてのスクリプト・モジュール）

## 概要

特定のスクリプトに限らず、プロジェクト全体で共通して守るコーディングの方針。

---

## 定数・モード値は短く

ユーザーが直接書き換える定数やモード値は、**短い文字列または記号**にする。

### 理由

- 書き換えコストが低い（タイプ量が少ない）
- 視認性が高い（差分がひと目でわかる）
- コメントに説明を書けば十分

### 実例（tdnet_download.py の DATE_MODE）

```python
# ❌ 冗長
DATE_MODE = "today"    # または "single" / "range"

# ✅ 短く
DATE_MODE = "t"        # "t"=今日 / "1"=特定の1日 / "r"=期間
```

### 適用パターン

| 用途 | 冗長（避ける） | 短く（推奨） |
|------|--------------|------------|
| モード選択 | `"today"` / `"single"` / `"range"` | `"t"` / `"1"` / `"r"` |
| フラグ | `"enabled"` / `"disabled"` | `"on"` / `"off"` または `True` / `False` |
| 環境 | `"production"` / `"development"` | `"prod"` / `"dev"` |
| 方向 | `"ascending"` / `"descending"` | `"asc"` / `"desc"` |

### 注意

- 短さを優先するのは**ユーザーが直接書き換える箇所**に限る
- 内部ロジックの変数名・関数名は可読性優先で通常の命名規則に従う
- 選択肢と意味はコメントに必ず記載する（短くしても意味を失わない）

---

## JSTタイムゾーン統一パターン（pytz 廃止）

日時処理は **stdlib の `datetime.timezone`** に統一する。`pytz` は使わない。

### 書き方

```python
from datetime import datetime, timezone, timedelta

JST = timezone(timedelta(hours=+9), "JST")

# ✅ 正しい
now = datetime.now(JST)

# ❌ 使わない（pytz 依存、インストール不要にする）
import pytz
JST_pytz = pytz.timezone('Asia/Tokyo')
now = datetime.now(JST_pytz)
```

### 適用範囲

- すべてのスクリプト・モジュールでこの書き方に統一する
- `datetime.now()`（タイムゾーンなし）は UTC になるため使用禁止。必ず `JST` を渡す
- `notify.py` の `send_mail()` に渡す時刻表示も JST で統一する

### 適用済みスクリプト

| スクリプト | 対応状況 |
|-----------|---------|
| `scripts/edinet_download.py` | ✅ JST 適用済み |
| `scripts/tdnet_download.py` | ✅ JST 適用済み |
| `scripts/jquants_get_fin_summary.py` | ✅ JST 適用済み |
| `scripts/edinet_load.py` | ✅ JST 適用済み |
| `scripts/tdnet_load.py` | ✅ JST 適用済み |
| `scripts/stock_price_load.py` | ✅ JST 適用済み |
| `scripts/stock_code_list_load.py` | ✅ JST 適用済み |

---

## TICKER（銘柄コード）は常に文字列型

### ルール

日本株の銘柄コードは**数字4桁が大半だが、アルファベット付きも存在する**（例: `174A`, `218A`）。

- BQ テーブル上: `STRING` 型で統一済み
- Python コード上: **`str` 型**で扱う。`int` への変換禁止
- CSV 読み込み時: `pd.read_csv(..., dtype={"TICKER": str})` または読み込み後に `df["TICKER"] = df["TICKER"].astype(str)` で明示的に文字列化する

### なぜ必要か

pandas の `read_csv` は列の全値が数値のみの場合 `int64` に推論する。キャッシュ CSV に `174A` 等が含まれない部分データセットでは TICKER が int 化し、他テーブルとの JOIN や `set` lookup で型不一致が起きる（サイレントに空結果になる）。

---

## Cloud Run Job / バックグラウンド実行では PYTHONUNBUFFERED=1 必須

### 問題

Python はターミナル以外（Cloud Run・バックグラウンドプロセス）に出力するとき、
stdout をバッファに溜めてからまとめて書き出す。

結果:
- Cloud Run Job のログがジョブ終了まで Cloud Logging に流れない（監視不能）
- `run_in_background=true` で実行したとき、進捗の `print()` がファイルに現れない
- stderr（警告・例外）は即時出力されるため、stderr だけ見えて stdout が消えたように見える

### 対処

すべての Cloud Run Job 向け Dockerfile に必ず両方を設定する:

```dockerfile
ENV PYTHONUTF8=1
ENV PYTHONUNBUFFERED=1
```

`PYTHONUTF8=1` だけでは**バッファリングは解消されない**（エンコーディングのみ変更）。

### 適用ルール

- Dockerfile を新規作成するときは必ず両方追加
- MCP サーバー系（HTTP ポート待受）は対象外

---

## ノートブック・スクリプトでの BQ コスト最適化（必須）

### 問題

BQ クエリを複数セルで個別に実行すると、セル再実行のたびに課金が発生する。
特に EDA ノートブックでは試行錯誤で何度もセルを再実行するため、BQ 料金が莫大になる。

### ルール

1. **BQ アクセスは1回のみ**: 必要な全テーブルを1つのセル/関数でまとめてダウンロードする
2. **ローカルキャッシュ必須**: ダウンロードしたデータは CSV としてローカルに保存する
   - Colab: `/content/<cache_dir>/`
   - ローカル: `C:\tmp\<cache_dir>/`
3. **2回目以降はキャッシュから読む**: `FORCE_RELOAD` フラグで明示的に再ダウンロードしない限り、ローカル CSV から読み込む
4. **JOIN・フィルタ・集計はすべて pandas で行う**: BQ 側で JOIN するクエリを複数回投げない。各テーブルを単純 SELECT で取得し、pandas でメモリ上結合する

### パターン

```python
FORCE_RELOAD = False
CACHE_DIR = Path('/content/cache')
_CACHE = {
    'table_a': CACHE_DIR / f'{tag}_table_a.csv',
    'table_b': CACHE_DIR / f'{tag}_table_b.csv',
}

def _download_all():
    """BQ から全テーブルを1回でダウンロード → CSV 保存."""
    for name, query in queries.items():
        df = bq.query(query).to_dataframe()
        df.to_csv(_CACHE[name], index=False)

if FORCE_RELOAD or not all(p.exists() for p in _CACHE.values()):
    _download_all()

df_a = pd.read_csv(_CACHE['table_a'])
df_b = pd.read_csv(_CACHE['table_b'])
```

### 禁止パターン

```python
# ❌ セルごとに個別 BQ クエリ（再実行のたびに課金）
df_sector = bq.query("SELECT ... FROM STOCK_CODE_LIST").to_dataframe()
# 別セル
df_price = bq.query("SELECT ... FROM STOCK_PRICE_JQUANTS").to_dataframe()
# さらに別セル
df_consensus = bq.query("SELECT ... FROM CONSENSUS").to_dataframe()
```

---

## ノートブックの保存先と実行環境（必須）

### 保存先（2箇所）

ノートブック（`.ipynb`）は必ず以下の **2箇所** に保存する:

| 保存先 | パス | 用途 |
|--------|------|------|
| プロジェクト内 | `scripts/<feature>/xxx.ipynb` | git管理・ローカル実行 |
| Colab Notebooks | `G:\マイドライブ\Colab Notebooks\xxx.ipynb` | Colab から直接開く |

ノートブックを新規作成・更新したら**常に両方に保存**すること。

**チェックポイント管理**: Jupyter が自動生成する `.ipynb_checkpoints/` は `data/tmp/.jupyter_checkpoints/` に集約設定済み（`~/.jupyter/jupyter_notebook_config.py`）。`.gitignore` で除外済み。散在を見つけたら手動削除。

```bash
# コピーコマンド（Git Bash）
cp scripts/<feature>/xxx.ipynb "/g/マイドライブ/Colab Notebooks/xxx.ipynb"
```

### 実行環境（Colab / ローカル 両対応）

ノートブックは Colab と Windows ローカルの **両方で動く** ように書く。
Setup セルで `RUNTIME` を自動判定し、認証・パスを分岐する。

```python
try:
    from google.colab import auth, userdata
    RUNTIME = 'colab'
except ImportError:
    RUNTIME = 'local'

if RUNTIME == 'colab':
    auth.authenticate_user()
    bq = bigquery.Client(project='gmailpj-357912')
else:
    # ローカル: SSL workaround + サービスアカウント
    ...
```

---

## バッチジョブ・ETL アンチパターン集

**適用範囲**: 全バッチジョブ・ETL スクリプト（Cloud Run Job / ローカルバッチ / Workflows 起動ジョブ 問わず）。
**出所**: 2026-04-20 の TDnet load 系ジョブ事故から抽出。個別の事故詳細はコミット履歴を参照。

**ドメイン固有の延長**（本節を前提として読む）:
- `078_gemma4_operation.md §7 Gemma ↔ Gemini 結合部アンチパターン` — G-1〜G-3（Gemma TPU worker / Gemini Batch 固有）
- `013_tdnet_load.md §TDnet ETL 固有の再発防止ルール` — T-1〜T-5（`scripts/tdnet_load_parallel.py` 固有）
- `080_workflows_runbook.md §12 次ステップ gate 設計` — A-8 の Cloud Workflows 適用版

### A. 失敗伝播（exit code / 例外）

| # | アンチパターン | 症状 | 対策 |
|---|---|---|---|
| A-1 | `errors += 1` したのに `sys.exit()` せず exit 0 で終わる | Cloud Run Job が SUCCESS 扱いになり、上流 Workflow が誤検知連鎖する | `main()` 末尾で `if errors > 0: sys.exit(1)` を徹底。呼び出し側は返り値ではなく例外で故障を知る |
| A-2 | 内部 `try/except` で例外を返り値に変換する | 副作用関数が `{errors: n}` を返すだけで、呼び出し側は成功扱いで継続しバグを隠蔽 | **副作用を伴う関数は失敗時に raise**。正常系返り値に errors カウントを混ぜない |
| A-3 | silent `except: continue` | 失敗件数が闇に消えサマリに現れない | `except X as e: logger.exception(...); errors += 1`。最後に `processed=/errors=` でサマリ出力 |
| A-4 | `ThreadPoolExecutor` で `fut.result()` のスレッド例外を main に素通りさせる | 1件の例外で後続 future が未回収のまま main が死ぬ | `for fut in as_completed(...): try: fut.result() except: logger.exception(...); errors += 1` で個別ハンドル |
| A-5 | 不正入力の fallback で値を捏造して処理続行（例: ファイル名 parse 失敗時に `datetime.now()` / `uuid.uuid4()` を返す） | 誤データが下流に伝搬し、retry 時に重複 insert・日付不整合・PK 衝突を誘発 | fallback は `raise ValueError(...)`。呼び出し側が `except → count + log + skip` で明示的に捨てる。**parser は失敗しても「何か返す」べきではない** |
| A-6 | バッチ単位で失敗する処理で `errors += 1` を 1インクリメントだけ加算 | サマリが実データ損失量を過小報告（1件失敗と 1,000件失敗が同じ扱い） | `errors += len(failed_batch)` 行数単位で加算。サマリ粒度を「実際の損失量」に合わせる |
| A-7 | 終了時に `processed` / `skipped` / `errors` のサマリを出さず exit | 監視側が「処理件数 0 の成功」を検知できず、異常検知の穴に | 全モードで terminal summary に 3 指標を `logger.info` で出力。structured field にすると監視クエリが書ける |
| A-8 | 連鎖実行で上流ジョブの exit code だけを信じて次段を発火 | exit 0 嘘（A-1）と合流すると「成功扱い → 次段発火 → データ不整合の雪崩」。chain script / Workflows YAML の gate 設計で頻発 | gate は**実測データ**で確認: BQ `row_count`、GCS 出力 blob 数、`_SUCCESS` マーカー、次段 input に必要な key set 等。「`state == SUCCEEDED` かつ `row_count > 0`」のような複合 gate を既定に |

### B. トランザクション整合性

| # | アンチパターン | 症状 | 対策 |
|---|---|---|---|
| B-1 | cleanup（state 削除等）を INSERT/UPDATE 成功確認なしで実行 | INSERT 失敗時に state も消失しリジューム不能 | `if processed > 0 and errors == 0:` で gate。**`finally` ではなく成功パスに置く** |
| B-2 | DELETE → INSERT を非トランザクションで流す | INSERT 失敗で pending 行も既存行も両方消える | INSERT 先行 → 成功後 DELETE / `MERGE` 1文 / 別カラムに入れて最後に swap |
| B-3 | 既存キー取得の BQ クエリ失敗時に空 set を返して処理続行 | 冪等性判定が崩れ重複 INSERT が大量増産 | 失敗は raise して job を止める。冪等性は UNIQUE 制約 or `MERGE` で担保（アプリ側 `set` 差分に依存しない） |
| B-4 | 複数ステップの write を非原子で行う（例: GCS に state 保存 → 続けて DB status を更新） | 中間で失敗すると state は残り DB は未更新（逆も）。再実行で二重処理・step skip・pending 取り残しが起きる | 後段 write が失敗しても次回で自己修復できる順序にする（例: DB status 更新を先、state 保存を後 — state なしでも不変条件が保たれる）。難しければ**不変条件を後から再構成するクエリ**を持つ、または前段に compensating action（rollback 書き戻し）を仕込む |
| B-5 | すべての cleanup を同じタイミングで処理する | resume 用 checkpoint を早期削除／一時ファイルが failure 時に残存 | **用途で分離**: ①state / checkpoint（resume 用）は**成功パスでのみ削除**（B-1 と同じ）。②一時 upload / scratch file は**`finally` ブロックで必ず削除**（GCS lifecycle に任せず即片付ける） |

### C. SQL / I/O スケール

| # | アンチパターン | 症状 | 対策 |
|---|---|---|---|
| C-1 | SQL を f-string で組み立てる | 引用符混入で SQL 破壊・injection 脆弱性 | `QueryJobConfig(query_parameters=[...])` で parametrize。`IN` 句は `ArrayQueryParameter` |
| C-2 | 大きな JSON/テキストを `download_as_text` / `upload_from_string` で一括処理 | HTTP buffer で GB 級メモリ膨張 → OOM | `blob.open("r")` ストリーム、BQ は `load_table_from_uri` で GCS 経由。**100MB / 100万行を目安に方式切替** |
| C-3 | `bucket.list_blobs(prefix="root/")` でフラットバケットを全走査 | 100万 blob 規模で15分+API課金発生 | 階層 prefix (`root/{date}/{shard}/`) に絞る。既存フラット配置は BQ に metadata index を作る |
| C-4 | I/O bound 処理を直列ループで回す | 数万件 × 数百ms で数時間の無駄 | `ThreadPoolExecutor(max_workers=20-50)` で並列化。GCS/BQ クライアントは thread-safe |
| C-5 | BQ への大量 insert で `insert_rows_json`（streaming insert）を選ぶ | streaming buffer は **90分間 DML 不可**。直後の DELETE/UPDATE がエラーになり、ETL の冪等化（DELETE→INSERT / MERGE）と相性最悪 | Load Job + `load_table_from_uri`（GCS 経由）を既定。streaming insert は low-latency が必須な場合だけ例外適用し、その時は後続 DML を挟まない設計にする |

### D. 外部ジョブ・リソース管理

| # | アンチパターン | 症状 | 対策 |
|---|---|---|---|
| D-1 | polling 無限ループに timeout がない | 終局状態が来ないと永久ハング（例: Workflows `ACTIVE` のまま15時間 stuck） | `deadline = time.time() + max_wait_sec` で break。未知 state は warn してリトライ回数を制限 |
| D-2 | client（BQ/GCS/API）を関数毎に生成 | connection pool 使い捨て、認証遅延が累積 | module-level `@lru_cache` で singleton 化、または `__init__` で一度だけ構築 |
| D-3 | 部分投入（N batch submit 中 k 個目失敗）の orphan | 既投入分が走り続けて課金発生、結果は main 側で回収不可 | submit 中に失敗したら投入済を `cancel()`。もしくは全 submit を先に済ませて `as_completed` で一括回収 |
| D-4 | BQ DML 結果取得に private API `result._job_ref` を使う | `google-cloud-bigquery` 更新で破綻 | `job = bq.query(sql); job.result(); affected = job.num_dml_affected_rows` |

### E. 設定・状態管理

| # | アンチパターン | 症状 | 対策 |
|---|---|---|---|
| E-1 | model / table / project をコード内にハードコード | dev/prod 切替不可、環境変更のたびに再ビルド | 環境変数 + `src.core.config.Settings` 経由で注入 |
| E-2 | module-level mutable global + 関数内 `global` 書き換え | テスト困難・並列実行で状態破綻 | 関数引数 or dataclass で渡す。config は immutable (`frozen=True` dataclass) |

### F. デプロイ・検証

| # | アンチパターン | 症状 | 対策 |
|---|---|---|---|
| F-1 | deploy 前の静的検証なし（Dockerfile build が通ればよしとする） | `NameError` / `ImportError` / f-string 内 syntax エラー等が本番で初めて顕在化。長時間バッチは失敗検知が遅く、データ損失に直結（例: 2026-04-20 TDnet ai-finalize 7bd8f で `bucket` NameError → 19K doc ロスト） | **最低限**: `python -m py_compile <target>.py` を pre-commit / CI で強制。**推奨**: 1件ダミー入力で主要関数を end-to-end 実行する smoke test を deploy 前に通す |

### 新規バッチジョブ作成時チェックリスト

- [ ] A-1: `main()` 末尾で `sys.exit(1 if errors else 0)`
- [ ] A-2: 副作用関数は失敗時 raise（返り値に errors を混ぜない）
- [ ] A-3/A-4: 例外は必ず log + count、ThreadPool は future 個別ハンドル
- [ ] A-5: 不正入力 fallback で値を捏造せず raise
- [ ] A-6: バッチ単位失敗は行数単位で error count（`errors += len(failed_batch)`）
- [ ] A-7: 終了時 `processed` / `skipped` / `errors` サマリを必ず出力
- [ ] A-8: 上流 exit code だけで gate せず実測データ（row_count / `_SUCCESS` 等）で verify
- [ ] B-1/B-2: cleanup は成功パスのみ、DELETE → INSERT 順序または MERGE
- [ ] B-3: 既存キー取得失敗時は raise
- [ ] B-4: 多段 write は冪等再実行で自己修復できる順序に、不可なら compensating action
- [ ] B-5: cleanup は state (成功パス) / 一時ファイル (`finally`) で分離
- [ ] C-1: SQL は parametrize（f-string 組立禁止）
- [ ] C-2/C-3: 大量 I/O はストリーム / GCS 経由、`list_blobs` は prefix で絞る
- [ ] C-4: I/O bound は並列化
- [ ] C-5: BQ 大量 insert は Load Job + `load_table_from_uri`（streaming insert 選ばない）
- [ ] F-1: deploy 前に `python -m py_compile` + 1件スモークテスト
- [ ] D-1: polling には deadline
- [ ] D-2: GCP client は singleton
- [ ] D-3: 部分失敗時の orphan cancel
- [ ] D-4: BQ DML は public API (`num_dml_affected_rows`)
- [ ] E-1/E-2: 設定は環境変数経由、mutable global 禁止
- [ ] CLAUDE.md§コーディング規約: `print()` 不使用、`structlog` でロギング
- [ ] CLAUDE.md§コーディング規約: GCP認証は `settings.google_application_credentials` 経由
- [ ] CLAUDE.md§コーディング規約: docstring は Google style（Args/Returns/Raises 記載）
- [ ] CLAUDE.md§コーディング規約: 全関数に型ヒント
- [ ] 一時ファイル・バックアップファイルの知見MD記載（下記ルール参照）

---

## 一時ファイル・バックアップファイルのMD記載義務

作業中に以下のファイルを作成した場合、対応する知見MDに所在・用途・削除可否を記載する。記載なしの放置ファイルはゴミになる。

**対象**:
- `C:\tmp\` に置いた一時スクリプト・一時データ
- 旧版バックアップ（`_v1.md`、`_backup.md` 等）
- 中間生成物（JSONL、CSV等の途中成果物）

**記載先**: 対応する知見MD（`docs/knowledges/`）のヘッダ付近またはファイル一覧セクション

**記載内容**（テーブル推奨）:

| 項目 | 必須 |
|------|------|
| ファイルパス（フルパス） | Yes |
| 用途（何のために作ったか） | Yes |
| 削除可否・条件（いつ消してよいか） | Yes |

---

## ファイル同期・コピー・上書き時の **内容種別検証** 必須

**名前の同一性 ≠ 意味の同一性**。ローカル↔GCS や異なるディレクトリ間で同期・コピー・上書きする際は、**内容の意味種別（スキーマ・キー・用途）を検証**してから操作する。

### 反面教師事例（2026-04-20）

`sync_latest_adapters_bg.py` がローカル `meta/monthly/{ticker}_extract_adapter.json`（extract adapter = `fields`/`row_label_regex`）を GCS `monthly/meta/{ticker}/adapter.json`（URL adapter = `ir_page_url`/`type`）と同一視し、`updated_at` 比較だけで **245 件の URL adapter を破壊**。両者ともファイル名が `adapter.json` / `{ticker}.json` で紛らわしかったのに意味種別を検証しなかったのが原因。

### 恒久ルール

1. **両者の意味種別を判定する手段を用意**（判定キー・必須フィールド等）
2. **種別不一致なら即エラー停止**。`updated_at` 比較は種別一致を確認した後で初めて意味を持つ
3. **dry-run で 10 件以上、種別一致を目視確認**してから本実行
4. **ペアリング（どのパスがどの意味か）を docstring に明記**。逆方向同期で対応関係が破綻しないか設計段階で検証

### チェックリスト

- [ ] 両パスの意味種別を事前定義したか
- [ ] sync/copy 前に種別比較で不一致停止するか
- [ ] dry-run で 10 件の種別一致を目視確認したか
- [ ] docstring にペアリング表を書いたか

---

## 破壊的操作

### 定義

「間違えた時にアンドゥコストが高い or 他者影響がある」= 破壊的:
- **上書き・削除**: 既存 blob/ファイルへの書き込み、`blob.delete()`, `rm -rf`, `Path.unlink()` 等
- **DB 破壊系 DML**: `DROP`, `TRUNCATE`, 広範囲 `DELETE`, `ALTER ... DROP COLUMN`
- **Git 破壊系**: `push --force`, `reset --hard`, `branch -D`, `clean -fd`
- **他者に見える変更**: PR/Issue/Slack/email/LINE 送信、Cloud Run/Workflows 実行
- **bucket/quota 系**: `gsutil rsync -d`, jobs delete, secrets 削除
- **副作用のあるスクリプトを `| head` 等のパイプで部分確認しない**: パイプ閉鎖前に副作用は実行される

### 事故事例

### sync_latest_adapters_bg.py 245本破壊（2026-04-20）

`sync_latest_adapters_bg.py` がローカル extract adapter を GCS URL adapter に誤マッピングで上書き → **245 本破壊**。原因: dry-run 未実装・内容種別検証無し・全銘柄一発実行。`monthly_adapter_index.csv` から再構築で復旧。

### monitor_backfill.py パイプ経由二重起動（2026-04-29）

`monitor_backfill.py ... | head -5` で設定パース確認のつもりが、`head` がパイプを閉じる前に Cloud Run Job がサブミットされ、孤立ジョブが稼働。その後の本番実行で2つ目の同一ジョブが並走。原因: `--dry-run` が既に実装済みなのに使わず `| head -5` で代用。→ review 028。

### GCPリソース変更時のMD更新漏れ（2026-05-02）

`tdnet-ai-weekly` Scheduler 作成 + `ai_processing_flow` Workflow 改修を完了したが、知見ファイル（013_tdnet_load.md）の「⚠️ Scheduler 未設定」表記を更新せず。ユーザー指摘まで陳腐化放置。→ review 060。

**ルール**: GCPリソース（Scheduler/Cloud Run Job/Workflows/Functions）を変更・作成・削除したら、変更完了直後に関連知見ファイルの「現況サマリ」「ステータス」「残課題」を自発更新する。判定:「次セッションがこのMDを読んだとき現在のGCP状態と矛盾しないか？」

---

## 改修プラン / バグ修正指示書 MD フォーマット

定義は `skills/planning.md` §改修プラン / バグ修正指示書 MD フォーマット に一元化した。テンプレは `docs/plans/_template_refactor.md` をコピーして使う。本ファイルの汎用アンチパターン集（A-x〜F-x）はプラン内の「対応アンチパターン」表で参照される。

---

## 一時スクリプトの命名（tmp_*.py）

### ルール

一過性の処理（調査・デバッグ・バッチ修正・PoC・1回限りの移行等）は **`tmp_`** プレフィックスで命名する。

```
scripts/tmp_investigate_7345.py      # 個別銘柄調査
scripts/tmp_debug_extract_2305.py    # デバッグ
scripts/tmp_apply_fixes_round3.py    # バッチ修正
scripts/tmp_poc_ocr_compare.py       # PoC実験
scripts/tmp_backfill_2023_efg.py     # 一過性バックフィル
```

### .gitignore で除外

```gitignore
scripts/tmp_*.py
```

一時スクリプトはコミットしない。成果（知見・データ・adapter修正等）は正規の場所に反映し、スクリプト自体は用が済んだら削除する。

### 判定基準

「3ヶ月後に別セッションがこのスクリプトを実行する場面があるか？」

- **No** → `tmp_` をつける
- **Yes** → 通常命名（`investigate_monthly_ng.py` 等）

### 旧命名との対応（参考）

| 旧パターン（もう使わない） | 今後の命名 |
|---------------------------|-----------|
| `*_bg.py`（セッション内BG処理） | `tmp_*_bg.py` または `tmp_*` |
| `investigate_<ticker>.py` | `tmp_investigate_<ticker>.py` |
| `poc_<実験名>.py` | `tmp_poc_<実験名>.py` |
| `debug_*.py` | `tmp_debug_*.py` |
| `apply_batch_fixes_round*.py` | `tmp_apply_fixes_*.py` |
| `*_copy.py` / `*コピー*.py` | `tmp_*` |

---

## テストスクリプト（test_*.py）

### 配置・命名

テストスクリプトは **`scripts/` 配下に `test_` プレフィックス**で配置する。`C:\tmp\` やプロジェクト外への配置は禁止（git 管理外になりバージョン管理・コードレビュー・他端末での再利用が不可能になるため）。

```
scripts/test_conse_quick.py       # コンセンサス取得テスト
scripts/test_yutai_nav.py         # 株主優待ナビゲーションテスト
```

### 命名規則

`test_<機能の短い説明>.py`

- `test_` で始めることで通常のスクリプト（本番バッチ等）と区別する
- テスト対象がわかる名前をつける

### 一時スクリプトとの違い

| 種別 | プレフィックス | git管理 | 用途 |
|------|--------------|---------|------|
| テストスクリプト | `test_` | する | 繰り返し実行する検証・動作確認 |
| 一時スクリプト | `tmp_` | しない（.gitignore除外） | 1回限りの調査・デバッグ・PoC |

判定基準: 「3ヶ月後に別セッションがこのスクリプトを実行する場面があるか？」Yes → `test_`、No → `tmp_`

### 事故事例

review 118: テストスクリプトを `C:\tmp\test_yutai_nav.py` に配置 → ユーザー指摘で `scripts/test_yutai_nav.py` に再作成。原因: 004 を参照せずに Write し、「テスト」→「一時的」→「テンポラリディレクトリ」と意味的に連想した。

---

## scripts/ 共通ライブラリ（lib_*.py）

### 配置・命名

複数スクリプトから共通で使う関数は `scripts/` 直下に `lib_` プレフィックス付きで置く。専用ディレクトリ（`scripts/lib/` 等）は作らない。

```
scripts/
  lib_conse_csv_from_view.py   # コンセンサスCSV出力（VIEW→ローカルCSV）
  update_conse_ifis.py         # ← import して使う
  update_conse_rakuten.py      # ← import して使う
```

### 命名規則

`lib_<機能の短い説明>.py`

- `lib_` で始めることで通常のスクリプト（直接実行するもの）と区別する
- 機能がわかる名前をつける（`lib_utils.py` のような汎用名は避ける）

### 既存一覧

| ファイル | 用途 | 利用元 |
|---------|------|--------|
| `lib_conse_csv_from_view.py` | `V_CONSENSUS_MERGED` VIEW → ローカルCSV出力 | `update_conse_ifis.py`, `update_conse_rakuten.py` |

---

## 日付時刻ルール（JST統一）

> CLAUDE.md から詳細を移動。

- **あらゆる日付時刻はJST（UTC+9）を使用**。ログ・API・BQ・gcloud・MCP・GCS・Colab等、出所を問わず変換する。UTC出力はNG
- **コード上**: `datetime.now()` は禁止。`datetime.now(tz=ZoneInfo('Asia/Tokyo'))` を使う。ファイル名・created_at・ログ等すべてJSTで統一
- **MD記述時**: プランMD・知見MD・レビューMD等に日付時刻を記載する際は、JST現在日付時刻を確認してから記述する。記憶・推定による日付時刻記載は禁止
- **JST取得方法**: Git Bash の `date` / `TZ=Asia/Tokyo date` はWindows環境でUTC値をJSTラベル付きで返す事故がある。`python -c "from datetime import datetime; from zoneinfo import ZoneInfo; print(datetime.now(tz=ZoneInfo('Asia/Tokyo')))"` を使うこと

---

## ディスク管理義務

> CLAUDE.md から詳細を移動。

多件DLバッチ（EDINET XBRL/TDnet PDF/GCS fetch 等）は以下必須:

1. **逐次削除**: 1件処理（DL→parse→BQ insert）毎に `shutil.rmtree()`。全件分溜めない
2. **ディスク監視**: N件毎に `shutil.disk_usage("C:\\tmp").free` チェック → 5GB未満で警告、1GB未満で abort
3. **レジューム**: 起動時に `(key1, key2) IN BQ既存` で処理済スキップ
4. **ピーク = 並列数 × 1ファイル**: 10 worker なら瞬間 ~10 ZIP 分のみ
5. **起動前**: `df C:\\tmp` で 10GB 以上空き確認

**禁止パターン**: 一括DL→最後に削除 / `except: pass` でゴミ残し / `--dry-run` キャッシュ放置

---

## 逐次永続化義務

> CLAUDE.md から詳細を移動。

AIがN件（N>=3）の対象を手動で反復処理する場合（adapter修正・NG調査・手動データ修正・BC突合等）:

1. **1件1永続化**: 1件の調査・判断が完了したら、即座にEdit/Writeでファイルに書き戻す。判断結果をコンテキストメモリに溜めて後でまとめて書くことを禁止（クラッシュで全消失するため）
2. **中間コミット**: 10件処理ごと、または15分経過ごと（いずれか早い方）にgit commitで中間成果を永続化
3. **処理サイクルの事前定義**: 反復処理を開始する前に、1件の処理サイクル（入力→判断→永続化→後始末→次の件）を明示的に定義してから着手する

**禁止パターン**: 全件調査→最後にまとめてEdit / 判断結果をコンテキストメモリにのみ保持 / 処理サイクル未定義のまま作業開始

---

## 既存コード移植ルール

> CLAUDE.md から詳細を移動。§4.2 ユーザー指示優先の具体化。

ユーザーが「そのまま使え」「そのまま移植せよ」「コピペせよ」「一字一句」「独自に考えるべき箇所ゼロ」「元ネタ通り」と指示した場合:

1. 元ネタのコードを Read し、関数単位でそのままコピーする
2. ブラウザ種別・ライブラリ・API・オプション・タイムアウト値を一切変更しない
3. 元ネタにない処理（wait、デバッグ出力、エラーハンドリング等）を追加しない
4. 技術的に「より良い」代替手段があっても採用しない
5. 変更が必要と判断した場合は、実装前にユーザーに確認する

---

## AI直接処理の指示ルール

> CLAUDE.md から詳細を移動。§4.2 ユーザー指示優先の具体化。

ユーザーが以下の語彙・文脈でAI自身による直接処理を指示した場合、Pythonスクリプトを書いてすり替えてはならない:

- **トリガー語彙**: 「1件ずつ」「1件1件」「丁寧に」「手動で」「君/自分が判断して」「君/自分が○○せよ」「君/自分で読んで」「コードを書くな」
- **対象タスク**: 文章成形、構造読み取り、分類判断、レビュー、要約、比較分析など、AIの言語理解力で直接処理可能なタスク
- **禁止行動**: タスクの一部または全部を、たとえ処理パターンが見えても、Pythonスクリプト・シェルスクリプトで代替すること
- **判断基準**: 迷ったら「ユーザーはAIの言語能力を使いたいのか、コードを書かせたいのか」をユーザーに質問する

---

## 認証系コードの特別扱い

> CLAUDE.md から詳細を移動。

証券口座・銀行口座・決済サービス等の認証系コードは、ログイン失敗が口座ロックに直結するため特別扱い:

- 実績のある既存コードがある場合、技術変更（ライブラリ変更・API変更）は禁止
- bot検知対策（User-Agent、AutomationControlled無効化、プロファイル利用等）は元ネタに存在する場合は必ず移植
- テスト実行前にユーザーに確認（1回の失敗でロックの可能性）

---

## GCS非git同期

> CLAUDE.md から移動。

端末間同期対象は以下の3種類のみ。`sync_push.sh` / `sync_pull.sh` で管理。

| 同期対象 | 理由 |
|---------|------|
| `.env` | APIキー類。git管理禁止 |
| `keys/gcp-service-account.json` | GCP認証キー。git管理禁止 |
| `claude-memory/`（GCS上） | Claude Codeメモリ。端末間で共有 |

会話ログ・ツールトレースは `C:\tmp\claude_logs\<session_id>\` にローカル保管。GCS同期対象外。

**同期禁止**: `scripts/`, `src/`, `.venv/`, `.claude/`, `.mcp.json`, `data/cache/`

---

## Webページングは無限ループ + 終了条件で打ち切る

外部サイト（TDNet等）のページ送りで `range(1, N)` の固定上限は禁止。`while True` + 空ページ/404 で自動停止にする。
正パターンは `zaraba_tdnet_poller.py` の `fetch_recent()`。

---

## シェルからのPython実行

PowerShellから直接python.exeを叩く。パスは日本語を含まないジャンクション `C:\gdrive\` を使う。here-stringは `@'...'@`（単一引用符）でPythonコード内にバックスラッシュや日本語を入れない。Bash + `source activate` はWindows venvで動かない。

---

## その他（CLAUDE.mdから委譲）

- **gcloud**: Git Bash を第一選択。クォートを含む複雑なコマンドは PowerShell で事故りやすい
- **グラフ**: JupyterLab ノートブック（.ipynb）で実装・実行する。`matplotlib.use("Agg")`+PNG保存は使わない
- **ローカルDL保存先**: `C:\tmp\`（Google Drive・Dropbox禁止）。検証後は削除する
- **requests.Session**: スレッドセーフでない。`ThreadPoolExecutor` 並列化時は `threading.local()` でスレッド毎にインスタンス分離
- **Gemini応答安定化**: 応答がlist/dict混在等で不安定な場合、コード側のtry/exceptで吸収せず**プロンプトとresponse_schemaを修正**して安定させる
- **外部API**: 必ずtry/exceptで囲む。リトライはtenacityを使用
- **設定値**: ハードコーディング禁止。config/以下のYAMLまたは環境変数で管理
- **新規スクリプト作成時**: Write実行前に本ファイル §新規バッチジョブ作成時チェックリスト および §スクリプト配置・命名 を Read し、全項目を確認してからコーディングに入ること。**配置先は必ず `scripts/` 配下**
- **裁量ツール索引更新義務**: スクリーナー/ザラバ補助/シグナル系スクリプトの追加・知見MD新規作成時は `docs/knowledges/tools/trading_tools_index.md` の該当行を追加または `—` を実パスに更新する
