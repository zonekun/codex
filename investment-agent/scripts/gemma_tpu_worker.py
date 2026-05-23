"""Gemma 4 31B TPU supervisor（本番、ai_processing_flow Workflows Step 2）.

TPU VM の startup-script から呼ばれる。vLLM (http://localhost:8000) は startup-script
が起動済み前提。

入力: gs://{BUCKET}/ai_job/{RUN_ID}/state.json
出力: gs://{BUCKET}/ai_job/{RUN_ID}/gemma_CURRENT.jsonl（continuous append + resume）
完了: GCS に _SUCCESS ファイル作成。Workflows Callback URL があれば POST（環境変数 CALLBACK_URL）

プロンプト: baseline（案A: 配当/特損/特利 PL数値 vs 開示イベント）+ 中計ルール（013-1 本番採用確定）
参照: docs/knowledges/tools/013-1_ai_cost_and_gemma_poc.md
      docs/knowledges/tools/078_gemma4_operation.md

環境変数:
  RUN_ID       — Workflows execution ID（必須）
  BUCKET_NAME  — GCS バケット名（デフォルト: stock_data_1930932）
  CALLBACK_URL — Workflows Callback URL（オプション）
  CONCURRENCY  — 並列数（デフォルト 8）
  TEXT_LIMIT   — prompt に乗せるテキスト最大文字数（デフォルト 20000）
"""
from __future__ import annotations

import asyncio
import json
import os
import re
import subprocess
import sys
import threading
import time
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import httpx

VLLM_URL = "http://localhost:8000/v1/chat/completions"
MODEL = "google/gemma-4-31B-it"

RUN_ID = os.environ.get("RUN_ID")
if not RUN_ID:
    raise SystemExit("RUN_ID env var required")
RESUME_RUN_ID = os.environ.get("RESUME_RUN_ID") or ""

BUCKET = os.environ.get("BUCKET_NAME", "stock_data_1930932")
CALLBACK_URL = os.environ.get("CALLBACK_URL") or ""
CONCURRENCY = int(os.environ.get("CONCURRENCY", "8"))
TEXT_LIMIT = int(os.environ.get("TEXT_LIMIT", "20000"))
PUSH_INTERVAL_SEC = 60
PUSH_TRIGGER_EVERY_N = 100

GCS_STATE_BLOB = f"ai_job/{RUN_ID}/state.json"
GCS_CURRENT_BLOB = f"ai_job/{RUN_ID}/gemma_CURRENT.jsonl"
GCS_PASS2_BLOB = f"ai_job/{RUN_ID}/gemma_pass2_CURRENT.jsonl"
GCS_SUCCESS_BLOB = f"ai_job/{RUN_ID}/_SUCCESS"
LOCAL_OUT = Path("/tmp/gemma_tpu_worker_results.jsonl")
LOCAL_PASS2_OUT = Path("/tmp/gemma_tpu_worker_pass2_results.jsonl")


VALID_CATEGORIES: list[str] = [
    "決算短信", "TOB・MBO", "業績修正", "買収防衛策", "上場廃止", "継続企業疑義(GC)",
    "決算説明資料", "自己株式取得", "役員異動（代表クラス）", "配当", "第三者割当・公募増資",
    "分配金", "合併・組織再編", "子会社化・買収", "主要株主異動", "新株予約権発行",
    "株式売出し", "配当変更（増減配）", "中期経営計画", "株式分割・併合", "特別損益計上",
    "業績予想", "事業計画（グロース）", "立会外分売", "監査人異動", "訴訟・法的",
    "インシデント（災害・事故）", "自己株式消却", "インシデント（セキュリティ）",
    "転換社債(CB)発行", "行政処分", "DES（債権株式化）", "リストラ・希望退職",
    "不祥事・社内調査", "その他（未分類）", "株主優待", "提携・協業", "月次開示",
    "子会社設立", "大型受注・契約", "資産売却（不動産）", "事業・子会社売却",
    "特別利益", "特別損失", "業績の重要な先行指標", "受注高/受注残高",
]
VALID_CATEGORIES_STR = ", ".join(VALID_CATEGORIES)

# ★ 同期義務: tdnet_load_parallel.py の _PASS2_CATEGORIES と一致させること
# （両ファイルは独立スクリプトで import 経路なし。片方修正時は必ず両方更新）
_PASS2_CATEGORIES: set[str] = {"決算短信", "決算説明資料"}


