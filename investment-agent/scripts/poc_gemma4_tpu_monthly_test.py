"""Gemma 4 31B (TPU v6e-4 via vLLM) の月次TDnet分類PoC (preemption/クラッシュ耐性強化版).

入力: TDnet 2024年1月分 Phase 3対象ドキュメント JSONL（事前にBQから抽出）
出力:
  - ローカル: /tmp/gemma4_tpu_monthly_results.jsonl (1件ごとappend+flush)
  - GCS: gs://stock_data_1930932/tdnet/poc/gemma4_tpu_monthly_202401_CURRENT.jsonl
         (60秒 or 100件ごとに非同期で push。完了時 _SUCCESS_<RUN_ID>.jsonl にリネーム)

TPU VM上で実行。vLLM OpenAI互換API (localhost:8000) に投げる。
resume: 起動時に GCS の CURRENT.jsonl を取得 → done_ids 集合化 → 未処理だけ実行

プロンプトは scripts/poc_gemma4_comparison.py の build_prompt_gemma を移植 (既存踏襲)。
"""
from __future__ import annotations

import argparse
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
CONCURRENCY = 8
TEXT_LIMIT = 20000

GCS_BUCKET = "stock_data_1930932"

# --- Prompt version selection --------------------------------------------
# baseline: 現v2（配当/特別損失/特別利益の案A PL数値ルール入り）
# v2     : baseline + 受注高/受注残高ブロック
# v3     : v2 + 中期経営計画/業績予想/業績の重要な先行指標/業績修正ブロック
VALID_PROMPT_VERSIONS = ("baseline", "v2", "v3")


def _resolve_prompt_version() -> str:
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("input_jsonl", nargs="?", default=None)
    parser.add_argument("local_out", nargs="?", default=None)
    parser.add_argument(
        "--prompt-version",
        choices=VALID_PROMPT_VERSIONS,
        default=None,
    )
    args, _unknown = parser.parse_known_args()
    if args.prompt_version:
        return args.prompt_version
    env_v = os.environ.get("PROMPT_VERSION")
    if env_v:
        if env_v not in VALID_PROMPT_VERSIONS:
            raise SystemExit(
                f"PROMPT_VERSION must be one of {VALID_PROMPT_VERSIONS}, got {env_v!r}"
            )
        return env_v
    return "baseline"


PROMPT_VERSION = _resolve_prompt_version()

# GCS blob 名に prompt version を埋め込んで衝突回避
# RUN_SUFFIX 環境変数で Phase D 等の別実験を分離可能
RUN_SUFFIX = os.environ.get("RUN_SUFFIX", "202401")
GCS_CURRENT_BLOB = (
    f"tdnet/poc/gemma4_tpu_monthly_{PROMPT_VERSION}_{RUN_SUFFIX}_CURRENT.jsonl"
)
RUN_ID = datetime.now(tz=ZoneInfo("Asia/Tokyo")).strftime("%Y%m%d_%H%M%S")
GCS_SUCCESS_BLOB = (
    f"tdnet/poc/gemma4_tpu_monthly_{PROMPT_VERSION}_{RUN_SUFFIX}_SUCCESS_{RUN_ID}.jsonl"
)


# ---- Truncation helper (inline fallback for TPU VM) ----------------------
try:
    _ROOT_P = Path(__file__).resolve().parent.parent
    if str(_ROOT_P) not in sys.path:
        sys.path.insert(0, str(_ROOT_P))
    from src.llm.truncation import truncate_for_model as _truncate_impl  # type: ignore
except Exception:  # noqa: BLE001
    # Fallback: TPU VM 上で src/ が無い場合
    _NO_TRUNCATE_CATEGORIES = {"決算短信"}

    def _truncate_impl(text: str, model: str, doc_category: str | None = None) -> str:  # type: ignore
        if text is None:
            return ""
        if doc_category is not None and doc_category in _NO_TRUNCATE_CATEGORIES:
            return text
        return text[:TEXT_LIMIT]


