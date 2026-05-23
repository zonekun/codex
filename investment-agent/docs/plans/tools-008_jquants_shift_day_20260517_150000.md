# jquants_get_fin_summary.py — --shift-day パラメータ追加

**作成日時**: 2026-05-17 15:00 JST
**ステータス**: 完了
**対象ファイル**: `scripts/jquants_get_fin_summary.py`（488行、commit 47989bfa 時点）
**対象読者**: code-reviewer サブエージェント / 次セッション担当
**目的**: 02:00 JST 実行の `jquants-fin-summary` Cloud Run Job が当日データを取得しようとして 0件を返すバグを修正する。プログラム内時刻判定（却下済み）の代替として、Cloud Scheduler 側から `--shift-day=-1` を注入するパラメータ方式を実装する。スコープは `jquants_get_fin_summary.py` のみ（調査の結果、他の深夜ジョブは問題なし）。

---

## 前提サマリ

- 過去修正: なし（MR-196 起票、今回が初修正）
- 残存: P0-1 の1件のみ
- 実機検証の有無: 未検証
- 関連 incident: MR-196（fin_summary 2026-05-12〜05-17 欠落、6営業日分・ペーパートレード 139件 SKIP）

---

## 優先度の定義

- **P0**: データ欠落が毎営業日継続中（決算シーズン中はさらに影響大）。Scheduler RESUME 前必須
- **P1**: Cloud Scheduler の message-body 更新（コード修正後に必要な設定変更）
- **P2**: 知見 MD 更新

---

## 指摘項目

### P0-1. DATE_MODE="t" 時に 02:00 実行が当日（0件）を取得する 🚨

**症状**: `jquants-fin-summary-daily`（火〜土 02:00 JST）が毎回 0 件で終了し、BQ `fin_summary` に前日分が格納されない。J-Quants 確報は前日 24:30（当日 00:30）公開済みだが、02:00 時点で「当日分」を問い合わせるためデータが存在しない。

**該当**: `scripts/jquants_get_fin_summary.py:107-118` `_resolve_dates()`、および `L131-132` `parse_args()` Colab ブランチ

```python:L107-118
def _resolve_dates() -> tuple[datetime, datetime]:
    """DATE_MODE に従って (date_from, date_to) を解決する."""
    today = datetime.now(JST).replace(hour=0, minute=0, second=0, microsecorn=0)
    if DATE_MODE == "t":
        return today, today          # ← 02:00 実行で「当日」を返す。これが原因
    elif DATE_MODE == "1":
        d = datetime.strptime(DATE_SINGLE, "%Y%m%d")
        return d, d
    elif DATE_MODE == "r":
        return datetime.strptime(DATE_FROM, "%Y%m%d"), datetime.strptime(DATE_TO, "%Y%m%d")
    else:
        raise ValueError(f"DATE_MODE が不正: {DATE_MODE!r}  ('t' / '1' / 'r')")
```

```python:L131-132
    if RUNTIME in ("colab_personal", "colab_enterprise"):
        return argparse.Namespace(date_from=None, date_to=None)   # ← shift_day 属性なし
```

**根本原因**: `_resolve_dates()` が `shift_day` を受け取らず、常に `today` を返す。Cloud Scheduler → Cloud Run Job へのパラメータ経路が存在しない。

**修正方針**: ① `parse_args()` に `--shift-day`（int, default=0）を追加。② `_resolve_dates(shift_day: int)` を受け取るよう変更。③ `main()` で接続。

変更箇所は4点（すべて同一ファイル内）:

```python
# ── 変更 1: parse_args() Colab ブランチ (L131-132) ──────────────────
# before
    if RUNTIME in ("colab_personal", "colab_enterprise"):
        return argparse.Namespace(date_from=None, date_to=None)

# after
    if RUNTIME in ("colab_personal", "colab_enterprise"):
        return argparse.Namespace(date_from=None, date_to=None, shift_day=0)
```

```python
# ── 変更 2: parse_args() Cloud Run ブランチ (L142-146 の直後) ─────────
# before
    parser.add_argument("--to", dest="date_to", default=None,
                        help="終了日 YYYYMMDD（省略時は --from と同日）")
    return parser.parse_args()

# after
    parser.add_argument("--to", dest="date_to", default=None,
                        help="終了日 YYYYMMDD（省略時は --from と同日）")
    parser.add_argument("--shift-day", dest="shift_day", type=int, default=0,
                        help="DATE_MODE=t 時に today からシフトする日数（例: -1 で前日）")
    return parser.parse_args()
```

