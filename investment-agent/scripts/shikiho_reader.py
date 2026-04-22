"""四季報 Excel 読み込みユーティリティモジュール。

コードから会社名・セクター等を引ける汎用モジュール。
他スクリプトから `from shikiho_reader import get_company_name, get_company_names` でインポートして使う。
"""
import logging
from pathlib import Path
from typing import Optional

import pandas as pd

logger = logging.getLogger(__name__)

# 四季報 Excel のパス（四半期ごとに更新すること）
SHIKIHO_EXCEL = Path(r"C:\Users\zonekun\Dropbox\stock\DA_四季報_2026_2.xlsx")

# モジュールレベルキャッシュ
_shikiho_df: Optional[pd.DataFrame] = None


def load_shikiho() -> pd.DataFrame:
    """四季報 Excel を読み込んで DataFrame を返す（コード列は4桁文字列）。

    2回目以降の呼び出しではキャッシュを返す（再読み込みしない）。
    ファイルが存在しない場合は警告ログを出して空の DataFrame を返す。

    Returns:
        四季報データの DataFrame。コード列は4桁文字列に正規化済み。
        ファイルが存在しない場合は空の DataFrame。
    """
    global _shikiho_df

    if _shikiho_df is not None:
        return _shikiho_df

    if not SHIKIHO_EXCEL.exists():
        logger.warning(
            "四季報 Excel が見つかりません: %s。空の DataFrame を返します。", SHIKIHO_EXCEL
        )
        _shikiho_df = pd.DataFrame()
        return _shikiho_df

    logger.info("四季報 Excel を読み込み中: %s", SHIKIHO_EXCEL)
    try:
        df = pd.read_excel(SHIKIHO_EXCEL, sheet_name="list", header=0)
        df["コード"] = df["コード"].astype(str).str.zfill(4)
        _shikiho_df = df
        logger.info("四季報 Excel 読み込み完了: %d 行", len(df))
    except Exception as e:
        logger.error("四季報 Excel の読み込みに失敗しました: %s", e)
        _shikiho_df = pd.DataFrame()

    return _shikiho_df


def get_company_name(code: str) -> str:
    """4桁コードから会社名を取得する。

    Args:
        code: 銘柄コード（4桁文字列）。

    Returns:
        会社名文字列。見つからない場合は空文字列。
    """
    df = load_shikiho()
    if df.empty or "コード" not in df.columns or "名前" not in df.columns:
        return ""

    code = str(code).zfill(4)
    matched = df[df["コード"] == code]
    if matched.empty:
        return ""
    return str(matched.iloc[0]["名前"])


def get_company_names(codes: list[str]) -> dict[str, str]:
    """複数の4桁コードから {code: name} dict を返す。

    Args:
        codes: 銘柄コードのリスト（4桁文字列）。

    Returns:
        {code: name} の辞書。見つからないコードは除外される。
    """
    df = load_shikiho()
    if df.empty or "コード" not in df.columns or "名前" not in df.columns:
        return {}

    normalized = [str(c).zfill(4) for c in codes]
    filtered = df[df["コード"].isin(normalized)][["コード", "名前"]]
    return dict(zip(filtered["コード"], filtered["名前"].astype(str)))


def get_company_info(code: str) -> dict:
    """4桁コードから会社情報辞書を返す（コード・名前・セクター等）。

    Args:
        code: 銘柄コード（4桁文字列）。

    Returns:
        会社情報の辞書。見つからない場合は空の辞書。
        キーは DataFrame のカラム名と同一（例: "コード", "名前", "セクター"）。
    """
    df = load_shikiho()
    if df.empty or "コード" not in df.columns:
        return {}

    code = str(code).zfill(4)
    matched = df[df["コード"] == code]
    if matched.empty:
        return {}

    return matched.iloc[0].to_dict()


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    logger.info("=== shikiho_reader 動作確認 ===")

    # 全データ読み込み確認
    df = load_shikiho()
    if df.empty:
        logger.warning("四季報データが空です（ファイルが存在しないか読み込みエラー）")
    else:
        logger.info("総レコード数: %d 行", len(df))
        logger.info("カラム一覧: %s", list(df.columns))

        # 先頭5件のコードと名前を表示
        if "コード" in df.columns and "名前" in df.columns:
            top5 = df[["コード", "名前"]].head(5)
            logger.info("先頭5件:\n%s", top5.to_string(index=False))

        # 代表的な銘柄でテスト
        test_codes = ["7203", "6758", "9984", "9999"]
        for code in test_codes:
            name = get_company_name(code)
            if name:
                logger.info("get_company_name('%s') -> '%s'", code, name)
            else:
                logger.info("get_company_name('%s') -> (未登録)", code)

        # 複数コード一括取得テスト
        names = get_company_names(["7203", "6758", "9984"])
        logger.info("get_company_names(['7203','6758','9984']) -> %s", names)

        # 会社情報取得テスト（最初の銘柄）
        first_code = df["コード"].iloc[0]
        info = get_company_info(first_code)
        info_summary = {k: v for k, v in info.items() if k in ["コード", "名前", "セクター"]}
        logger.info("get_company_info('%s') -> %s", first_code, info_summary)
