# ザラ場 F4c コンセ乖離: 四半期/FY 不一致による誤検出を修正

**作成日時**: 2026-04-30 15:35 JST
**対象ファイル**: `scripts/zaraba_earnings.py`（1797 行、commit `135d5b8` 時点）
**対象読者**: code-reviewer サブエージェント / 次セッション担当
**目的**: prepare 時に CONSENSUS 行を `iloc[0]` で先頭取得しているため FY 開示と 1Q コンセが比較され +320% 等の異常乖離が出る。CURRENT/NEXT とも開示の四半期・期に整合する行を選ぶよう構造を修正。

> **使い方**: 本プランは `_template_refactor.md` 準拠。
> **フォーマット正本**: `skills/planning.md` §改修プラン / バグ修正指示書 MD フォーマット
> **分類**: (b) 継続改修型（親知見 `docs/knowledges/tools/066_zaraba_tool.md` に追記）

---

## 前提サマリ

- 過去修正: `2026-04-14` で `_build_prior_data` の `fin.iloc[0]` バグ（同FY最古の四半期を拾う）を修正済み（`066_zaraba_tool.md §落とし穴: fin.iloc[0]...`）
- **今回**: 同じ "iloc[0] で集約キー無視" アンチパターンが**別の経路（CONSENSUS 取り込み）に残存**していた横展開漏れ
- 残存: 本プランで扱う 3 件（P0-1 / P0-2 / P0-3）
- 実機検証の有無: 2026-04-30 11:30 disclosure（1878 大東建託 FY）で **+320.5% 誤検出** を確認（results.csv 実機データ）
- 関連 incident: 2026-04-30 13:30 報告 — `/home/zonekun/zaraba_cache/20260430/results.csv` の 4 行目

---

## 観測事実（1878 大東建託 FY 2026-04-30 11:30）

| 値 | 観測値 | 出典 |
|---|---|---|
| `prior_data.json["1878"].consensus_profit` | **33,100 百万円**（= 1Q コンセ） | `prior_data.json` 実機 dump |
| 正しい FY コンセ | **138,850 百万円** | `consensus_20260428.csv` 5 行目 (`FY,138850,CURRENT,IFIS`) |
| 表示された `cd` | +320.5% | `results.csv` 5 行目 |
| 数式逆算 actual_odp | `(3.205 + 1) × 33.1B = 約 139.2B 円` | `cd = (actual_odp - cons*1e6) / abs(cons*1e6)` |
| 真の `cd` (vs 138,850) | **+0.3%（実質コンセ通り）** | `(139.2B - 138.85B) / 138.85B` |

---

## 優先度の定義

- **P0**: ザラ場運用の判定が壊れる。本プラン修正前は FY 開示の F4c コンセ乖離 を信頼してはならない（本日 1878 のように +3 加点が誤発火し STRONG_BUY 判定）
- **P1**: スキーマ整合性・後方互換（既存キャッシュ）
- **P2**: 観察用 obs フィールドの精度向上

---

## 指摘項目

### P0-1. CURRENT コンセが `iloc[0]` で 1Q を拾う 🚨

**症状**: FY 開示で `consensus_profit` が 1Q コンセ値になる。1878 大東建託 FY で 33,100 百万円（1Q）が入り、F4c が +320.5% を誤検出 → +3 加点で STRONG_BUY に押し上げ。

**該当**: `scripts/zaraba_earnings.py:776-786` / `_build_prior_data()` 内コンセ部分

```python:L775-L786
        # コンセンサス
        conse = df_conse[df_conse["TICKER"] == ticker]
        if not conse.empty:
            # CURRENT の最新
            cur = conse[conse["TARGET"] == "CURRENT"]
            if not cur.empty:
                info["consensus_profit"] = _to_num(cur.iloc[0].get("PROFIT"))
                info["consensus_profit_unit"] = "百万円"
            # NEXT（来期コンセンサス）— F16 観察用
            nxt = conse[conse["TARGET"] == "NEXT"]
            if not nxt.empty:
                info["consensus_profit_next"] = _to_num(nxt.iloc[0].get("PROFIT"))
```

