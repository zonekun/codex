# コードレビュー: extract_monthly_data.py — fields=[] アダプタ検証ロジック実装

- 日時: 2026-05-07 15:56 JST
- 対象: `docs/plans/refactor_extract_fields_validation_20260507_115942.md` / `scripts/extract_monthly_data.py` L3325-3363 (実装済み差分)
- パターン: 2 (既存コード改修)
- レビュアー: Claude (code-reviewer runbook)
- 前回レビュー: `docs/reviews/094_cr_extract_fields_validation.md` (パターン4・計画段階レビュー、2026-05-07 12:02 JST)

---

## 【サマリー】

- 変更の要約: `phase_extract()` の `_excluded` チェック後・`extraction_method` 取得前に、adapter の `fields=[]` チェックおよび structure.json の metrics との突合チェックを追加。2パターンとも `adapter_no_fields` error_type でスキップする
- 品質評価: **C** — fields=[] チェック（パターン1）は正しく動作するが、structure.json metrics 突合チェック（パターン2、L3350-3363）に**重大なロジック欠陥**があり、正常動作中のアダプタを大量にスキップするリグレッションを引き起こす
- 主要リスク:
  1. **structure.json metrics と adapter fields の不完全一致で正常アダプタが全スキップされる（P0）**
  2. GCS I/O が全 ticker に対して毎回 structure.json を追加読み込みするパフォーマンス影響
  3. 042-1 パターン DB 未追記のまま deploy した場合の monthly-error-autofix 未知パターン問題

---

## 【パターン2: 改修プラン評価】

### 妥当性

**パターン1（fields=[] チェック、L3339-3349）は妥当。根本原因に正しく対処している。**

`fields=[]` のアダプタが `no_records` に紛れてサイレントにスキップされる問題に対し、adapter ロード直後の早期検証で `adapter_no_fields` として明示分離する方針は正しい。

**パターン2（structure.json metrics 突合、L3350-3363）は方向性に重大な問題がある。** 独自推定による根本原因分析の結果：

- 問題の真因は「adapter の fields が空」であって「adapter の fields が structure.json の metrics を完全にカバーしていない」ではない
- structure.json の metrics は BuffettCode 由来の正解ラベル集であり、adapter はそのうちの一部（PDF/テキストから抽出可能なもの）のみを対象とする設計。adapter が structure metrics の部分集合をカバーするのは正常動作
- 実データで確認: 138A は adapter に 7 fields あり正常に抽出できるが、structure.json に 8 metrics あるうち "全店 店舗数" が adapter fields に含まれないため、差分 `{"全店 店舗数"}` が発生し `adapter_no_fields` でスキップされてしまう

**結論**: パターン2は対症療法を超えて**正常系を破壊する過剰検証**であり、リリースすると大量のリグレッションが発生する。

### 副作用・デグレードチェック

- [x] **パターン1（fields=[] チェック）**: 既存の正常系への影響なし。fields が非空の ticker はこの分岐に入らない。安全
- [ ] **パターン2（structure metrics 突合）**: **多数の正常アダプタが誤スキップされる。** structure.json に定義はあるが adapter で意図的に抽出対象外としている metric がある場合（ほぼ全社で該当する可能性が高い）、 `_missing_metrics` が非空になり `continue` で処理スキップされる。これは既存の成功系を破壊するリグレッション
- [x] `results["skip"]` / `_error_entries` の構造: 既存パターンと同一構造の dict で追加コード不要。互換性問題なし
- [x] 過去の緩和策: 各抽出関数（L243, L477, L607 等）の `if not fields: return None` 自己防衛は剥がされていない。パターン1のチェックはこれらの手前で動作するため整合

### 抜け漏れ（類似観点での横展開含む）

- [ ] **adapter の `name` フィールドとの突合漏れ**: L3327-3328 で `f.get("bc_key") or f.get("key")` を使ってキーセットを構築しているが、138A のサンプルでは `name` フィールドも存在する。`key` と `name` が異なる場合（例: key="店舗数 計", name="大黒・魚椿 店舗数", bc_key=なし）、`key` で突合すると structure.json の metric name とマッチしない可能性がある。ただしこれはパターン2自体を撤去すべきであるため、突合キーの選定問題は二次的
- [ ] **GCS structure.json が存在しない ticker の挙動**: L3331 で `gcs_read_json` が `None` を返し、L3332 `no_gcs` でもローカルに存在しない場合、`_structure_metric_names` は空集合。L3350 `elif _structure_metric_names:` が False になり、パターン2のチェックはスキップされる。この場合は正しく動作する（偶然に安全）
- [ ] **042-1 パターン DB への追記**: 前回レビュー（094）で指摘済み。まだ同一 commit に含まれていない模様
- [ ] **CLI サマリーログの adapter_no_fields カウント表示**: 前回レビュー（094）#1 で提案済み。未実装

### 新規リスク

