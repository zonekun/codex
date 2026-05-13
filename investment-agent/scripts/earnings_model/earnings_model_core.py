"""決算反応モデル — 共通ロジック.

compute_score / score_to_prediction / classify_return を一元管理し、
ノートブック(earnings_model_predict.ipynb) と バッチ(batch_rerun_predict.py) の
両方から import して使う。
"""

from __future__ import annotations

import pandas as pd

# ── 四半期マッピング定数 ──
Q_MAP: dict[str, str] = {"1Q": "1Q", "2Q": "2Q", "3Q": "3Q", "FY": "4Q"}
CUM_PREV_Q: dict[str, str] = {"2Q": "1Q", "3Q": "2Q", "FY": "3Q"}
PREV_Q_MAP: dict[str, str] = {"2Q": "1Q", "3Q": "2Q", "4Q": "3Q"}
LOW_PROFIT_THRESHOLD: int = 500_000_000
GUIDANCE_CAP: int = 3
PERFORMANCE_CAP: int = 3

# ── GCS 保存時カラム一覧 ──
PRED_COLUMNS: list[str] = [
    "ticker", "name", "industry_33", "market_division", "quarter",
    "is_intraday", "disc_time", "score", "prediction", "reasons",
    "progress_op", "has_guidance_revision", "guidance_op_change",
    "yoy_op", "consensus_deviation", "np_consensus_deviation", "f4_source",
    "next_year_op_change", "next_year_eps_change",
    "next_year_disclosed", "selloff_risk",
    "baseline_yoy_op", "has_special_dividend",
    "has_buyback", "has_stock_split", "market_cap_oku",
    "div_change", "qoq_op", "per",
    "adj_close", "prev_close",
    "is_low_base", "is_low_profit", "median_5y_op", "is_turnaround", "fy_achievement",
]