**根本原因**: コメントは「CURRENT の最新」と書いてあるが、ソートも QUARTER 一致もせず `iloc[0]` で先頭行を取得。`consensus_*.csv` は CSV 並び順で `1Q → 2Q → 3Q → FY` なので必ず 1Q が選ばれる。`066_zaraba_tool.md §落とし穴: fin.iloc[0] が同FY最古の四半期行を拾い F2/F4/QoQ が全て崩壊` と**完全に同型のバグ**。 prepare 段階では「今日の開示が何 Q か」を確定できない（カレンダー無し銘柄もある）ため、**プレフィルタで単一値を選ぶ設計自体が誤り**。

**修正方針**: prior に四半期別 dict を保存し、scoring 時に rec の `CurPerType` でルックアップする。

```python
# before（L775-L786）
conse = df_conse[df_conse["TICKER"] == ticker]
if not conse.empty:
    cur = conse[conse["TARGET"] == "CURRENT"]
    if not cur.empty:
        info["consensus_profit"] = _to_num(cur.iloc[0].get("PROFIT"))
        info["consensus_profit_unit"] = "百万円"
    nxt = conse[conse["TARGET"] == "NEXT"]
    if not nxt.empty:
        info["consensus_profit_next"] = _to_num(nxt.iloc[0].get("PROFIT"))

# after — 実装は _consensus_to_prior_fields() に切り出し（P0-3 で再利用、レビュー#3 反映）
conse = df_conse[df_conse["TICKER"] == ticker]
if not conse.empty:
    fields = _consensus_to_prior_fields(conse)
    for k, v in fields.items():
        info[k] = v
```

新ヘルパー（モジュールトップに追加。`TypedDict` で型固定 ＝ レビュー改善提案 #2 反映）:

```python
class ConsensusFields(TypedDict, total=False):
    consensus_profit_by_q: dict[str, float | None]
    consensus_profit_unit: str
    consensus_profit_next: float | None
    consensus_profit_next_fy: str


def _consensus_to_prior_fields(conse_one_ticker: pd.DataFrame) -> ConsensusFields:
    """1 銘柄分の CONSENSUS DataFrame から prior に書き込むキー群を組み立てる.

    CURRENT は QUARTER 別 dict（`{"1Q":..,"2Q":..,"3Q":..,"FY":..}`）として保存し、
    scoring 時に rec の CurPerType でルックアップする。
    NEXT は FY 最大の行を採用。同 FY が複数 broker に跨る場合（IFIS/RAKU 並走）は
    SOURCE_USED 昇順 stable sort で順序固定 → 平均値で集約（レビュー指摘 #3）。
    """
    out: ConsensusFields = {}
    cur = conse_one_ticker[conse_one_ticker["TARGET"] == "CURRENT"]
    if not cur.empty:
        cur_map: dict[str, float | None] = {}
        for _, row in cur.iterrows():
            q = str(row.get("QUARTER", "")).upper()
            if q in {"1Q", "2Q", "3Q", "FY"}:
                v = _to_num(row.get("PROFIT"))
                if v is not None:
                    cur_map[q] = v
        if cur_map:
            out["consensus_profit_by_q"] = cur_map
            out["consensus_profit_unit"] = "百万円"
    nxt = conse_one_ticker[conse_one_ticker["TARGET"] == "NEXT"]
    if not nxt.empty:
        nxt_sorted = nxt.sort_values(
            ["FY", "SOURCE_USED"], ascending=[False, True], kind="stable"
        )
        max_fy = nxt_sorted["FY"].max()
        same_fy = nxt_sorted[nxt_sorted["FY"] == max_fy]
        profits = same_fy["PROFIT"].apply(_to_num).dropna()
        if not profits.empty:
            out["consensus_profit_next"] = float(profits.mean())
            out["consensus_profit_next_fy"] = str(max_fy)
    return out
```

**呼び出し側への波及**:
- `scripts/zaraba_earnings.py:1614-1637` — `cons_profit = p.get("consensus_profit")` を `cons_profit = (p.get("consensus_profit_by_q") or {}).get(cur_per)` に変更。`cur_per` は同関数内で既に `rec.get("CurPerType", "")` 取得済み（L1444 / L1514）。
- `scripts/zaraba_earnings.py:849-850` — `prepare` 時の事前サマリ表示。**レビュー指摘 #1 反映**:
  ```python
  # before
  conse = item.get("consensus_profit")
  conse_s = _fmt_yen(conse * 1e6) if conse else "-"

  # after
  by_q = item.get("consensus_profit_by_q") or {}
  q_label = _normalize_quarter(item.get("quarter")) or "FY"
  conse = by_q.get(q_label) if isinstance(by_q, dict) else None
  conse_s = _fmt_yen(conse * 1e6) if conse else "-"
  ```
  `_normalize_quarter` は `"本決算" → "FY"`, `"第1四半期" → "1Q"` 等を返すヘルパー（新規、モジュールトップに追加）。