- **リグレッション（P0・即修正）**: パターン2（L3350-3363）が全社の structure metrics 完全カバーを要求するため、adapter fields が structure metrics の部分集合である正常アダプタが `adapter_no_fields` でスキップされる。実データ上、多数の銘柄で structure.json は adapter より多くの metric を定義しており、この条件に該当する
- **パフォーマンス（低影響）**: 全 ticker に対して `gcs_read_json(gcs, _structure_path)` が追加で発行される。~250 社で ~250 回の GCS HEAD + GET。処理時間は 1 社あたり数百 ms 程度だが、全体で 1-2 分の追加。fields=[] チェックだけなら GCS アクセスは不要

---

## 【フォーマット適合性チェック】

- [x] 冒頭に対象ファイルの基準 commit hash が書かれているか → `6dd20b7` 記載あり
- [x] 前提サマリで過去修正と残件数が明示されているか → 明示あり
- [x] 優先度の定義（P0/P1/P2 昇格基準）が冒頭にあるか → P0/P1 定義あり
- [x] 各項目が 7 フィールドを揃えているか → P0-1: 全揃い。P1-1 も同様
- [ ] 修正方針に before/after の両方があるか → **before のコードブロックはある（L3318-3326 引用）が、after の完成コードブロックは無い。修正方針は文章で記述されている。** 厳密には before/after 対比不足だが、差分が明確なため実害は小さい
- [x] 呼び出し側への波及が該当行リストで明示されているか → L4059-L4067, validate_monthly_first_run.py を記載
- [x] 「既に〜がある」系の前提記述を実コードと照合 → L3318-L3326 の引用が実コードと一致確認済み
- [x] アンチパターン対応表が末尾にあるか → あり
- [x] 検証戦略が 4 段を網羅しているか → 4 段あり
- [x] ロールバック手順があるか → 「commit revert で済む」明記
- [x] 関連 commit・知見 MD へのリンクがあるか → あり

**フォーマット違反**: 1件（before/after コード対比が不完全）。軽微。

---

## 【重大な指摘】（即修正）

### #1 structure.json metrics 突合チェック（パターン2）が正常アダプタをスキップするリグレッション

- 箇所: `scripts/extract_monthly_data.py:3350-3363`
- 事象: adapter の fields が structure.json の metrics を**完全カバーしていない**場合に `adapter_no_fields` としてスキップする。しかし、adapter は structure.json metrics の**部分集合**のみを対象とするのが正常動作であり、完全一致を要求するのは設計上の誤り
- トリガー: structure.json に adapter fields より多い metric が定義されている全 ticker（実データでは大多数が該当する見込み）。具体例: 138A は adapter に 7 fields、structure に 8 metrics があり、"全店 店舗数" が adapter に未定義のため `_missing_metrics = {"全店 店舗数"}` となりスキップ
- 影響: **正常動作中のアダプタが大量にスキップされ、月次抽出が全面停止に近い状態になる。** skip カウントが急増し、success が激減する。GCS の monthly_records.json が更新されなくなり、月次パイプライン下流に影響
- 根拠: L3350-3363 のロジック:
  ```python
  elif _structure_metric_names:
      _missing_metrics = _structure_metric_names - _adapter_field_keys
      if _missing_metrics:
          # ... skip with adapter_no_fields
          continue
  ```
  集合差分 `_structure_metric_names - _adapter_field_keys` は「structure にあるが adapter にない metric」。adapter が structure の部分集合を対象とする設計において、この差分は常に非空になり得る
- 推奨対応: **L3350-3363 のパターン2ブロック全体を削除する。** fields=[] チェック（パターン1、L3339-3349）のみを残す。パターン1 だけで「adapter 設定不備のサイレントスキップ防止」という当初目的は十分に達成される。

  もし「structure に metric 定義があるのに adapter fields が空」のケースも検出したい場合は、パターン1の `_missing_detail` 補足情報（L3341-3342）で既にカバーされている。

  代替案として、完全スキップではなく**警告ログのみ**にする（`continue` を削除して処理は続行する）ことも考えられるが、その場合は error_type への記録は不要になるため、パターン2ブロック全体の目的が失われる。削除が最もシンプル。

### #2 GCS I/O の無条件追加（structure.json 読み込み）

- 箇所: `scripts/extract_monthly_data.py:3330-3335`
- 事象: パターン1（fields=[] チェック）を判定するだけなら、L3326 `_adapter_fields = adapter.get("fields", [])` の truthiness チェックで十分であり、structure.json の GCS 読み込みは不要。しかし現在の実装では**全 ticker で structure.json を GCS から読み込んでいる**
- トリガー: 全 ticker の処理時（約 250 社）
- 影響: 1 社あたり GCS HEAD + GET で ~200-500ms の追加レイテンシ。全体で 50-125 秒の処理時間増加。GCS API 呼び出し回数は倍増
- 根拠: L3330-3335 は `_structure_metric_names` を構築するためのコードだが、パターン2（L3350-3363）を削除すれば `_structure_metric_names` は L3341 の補足メッセージ生成にしか使われない。fields=[] の場合のみ structure を読めばよい
- 推奨対応: structure.json の読み込み（L3330-3338）を `if not _adapter_fields:` ブロックの内部に移動する。fields が非空の場合は structure.json を読まない

---

## 【改善提案】（可読性・保守性）

### #1 error_type 名の意味的正確性