def _truncate(text: str, doc_category: str | None) -> str:
    """gemma-4-31b 用 truncate（決算短信のみ切り詰めなし）."""
    try:
        return _truncate_impl(text or "", "gemma-4-31b", doc_category)
    except Exception:  # noqa: BLE001
        # 未登録モデル等の不測エラー → 末尾切り詰め
        if text is None:
            return ""
        if doc_category == "決算短信":
            return text
        return text[:TEXT_LIMIT]

def _resolve_io_paths() -> tuple[Path, Path]:
    """位置引数 (input_jsonl, local_out) を許容しつつ、--prompt-version は除去して解釈."""
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("input_jsonl", nargs="?", default=None)
    parser.add_argument("local_out", nargs="?", default=None)
    parser.add_argument("--prompt-version", default=None)
    args, _unknown = parser.parse_known_args()
    input_path = Path(args.input_jsonl) if args.input_jsonl else Path("/tmp/gemma4_monthly_input.jsonl")
    if args.local_out:
        out_path = Path(args.local_out)
    else:
        out_path = Path(f"/tmp/gemma4_tpu_monthly_{PROMPT_VERSION}_results.jsonl")
    return input_path, out_path


INPUT_JSONL, LOCAL_OUT = _resolve_io_paths()

PUSH_INTERVAL_SEC = 60
PUSH_INTERVAL_COUNT = 100

VALID_CATEGORIES = [
    "決算短信", "決算説明資料", "業績修正", "業績予想", "月次開示",
    "配当", "配当変更（増減配）", "株主優待", "自己株式取得", "自己株式消却",
    "株式分割・併合", "第三者割当・公募増資", "新株予約権発行", "株式売出し",
    "TOB・MBO", "子会社化・買収", "資産売却（不動産）",
    "合併・組織再編", "会社分割", "上場廃止", "主要株主異動",
    "大型受注・契約", "提携・協業", "事業計画（グロース）", "中期経営計画",
    "リストラ・希望退職", "特別利益", "特別損失",
    "役員異動（代表クラス）", "監査人異動",
    "インシデント（セキュリティ）", "訴訟・法的手続き",
    "受注高/受注残高", "業績の重要な先行指標",
]
VALID_CATEGORIES_STR = "\n".join(f"- {c}" for c in VALID_CATEGORIES)


_V2_ORDER_BLOCK = """
━━━━━━━━━━━━━━━━━━━━━━━━
■ 受注高/受注残高

このカテゴリは、受注高や受注残高が主要な業績先行指標となる業態で、
定量的な開示があった場合に True とする。

True:
  - 受注高または受注残高の具体的な数値（金額・件数）が決算短信・
    決算説明資料・その他開示資料に記載されている
  - 全社合算の単一数値でも、内訳あり（セグメント別・製品群別・
    地域別・事業別 等）でもよい（内訳の有無や粒度は問わない）
  - 対象となりやすい業態: 製造業・建設・プラント/エンジニアリング・
    造船・受託開発・ソフトウェア受託・その他の受託サービス業

False:
  - 「受注」「受注残」の語は出てくるが、定量的な数値が一切伴わない
    （例:「受注は順調に推移」「受注動向は底堅い」「受注は回復傾向」
    「受注は低調に推移」等の定性的言及のみ。好調/不調問わず数値が
    ないものは False）
  - 業態上「受注」概念が主要業績指標でない場合:
    不動産賃貸販売・EC/小売・SaaS（ARR中心）・人材派遣/BPO・
    金融/保険代理店・電力ガス 等

振り分け:
  - 個別の大型案件の受注告知 → `大型受注・契約`
"""