- `scripts/zaraba_earnings.py:538-547` — `_refresh_prior_consensus` 側（P0-3 で同型修正）。
- 既存 `consensus_profit` キーは P1-1 で**スキーマ判定 + 起動時 abort** で運用ミス防止（レビュー指摘 #2 反映）。

**検証**: 1878 prior_data を再生成 → `consensus_profit_by_q == {"1Q":33100, "2Q":71667, "3Q":105487, "FY":138850}` を確認 → cur_per="FY" で `cons_profit=138850` 取得 → 4/30 の actual_odp (実 XBRL = ~139.2B 円) と比較で `cd ≈ +0.003` (+0.3%) になり STRONG_BUY が NEUTRAL/SLIGHT_SELL 圏に降りること。1Q 開示銘柄（7172 JIA）で `cur_per="1Q"` → `cons_profit=5700` を取得し既存挙動と一致することを併せて確認。

**ロールバック**: `consensus_profit` 旧キーを並行書き出しすれば戻し容易（commit revert で済む）。prior_data.json のみ regenerate 必要、外部影響なし。

---

### P0-2. NEXT コンセが `iloc[0]` で古い FY を拾う 🚨

**症状**: 1878 NEXT 行は 2 件（`FY=202603, 147,193 RAKU`／`FY=202703, 141,842 RAKU`）。`iloc[0]` で **FY=202603（旧サイクル分）** が選ばれ、obs_guidance_vs_consensus（NxFOP=翌期通期予想 vs cons_next）が誤値。

**該当**: `scripts/zaraba_earnings.py:783-786` / `_build_prior_data()` 同関数 NEXT 部分

```python:L783-L786
            # NEXT（来期コンセンサス）— F16 観察用
            nxt = conse[conse["TARGET"] == "NEXT"]
            if not nxt.empty:
                info["consensus_profit_next"] = _to_num(nxt.iloc[0].get("PROFIT"))
```

**根本原因**: P0-1 と同じ `iloc[0]` パターン。NEXT は FY 値が混在する（broker のカバー範囲差異）ため、`FY` カラムでソートして最新（=翌期 FY）を選ぶ必要がある。

**修正方針**: P0-1 の after コードに含める（`nxt.sort_values("FY", ascending=False).iloc[0]`）。`consensus_profit_next_fy` を併記して観察用に検証可能にする。

**呼び出し側への波及**:
- `scripts/zaraba_earnings.py:1391` — `_guidance_vs_consensus()` の `cons_next = prior.get("consensus_profit_next")`。値の意味が「次期 FY コンセ」に確定するため、現状ロジック（`(nx_op - cons_yen) / abs(cons_yen)`）は変更不要。
- `_refresh_prior_consensus()` 内 NEXT 経路も同方針で修正（P0-3）。

**検証**: 1878 prior_data を regenerate → `consensus_profit_next == 141842` かつ `consensus_profit_next_fy == "202703"` を確認 → obs_guidance_vs_consensus が更新されること。

**ロールバック**: P0-1 と同じ。

---

### P0-3. `_refresh_prior_consensus` も同パターンで TICKER→PROFIT 単純 dict 化 🚨

**症状**: `prepare --data consensus`（コンセだけ更新するモード）でも CURRENT/NEXT とも quarter / FY 識別なしで上書き。本日 1878 のような誤値が入る経路がもう一本ある。

**該当**: `scripts/zaraba_earnings.py:518-547`