# ---- プロンプト構築 --------------------------------------------------------

def _baseline_prompt(doc_title: str) -> str:
    return f"""以下のTDnet適時開示文書を分析し、2つの判定を行ってください。

【タスク1: 月次開示判定】
タイトル「{doc_title}」は「月次開示」ですか？

月次開示の定義: 毎月1回の頻度で開示される、月単位の売上・業績・受注・顧客数等の定期報告。
該当する例: 「2024年3月度 月次売上高」「12月 月次IRニュース」
該当しない例:
- 四半期決算に伴う開示（「第3四半期の売上状況」「Q3主要KPI」等）→ 開示頻度が四半期なので月次ではない
- 四半期末時点のストック数（「第3四半期末現在の○○数」）→ 四半期スナップショットであり月次ではない
- タイトルに「月」を含んでも四半期単位なら月次ではない

【タスク2: サブカテゴリ抽出】
この文書に含まれる投資判断に関連する情報カテゴリをすべて抽出してください。

★重要ルール:
- 文書のメインテーマだけでなく、具体的な事実・実績・数値・施策・計画・見通しとして記載されている情報カテゴリを抽出すること
- 除外するのは以下のみ: 「株主還元方針として自己株式取得を検討」「成長を目指す」等の**具体性のない方針表明**、および連結決算における「連結子会社○社」等の**単なる事実の列挙**
- 配当金額・合併・提携・組織再編など、具体的な施策や金額が記載されていれば必ず含めること
- 以下のカテゴリ名から選択すること（一言一句違わず出力）:
{VALID_CATEGORIES_STR}

★カテゴリ判定の具体的基準:
- 「受注高/受注残高」: 受注高・受注残高・受注額・オーダーの数値や前年比の記載があれば必ず選択
- 「業績の重要な先行指標」: SaaS解約率・ARPU・新規出店数/退店数・販売数量・出荷台数・稼働率・入居率などの記載があれば選択
- 「配当変更（増減配）」: 以下のいずれかに該当すれば必ず選択:
  - 決算短信の「直近に公表されている配当予想からの修正の有無：有」の記載
  - 「増配」「減配」「配当予想の修正」「記念配当」「特別配当」の文言
- 「業績修正」: 「修正」「上方修正」「下方修正」「前回発表予想からの変更」の文言があれば選択
- 「事業計画（グロース）」: 新規事業への投資計画・事業拡大戦略・TAM/SAM分析があれば選択
- 「中期経営計画」: 中期経営計画・中計の策定・進捗・見直しの記載がある場合のみ選択（下の詳細ルール参照）
- 「子会社化・買収」: 子会社の新規取得・株式取得・買収の具体的記載がある場合のみ選択
- 「自己株式取得」: 自己株式の取得枠設定・取得実施・取得状況の具体的記載がある場合のみ選択
- 「大型受注・契約」: 個別の大型受注・大口契約・大型案件の獲得の具体的記載がある場合のみ選択
- 「資産売却（不動産）」: 固定資産・不動産・土地建物の売却・譲渡の具体的記載があれば選択
- 「株式分割・併合」: 株式分割・株式併合の実施や予定の発表があれば選択
- 「リストラ・希望退職」: 「希望退職」「人員削減」「構造改革費用」「事業撤退」の記載があれば選択
- 「合併・組織再編」: 合併・会社分割・事業統合・組織再編の具体的記載があれば選択
- 「提携・協業」: 業務提携・資本提携・協業・共同開発の具体的記載があれば選択

### 判定ルール（重要：過剰検知抑制）

以下のカテゴリは「開示イベントとしての告知」のみ True とする。
定例決算の数値表に記載があるだけでは False。

■ 配当
  True: 配当予想の修正・増配・減配・復配・無配転落・記念配当・特別配当・配当政策変更を告知
  False: 決算短信の「配当の状況」欄に前期/当期の実績・予想額が記載されているだけ

■ 特別損失
  True: 特別損失の計上を告知（減損損失発生、事業構造改革費用発生、訴訟和解金計上、etc.）
  False: PL上に特別損失が計上されているだけ（定例決算の財務数値の一部）

■ 特別利益
  True: 特別利益の計上を告知（固定資産売却益、投資有価証券売却益、事業譲渡益、etc.）
  False: PL上に特別利益が計上されているだけ

### 判定の原則

タイトルに「〜のお知らせ」「〜について」「〜の計上」「〜の修正」等の告知表現があり、
当該カテゴリに関する独立した開示意図が読み取れる場合に限り True。
決算短信・四半期決算短信で上記イベントが同時告知されている場合は True。
"""