def compute_score(row: pd.Series) -> tuple[int, list[str]]:
    """15因子で決算反応スコアを算出する（F9廃止済み）.

    Args:
        row: 特徴量 DataFrame の1行（dict-like でも可）。

    Returns:
        (score, reasons) のタプル。
    """
    score = 0
    reasons: list[str] = []
    _guidance_group = 0
    _performance_group = 0
    cur_per: str = row["quarter"]
    exp: float | None = {"1Q": 0.25, "2Q": 0.50, "3Q": 0.75}.get(cur_per)

    # IFRS/OdP欠損によるコンセ比較スキップの reason を引き継ぐ
    _f4r = row.get("f4_reasons")
    if _f4r:
        reasons.extend(_f4r)

    # F1: 進捗率
    prog = row.get("progress_op")
    if prog is not None and exp is not None:
        if prog > exp * 1.2:
            score += 1; reasons.append(f"進捗率高 {prog:.0%} (期待{exp:.0%})")
        elif prog < exp * 0.8:
            score -= 1; reasons.append(f"進捗率低 {prog:.0%} (期待{exp:.0%})")

    # F2: 業績修正
    gc = row.get("guidance_op_change")
    if row.get("has_guidance_revision") and gc is not None:
        if gc > 0:
            score += 1; reasons.append(f"上方修正 {gc:+.1%}")
        elif gc < 0:
            score -= 1; reasons.append(f"下方修正 {gc:+.1%}")

    # F3: YoY OP — 低利益率企業は無効化
    _is_low_profit = bool(row.get("is_low_profit"))
    yoy = row.get("yoy_op")
    if yoy is not None and not _is_low_profit:
        if yoy > 0.30:
            _performance_group += 1; reasons.append(f"YoY OP +{yoy:.0%}")
        elif yoy < -0.30:
            _performance_group -= 1; reasons.append(f"YoY OP {yoy:.0%}")

    # F4: コンセンサス乖離（段階的スコア）— max(|ORD_PROFIT乖離|, |NET_PROFIT乖離|) を採用
    cd = row.get("consensus_deviation")
    np_cd = row.get("np_consensus_deviation")
    # F4a/F4b のうち絶対値が大きい方を使用（二重加算回避）
    if cd is not None and pd.notna(cd) and np_cd is not None and pd.notna(np_cd):
        cd = cd if abs(cd) >= abs(np_cd) else np_cd
    elif np_cd is not None and pd.notna(np_cd) and (cd is None or pd.isna(cd)):
        cd = np_cd
    if cd is not None and pd.notna(cd):
        _f4_label = "純利コンセ乖離" if (np_cd is not None and pd.notna(np_cd) and cd == np_cd and cd != row.get("consensus_deviation")) else "コンセ乖離"
        if cd > 0.10:
            score += 3; reasons.append(f"{_f4_label} {cd:+.1%}")
        elif cd > 0.05:
            score += 2; reasons.append(f"{_f4_label} {cd:+.1%}")
        elif cd > 0:
            score += 1; reasons.append(f"{_f4_label} {cd:+.1%}")
        elif cd < -0.30:
            score -= 5; reasons.append(f"{_f4_label} {cd:+.1%}")
        elif cd < -0.20:
            score -= 4; reasons.append(f"{_f4_label} {cd:+.1%}")
        elif cd < -0.10:
            score -= 3; reasons.append(f"{_f4_label} {cd:+.1%}")
        elif cd < -0.05:
            score -= 2; reasons.append(f"{_f4_label} {cd:+.1%}")
        elif cd < 0:
            score -= 1; reasons.append(f"{_f4_label} {cd:+.1%}")

    # F5: 来期ガイダンス — F5b(EPS)が主、F5a(OP)は従
    # F14(分割)発火時はEPS成長率を無効化（分割で非連続）
    _nyc_op = row.get("next_year_op_change")
    _nyc_eps = row.get("next_year_eps_change")
    if row.get("has_stock_split"):
        _nyc_eps = None
    if cur_per == "FY":
        _f5b_valid = _nyc_eps is not None and pd.notna(_nyc_eps)
        _f5a_valid = _nyc_op is not None and pd.notna(_nyc_op)
        # F5b（EPS）: 主因子 — 単独でスコア付与
        if _f5b_valid:
            if _nyc_eps > 0.10:
                _guidance_group += 2; reasons.append(f"来期EPS増益 {_nyc_eps:+.1%}")
            elif _nyc_eps < -0.10:
                _guidance_group -= 2; reasons.append(f"来期EPS減益 {_nyc_eps:+.1%}")
        # F5a（OP）: 従因子 — F5bと同符号の場合のみ有効
        if _f5a_valid:
            _f5a_sign_ok = (not _f5b_valid
                            or (_nyc_op > 0 and _nyc_eps > 0)
                            or (_nyc_op < 0 and _nyc_eps < 0))
            if _f5a_sign_ok:
                if _nyc_op > 0.10:
                    _guidance_group += 1; reasons.append(f"来期OP増益(従) {_nyc_op:+.1%}")
                elif _nyc_op < -0.10:
                    _guidance_group -= 1; reasons.append(f"来期OP減益(従) {_nyc_op:+.1%}")
        if not _f5b_valid and not _f5a_valid and row.get("next_year_disclosed") is False:
            _cap = row.get("market_cap_oku")
            if _cap is not None and _cap >= 3000:
                score -= 1; reasons.append(f"翌期予想非開示 cap={_cap:.0f}億")
            else:
                reasons.append("来期予想未開示 (F5/F7/F12無効)")

    # F6: 売り圧力リスク
    if row.get("selloff_risk"):
        score -= 2; reasons.append("売り圧力リスク(3Q高進捗+修正なし)")

    # F7: 成長加速/減速（FY only）— 低利益率企業は無効化
    baseline = row.get("baseline_yoy_op")
    if (
        cur_per == "FY"
        and not _is_low_profit
        and _nyc_op is not None and pd.notna(_nyc_op)
        and baseline is not None and pd.notna(baseline)
    ):
        gap = _nyc_op - baseline
        if gap > 0.20:
            _guidance_group += 1; reasons.append(f"成長加速 (翌期{_nyc_op:+.0%} vs baseline{baseline:+.0%})")
        elif gap < -0.20:
            _guidance_group -= 1; reasons.append(f"成長減速 (翌期{_nyc_op:+.0%} vs baseline{baseline:+.0%})")

    # F8: 記念配当/特別配当
    if row.get("has_special_dividend"):
        score += 1; reasons.append("記念配当/特別配当")

    # F9: テーマブースト — 廃止（βでは個人投資家関心度を代理できず）

    # F10: 自社株買い
    if row.get("has_buyback"):
        score += 2; reasons.append("自社株買い")

    # F11: 増配/減配 — 株式分割ガード
    div_chg = row.get("div_change")
    if not row.get("has_stock_split") and div_chg is not None and pd.notna(div_chg):
        if div_chg > 0.05:
            score += 1; reasons.append(f"増配 {div_chg:+.0%}")
        elif div_chg < -0.20:
            score -= 3; reasons.append(f"大幅減配 {div_chg:+.0%}")
        elif div_chg < -0.05:
            score -= 2; reasons.append(f"減配 {div_chg:+.0%}")

    # F12: PER / PEG（全Q対応） — 株式分割ガード
    _per = row.get("per")
    _is_fy = cur_per == "FY"
    if (not row.get("has_stock_split")
            and _per is not None and pd.notna(_per) and _per > 0):
        _growth_rate = None
        if _is_fy:
            _nyc12 = row.get("next_year_op_change")
            if _nyc12 is not None and pd.notna(_nyc12) and _nyc12 > 0:
                _growth_rate = _nyc12
        else:
            _yoy12 = row.get("yoy_op")
            if _yoy12 is not None and pd.notna(_yoy12) and _yoy12 > 0:
                _growth_rate = _yoy12
        if _growth_rate is not None:
            _growth_pct = _growth_rate * 100
            _peg = _per / _growth_pct if _growth_pct > 0 else float("inf")
            _f12_score = 0
            if _peg < 0.5:
                _f12_score = 2; reasons.append(f"PEG割安 {_peg:.1f} (PER{_per:.0f}x/成長{_growth_pct:.0f}%)")
            elif _peg < 1.0:
                _f12_score = 1; reasons.append(f"PEG割安 {_peg:.1f} (PER{_per:.0f}x/成長{_growth_pct:.0f}%)")
            elif _peg > 10.0:
                _f12_score = -3; reasons.append(f"PEG超割高 {_peg:.1f} (PER{_per:.0f}x/成長{_growth_pct:.0f}%)")
            elif _peg > 5.0:
                _f12_score = -2; reasons.append(f"PEG割高 {_peg:.1f} (PER{_per:.0f}x/成長{_growth_pct:.0f}%)")
            elif _peg > 2.0:
                _f12_score = -1; reasons.append(f"PEG割高 {_peg:.1f} (PER{_per:.0f}x/成長{_growth_pct:.0f}%)")
            # F12 is guidance for FY and current performance for non-FY periods.
            if _is_fy:
                _guidance_group += _f12_score
            else:
                _performance_group += _f12_score

    # F13: QoQ OP（前Q比急変）— 低利益率企業は無効化
    qoq = row.get("qoq_op")
    if qoq is not None and pd.notna(qoq) and not _is_low_profit:
        if qoq > 0.50:
            score += 1; reasons.append(f"QoQ OP急伸 {qoq:+.0%}")
        elif qoq < -0.50:
            score -= 2; reasons.append(f"QoQ OP急落 {qoq:+.0%}")

    # F14: 株式分割
    if row.get("has_stock_split"):
        score += 1; reasons.append("株式分割")

    # F15: 黒字転換サプライズ
    if row.get("is_turnaround"):
        _yoy_f15 = row.get("yoy_op")
        if _yoy_f15 is not None and pd.notna(_yoy_f15):
            _performance_group += 2; reasons.append(f"黒字転換 (前年同期赤字→黒字, YoY {_yoy_f15:+.0%})")
        else:
            _performance_group += 1; reasons.append("黒字転換 (前年同期赤字→黒字)")

    # F16: FY予想未達ペナルティ
    _fy_ach = row.get("fy_achievement")
    if cur_per == "FY" and _fy_ach is not None and pd.notna(_fy_ach):
        if _fy_ach < 0.65:
            score -= 2; reasons.append(f"FY大幅未達 達成率{_fy_ach:.0%}")
        elif _fy_ach < 0.80:
            score -= 1; reasons.append(f"FY未達 達成率{_fy_ach:.0%}")

    _capped_g = max(-GUIDANCE_CAP, min(GUIDANCE_CAP, _guidance_group))
    if _capped_g != _guidance_group:
        reasons.append(f"来期見通しキャップ ({_guidance_group:+d}→{_capped_g:+d})")
    score += _capped_g

    _capped_p = max(-PERFORMANCE_CAP, min(PERFORMANCE_CAP, _performance_group))
    if _capped_p != _performance_group:
        reasons.append(f"当期業績キャップ ({_performance_group:+d}→{_capped_p:+d})")
    score += _capped_p

    return score, reasons


def score_to_prediction(score: int) -> str:
    """スコアを UP / NEUTRAL / DOWN に変換する.

    Args:
        score: compute_score の出力。

    Returns:
        予測カテゴリ文字列。
    """
    if score >= 2:
        return "UP"
    if score <= -2:
        return "DOWN"
    return "NEUTRAL"


def classify_return(ret: float) -> str:
    """実績リターンを UP / NEUTRAL / DOWN に分類する.

    Args:
        ret: 実績リターン（小数, e.g. 0.02 = +2%）。

    Returns:
        分類カテゴリ文字列。
    """
    if ret > 0.02:
        return "UP"
    if ret < -0.02:
        return "DOWN"
    return "NEUTRAL"