```python:L518-L547
    cur = df_conse[df_conse["TARGET"] == "CURRENT"].copy()
    if cur.empty:
        log.warning("prior_consensus_refresh_skipped", reason="no_current_consensus")
        return 0
    cur["TICKER"] = cur["TICKER"].astype(str).str[:4]
    conse_map = {
        str(row["TICKER"])[:4]: _to_num(row.get("PROFIT"))
        for _, row in cur.iterrows()
    }
    nxt = df_conse[df_conse["TARGET"] == "NEXT"].copy()
    nxt["TICKER"] = nxt["TICKER"].astype(str).str[:4]
    conse_next_map = {
        str(row["TICKER"])[:4]: _to_num(row.get("PROFIT"))
        for _, row in nxt.iterrows()
    }

    updated = 0
    for ticker, info in prior.items():
        tk = str(ticker)[:4]
        if tk in conse_map:
            info["consensus_profit"] = conse_map[tk]
            info["consensus_profit_unit"] = "百万円"
            updated += 1
        else:
            info.pop("consensus_profit", None)
            info.pop("consensus_profit_unit", None)
        if tk in conse_next_map:
            info["consensus_profit_next"] = conse_next_map[tk]
        else:
            info.pop("consensus_profit_next", None)
```

**根本原因**: dict comprehension で `TICKER → PROFIT` の単純マッピングを作っているため、同一 TICKER の複数行（QUARTER / FY 違い）が**最後の行で上書き**されて任意の Q が残る。仕様上の "代表" を指定していない実装。

**修正方針**: P0-1 と同じ構造（dict）を `_refresh_prior_consensus` 内で再利用できるよう、抽出ロジックを単一関数に切り出す。

```python
# 新規ヘルパー関数（モジュールトップに追加）
def _consensus_to_prior_fields(
    conse_one_ticker: pd.DataFrame,
) -> dict[str, object]:
    """1 銘柄分の CONSENSUS DataFrame から prior に書き込むキー群を組み立てる.

    戻り値の dict は {
        "consensus_profit_by_q": {"1Q":..,"2Q":..,"3Q":..,"FY":..} or None,
        "consensus_profit_unit": "百万円" or None,
        "consensus_profit_next": float or None,
        "consensus_profit_next_fy": str or None,
    }
    """
    out: dict[str, object] = {
        "consensus_profit_by_q": None,
        "consensus_profit_unit": None,
        "consensus_profit_next": None,
        "consensus_profit_next_fy": None,
    }
    cur = conse_one_ticker[conse_one_ticker["TARGET"] == "CURRENT"]
    if not cur.empty:
        cur_map: dict[str, float | None] = {}
        for _, row in cur.iterrows():
            q = str(row.get("QUARTER", "")).upper()
            if q in {"1Q", "2Q", "3Q", "FY"}:
                cur_map[q] = _to_num(row.get("PROFIT"))
        if cur_map:
            out["consensus_profit_by_q"] = cur_map
            out["consensus_profit_unit"] = "百万円"
    nxt = conse_one_ticker[conse_one_ticker["TARGET"] == "NEXT"]
    if not nxt.empty:
        nxt_sorted = nxt.sort_values("FY", ascending=False)
        out["consensus_profit_next"] = _to_num(nxt_sorted.iloc[0].get("PROFIT"))
        out["consensus_profit_next_fy"] = str(nxt_sorted.iloc[0].get("FY"))
    return out

# _build_prior_data 内（L776 付近）
conse_t = df_conse[df_conse["TICKER"] == ticker]
if not conse_t.empty:
    fields = _consensus_to_prior_fields(conse_t)
    for k, v in fields.items():
        if v is not None:
            info[k] = v

# _refresh_prior_consensus 内（L518 周辺の dict 構築を全削除して以下に置換）
# updated カウンタは「CURRENT ヒット銘柄数」固定（レビュー指摘 #4 反映）
df_conse["TICKER"] = df_conse["TICKER"].astype(str).str[:4]
NEW_KEYS = ("consensus_profit_by_q", "consensus_profit_unit",
            "consensus_profit_next", "consensus_profit_next_fy")
for ticker, info in prior.items():
    tk = str(ticker)[:4]
    conse_t = df_conse[df_conse["TICKER"] == tk]
    # 旧キー consensus_profit はスキーマ移行で全銘柄から削除
    info.pop("consensus_profit", None)
    if conse_t.empty:
        for k in NEW_KEYS:
            info.pop(k, None)
        continue
    fields = _consensus_to_prior_fields(conse_t)
    # 既存キーをいったん全クリアしてから新値を書き込む（古い QUARTER の残存防止）
    for k in NEW_KEYS:
        info.pop(k, None)
    for k, v in fields.items():
        info[k] = v
    if "consensus_profit_by_q" in fields:
        updated += 1  # CURRENT ヒット銘柄数だけ加算
```

**呼び出し側への波及**: P0-1 と同じ scoring 側 1 箇所（L1615）。