_CHUCHKKEI_BLOCK = """
━━━━━━━━━━━━━━━━━━━━━━━━
■ 中期経営計画（詳細ルール）

このカテゴリは、中期経営計画（通常3〜5年）の**新規策定・改定**の
告知を対象とする。中計の進捗報告・実績報告は対象外。

True:
  - 中期経営計画の新規策定・改定・公表の独立告知
  - 新中計の定量目標（売上高・営業利益・ROE等）が初出の形で提示

False:
  - 既存の中計を単に名称として言及しているだけ
  - 中計の進捗状況・達成度報告・実績報告
  - 「資本コストや株価を意識した経営」「PBR改善計画」の告知は別トピック
"""


def _prompt_tail(doc_title: str, text: str) -> str:
    return f"""
文書タイトル: {doc_title}
テキスト: {text[:TEXT_LIMIT]}

【出力形式】必ず単一のJSONオブジェクトのみを返してください。配列で包まないこと。
{{ "is_monthly": true, "sub_categories": ["カテゴリ1", "カテゴリ2"] }}
- is_monthly: 月次開示なら true、そうでなければ false
- sub_categories: 抽出したカテゴリのリスト（該当なしは空リスト []）"""


def build_prompt(doc_title: str, text: str) -> str:
    """本番プロンプト: baseline + 中計ルール."""
    return _baseline_prompt(doc_title) + _CHUCHKKEI_BLOCK + _prompt_tail(doc_title, text)


def build_prompt_pass2(doc_title: str, text: str) -> str:
    """Pass 2 プロンプト: _build_merged_prompt() の Gemma 移植（受注高/受注残高 判定専用）.

    対象: _PASS2_CATEGORIES（決算短信 + 決算説明資料）のみ。
    TEXT_LIMIT は build_prompt() と共通。
    移植元: tdnet_load_parallel.py の _build_merged_prompt()（一言一句移植）。
    """
    truncated = text[:TEXT_LIMIT]
    return f"""以下のTDnet適時開示文書を分析し、2つの判定を行ってください。

【タスク1: 月次開示判定】
タイトル「{doc_title}」は「月次開示」（月次売上・月次業績・月次受注、月次顧客数等の、企業業績に影響ある定期的な月次報告）ですか？

【タスク2: サブカテゴリ抽出】
投資判断に影響を与える【他カテゴリの重要情報】が内包されているか抽出してください。

以下のカテゴリ名からのみ選択すること（一言一句違わず出力）:
{VALID_CATEGORIES_STR}

※カテゴリ選択における特記事項:
- 「業績の重要な先行指標」: SaaSの解約率やARPU、小売の新規出店数/退店数、販売数量・出荷台数、不動産の客室稼働率・オフィス入居率など
- 「受注高/受注残高」: 上記の先行指標の一部だが、極めて重要な情報のため独立カテゴリとして選択

文書タイトル: {doc_title}
テキスト: {truncated}

【出力形式】必ず単一のJSONオブジェクトのみを返してください。配列で包まないこと。
{{ "is_monthly": true, "sub_categories": ["カテゴリ1", "カテゴリ2"] }}
- is_monthly: 月次開示なら true、そうでなければ false
- sub_categories: 抽出したカテゴリのリスト（該当なしは空リスト []）"""


def parse_response(content: str) -> dict | None:
    try:
        return json.loads(content)
    except json.JSONDecodeError:
        pass
    m = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", content, re.DOTALL)
    if m:
        try:
            return json.loads(m.group(1))
        except json.JSONDecodeError:
            pass
    start = content.find("{")
    end = content.rfind("}")
    if start >= 0 and end > start:
        try:
            return json.loads(content[start:end + 1])
        except json.JSONDecodeError:
            pass
    return None


# ---- GCS helpers ----------------------------------------------------------

def _gcs_cp(src: str, dst: str) -> tuple[int, str]:
    rc = subprocess.run(
        ["gcloud", "storage", "cp", src, dst],
        capture_output=True, text=True,
    )
    return rc.returncode, (rc.stderr[:300] if rc.stderr else "")