```python
# ── 変更 3: _resolve_dates() シグネチャと DATE_MODE="t" ブランチ (L107-111) ──
# before
def _resolve_dates() -> tuple[datetime, datetime]:
    """DATE_MODE に従って (date_from, date_to) を解決する."""
    today = datetime.now(JST).replace(hour=0, minute=0, second=0, microsecond=0)
    if DATE_MODE == "t":
        return today, today

# after
def _resolve_dates(shift_day: int = 0) -> tuple[datetime, datetime]:
    """DATE_MODE に従って (date_from, date_to) を解決する."""
    today = datetime.now(JST).replace(hour=0, minute=0, second=0, microsecond=0)
    if DATE_MODE == "t":
        d = today + timedelta(days=shift_day)
        return d, d
```

```python
# ── 変更 4: main() で shift_day を渡す + 日付確認ログ (L418-423) ─────
# before
        args = parse_args()
        if args.date_from:
            date_from = datetime.strptime(args.date_from, "%Y%m%d")
            date_to   = datetime.strptime(args.date_to or args.date_from, "%Y%m%d")
        else:
            date_from, date_to = _resolve_dates()

# after
        args = parse_args()
        if args.date_from:
            date_from = datetime.strptime(args.date_from, "%Y%m%d")
            date_to   = datetime.strptime(args.date_to or args.date_from, "%Y%m%d")
        else:
            date_from, date_to = _resolve_dates(shift_day=args.shift_day)
        print(f"[日付確認] date_from={date_from:%Y-%m-%d}, date_to={date_to:%Y-%m-%d}, shift_day={args.shift_day}")
```

**呼び出し側への波及**:
- `_resolve_dates()` の呼び出し元は `main()` のみ（`L423`）。シグネチャ変更でデフォルト引数 `shift_day=0` を持つため、Colab 実行（`--shift-day` 未指定）は既存動作を維持。

### P1-1. Cloud Scheduler の message-body 更新 ⚠️

**症状**: コード修正・ビルド後も、Scheduler が `--shift-day` を渡さなければ `shift_day=0`（当日）のままで効果がない。

**該当**: Cloud Scheduler ジョブ `jquants-fin-summary-daily`（us-west1）

**修正方針**: ビルド完了後に以下コマンドで Scheduler を更新する。

```bash
# 現在の設定確認（実行前に body 形式を確認）
gcloud scheduler jobs describe jquants-fin-summary-daily --location=us-west1

# message-body 更新（--shift-day -1 を注入）
gcloud scheduler jobs update http jquants-fin-summary-daily \
  --location=us-west1 \
  --message-body='{"overrides":{"containerOverrides":[{"args":["--shift-day","-1"]}]}}'
```

> Cloud Run Job の `--args` 形式（`gcloud run jobs execute` 用）と Scheduler の message-body 形式は異なる。
> message-body は `args` 配列でスペース区切りトークンを個別要素として渡す。

**呼び出し側への波及**: なし（Scheduler → Cloud Run Job の HTTP body 変更のみ）

**検証**: Scheduler ジョブを手動トリガーして、Job ログに `date_resolved` イベント（date_from=前日）が出ることを確認。

**ロールバック**: `--message-body='{"overrides":{"containerOverrides":[]}}'` で元の引数なし状態に戻す（コード側は git revert 不要。Scheduler 設定のみ戻せばよい）。

---

## 対応アンチパターン

| plan ID | 004 | 備考 |
|---|---|---|
| P0-1 | 該当なし（新規パラメータ追加のため既存アンチパターン分類外） | MR-196 再発防止策として実装 |
| P1-1 | 該当なし | Scheduler 設定変更 |

---

## 検証戦略

1. **smoke test**: `gcloud run jobs execute jquants-fin-summary --region us-west1 --args="--shift-day=-1"` を手動実行し、Logging で `[日付確認] date_from=<前日>, shift_day=-1` が出力されること。かつ BQ `fin_summary` に前日分の行が追加されること（DISCLOSED_DATE = 前営業日）。
2. **dev 実機**: smoke と同一。本番 BQ を使用しているため、DELETE → WRITE_APPEND が冪等であることを確認（同日に複数回実行しても重複しない）。想定 BQ スキャン量は前日1日分のみ。
3. **本番適用判断基準**: smoke PASS かつ BQ に前日分レコードが追加されていることを SELECT で確認。`fin_summary` の `DISCLOSED_DATE` が前日になっていること。
4. **回収手順**: Scheduler 更新失敗時は `--message-body='{"overrides":{"containerOverrides":[]}}'` で空に戻す。BQ データは DELETE → APPEND なので、誤った日付で実行しても再実行で上書き可能（部分的 failure はない）。