- 箇所: `scripts/extract_monthly_data.py:3346, 3360`
- 現状: パターン1（fields=[]）とパターン2（fields不足）が同一の `adapter_no_fields` を使用。前者は「fields がない」、後者は「fields はあるが不足」であり意味が異なる
- 提案: パターン2を削除すれば問題解消。パターン2を何らかの形で残す場合は `adapter_fields_incomplete` 等で区別することを推奨

### #2 `_adapter_field_keys` セットに `None` が混入する可能性

- 箇所: `scripts/extract_monthly_data.py:3327-3328`
- 現状: `f.get("bc_key") or f.get("key")` は、`bc_key` が空文字列 `""` の場合に `key` にフォールバックする（falsy チェック）。しかし `bc_key` と `key` の両方が `None` または欠落の場合、式全体が `None` になり `_adapter_field_keys` に `None` が含まれる。集合演算の結果に影響はない（structure.json の metric name が `None` であることはないため差分に `None` は出ない）が、意図しない要素がセットに入る
- 提案: パターン2を削除すれば `_adapter_field_keys` 自体が不要になるため、問題解消。残す場合はフィルタを追加: `{k for k in (f.get("bc_key") or f.get("key") for f in _adapter_fields if isinstance(f, dict)) if k}`

---

## 【修正例】

#### #1 に対する修正案（パターン2 削除 + structure.json 読み込みを fields=[] 内に移動）

```python
# before: scripts/extract_monthly_data.py:3325-3363
        # adapter 必須フィールド検証: structure.json の metrics と突合
        _adapter_fields = adapter.get("fields", [])
        _adapter_field_keys = {
            f.get("bc_key") or f.get("key") for f in _adapter_fields if isinstance(f, dict)
        }
        _structure_path = f"{GCS_META}/{ticker}/structure.json"
        _structure = gcs_read_json(gcs, _structure_path)
        if not _structure and no_gcs:
            _local_struct = PROJECT_ROOT / "meta" / "monthly" / f"{ticker}_structure.json"
            if _local_struct.exists():
                _structure = json.loads(_local_struct.read_text(encoding="utf-8"))
        _structure_metric_names = {
            m.get("name") for m in (_structure or {}).get("metrics", []) if isinstance(m, dict) and m.get("name")
        }
        if not _adapter_fields:
            _missing_detail = "adapter.fields が空リスト（抽出項目未定義）"
            if _structure_metric_names:
                _missing_detail += f"。structure.json に {len(_structure_metric_names)} メトリクス定義あり"
            logger.warning("[%s] adapter fields=[] → %s", ticker, _missing_detail)
            results["skip"].append(ticker)
            _error_entries.append({
                "ticker": ticker, "error_type": "adapter_no_fields",
                "error_detail": _missing_detail, "elapsed": 0.0,
            })
            continue
        elif _structure_metric_names:
            _missing_metrics = _structure_metric_names - _adapter_field_keys
            if _missing_metrics:
                _missing_detail = (
                    f"structure.json に定義あるが adapter.fields に未定義: "
                    f"{sorted(_missing_metrics)}"
                )
                logger.warning("[%s] adapter fields 不足 → %s", ticker, _missing_detail)
                results["skip"].append(ticker)
                _error_entries.append({
                    "ticker": ticker, "error_type": "adapter_no_fields",
                    "error_detail": _missing_detail, "elapsed": 0.0,
                })
                continue

# after
        # adapter 必須フィールド検証: fields=[] なら早期スキップ
        _adapter_fields = adapter.get("fields", [])
        if not _adapter_fields:
            _missing_detail = "adapter.fields が空リスト（抽出項目未定義）"
            # 補足: structure.json に metrics 定義があれば件数を付記
            _structure_path = f"{GCS_META}/{ticker}/structure.json"
            _structure = gcs_read_json(gcs, _structure_path)
            if not _structure and no_gcs:
                _local_struct = PROJECT_ROOT / "meta" / "monthly" / f"{ticker}_structure.json"
                if _local_struct.exists():
                    _structure = json.loads(_local_struct.read_text(encoding="utf-8"))
            _n_metrics = sum(
                1 for m in (_structure or {}).get("metrics", [])
                if isinstance(m, dict) and m.get("name")
            )
            if _n_metrics:
                _missing_detail += f"。structure.json に {_n_metrics} メトリクス定義あり"
            logger.warning("[%s] adapter fields=[] → %s", ticker, _missing_detail)
            results["skip"].append(ticker)
            _error_entries.append({
                "ticker": ticker, "error_type": "adapter_no_fields",
                "error_detail": _missing_detail, "elapsed": 0.0,
            })
            continue
```

---

## 【確認できなかった事項】

- 全 ticker の adapter fields と structure.json metrics の突合結果の実数値（何社がパターン2で誤スキップされるか）。138A の 1 社で確認したが、全社の統計は実行しないと得られない
- Cloud Run Job 環境での `gcs_read_json` の平均レイテンシ（ローカルとは異なる可能性）
- `build_monthly_extractor.py` 再実行時に fields=[] が確実に解消されるかの保証（アダプタ生成ロジック未確認。プランのスコープ外と明記されている）