def gcs_download_state(dest: Path) -> dict:
    uri = f"gs://{BUCKET}/{GCS_STATE_BLOB}"
    rc, err = _gcs_cp(uri, str(dest))
    if rc != 0:
        raise SystemExit(f"state.json download failed: {err}")
    return json.loads(dest.read_text(encoding="utf-8"))


def gcs_download_current(dest: Path) -> int:
    uri = f"gs://{BUCKET}/{GCS_CURRENT_BLOB}"
    rc = subprocess.run(
        ["gcloud", "storage", "cp", uri, str(dest)],
        capture_output=True, text=True,
    )
    if rc.returncode == 0 and dest.exists():
        return sum(1 for _ in dest.open("r", encoding="utf-8"))
    dest.write_text("", encoding="utf-8")
    return 0


def gcs_push_current(src: Path) -> tuple[int, str]:
    return _gcs_cp(str(src), f"gs://{BUCKET}/{GCS_CURRENT_BLOB}")


def gcs_push_pass2(src: Path) -> tuple[int, str]:
    return _gcs_cp(str(src), f"gs://{BUCKET}/{GCS_PASS2_BLOB}")


def gcs_download_pass2_current(dest: Path) -> int:
    """Pass 2 gemma_pass2_CURRENT.jsonl を取得 → 処理済行数を返す."""
    uri = f"gs://{BUCKET}/{GCS_PASS2_BLOB}"
    rc = subprocess.run(
        ["gcloud", "storage", "cp", uri, str(dest)],
        capture_output=True, text=True,
    )
    if rc.returncode == 0 and dest.exists():
        return sum(1 for _ in dest.open("r", encoding="utf-8"))
    dest.write_text("", encoding="utf-8")
    return 0


def gcs_write_success() -> tuple[int, str]:
    success_local = Path("/tmp/_SUCCESS")
    success_local.write_text(
        datetime.now(tz=ZoneInfo("Asia/Tokyo")).isoformat(),
        encoding="utf-8",
    )
    return _gcs_cp(str(success_local), f"gs://{BUCKET}/{GCS_SUCCESS_BLOB}")


class GcsPusher(threading.Thread):
    """ローカルファイルを定期的に GCS にプッシュ.

    push_fn: (src: Path) -> (int, str) の関数。デフォルトは gcs_push_current。
    Pass 2 用には gcs_push_pass2 を渡す。
    """

    def __init__(self, src: Path, push_fn=None):
        super().__init__(daemon=True)
        self.src = src
        self._push_fn = push_fn if push_fn is not None else gcs_push_current
        self._stop_evt = threading.Event()
        self._trigger = threading.Event()
        self.lock = threading.Lock()
        self.last_pushed_lines = 0
        self.pushes_ok = 0
        self.pushes_err = 0

    def trigger(self):
        self._trigger.set()

    def request_stop(self):
        self._stop_evt.set()

    def run(self):
        while not self._stop_evt.is_set():
            self._trigger.wait(timeout=PUSH_INTERVAL_SEC)
            self._trigger.clear()
            if self._stop_evt.is_set():
                break
            self._push_once()
        self._push_once()

    def _push_once(self):
        if not self.src.exists():
            return
        try:
            with self.lock:
                current_lines = sum(1 for _ in self.src.open("r", encoding="utf-8"))
            if current_lines == self.last_pushed_lines:
                return
            rc, err = self._push_fn(self.src)
            if rc == 0:
                self.pushes_ok += 1
                self.last_pushed_lines = current_lines
                print(f"[gcs-push] lines={current_lines} ok={self.pushes_ok}", flush=True)
            else:
                self.pushes_err += 1
                print(f"[gcs-push][ERR] rc={rc} err={err}", flush=True)
        except Exception as e:  # noqa: BLE001
            self.pushes_err += 1
            print(f"[gcs-push][EXC] {e!r}", flush=True)


# ---- Worker ----------------------------------------------------------------