**検証**: `prepare --data consensus --force` 実行 → `prior_data.json` に `consensus_profit_by_q` キーが入ること、`_refresh_prior_consensus` のログ `prior_consensus_refreshed updated=N` が出ること、N が CURRENT ヒット銘柄数と一致すること。

**ロールバック**: `_consensus_to_prior_fields` を削除して旧コードに戻す。

---

### P1-1. スキーマ変更後の旧キー扱い（schema version + 起動時 abort）⚠️

**症状**: 既存の `prior_data.json`（先行 prepare 済みキャッシュ）には `consensus_profit`（= 旧 1Q バグ値）が残ったまま。スキーマを `consensus_profit_by_q` に切り替えると、既存キャッシュ + 新コードでスコアリングしたとき F4c 不発になる。**per-ticker silent skip にすると初回適用日に全銘柄で一斉沈黙し、運用ミスとして気付きにくい**（レビュー指摘 #2）。

**該当**: `scripts/zaraba_earnings.py:1614-1637` / scoring F4c + `cmd_watch` / `cmd_catchup` の prior ロード直後

**修正方針**:
1. `_build_prior_data` / `_refresh_prior_consensus` の出力に `_schema_version: 2` を埋め込む
2. `cmd_watch` / `cmd_catchup` の prior ロード直後に `_check_prior_schema(prior)` を必ず実行
3. 旧スキーマ銘柄が**過半数**を超えた場合は `SystemExit` で abort（ユーザーに `prepare --data consensus --force` 実行を促す）
4. 過半数以下なら warning ログのみ（部分 migrate 状態を許容）。F4c は新キー優先、無ければスキップ
5. 同時に scoring F4c は新キーのみを使い、旧キーは参照しない（誤値拡散を防ぐ）

```python
# 新規ヘルパー（モジュールトップ）
PRIOR_SCHEMA_VERSION = 2

def _check_prior_schema(prior: dict) -> None:
    """旧スキーマ (consensus_profit only) の銘柄数を集計し、過半数なら abort."""
    legacy = sum(
        1 for v in prior.values()
        if isinstance(v, dict)
        and v.get("consensus_profit") is not None
        and v.get("consensus_profit_by_q") is None
    )
    total = len(prior)
    if legacy > 0:
        log.warning(
            "prior_legacy_consensus_schema",
            legacy=legacy, total=total,
            hint="`prepare --data consensus --force` を実行して再生成してください",
        )
    if total > 0 and legacy / total > 0.5:
        raise SystemExit(
            f"旧コンセンサススキーマ {legacy}/{total} 件検出。"
            f"`PYTHONUTF8=1 python scripts/zaraba_earnings.py prepare "
            f"--date <today> --data consensus --force` を実行してから再起動してください。"
        )

