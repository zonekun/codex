# 休日ハンドリング — jpholiday + 特別休日の補完

**カテゴリ**: tools
**作成日**: 2026-03-02
**ステータス**: 有効
**関連ファイル**: `scripts/is_holiday.py`, `scripts/stock_price_load.py`, `scripts/shina_margin_balance_load.py`

## 概要

日本の休日判定には `jpholiday` ライブラリを使用するが、法定祝日以外の特別休日は
別途 `SPECIAL_DATES` で補完する必要がある。

---

## jpholiday の対応範囲

| 種別 | jpholiday | 備考 |
|------|-----------|------|
| 法定祝日（元日・成人の日・春分の日 等） | ✅ 自動対応 | 毎年自動で更新される |
| 大晦日（12/31） | ❌ 対象外 | 法定祝日ではないため |
| 年始休暇（1/2, 1/3） | ❌ 対象外 | 法定祝日ではないため |
| 土日 | ❌ 対象外 | `weekday() >= 5` で別途判定 |

---

## 実装パターン

```python
import datetime
import jpholiday

# jpholiday が対応しない特別休日（大晦日・年始休暇）
# ※ 年が変わったら更新すること
import datetime as _dt
SPECIAL_DATES = {
    _dt.date(2025, 12, 31),  # 大晦日
    _dt.date(2026,  1,  2),  # 年始休暇
    _dt.date(2026,  1,  3),  # 年始休暇
}

def is_holiday(target: datetime.date) -> bool:
    """土日・祝日・特別休日かどうかを返す."""
    is_weekend = target.weekday() >= 5
    is_holiday = jpholiday.is_holiday(target) or (target in SPECIAL_DATES)
    return is_weekend or is_holiday
```

---

## 注意事項

- **`SPECIAL_DATES` は毎年手動更新が必要**。年末になったら翌年分を追記すること
- `jpholiday` は法定祝日を自動管理するため、振替休日・国民の休日も自動対応される
- 旧来の固定リスト方式（`holidays_2026 = {...}`）は `is_holiday.py` にコメントアウトで保存済み

---

## 旧来方式（固定リスト）との使い分け

| 方式 | メリット | デメリット |
|------|---------|-----------|
| `jpholiday` + `SPECIAL_DATES` | 法定祝日は自動更新、メンテ最小 | 特別休日は手動更新が必要 |
| 固定リスト（`holidays_YYYY`） | 完全に自分でコントロール可能 | 毎年全祝日を手動更新が必要 |

---

## 休日フラグファイル（`is_holiday.py`）

`scripts/is_holiday.py` は Claude Code 管理外のローカルツール群が参照する
`holiday.txt` フラグを管理する補助プログラム。

- **ローカル**: `C:\Users\zonekun\Dropbox\stock\script\holiday.txt` に直接書き込み/削除
- **サーバ**: Dropbox API 経由で `/stock/script/holiday.txt` を操作

フラグは「平日の祝日・特別休日」の場合のみ作成される（土日は対象外）。