async def call_one(
    client: httpx.AsyncClient, sem: asyncio.Semaphore, row: dict,
    prompt_fn=None,
) -> dict:
    """vLLM に 1 doc を送信して結果を返す.

    prompt_fn: (doc_title: str, text: str) -> str。
      Pass 1 は build_prompt（デフォルト）、Pass 2 は build_prompt_pass2 を渡す。
    """
    async with sem:
        text = (row.get("text") or "")[:TEXT_LIMIT]
        _prompt_fn = prompt_fn if prompt_fn is not None else build_prompt
        prompt = _prompt_fn(row.get("doc_title", ""), text)
        t0 = time.perf_counter()
        base_result = {
            "doc_id": row["doc_id"],
            "ticker": row.get("ticker"),
            "doc_title": row.get("doc_title"),
        }
        try:
            resp = await client.post(
                VLLM_URL,
                json={
                    "model": MODEL,
                    "messages": [{"role": "user", "content": prompt}],
                    "max_tokens": 400,
                    "temperature": 0.0,
                },
                timeout=600,
            )
            elapsed = time.perf_counter() - t0
            if resp.status_code != 200:
                return {**base_result, "sub_categories": [],
                        "is_monthly": None, "error": f"HTTP {resp.status_code}: {resp.text[:300]}",
                        "elapsed_sec": elapsed}
            data = resp.json()
            if "choices" not in data:
                return {**base_result, "sub_categories": [],
                        "is_monthly": None, "error": f"no-choices: {json.dumps(data)[:300]}",
                        "elapsed_sec": elapsed}
            content = data["choices"][0]["message"]["content"]
            parsed = parse_response(content)
            is_monthly = bool(parsed.get("is_monthly")) if parsed else None
            sub_categories_raw = parsed.get("sub_categories", []) if parsed else []
            if not isinstance(sub_categories_raw, list):
                sub_categories_raw = []
            sub_categories = [c for c in sub_categories_raw if c in VALID_CATEGORIES]
            # MAIN 決定は ai-finalize 側（_apply_gemma_results）で行う。
            # 旧アーキ準拠で pre_main_category（ファイル名由来） +
            # _AMBIGUOUS_OVERWRITE ルールで決定する。
            # worker は is_monthly / sub_categories のみ返せばよい。
            return {
                **base_result,
                "sub_categories": sub_categories,
                "is_monthly": is_monthly,
                "elapsed_sec": elapsed,
            }
        except Exception as e:  # noqa: BLE001
            elapsed = time.perf_counter() - t0
            return {**base_result, "sub_categories": [],
                    "is_monthly": None, "error": repr(e), "elapsed_sec": elapsed}


def _get_metadata_access_token() -> str | None:
    """TPU VM の metadata server から OAuth2 access token を取得."""
    try:
        r = httpx.get(
            "http://metadata.google.internal/computeMetadata/v1/instance/service-accounts/default/token",
            headers={"Metadata-Flavor": "Google"},
            timeout=10,
        )
        if r.status_code == 200:
            return r.json().get("access_token")
    except Exception:  # noqa: BLE001
        pass
    return None


def _get_metadata_id_token(audience: str) -> str | None:
    """TPU VM の metadata server から OIDC ID token を取得（audience指定）."""
    try:
        r = httpx.get(
            "http://metadata.google.internal/computeMetadata/v1/instance/service-accounts/default/identity",
            params={"audience": audience, "format": "full"},
            headers={"Metadata-Flavor": "Google"},
            timeout=10,
        )
        if r.status_code == 200:
            return r.text.strip()
    except Exception:  # noqa: BLE001
        pass
    return None


def notify_callback(status: str, summary: dict) -> None:
    """Workflows Callback URL に POST で完了通知。

    順番に試す:
      1) OAuth2 access token（同プロジェクト workflows.invoker 必要）
      2) OIDC ID token（audience=Callback URL）
    """
    if not CALLBACK_URL:
        return
    body = {"run_id": RUN_ID, "status": status, **summary}

    # try 1: access token
    access_token = _get_metadata_access_token()
    if access_token:
        try:
            r = httpx.post(
                CALLBACK_URL, json=body,
                headers={"Authorization": f"Bearer {access_token}"},
                timeout=30,
            )
            print(f"[callback] access_token http={r.status_code}", flush=True)
            if r.status_code < 300:
                return
        except Exception as e:  # noqa: BLE001
            print(f"[callback][access_token][ERR] {e!r}", flush=True)

    # try 2: OIDC ID token
    id_token = _get_metadata_id_token(CALLBACK_URL)
    if id_token:
        try:
            r = httpx.post(
                CALLBACK_URL, json=body,
                headers={"Authorization": f"Bearer {id_token}"},
                timeout=30,
            )
            print(f"[callback] id_token http={r.status_code}", flush=True)
            if r.status_code < 300:
                return
        except Exception as e:  # noqa: BLE001
            print(f"[callback][id_token][ERR] {e!r}", flush=True)

    print(f"[callback] FAIL — both access/id token failed status={status}", flush=True)