_V3_EXTRA_BLOCK = """
━━━━━━━━━━━━━━━━━━━━━━━━
■ 中期経営計画

このカテゴリは、中期経営計画（通常3〜5年）の**新規策定・改定**の
告知を対象とする。中計の進捗報告・実績報告は対象外（それを含めると
決算説明資料で中計進捗に触れているものを広く拾ってしまう）。

True:
  - 中期経営計画の新規策定・改定・公表の独立告知
  - 新中計の定量目標（売上高・営業利益・ROE等）が**初出の形**で
    提示されているもの
False:
  - 既存の中計を単に名称として言及しているだけ
  - 中計の進捗状況・達成度報告・実績報告
  - 「資本コストや株価を意識した経営」「PBR改善計画」の告知は
    別トピック（中計ではない）

━━━━━━━━━━━━━━━━━━━━━━━━
■ 業績予想

このカテゴリは、業績予想の新規開示（通常は**次期会計年度**の通期
予想）を対象とする。既に公表された予想の修正は `業績修正` へ振り分け。

True:
  - 業績予想の新規開示告知（「〇年〇月期 業績予想に関するお知らせ」等）
  - 決算短信内の「次期通期予想」セクションに具体的な数値が記載
    されている場合（通常の決算短信では True）
False:
  - 新株予約権発行・災害影響等の告知の中で、副次的に予想数値に
    言及されているだけ
振り分け:
  - 業績予想の上方/下方修正 → `業績修正`
  - 「予実差異」「業績フォーキャスト更新」のうち、新規予想の提示
    に該当するものは本カテゴリ、修正明示があれば `業績修正`

━━━━━━━━━━━━━━━━━━━━━━━━
■ 業績の重要な先行指標

このカテゴリは、売上/利益に先行する KPI（受注残高・既存店売上・
店舗数・稼働率 等）の定量的な継続推移を対象とする。

True:
  - 受注残高・既存店売上高・店舗数・稼働率 等のKPIが具体的な数値で
    **2箇所以上**記載されている
  - 月次・四半期などで継続的な推移として提示されている
False:
  - 発電所稼働・人事異動・新株予約権発行 等の単発イベント告知
  - 通常の売上・利益の記載のみで、先行指標としてのKPIがない場合
振り分け:
  - 受注高/受注残高 が主題の場合は `受注高/受注残高` を優先付与、
    先行指標は併記してよい（両カテゴリが成立し得る）

━━━━━━━━━━━━━━━━━━━━━━━━
■ 業績修正

このカテゴリは、既に公表された業績予想からの修正告知を対象とする。

True:
  - 決算短信の「直近に公表されている業績予想からの修正の有無：有」
    のフラグが立っている
  - 「業績予想の修正に関するお知らせ」の独立告知
  - 上方修正・下方修正の明示的な告知
False:
  - 決算短信で過去予想との差分を本文で単に説明しているだけで、
    明示的な「修正」宣言がない場合
"""


def _baseline_prompt(doc_title: str, text: str) -> str:
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
- 「受注高/受注残高」: 受注高・受注残高・受注額・オーダーの数値や前年比の記載があれば必ず選択。「業績の重要な先行指標」とは別に独立して選択すること（両方該当すれば両方出力）
- 「業績の重要な先行指標」: SaaS解約率・ARPU・新規出店数/退店数・販売数量・出荷台数・稼働率・入居率など、将来業績を予測するKPIの記載があれば選択
- 「配当変更（増減配）」: 以下のいずれかに該当すれば必ず選択:
  - 決算短信の「直近に公表されている配当予想からの修正の有無：有」の記載
  - 「増配」「減配」「配当予想の修正」「記念配当」「特別配当」の文言
  - 前期比・前回予想比で配当金額が変更されている記載
  「配当」とは別に独立して選択すること
- 「業績修正」: 以下のいずれかの文言・文脈があれば必ず選択:
  キーワード: 「修正」「上方修正」「下方修正」「前回発表予想からの変更」「予想との差異」「業績予想の修正」「修正に関するお知らせ」
  上方修正の文脈: 「当初予想を上回る」「通期予想を増額」
  下方修正の文脈: 「下回る見込み」「慎重な見通し」「減額」
  「業績予想」とは別カテゴリであり、両方同時に該当することが多い