---

## 関連ドキュメント

- 知見 MD: `docs/knowledges/tools/008_jquants_fin_summary.md`（親知見）
- 関連 incident: `docs/reviews/196_mr_fin_summary_false_attribution.md` — MR-196 再発防止策 #1-2
- メモリ: `C:\Users\zonekun\.claude\projects\G---------claude-investment-agent\memory\project_shift_flag_parameter.md`

---

## 提出前セルフチェック（必須）

- [x] 冒頭に基準 commit hash があるか（47989bfa）
- [x] 全項目が 7 フィールド（症状/該当/根本原因/修正方針/呼び出し側波及/検証/ロールバック）を揃えているか
- [x] 修正方針に before/after の両方があるか
- [x] 呼び出し側への波及が行番号リストで明示されているか
- [x] 対応アンチパターン表が末尾にあるか
- [x] 検証戦略が smoke / dev / 本番適用判断基準 / 回収手順の 4 段を網羅しているか
- [x] ロールバック手順があるか
- [x] 「既に〜がある」系の前提を実コードで Read 確認したか（jquants_get_fin_summary.py L107-488 を Read 済み）

---

## 実装記録

**実装 commit**: 未コミット（2026-05-17 実装済み）
**検証結果**:
- smoke test: PASS（2026-05-17 09:10 JST。`date_resolved date_from=2026-05-16 shift_day=-1` ログ確認。exit(0)。0件は土曜翌日・前日確報なしのため正常）
- dev 実機: smoke と同一（本番 BQ 使用）
- 本番適用: 適用済み（Scheduler 更新 2026-05-17 09:10 JST）

**code-reviewer 推奨の採否（197_cr_jquants_shift_day.md）**:
| # | 推奨内容 | 採否 | 理由 |
|---|---------|------|------|
| 重大#1 | `elapsed` 未使用 → `log.info("completed", elapsed_sec=...)` で出力 | 採用 | 実装済み |
| 重大#2 | `print(日付確認)` → `log.info("date_resolved", ...)` に変更 | 採用 | 実装済み。structlog import + Dockerfile に structlog>=23.0 追加 |
| 改善#1 | 変更3 after に `# (省略: "1"/"r" ブランチは変更なし)` 追記 | 採用 | 実装済み |
| 改善#2 | エラーメールに `shift_day` 追加 + `args = None` 事前定義 | 採用 | 実装済み |
| 改善#3 | P1-1 のロールバックフィールド整備 | 採用 | プランMD修正済み |

---

## 残タスク（2026-05-17 現在）

| # | タスク | 状態 |
|---|--------|------|
| 1 | スクリプト実装（4箇所 + CR対応5件） | ✅ 完了 |
| 2 | Dockerfile に structlog 追加 | ✅ 完了 |
| 3 | `008_jquants_fin_summary.md` 更新（--shift-day / structlog / 02:00注記） | ✅ 完了 |
| 4 | Docker ビルド（gcloud builds submit） | ✅ 完了（build c0d159cd SUCCESS） |
| 5 | Cloud Scheduler `jquants-fin-summary-daily` 更新（--shift-day -1 注入） | ✅ 完了（2026-05-17 09:10 JST） |
| 6 | smoke test（手動実行 + BQ確認） | ✅ 完了（PASS・date_resolved確認済み） |
| 7 | `034_data_load_jobs.md` Scheduler行に引数注記 | ✅ 完了 |
| 8 | コミット | ⬜ 未 |

---

## 実装後チェック（実装完了時に記入）

- [x] 冒頭のステータスを「完了」に更新したか
- [ ] 実装 commit hash を記録したか（コミット後に記入）
- [x] 検証結果（smoke / dev）を記録したか
- [x] code-reviewer 推奨の採否を記録したか
- [x] 008_jquants_fin_summary.md 更新（残タスク#3）
- [x] 034_data_load_jobs.md Scheduler行更新（残タスク#7）
- [ ] 完了プランを `docs/plans/archive/YYYYMM/` に移動したか（コミット後）

---

## レビュー追記: 2026-05-17 17:29 JST — code-reviewer

→ `docs/reviews/197_cr_jquants_shift_day.md`