async def amain() -> None:
    # vLLM ヘルスチェック
    print("[healthcheck] checking vLLM at http://localhost:8000/v1/models ...", flush=True)
    try:
        hc = httpx.get("http://localhost:8000/v1/models", timeout=10)
        if hc.status_code != 200:
            raise RuntimeError(f"vLLM health check failed: HTTP {hc.status_code}")
        print(f"[healthcheck] vLLM ready", flush=True)
    except Exception as e:  # noqa: BLE001
        print(f"[healthcheck][FATAL] {e!r}", flush=True)
        notify_callback("failed", {"reason": "vllm-healthcheck", "error": repr(e)})
        raise

    # 1. state.json 取得
    state_local = Path("/tmp/gemma_tpu_state.json")
    state = gcs_download_state(state_local)
    docs_all = state.get("docs", [])
    print(f"[state] run_id={state.get('run_id')} docs={len(docs_all)}", flush=True)

    # ---- Pass 1 resume -------------------------------------------------------

    # 2a. Cross-execution resume: 旧 execution の Pass 1 checkpoint を引き継ぐ
    if RESUME_RUN_ID:
        resume_blob = f"ai_job/{RESUME_RUN_ID}/gemma_CURRENT.jsonl"
        resume_uri = f"gs://{BUCKET}/{resume_blob}"
        rc, _ = _gcs_cp(resume_uri, str(LOCAL_OUT))
        if rc == 0:
            print(f"[resume] copied pass1 checkpoint from {RESUME_RUN_ID}", flush=True)
            gcs_push_current(LOCAL_OUT)
        else:
            print(f"[resume] no pass1 checkpoint found for {RESUME_RUN_ID}, starting fresh", flush=True)

    # 2b. 既存 gemma_CURRENT.jsonl を取得 → 処理済 doc_id 集合化
    resume_lines = gcs_download_current(LOCAL_OUT)
    done_ids: set[str] = set()
    if resume_lines > 0:
        for line in LOCAL_OUT.open("r", encoding="utf-8"):
            try:
                rec = json.loads(line)
                did = rec.get("doc_id")
                if did:
                    done_ids.add(did)
            except json.JSONDecodeError:
                continue
    print(f"[resume] pass1 existing lines={resume_lines} done_ids={len(done_ids)}", flush=True)

    # 2c. 整合性チェック: resume_run_id の doc_id と state.json の重複率を検証
    if RESUME_RUN_ID and done_ids:
        state_doc_ids = {d.get("doc_id") for d in docs_all if d.get("doc_id")}
        overlap = done_ids & state_doc_ids
        overlap_ratio = len(overlap) / len(done_ids) if done_ids else 1.0
        if overlap_ratio < 0.5:
            print(
                f"[resume][WARNING] low overlap: {len(overlap)}/{len(done_ids)} "
                f"({overlap_ratio:.1%}) — resume_run_id may be wrong",
                flush=True,
            )

    # ---- Pass 2 resume -------------------------------------------------------

    # 3a. Cross-execution resume: Pass 2 checkpoint を引き継ぐ（Pass 1 と独立）
    if RESUME_RUN_ID:
        resume_pass2_blob = f"ai_job/{RESUME_RUN_ID}/gemma_pass2_CURRENT.jsonl"
        rc, _ = _gcs_cp(f"gs://{BUCKET}/{resume_pass2_blob}", str(LOCAL_PASS2_OUT))
        if rc == 0:
            print(f"[resume] copied pass2 checkpoint from {RESUME_RUN_ID}", flush=True)
            gcs_push_pass2(LOCAL_PASS2_OUT)
        else:
            print(f"[resume] no pass2 checkpoint found for {RESUME_RUN_ID}, starting fresh", flush=True)

    # 3b. 既存 gemma_pass2_CURRENT.jsonl を取得 → 処理済 doc_id 集合化
    resume_pass2_lines = gcs_download_pass2_current(LOCAL_PASS2_OUT)
    pass2_done_ids: set[str] = set()
    if resume_pass2_lines > 0:
        for line in LOCAL_PASS2_OUT.open("r", encoding="utf-8"):
            try:
                rec = json.loads(line)
                did = rec.get("doc_id")
                if did:
                    pass2_done_ids.add(did)
            except json.JSONDecodeError:
                continue
    print(f"[resume] pass2 existing lines={resume_pass2_lines} done_ids={len(pass2_done_ids)}", flush=True)

    # ---- 未処理 doc 抽出 -------------------------------------------------------

    # 4. Pass 1 未処理 doc
    pending_docs = [d for d in docs_all if d.get("doc_id") and d["doc_id"] not in done_ids]
    print(f"[pending] pass1: {len(pending_docs)} / {len(docs_all)}", flush=True)

    # Pass 2 対象 doc: _PASS2_CATEGORIES に属するもの（pre_main_category で判定）
    pass2_all_docs = [
        d for d in docs_all
        if d.get("doc_id") and d.get("pre_main_category") in _PASS2_CATEGORIES
    ]
    pass2_pending_docs = [d for d in pass2_all_docs if d["doc_id"] not in pass2_done_ids]
    print(
        f"[pending] pass2: {len(pass2_pending_docs)} / {len(pass2_all_docs)} "
        f"(eligible: {len(pass2_all_docs)})",
        flush=True,
    )

    # Pass 1 + Pass 2 ともに完了済みなら即終了
    if not pending_docs and not pass2_pending_docs:
        print("[done] all docs already processed (pass1 + pass2)", flush=True)
        _, err = gcs_write_success()
        notify_callback("succeeded", {"processed": 0, "pass2_processed": 0, "total": len(docs_all)})
        return

    sem = asyncio.Semaphore(CONCURRENCY)
    pass1_processed = 0
    pass2_processed = 0

    async with httpx.AsyncClient() as client:

        # ---- Pass 1 推論（全件） -----------------------------------------------
        if pending_docs:
            pusher = GcsPusher(LOCAL_OUT)
            pusher.start()

            tasks = [call_one(client, sem, d) for d in pending_docs]
            with LOCAL_OUT.open("a", encoding="utf-8") as fout:
                for coro in asyncio.as_completed(tasks):
                    res = await coro
                    with pusher.lock:
                        fout.write(json.dumps(res, ensure_ascii=False) + "\n")
                        fout.flush()
                    pass1_processed += 1
                    if pass1_processed % PUSH_TRIGGER_EVERY_N == 0:
                        pusher.trigger()

            pusher.request_stop()
            pusher.join(timeout=30)
            print(
                f"[push] pass1 final: ok={pusher.pushes_ok} err={pusher.pushes_err}",
                flush=True,
            )
        else:
            print("[pass1] skip: all docs already processed", flush=True)

        # ---- Pass 2 推論（_PASS2_CATEGORIES のみ） -----------------------------
        if pass2_pending_docs:
            pusher2 = GcsPusher(LOCAL_PASS2_OUT, push_fn=gcs_push_pass2)
            pusher2.start()

            tasks2 = [
                call_one(client, sem, d, prompt_fn=build_prompt_pass2)
                for d in pass2_pending_docs
            ]
            with LOCAL_PASS2_OUT.open("a", encoding="utf-8") as fout2:
                for coro in asyncio.as_completed(tasks2):
                    res = await coro
                    with pusher2.lock:
                        fout2.write(json.dumps(res, ensure_ascii=False) + "\n")
                        fout2.flush()
                    pass2_processed += 1
                    if pass2_processed % PUSH_TRIGGER_EVERY_N == 0:
                        pusher2.trigger()

            pusher2.request_stop()
            pusher2.join(timeout=30)
            print(
                f"[push] pass2 final: ok={pusher2.pushes_ok} err={pusher2.pushes_err}",
                flush=True,
            )
        else:
            print("[pass2] skip: all pass2 docs already processed", flush=True)

    # 5. 完了: _SUCCESS ファイル + Workflows Callback（Pass 2 完了後に送信）
    rc, err = gcs_write_success()
    if rc != 0:
        print(f"[success][ERR] rc={rc} err={err}", flush=True)

    summary = {
        "processed": pass1_processed,
        "pass2_processed": pass2_processed,
        "total": len(docs_all),
        "resumed": resume_lines,
        "pass2_resumed": resume_pass2_lines,
    }
    notify_callback("succeeded", summary)
    print(f"[done] {json.dumps(summary, ensure_ascii=False)}", flush=True)


def main() -> None:
    try:
        asyncio.run(amain())
    except SystemExit:
        raise
    except Exception as e:  # noqa: BLE001
        print(f"[FATAL] {e!r}", flush=True)
        notify_callback("failed", {"error": repr(e)})
        sys.exit(1)


if __name__ == "__main__":
    main()