- 「事業計画（グロース）」: 新規事業への投資計画・事業拡大戦略・TAM/SAM分析・成長投資の具体的計画があれば選択。「中期経営計画」とは別カテゴリであり、両方同時に該当することが多い
- 「中期経営計画」: 中期経営計画・中計の策定・進捗・見直しの記載がある場合のみ選択。決算短信の「今後の見通し」セクションの一般記述や「成長を目指す」等の抽象的方針表明は該当しない
- 「子会社化・買収」: 子会社の新規取得・株式取得・買収の具体的記載がある場合のみ選択。「連結子会社○社」「関係会社○社」等の社数記載やセグメント説明中の既存子会社名列挙、連結範囲注記の定型文は該当しない
- 「自己株式取得」: 自己株式の取得枠設定・取得実施・取得状況の具体的記載がある場合のみ選択。「自己株式取得を検討」等の一般的方針記述だけでは該当しない
- 「大型受注・契約」: 個別の大型受注・大口契約・大型案件の獲得の具体的記載がある場合のみ選択
- 「資産売却（不動産）」: 固定資産・不動産・土地建物の売却・譲渡の具体的記載があれば選択
- 「株式分割・併合」: 株式分割・株式併合の実施や予定の発表があれば選択（過去の分割を参考情報として言及しているだけは除く）
- 「リストラ・希望退職」: 「希望退職」「人員削減」「構造改革費用」「事業撤退」の記載があれば選択
- 「合併・組織再編」: 合併・会社分割・事業統合・組織再編の具体的記載があれば選択
- 「提携・協業」: 業務提携・資本提携・協業・共同開発の具体的記載があれば選択

### 判定ルール（重要：過剰検知抑制）

以下のカテゴリは「開示イベントとしての告知」のみ True とする。
定例決算の数値表に記載があるだけでは False。

■ 配当
  True: 配当予想の修正・増配・減配・復配・無配転落・記念配当・特別配当・配当政策変更を告知
  False: 決算短信の「配当の状況」欄に前期/当期の実績・予想額が記載されているだけ
         （1Q/2Q/3Q/4Q決算短信の配当欄は原則 False、タイトル/冒頭で「配当修正」等の
          告知があれば True）

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


def _prompt_tail(doc_title: str, text: str) -> str:
    return f"""
文書タイトル: {doc_title}
テキスト: {text[:TEXT_LIMIT]}

【出力形式】必ず単一のJSONオブジェクトのみを返してください。配列で包まないこと。
{{ "is_monthly": true, "sub_categories": ["カテゴリ1", "カテゴリ2"] }}
- is_monthly: 月次開示なら true、そうでなければ false
- sub_categories: 抽出したカテゴリのリスト（該当なしは空リスト []）"""


def build_prompt(doc_title: str, text: str, prompt_version: str = PROMPT_VERSION) -> str:
    """Prompt dispatcher: baseline / v2 / v3."""
    base = _baseline_prompt(doc_title, text)
    extras = ""
    if prompt_version == "v2":
        extras = _V2_ORDER_BLOCK
    elif prompt_version == "v3":
        extras = _V2_ORDER_BLOCK + _V3_EXTRA_BLOCK
    elif prompt_version == "baseline":
        extras = ""
    else:
        raise ValueError(f"unknown prompt_version: {prompt_version}")
    return base + extras + _prompt_tail(doc_title, text)


def parse_response(content: str) -> dict | None:
    try:
        return json.loads(content)
    except json.JSONDecodeError:
        pass
    m = re.search(r"\[\s*(\{.*?\})\s*\]", content, re.DOTALL)
    if m:
        try:
            return json.loads(m.group(1))
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


# ---- GCS helpers -----------------------------------------------------------

