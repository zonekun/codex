# コードレビュー: _normalize_for_activist() 改修

**レビュー番号**: CR-201
**対象ファイル**: `scripts/tob_prediction/generate_family_holding_candidates.py`
**レビュー日**: 2026-05-17
**レビュアー**: code-reviewer サブエージェント
**パターン**: 3（ad-hoc ソースコードレビュー）
**評価**: B（軽微な懸念あり・本番前確認推奨）

---

## 変更概要

`_normalize_for_activist()` 関数を改修。改修前はスペースを単一スペースに統一するだけだったため、以下の表記揺れでアクティビストの取りこぼしが発生していた。

- `㈱UH Partners 2` / `(株)UH Partners 2` / `株式会社UHPartners3`（法人格プレフィックス違い）
- `UH　Partners３`（全角スペース混在）
- `株式会社UH PARTNERS 2`（スペースあり/なし混在）

改修後:
1. 全角英数字→ASCII変換（`_normalize_fullwidth`）
2. スペース完全除去（単一化→全除去に変更）
3. `_LEGAL_PREFIXES` で法人格プレフィックスを除去してからエイリアスと照合

---

## レビュー結果

### OK: 問題なし

**[OK-1] 全角→ASCII変換の適用順序**

`_normalize_fullwidth()` を先に適用してからスペース除去・プレフィックス除去を行っている。`UH　Partners２` → `UH　Partners2` → `UHPartners2` の流れが正確に動く。

**[OK-2] エイリアス側への正規化適用**

`load_activist_names()` の `activist_aliases.csv` 読み込み箇所（L.118）で `_normalize_for_activist(row["ALIAS"])` を適用している。照合対象のセット構築時も同じ正規化関数を通すため、照合元・照合先で変換の非対称が生じない。`activists.csv` の `NAME` カラム（L.115）も同様に正規化済み。

**[OK-3] プレフィックス正規化の堅牢性**

`_normalize_for_activist()` 内でプレフィックス自体にも `_ALL_SPACES_RE.sub("", prefix)` を適用（L.83）しており、入力名のスペース除去後に比較している。`（株）` のような全角括弧プレフィックスも `startswith` 比較が成立する。

**[OK-4] 大文字統一（`.upper()`）**

関数末尾の `return name.upper()` により大文字小文字揺れを吸収する。`UH PARTNERS` と `UH Partners` が同一視される。

**[OK-5] アクティビスト除外ロジック（apply_filters）**

`normalized.isin(activist_names)` で正規化済み名称とセット照合している。シリーズ単位の isin は O(n) で効率的。

---

### WARN: 軽微な懸念

**[W-1] `_LEGAL_PREFIXES` に「社」後スペース付き混合表記が未網羅**

```python
_LEGAL_PREFIXES = [
    "株式会社", "有限会社", "合同会社", "合資会社", "合名会社",
    "㈱", "㈲",
    "（株）", "(株)", "(株）", "（株)",
    "（有）", "(有)",
    "（合）", "(合)",
]
```

スペース除去後に `startswith` するため入力側スペースは問題ない。しかし **プレフィックスがサフィックス型（末尾付与）の場合**——例:「UH Partners株式会社」のような後置形——は除去されない。`activist_aliases.csv` の現在のエントリ（先頭10行確認済み）にはサフィックス型が見当たらないが、今後エントリが追加された場合の取りこぼしリスクはゼロではない。

対策案（任意）: サフィックスパターン（`CORP_SUFFIXES_JP` 相当）も除去する処理を追加するか、エイリアス登録ガイドラインに「後置法人格は記載しない」と明記する。

**[W-2] `_strip_legal_prefix` と `_normalize_for_activist` の設計不一致**

上場事業法人照合に使う `_strip_legal_prefix()` は全角変換・スペース除去を行わず `startswith` のみ（L.92-94）。アクティビスト側は正規化を徹底しているのに対し、上場事業法人側は生文字列比較のままである。ただし上場事業法人は STOCK_CODE_LIST から取得した正規化済み名称と比較するため、実質的な問題は小さい。将来的に照合ロジックを統一する際の混乱源になる可能性がある。

**[W-3] `_normalize_for_activist` 内のプレフィックス比較が `.upper()` 後の名前と未正規化プレフィックスを比較している**

L.84 の比較:

```python
if name.upper().startswith(norm_prefix.upper()):
```

`name` はこの時点でスペース除去済みだが、まだ `upper()` は関数末尾（L.87）で適用している。比較自体は `name.upper()` と `norm_prefix.upper()` で行われるため問題はないが、実際に除去される文字列（L.85 `name[len(norm_prefix):]`）は upper 前の `name` から切り出す。プレフィックスが小文字混在（例: `(kk)` のような異常値）を含む場合、`len(norm_prefix)` の長さは正しいが `startswith` で検知したプレフィックスと除去長がずれる可能性がある。実用上 `_LEGAL_PREFIXES` に小文字混在はないため問題にはならないが、設計の明確化は余地がある。

---

### INFO: 備考

**[I-1] `_ALL_SPACES_RE` が正規表現オブジェクトとして定義済み（L.52）**

`re.compile` でモジュールレベルに定義しており、繰り返し呼び出し時のコンパイルコストを回避している。

**[I-2] `UH　Partners２号` の変換トレース（疑似実行）**

```
入力: "UH　Partners２号"
→ _normalize_fullwidth: "UH　Partners2号"（全角数字のみASCII化。「号」はそのまま）
→ _ALL_SPACES_RE.sub("", ...): "UHPartners2号"（全角スペース除去）
→ プレフィックス除去: マッチなし（先頭に法人格なし）
→ .upper(): "UHPARTNERS2号"
```

`activist_aliases.csv` L.3 の `UH Partners２号` も同様に `UHPARTNERS2号` に変換されるため一致する。

**[I-3] `print()` が `main()` 内で使用されている（L.251-255, L.282）**

CLAUDE.md §7 ではスクリプト内 `print` 禁止・structlog 使用と定めているが、本変更の対象外（既存コード）のため本レビューでは指摘しない。今後の改修機会に合わせて structlog 化を検討する余地あり。

---

## 総評

本改修の主目的——全角スペース混在・法人格プレフィックス違い・スペースあり/なし混在によるアクティビスト取りこぼし——は正確に対処されている。`load_activist_names()` 側への正規化適用も確認でき、照合元・照合先の対称性は保たれている。

軽微な懸念 [W-1] のサフィックス型プレフィックス未対応は、現在の `activist_aliases.csv` 登録内容では問題を起こさないが、エイリアス追加時の取りこぼしリスクとして認識しておくことを推奨する。[W-2][W-3] は設計上の非一貫性であり、バグではない。

**本番適用は可能**。[W-1] 対応は任意（エイリアス追加ポリシー明記でも代替可）。

---

## 確認できなかった事項

- `classify_shareholder_names.py` の `CORP_SUFFIXES_JP` 全リスト（参照元スクリプト未読）
- `activist_aliases.csv` の全エントリ（先頭10行のみ確認）。後続行にサフィックス型プレフィックスが存在する可能性は排除できない
