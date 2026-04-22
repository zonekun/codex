"""Cloud Run Job + Workflows バックフィル監視オーケストレータ。

毎回 bash を書き散らす代わりに YAML 1本で load→workflows 通しを宣言する。

使用例:
    PYTHONUTF8=1 python scripts/monitor_backfill.py config/backfill/2023_batch1.yaml

YAML 形式:
    name: 2023_batch1        # 識別子（LINE通知タイトルに使用）
    loads:                   # 先に走らせる Cloud Run Job 群（0件でもOK）
      - job: tdnet-load-daily
        region: us-west1
        env:
          DATE_FROM: "20230101"
          DATE_TO: "20231231"
          TICKER_FROM: "1301"
          TICKER_TO: "1909"
    workflows:               # 全 load 成功後に走らせる Workflow 群（0件でもOK）
      - name: ai_processing_flow
        location: us-central1
        data:
          date_from: "20230101"
          date_to: "20231231"
          ticker_from: "1301"
          ticker_to: "1909"
    options:
      parallel_loads: 1        # load並列度（デフォルト1=直列、N並列も可）
      poll_interval_s: 120     # 完了ポーリング間隔（秒）
      notify_on_start: true    # 開始時LINE通知
      notify_each_load: false  # load完了ごとにLINE通知するか（大量ショット時false推奨）
      resume_execs:            # 既存のexecutionを引き継ぐ場合（省略可）
        loads: ["tdnet-load-daily-xxxx"]        # 省略時は新規起動
        workflows: ["uuid-...-..."]              # 省略時は新規起動

パターン例:
- 11バッチ（load+AIペア）: YAML 11本 or loads/workflows 1セットずつを11回呼ぶ
- 10 load + 1 AI: loads 10件 + workflows 1件 + parallel_loads=3 等
- load のみ: workflows 空
- workflows のみ（load既完了後のリトライ）: loads 空
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import shutil
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import yaml

JST = ZoneInfo("Asia/Tokyo")
PYTHON = "C:/venvs/investment-agent/Scripts/python.exe"
GCLOUD = shutil.which("gcloud") or "gcloud"


def notify(priority: str, title: str, message: str) -> None:
    """LINE (ntfy) 通知を送信。失敗しても監視は継続する。"""
    try:
        subprocess.run(
            [
                PYTHON,
                "scripts/notify.py",
                "ntfy",
                "--title",
                title,
                "--priority",
                priority,
                message,
            ],
            env={**os.environ, "PYTHONUTF8": "1"},
            timeout=30,
            check=False,
            capture_output=True,
        )
    except Exception as e:
        logging.warning(f"notify failed: {e}")


def gcloud(*args: str, timeout: int = 120) -> str:
    """gcloud CLI 実行。stdout を返す（改行と CR は正規化）。

    Windows parallel 実行時に cp932 混入で UnicodeDecodeError 発生するため
    `errors='replace'` で invalid byte を置換、stdout None も防御的に扱う。
    """
    result = subprocess.run(
        [GCLOUD, *args],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout,
        check=False,
    )
    if result.returncode != 0:
        logging.error(f"gcloud failed: {' '.join(args)}\nstderr={result.stderr}")
    out = result.stdout or ""
    return out.strip().replace("\r", "")


def run_load_job(job: str, region: str, env: dict[str, str]) -> str:
    """Cloud Run Job を async 起動し execution 名（`projects/.../executions/XXX`）を返す。"""
    env_csv = ",".join(f"{k}={v}" for k, v in env.items())
    out = gcloud(
        "run",
        "jobs",
        "execute",
        job,
        "--region",
        region,
        "--update-env-vars",
        env_csv,
        "--async",
        "--format",
        "value(name)",
    )
    # 最後の非空行を execution 名として採用（short name or full resource name）
    for line in reversed(out.split("\n")):
        stripped = line.strip()
        if stripped and not stripped.lower().startswith(("view", "http", "executed", "to view")):
            return stripped
    raise RuntimeError(f"execution 名が取れない: {out!r}")


def wait_load_job(exec_name: str, region: str, poll_s: int) -> bool:
    """Cloud Run Job の完了を待つ。成功なら True、失敗なら False。"""
    short = exec_name.split("/")[-1]
    while True:
        completion = gcloud(
            "run",
            "jobs",
            "executions",
            "describe",
            short,
            "--region",
            region,
            "--format",
            "value(status.completionTime)",
        )
        if completion:
            succ = gcloud(
                "run",
                "jobs",
                "executions",
                "describe",
                short,
                "--region",
                region,
                "--format",
                "value(status.succeededCount)",
            )
            return succ == "1"
        time.sleep(poll_s)


def run_workflow(name: str, location: str, data: dict[str, Any]) -> str:
    """Workflow を起動し execution ID を返す。"""
    out = gcloud(
        "workflows",
        "execute",
        name,
        "--location",
        location,
        "--data",
        json.dumps(data),
        "--format",
        "value(name)",
    )
    # short UUID or full "projects/.../executions/UUID" のいずれも受ける
    for line in reversed(out.split("\n")):
        stripped = line.strip()
        if "/executions/" in stripped:
            return stripped.split("/")[-1]
        if stripped and not stripped.lower().startswith(("view", "http", "to view")):
            return stripped
    raise RuntimeError(f"workflow execution ID が取れない: {out!r}")


def wait_workflow(exec_id: str, name: str, location: str, poll_s: int) -> str:
    """Workflow の完了を待ち final state を返す（SUCCEEDED/FAILED/CANCELLED）。"""
    while True:
        state = gcloud(
            "workflows",
            "executions",
            "describe",
            exec_id,
            "--workflow",
            name,
            "--location",
            location,
            "--format",
            "value(state)",
        )
        if state in ("SUCCEEDED", "FAILED", "CANCELLED"):
            return state
        time.sleep(poll_s)


def workflow_error(exec_id: str, name: str, location: str) -> str:
    """FAILED/CANCELLED Workflow の error 先頭300字を返す。"""
    err = gcloud(
        "workflows",
        "executions",
        "describe",
        exec_id,
        "--workflow",
        name,
        "--location",
        location,
        "--format",
        "value(error)",
    )
    return err[:300]


def main() -> None:
    ap = argparse.ArgumentParser(
        description="Cloud Run Job + Workflows バックフィル監視"
    )
    ap.add_argument("config", help="YAML config file")
    ap.add_argument(
        "--dry-run", action="store_true", help="起動せず設定のみ確認して exit"
    )
    args = ap.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    cfg_path = Path(args.config)
    cfg = yaml.safe_load(cfg_path.read_text(encoding="utf-8"))

    name: str = cfg["name"]
    loads: list[dict[str, Any]] = cfg.get("loads", []) or []
    workflows: list[dict[str, Any]] = cfg.get("workflows", []) or []
    opts: dict[str, Any] = cfg.get("options", {}) or {}
    parallel: int = int(opts.get("parallel_loads", 1))
    poll_s: int = int(opts.get("poll_interval_s", 120))
    notify_start: bool = bool(opts.get("notify_on_start", True))
    notify_each_load: bool = bool(opts.get("notify_each_load", False))
    resume: dict[str, list[str]] = opts.get("resume_execs", {}) or {}
    resume_loads: list[str] = resume.get("loads", []) or []
    resume_wfs: list[str] = resume.get("workflows", []) or []

    logging.info(
        f"batch={name} loads={len(loads)} workflows={len(workflows)} "
        f"parallel={parallel} poll={poll_s}s "
        f"resume_loads={len(resume_loads)} resume_wfs={len(resume_wfs)}"
    )

    if args.dry_run:
        logging.info("dry-run: exit")
        return

    if notify_start:
        notify(
            "default",
            f"{name} 開始",
            f"loads={len(loads)} workflows={len(workflows)} parallel={parallel}",
        )

    # ── Phase 1: load jobs ──
    # resume_loads が指定されてたら新規起動せず、それらの完了を待つ
    load_pairs: list[tuple[dict, str]] = []
    if resume_loads:
        for short_id, load_cfg in zip(resume_loads, loads):
            # region が必要なので loads と zip。長さは一致前提
            fake_name = f"projects/_/locations/{load_cfg['region']}/jobs/_/executions/{short_id}"
            load_pairs.append((load_cfg, fake_name))
            logging.info(f"load resume: {short_id}")
    elif loads:
        if parallel > 1:
            with ThreadPoolExecutor(max_workers=parallel) as ex:
                futs = {
                    ex.submit(run_load_job, l["job"], l["region"], l["env"]): l
                    for l in loads
                }
                for fut in as_completed(futs):
                    l = futs[fut]
                    exec_name = fut.result()
                    logging.info(
                        f"load submitted: {l['job']} env={l['env']} exec={exec_name.split('/')[-1]}"
                    )
                    load_pairs.append((l, exec_name))
        else:
            for l in loads:
                exec_name = run_load_job(l["job"], l["region"], l["env"])
                logging.info(
                    f"load submitted: {l['job']} env={l['env']} exec={exec_name.split('/')[-1]}"
                )
                load_pairs.append((l, exec_name))

    fail_loads = 0
    for l, exec_name in load_pairs:
        ok = wait_load_job(exec_name, l["region"], poll_s)
        short = exec_name.split("/")[-1]
        if ok:
            logging.info(f"load done: {short} ✓")
            if notify_each_load:
                notify("default", f"{name} load成功", f"exec={short}")
        else:
            fail_loads += 1
            logging.error(f"load FAILED: {short}")
            notify("high", f"{name} load失敗", f"exec={short} env={l['env']}")

    if fail_loads > 0:
        notify(
            "high",
            f"{name} 中断",
            f"load 失敗 {fail_loads}/{len(load_pairs)} → workflows スキップ",
        )
        sys.exit(1)

    if load_pairs:
        notify(
            "default",
            f"{name} load全完了",
            f"{len(load_pairs)}件成功、workflows起動へ",
        )

    # ── Phase 2: workflows ──
    fail_wfs = 0
    wf_pairs: list[tuple[dict, str]] = []
    if resume_wfs:
        for exec_id, w in zip(resume_wfs, workflows):
            wf_pairs.append((w, exec_id))
            logging.info(f"workflow resume: {exec_id}")
    elif workflows:
        for w in workflows:
            exec_id = run_workflow(w["name"], w["location"], w["data"])
            logging.info(f"workflow submitted: {w['name']} exec={exec_id}")
            wf_pairs.append((w, exec_id))

    for w, exec_id in wf_pairs:
        state = wait_workflow(exec_id, w["name"], w["location"], poll_s=max(180, poll_s))
        if state == "SUCCEEDED":
            logging.info(f"workflow done: {exec_id} ✓")
        else:
            fail_wfs += 1
            err = workflow_error(exec_id, w["name"], w["location"])
            logging.error(f"workflow {state}: {exec_id} err={err}")
            # 失敗時は即中断（後続 workflow で Gemini/Embedding Batch の重複課金回避）
            notify(
                "high",
                f"{name} WF {state} 中断",
                f"exec={exec_id} 以降の workflow 中止 err={err}",
            )
            sys.exit(1)

    notify(
        "default",
        f"{name} 完遂",
        f"loads={len(load_pairs)} workflows={len(wf_pairs)}",
    )
    logging.info(f"ALL DONE: {name}")


if __name__ == "__main__":
    main()