def gcs_download_current(dest: Path) -> int:
    """Resume 用。既存 CURRENT.jsonl を取得。なければ 0。"""
    uri = f"gs://{GCS_BUCKET}/{GCS_CURRENT_BLOB}"
    rc = subprocess.run(
        ["gcloud", "storage", "cp", uri, str(dest)],
        capture_output=True, text=True,
    )
    if rc.returncode == 0 and dest.exists():
        return sum(1 for _ in dest.open("r", encoding="utf-8"))
    # 無い場合は空ファイルで初期化
    dest.write_text("", encoding="utf-8")
    return 0


def gcs_push_current(src: Path) -> tuple[int, str]:
    uri = f"gs://{GCS_BUCKET}/{GCS_CURRENT_BLOB}"
    rc = subprocess.run(
        ["gcloud", "storage", "cp", str(src), uri],
        capture_output=True, text=True,
    )
    return rc.returncode, rc.stderr[:200] if rc.stderr else ""


def gcs_rename_to_success(src: Path) -> tuple[int, str]:
    """完了ファイルを SUCCESS_<RUN_ID>.jsonl にコピー（CURRENTもそのまま残す）."""
    uri = f"gs://{GCS_BUCKET}/{GCS_SUCCESS_BLOB}"
    rc = subprocess.run(
        ["gcloud", "storage", "cp", str(src), uri],
        capture_output=True, text=True,
    )
    return rc.returncode, rc.stderr[:200] if rc.stderr else ""


class GcsPusher(threading.Thread):
    """LOCAL_OUT を定期的に GCS CURRENT にプッシュするバックグラウンドスレッド."""

    def __init__(self, src: Path):
        super().__init__(daemon=True)
        self.src = src
        self._stop = threading.Event()
        self._count_trigger = threading.Event()
        self.lock = threading.Lock()
        self.last_pushed_lines = 0
        self.pushes_ok = 0
        self.pushes_err = 0

    def trigger(self):
        self._count_trigger.set()

    def stop(self):
        self._stop.set()

    def run(self):
        while not self._stop.is_set():
            # Wait up to PUSH_INTERVAL_SEC or until trigger fires
            self._count_trigger.wait(timeout=PUSH_INTERVAL_SEC)
            self._count_trigger.clear()
            if self._stop.is_set():
                break
            self._push_once()
        # 最終push
        self._push_once()

    def _push_once(self):
        if not self.src.exists():
            return
        try:
            with self.lock:
                current_lines = sum(1 for _ in self.src.open("r", encoding="utf-8"))
            if current_lines == self.last_pushed_lines:
                return
            rc, err = gcs_push_current(self.src)
            if rc == 0:
                self.pushes_ok += 1
                self.last_pushed_lines = current_lines
                print(f"[gcs-push] lines={current_lines} ok_pushes={self.pushes_ok}", flush=True)
            else:
                self.pushes_err += 1
                print(f"[gcs-push][ERR] rc={rc} err={err}", flush=True)
        except Exception as e:  # noqa: BLE001
            self.pushes_err += 1
            print(f"[gcs-push][EXC] {e!r}", flush=True)


# ---- Worker ---------------------------------------------------------------