# scoring F4c 内（旧キーは見ない）
cons_by_q = p.get("consensus_profit_by_q")
cons_profit = cons_by_q.get(cur_per) if isinstance(cons_by_q, dict) else None
# 以降は cons_profit が真値ならスコア加算（旧コードと同じ）
```

**呼び出し側への波及**:
- `cmd_watch` / `cmd_catchup` の prior ロード直後に `_check_prior_schema(prior)` 1 行追加
- `_build_prior_data` 関数末尾で `prior["_schema_version"] = PRIOR_SCHEMA_VERSION` を保存（既存 dict のキーが ticker のみという暗黙仕様を破るため、保存・復元側で `_` プレフィックスはメタとして除外する）

**検証**:
- 旧 `prior_data.json` を残したまま `watch --date 20260430` を起動 → `prior_legacy_consensus_schema legacy=N total=M` ログ + 過半数なら `SystemExit`
- `prepare --data consensus --force` 後は legacy=0 で正常起動

**ロールバック**: `_check_prior_schema` 呼び出しを削除すれば旧挙動。F4c は無加点で安全側に倒れる。

---

### P1-2. 既存 results.csv の遡及修正は手動 ⚠️

**症状**: 4/30 results.csv 5 行目（1878）は誤った factors / score を持つ。

**修正方針**: `results.csv` は append-only 監査記録として扱う（過去行は触らない）。代わりに手動コメント行 or 別ファイル `results_fixed.csv` を作成。predict notebook 側の答え合わせは「2026-04-30 以前の F4c は信頼しない」としてフィルタ。

**呼び出し側への波及**: predict notebook 側（`docs/knowledges/tools/059_earnings_model_eda.md` 配下）で 1878 のような STRONG_BUY 行を引かないよう日付フィルタの追加検討。本プランでは方針提示のみ。

**検証**: 該当しない（運用ノート）。

**ロールバック**: 該当しない。

---

## 対応アンチパターン

| plan ID | 004 | T-x | G-x | 親知見 (066) |
|---|---|---|---|---|
| P0-1 | E-2（mutable global / 単一値で複数行を代表） — 厳密一致は無し。**新規パターン候補**: 「集約キー無視の `iloc[0]` / first」 | — | — | §落とし穴: fin.iloc[0]... と同型 |
| P0-2 | 同上 | — | — | 同上（NEXT 版） |
| P0-3 | 同上 + `dict comprehension` での silent overwrite | — | — | 同上 |
| P1-1 | A-5（fallback で値を捏造）に近い: 旧キー残存時に誤値で計算継続するのを避け、明示 skip + warning | — | — | — |

> **新規アンチパターン提案**: 004 §B または新節として「集約キー（QUARTER/FY 等）を持つ DataFrame から `iloc[0]` / `first()` で代表値を取る前にソートと一致確認を入れる」を追加候補。本プランでは提案のみ、追加判断は code-reviewer に委ねる。

---

## 検証戦略

1. **smoke test**:
   - `prepare --date 20260430 --data consensus --force` 実行
   - `python -c "import json; d=json.load(open('/home/zonekun/zaraba_cache/prior_data.json',encoding='utf-8'))['1878']; print(d.get('consensus_profit_by_q'), d.get('consensus_profit_next'), d.get('consensus_profit_next_fy'))"` で
     `{'1Q':33100, '2Q':71667, '3Q':105487, 'FY':138850} 141842 202703` を確認
   - 既存 4 銘柄（1878 / 7172 / 8616 / 4679）について `_score_record` 単体テストを書き、cur_per ごとに正しい cons_profit が引かれることを assert

2. **dev 実機**:
   - `catchup --date 20260430 --until 11:35` を再実行（既存 results.csv はリネーム退避）→ 1878 行の `factors` から `コンセ乖離+320.5%` が消え、+0.3% など実態に近い値になること
   - 8616（1 行のみ FY コンセ）は変化が無い（=旧コードと同値）こと、7172（1Q 開示）は `consensus_profit_by_q["1Q"]=5700` 引きで挙動同値となることを確認

3. **本番適用判断基準**:
   - smoke + dev 両方 PASS で初めて prod
   - 翌営業日（2026-05-01）の `watch` 開始前に `prepare --data consensus --force` を必ず実行（旧キャッシュの上書き）
   - `watch` 起動後 10 分以内に最低 1 銘柄の F4c 因子が発火することを目視

4. **回収手順**:
   - 修正コミット直後に F4c が全銘柄で不発になった場合: 旧 `consensus_profit` キー直読みする hot-fix を deploy（数行 patch）
   - prior_data.json 破損時: `prepare --date <today> --force` で再生成（BQ 再読み込み）
   - results.csv 誤値混入時: 該当行を別ファイルに退避し、predict notebook 側で除外

---

## 関連ドキュメント

- 親知見 MD: `docs/knowledges/tools/066_zaraba_tool.md` — §スコアリング因子 F4c コンセ乖離（行 134-140 周辺）／§落とし穴: fin.iloc[0]...（同型バグ）
- 関連 commit:
  - `135d5b8` — 本プラン基準 commit
  - `0e1fba9` — `fix: ザラ場ツール fin.iloc[0] 誤参照で F2/F4/QoQ 全崩壊を修正 + UI刷新`（2026-04-14、横展開漏れの起点。`git log -- scripts/zaraba_earnings.py` で確定）
- 関連 incident: `/home/zonekun/zaraba_cache/20260430/results.csv` 5 行目 1878 大東建託 + 320.5% 誤検出 / `/home/zonekun/zaraba_cache/prior_data.json` の `1878.consensus_profit=33100`（1Q 値）
- フォーマット正本: `skills/planning.md` §改修プラン / バグ修正指示書 MD フォーマット

---

## レビュー追記: 2026-04-30 16:10 JST — code-reviewer

→ `docs/reviews/033_cr_zaraba_consensus_quarter_match.md`