async def call_one(client: httpx.AsyncClient, sem: asyncio.Semaphore, row: dict) -> dict:
    async with sem:
        # Truncate via repository helper (fallback to inline if import failed)
        text = _truncate(row.get("full_text", "") or "", row.get("main_category"))
        prompt = build_prompt(row.get("doc_title", ""), text)
        t0 = time.perf_counter()
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
                return {
                    "doc_id": row["doc_id"],
                    "ticker": row.get("ticker"),
                    "doc_title": row.get("doc_title"),
                    "gemini_main_category": row.get("main_category"),
                    "gemini_sub_categories": row.get("sub_categories", []),
                    "main_cat_pred": None,
                    "sub_cat_pred": [],
                    "is_monthly_pred": None,
                    "error": f"HTTP {resp.status_code}: {resp.text[:300]}",
                    "elapsed_sec": elapsed,
                }
            try:
                data = resp.json()
            except Exception as je:  # noqa: BLE001
                return {
                    "doc_id": row["doc_id"],
                    "ticker": row.get("ticker"),
                    "doc_title": row.get("doc_title"),
                    "gemini_main_category": row.get("main_category"),
                    "gemini_sub_categories": row.get("sub_categories", []),
                    "main_cat_pred": None,
                    "sub_cat_pred": [],
                    "is_monthly_pred": None,
                    "error": f"JSON parse error: {je!r} body={resp.text[:300]}",
                    "elapsed_sec": elapsed,
                }
            if "choices" not in data:
                return {
                    "doc_id": row["doc_id"],
                    "ticker": row.get("ticker"),
                    "doc_title": row.get("doc_title"),
                    "gemini_main_category": row.get("main_category"),
                    "gemini_sub_categories": row.get("sub_categories", []),
                    "main_cat_pred": None,
                    "sub_cat_pred": [],
                    "is_monthly_pred": None,
                    "error": f"no-choices body={json.dumps(data, ensure_ascii=False)[:300]}",
                    "elapsed_sec": elapsed,
                }
            content = data["choices"][0]["message"]["content"]
            usage = data.get("usage", {})
            parsed = parse_response(content)
            is_monthly = bool(parsed.get("is_monthly")) if parsed else None
            sub_categories = parsed.get("sub_categories", []) if parsed else []
            if not isinstance(sub_categories, list):
                sub_categories = []
            return {
                "doc_id": row["doc_id"],
                "ticker": row.get("ticker"),
                "doc_title": row.get("doc_title"),
                "gemini_main_category": row.get("main_category"),
                "gemini_sub_categories": row.get("sub_categories", []),
                "main_cat_pred": row.get("main_category"),  # MAIN は Gemini 既存を維持
                "sub_cat_pred": sub_categories,
                "is_monthly_pred": is_monthly,
                "elapsed_sec": elapsed,
                "prompt_tokens": usage.get("prompt_tokens"),
                "completion_tokens": usage.get("completion_tokens"),
                "raw": content[:500],
            }
        except Exception as e:  # noqa: BLE001
            elapsed = time.perf_counter() - t0
            return {
                "doc_id": row["doc_id"],
                "ticker": row.get("ticker"),
                "doc_title": row.get("doc_title"),
                "gemini_main_category": row.get("main_category"),
                "gemini_sub_categories": row.get("sub_categories", []),
                "main_cat_pred": None,
                "sub_cat_pred": [],
                "is_monthly_pred": None,
                "error": repr(e),
                "elapsed_sec": elapsed,
            }


async def main() -> None:
    # ★ vLLM ヘルスチェック: 起動前に /v1/models が応答することを確認
    # ConnectError 全件汚染を防止する
    print("[healthcheck] checking vLLM at http://localhost:8000/v1/models ...", flush=True)
    try:
        hc_resp = httpx.get("http://localhost:8000/v1/models", timeout=10)
        if hc_resp.status_code != 200:
            raise RuntimeError(
                f"vLLM health check failed: HTTP {hc_resp.status_code} body={hc_resp.text[:200]}"
            )
        print(f"[healthcheck] vLLM ready: {hc_resp.text[:200]}", flush=True)
    except httpx.ConnectError as e:
        raise RuntimeError(
            f"vLLM not reachable at localhost:8000 (ConnectError). "
            f"Aborting to prevent GCS pollution. Detail: {e!r}"
        ) from e

    all_rows = [json.loads(line) for line in INPUT_JSONL.read_text(encoding="utf-8").splitlines() if line.strip()]
    print(
        f"[start] total_samples={len(all_rows)} run_id={RUN_ID} "
        f"text_limit={TEXT_LIMIT} prompt_version={PROMPT_VERSION} "
        f"gcs_current=gs://{GCS_BUCKET}/{GCS_CURRENT_BLOB} local_out={LOCAL_OUT}",
        flush=True,
    )

    # --- Resume: GCS CURRENT.jsonl を LOCAL_OUT にDLして再利用 ---
    LOCAL_OUT.parent.mkdir(parents=True, exist_ok=True)
    existing_lines = gcs_download_current(LOCAL_OUT)
    done_ids: set[str] = set()
    if existing_lines > 0:
        with LOCAL_OUT.open("r", encoding="utf-8") as f:
            for line in f:
                try:
                    done_ids.add(json.loads(line)["doc_id"])
                except Exception:  # noqa: BLE001
                    pass
    rows = [r for r in all_rows if r["doc_id"] not in done_ids]
    print(f"[resume] done={len(done_ids)} remaining={len(rows)} local_out={LOCAL_OUT}", flush=True)

    # --- GCS pusher 起動 ---
    pusher = GcsPusher(LOCAL_OUT)
    pusher.start()

    sem = asyncio.Semaphore(CONCURRENCY)
    t_all0 = time.perf_counter()
    # 1件ごと append + flush するロック付きのハンドル
    out_lock = threading.Lock()

    def append_result(r: dict) -> None:
        line = json.dumps(r, ensure_ascii=False)
        with out_lock:
            with LOCAL_OUT.open("a", encoding="utf-8") as f:
                f.write(line + "\n")
                f.flush()
                try:
                    os.fsync(f.fileno())
                except OSError:
                    pass

    async with httpx.AsyncClient() as client:
        tasks = [call_one(client, sem, r) for r in rows]
        done_count = 0
        ok = 0
        err = 0
        last_print = time.perf_counter()
        for t in asyncio.as_completed(tasks):
            r = await t
            append_result(r)
            done_count += 1
            if r.get("error") or r.get("is_monthly_pred") is None:
                err += 1
            else:
                ok += 1
            # GCS push trigger every 100 records
            if done_count % PUSH_INTERVAL_COUNT == 0:
                pusher.trigger()
            now = time.perf_counter()
            if done_count % 20 == 0 or done_count == len(rows) or (now - last_print) > 30:
                rate = done_count / (now - t_all0) if (now - t_all0) > 0 else 0.0
                print(
                    f"[progress] {done_count}/{len(rows)} ok={ok} err={err} "
                    f"rate={rate:.2f}/s elapsed={now-t_all0:.0f}s",
                    flush=True,
                )
                last_print = now

    total_sec = time.perf_counter() - t_all0

    # Stop pusher and flush final
    pusher.stop()
    pusher.trigger()
    pusher.join(timeout=120)

    # 最終 SUCCESS コピー
    rc, err_msg = gcs_rename_to_success(LOCAL_OUT)
    print(f"[gcs-success-copy] rc={rc} blob=gs://{GCS_BUCKET}/{GCS_SUCCESS_BLOB} err={err_msg}", flush=True)

    # Summary
    total_in_file = sum(1 for _ in LOCAL_OUT.open("r", encoding="utf-8"))
    summary = {
        "run_id": RUN_ID,
        "remaining_processed": len(rows),
        "session_ok": ok,
        "session_err": err,
        "total_in_file": total_in_file,
        "session_sec": total_sec,
        "throughput_per_sec": (len(rows) / total_sec) if total_sec else 0,
        "gcs_current_blob": f"gs://{GCS_BUCKET}/{GCS_CURRENT_BLOB}",
        "gcs_success_blob": f"gs://{GCS_BUCKET}/{GCS_SUCCESS_BLOB}",
    }
    print("\n=== SUMMARY ===", flush=True)
    print(json.dumps(summary, ensure_ascii=False, indent=2), flush=True)


if __name__ == "__main__":
    asyncio.run(main())
    # Force clean exit to avoid GcsPusher thread errors affecting exit code
    os._exit(0)
