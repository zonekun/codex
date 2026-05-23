"""株主名TYPE分類スクリプト（ルールベース）.

TOP10_NAMES_JSON内の全ユニーク株主名を6カテゴリに分類する。
ルールで分類できなかったものはUNCLASSIFIEDとしてCSV出力し、
Sonnet（コンソール手動切替）で判定する。

Usage:
    PYTHONUTF8=1 python scripts/tob_prediction/classify_shareholder_names.py
    PYTHONUTF8=1 python scripts/tob_prediction/classify_shareholder_names.py --dry-run
    PYTHONUTF8=1 python scripts/tob_prediction/classify_shareholder_names.py --merge-sonnet
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

import pandas as pd
import structlog

logger = structlog.get_logger()

INPUT_CSV = Path(r"C:\tmp\tob_prediction\all_unique_names.csv")
OUTPUT_CSV = Path(r"C:\tmp\tob_prediction\shareholder_name_types.csv")
UNCLASSIFIED_CSV = Path(r"C:\tmp\tob_prediction\unclassified_names.csv")
SONNET_CSV = Path(r"C:\tmp\tob_prediction\shareholder_name_types_sonnet.csv")

# module-level set, populated by load_listed_names() before classification
_listed_normalized_names: set[str] = set()

_LISTED_CHECK_PREFIXES = (
    "株式会社", "㈱", "（株）", "(株)", "有限会社", "合同会社",
    "合資会社", "合名会社",
)
_LISTED_CHECK_SUFFIXES_END = ("株式会社", "㈱", "（株）", "(株)")

# ---------------------------------------------------------------------------
# Rule patterns
# ---------------------------------------------------------------------------

TRUST_BANK_PATTERNS = [
    "信託銀行",
    "カストディ",
    "マスタートラスト",
    "資産管理サービス信託",
    "信託口",
]

# Known foreign custodian bank names (when appearing as the PRIMARY shareholder)
FOREIGN_CUSTODIAN_BANKS = [
    "STATE STREET",
    "JP MORGAN",
    "J.P.MORGAN",
    "JPMORGAN",
    "JPMBL",    # JP Morgan Bank Luxembourg
    "JPMSE",    # JP Morgan SE
    "CITIBANK",
    "CITIGROUP",
    "CGML",     # Citigroup Global Markets Limited
    "NORTHERN TRUST",
    "SSBTC",
    "BNYM",
    "BNY MELLON",
    "BNY FOR ",  # BNY as custodian
    "BNY GCM",   # BNY GCM accounts
    "GOLDMAN SACHS",
    "GOLDMAN,SACHS",
    "GOLDMAN，SACHS",
    "GOLDMANSACHS",
    "MORGAN STANLEY",
    "MERRILL LYNCH",
    "MLI FOR",   # Merrill Lynch International
    "MLPFS",     # Merrill Lynch Pierce Fenner & Smith
    "MSIP",      # Morgan Stanley & Co. International
    "CLEARSTREAM",
    "EUROCLEAR",
    "CHASE MANHATTAN",
    "BANK OF NEW YORK",
    "BROWN BROTHERS",
    "BBH",  # Brown Brothers Harriman abbreviation
    "BNP PARIBAS",
    "BNP　PARIBAS",
    "PARIBAS",   # catch corrupted variants like B1.53NP PARIBAS
    "DEUTSCHE BANK",
    "BARCLAYS",
    "RBC ",      # Royal Bank of Canada
    "UBS AG",
    "UBS ",      # catch USB typo variant with space
    "CREDIT SUISSE",
    "MELLON BANK",
    "HSBC",
    "JULIUS BAER",
    "BANQUE PICTET",
    "BANQUE CANTONALE",
    "PICTET",
    "SOCIETE GENERALE",
    "BCSL",     # Barclays Capital Securities Ltd
    "MSCO",     # Morgan Stanley Customer Securities
    "МSCO",     # Cyrillic M variant (OCR artifact)
    "CA INDOSUEZ",
    "CREDIT AGRICOLE",
    "NOMURA INTERNATIONAL",
    "NOMURA INT",
    "LGT BANK",
]

# Katakana transliterations of known foreign custodian/investment banks
# (checked after stripping internal spaces)
FOREIGN_CUSTODIAN_KATA = [
    "ゴールドマン",            # Goldman
    "ジェーピーモルガン",       # JP Morgan
    "ジエーピーモルガン",       # JP Morgan (variant)
    "ジエイピーモルガン",       # JP Morgan (variant 2)
    "JPモルガン",
    "ノーザントラスト",         # Northern Trust
    "ビーエヌワイ",            # BNY
    "ビービーエイチ",           # BBH
    "シティバンク",            # Citibank
    "バンクオブニューヨーク",    # Bank of New York
    "バンクオブニユーヨーク",    # Bank of New York (variant)
    "ドイツ証券",
    "バークレイズ",            # Barclays
    "ステートストリートバンク",  # State Street Bank
    "チェースマンハッタンバンク",  # Chase Manhattan Bank
    "チェースマンハッタン",
    "メリルリンチ",            # Merrill Lynch
    "バンクジュリウスベア",      # Bank Julius Baer
]

INSTITUTION_PATTERNS = [
    "生命保険",
    "損害保険",
    "火災保険",
    "保険相互会社",
    "信用金庫",
    "信用組合",
    "農業協同組合",
    "漁業協同組合",
    "共済農業",
    "共済連",
    "農林中央金庫",
    "GOVERNMENT OF",
    "年金基金",
    "退職給付信託",
    "企業年金",
    "厚生年金",
    "国民年金",
    "労働金庫",
    "商工中金",
    "商工組合中央金庫",
    "日本政策投資銀行",
    "国際協力銀行",
    "住宅金融支援機構",
    "預金保険機構",
]

# Abbreviated legal form prefixes that map to INSTITUTION
INSTITUTION_ABBR_PREFIXES = [
    "(一財)",   # 一般財団法人
    "(公財)",   # 公益財団法人
    "(一社)",   # 一般社団法人
    "(公社)",   # 公益社団法人
    "（一財）",
    "（公財）",
    "（一社）",
    "（公社）",
]

# Employee/business-partner shareholding associations → treated as INSTITUTION
MOCHIKAIKABU_PATTERNS = [
    "従業員持株会",
    "社員持株会",
    "取引先持株会",
    "役員持株会",
    "持株会",
]

# Patterns indicating an investment fund/vehicle → ASSET_MGMT
ASSET_MGMT_PATTERNS = [
    "投資事業有限責任組合",
    "有限責任事業組合",
    "投資事業組合",    # 有限責任なしの variant
    "投資組合",
    "ファンド",
]

ASSET_MGMT_EN_PATTERNS = [
    " L.P.",
    ", L.P.",
    "(L.P.)",
    "LIMITED PARTNERSHIP",
    "INVESTMENT FUND",
    " FUND ",
    " FUND,",
    " FUND.",
    "SICAV",     # European open-end fund (Société d'Investissement à Capital Variable)
    "UCITS",     # EU investment fund umbrella
    "FCP ",      # Fonds Commun de Placement (French)
    "CEPLUX",    # Luxembourg UCITS platform
    "PORTFOLIO",
]

# Suffix-based institution patterns (banks/securities without 株式会社)
INSTITUTION_SUFFIX_PATTERNS = [
    "銀行",
    "證券",
    "証券",
]

# Company suffixes that disqualify INDIVIDUAL classification
CORP_SUFFIXES_JP = [
    "株式会社", "有限会社", "合同会社", "合資会社", "合名会社",
    "一般社団法人", "一般財団法人", "公益社団法人", "公益財団法人",
    "特定非営利活動法人", "学校法人", "医療法人", "社会福祉法人",
    "宗教法人", "独立行政法人", "国立大学法人", "協同組合",
    "㈱", "㈲", "（株）", "(株)", "（有）", "(有)",
    "(合)", "（合）", "(合名)", "(合資)",
    "(同)",   # 合同会社の略称として使われるケース
    "(株）", "（株)",  # 混在ブラケット
    "㈶",     # 財団法人
    "㈳",     # 社団法人
    "相互会社", "ホールディングス",
]

CORP_SUFFIXES_EN = [
    "HOLDINGS", "HOLDING", "INC.", "INC", "CORP.", "CORP",
    "CO.,LTD", "CO., LTD", "LTD.", "LTD", "LLC", "L.L.C.",
    "PTE", "B.V.", "S.A.", "AG ", "GMBH", "LIMITED",
    "CORPORATION", "COMPANY", "FUND", "TRUST", "PARTNERS",
    "CAPITAL", "ASSET", "INVESTMENT", "MANAGEMENT", "ADVISORY",
    "SECURITIES", "BANK", "INSURANCE",
]

# Association/group patterns → treated as INSTITUTION (not INDIVIDUAL)
INSTITUTION_ASSOCIATION_PATTERNS = [
    "共栄会", "保有会", "親栄会", "共伸会", "炎友会", "共和会",
    "共親会", "共友会", "共聖会", "共誠会", "共済会", "栄勇会",
    "親睦会", "互助会", "福利会", "厚生会",
    "持株組合", "従業員組合", "労働組合",
    "連合会", "連合",
    "基金",
    "投資会",    # 自社株投資会, 従業員投資会 etc.
    "制度会",    # 従業員持株制度会
    "ユニオン",  # union
    "匿名組合",  # anonymous partnership (investment vehicle too, but INSTITUTION here)
    "事業協組合",  # 事業協同組合
    "公社",     # public corporation
]

# Katakana suffixes that indicate non-individual entity
KATA_CORP_SUFFIXES = [
    "リミテッド",    # Limited
    "ホールディング",  # Holdings (without ス)
    "ホールディングス",  # Holdings
    "インク",        # Inc.
    "コーポレーション",  # Corporation
    "ビーヴイ",      # B.V.
    "エヌヴイ",      # N.V.
    "エスエー",      # S.A.
    "エスエイ",      # S.A. (alt)
    "ゲーエムベーハー",  # GmbH
    "アクチエンゲゼルシャフト",  # AG
    "スティフツング",  # Stiftung (foundation)
]

# Katakana terms indicating asset management / fund
KATA_ASSET_MGMT_TERMS = [
    "ベンチャーキャピタル",
    "インベストメント",    # Investment
    "アドバイザーズ",     # Advisors
    "マネジメント",       # Management
]

# Regex to match katakana personal names with punctuation (・ or ー separators)
# e.g. ジョン・マクドナルド, デイモン・スコット・ジャクソン
_KATA_PUNCT_NAME_RE = re.compile(
    rf"^[゠-ヿ]{{2,8}}[・ー][゠-ヿ・ー・ー]{{2,20}}$"
)

# Regex for Japanese personal name pattern
_KANJI = r"一-鿿㐀-䶿"
_HIRA = r"぀-ゟ"
_KATA = r"゠-ヿ"
_JP_CHAR = rf"[{_KANJI}{_HIRA}{_KATA}]"

# Family + space(s) + given (most common pattern)
_JP_NAME_RE = re.compile(rf"^{_JP_CHAR}{{1,5}}[\s　\xa0]+{_JP_CHAR}{{1,5}}$")
# Katakana name with space (foreign names in katakana: イ ジュノ, etc.)
_KATA_NAME_RE = re.compile(rf"^[゠-ヿ]{{1,8}}[\s　\xa0]+[゠-ヿ]{{1,8}}$")

# "○○会" suffix that looks like a name but is actually an association (→ INSTITUTION)
_KAI_SUFFIX_RE = re.compile(rf"^{_JP_CHAR}{{2,8}}会$")

# Single-word Japanese personal name: 3-5 kanji chars with no corp suffix
# Used as fallback for names without spaces (e.g., 北城恪太郎, 七野恵子)
_JP_SINGLE_NAME_RE = re.compile(rf"^{_JP_CHAR}{{3,6}}$")

# Regex to detect space-separated names that normalize to JP personal names
_ALL_SPACES_RE = re.compile(r"[\s　\xa0]+")

# Government entity suffixes (→ INSTITUTION)
_GOVT_SUFFIX_RE = re.compile(rf"{_JP_CHAR}+(県|市|区|町|村|都|道|府|省|庁)$")


def _normalize_fullwidth(name: str) -> str:
    """Convert fullwidth Latin letters/digits to ASCII equivalents."""
    result = []
    for ch in name:
        cp = ord(ch)
        if 0xFF21 <= cp <= 0xFF3A:  # fullwidth uppercase A-Z
            result.append(chr(cp - 0xFEE0))
        elif 0xFF41 <= cp <= 0xFF5A:  # fullwidth lowercase a-z
            result.append(chr(cp - 0xFEE0))
        elif 0xFF10 <= cp <= 0xFF19:  # fullwidth digits 0-9
            result.append(chr(cp - 0xFEE0))
        else:
            result.append(ch)
    return "".join(result)


def _normalize_for_listed_check(name: str) -> str:
    """上場銘柄名照合用の正規化（法人格プレフィックス/サフィックス除去 + スペース除去 + upper）.

    引数は原文でも fullwidth 変換済みでも可（内部で再変換するため結果は同一）。
    """
    # TODO: export_listed_company_names.py に同一実装あり。変更時は両ファイルを同期すること。
    n = _normalize_fullwidth(name).strip()
    n = _ALL_SPACES_RE.sub("", n)
    for prefix in _LISTED_CHECK_PREFIXES:
        if n.startswith(prefix):
            n = n[len(prefix):]
            break
    for suffix in _LISTED_CHECK_SUFFIXES_END:
        if n.endswith(suffix):
            n = n[:-len(suffix)]
            break
    return n.upper()


def load_listed_names(csv_path: Path) -> None:
    """上場銘柄名CSVをグローバルセットに読み込む."""
    global _listed_normalized_names
    df = pd.read_csv(csv_path, encoding="utf-8")
    _listed_normalized_names = set(df["name_normalized"].dropna().tolist())


def _extract_main_name(name: str) -> str:
    """Extract main entity name (before custodian annotations)."""
    for marker in ["常任代理人", "［常任代理人］"]:
        pos = name.find(marker)
        if pos >= 0:
            before = name[:pos]
            for i in range(len(before) - 1, -1, -1):
                if before[i] in "（([［":
                    return name[:i].strip()
            return before.strip()
    cleaned = re.sub(r"[（(]注\d*[）)]?\s*\d*", "", name)
    return cleaned.strip()


def _extract_before_domestic_agent(name: str) -> str:
    """Extract main name before 国内代理人/国内連絡先 annotations."""
    for marker in ["（国内代理人", "(国内代理人", "（国内連絡先", "(国内連絡先",
                   "国内代理人", "国内連絡先"]:
        pos = name.find(marker)
        if pos >= 0:
            return name[:pos].strip()
    return name.strip()


# Regex to detect foreign personal names: 2-4 capitalized ASCII words, no corp keywords
_CORP_KEYWORDS_EN = {
    "INC", "CORP", "CO", "LTD", "LLC", "LLP", "PLC", "SA", "AG", "BV", "NV",
    "HOLDINGS", "HOLDING", "CAPITAL", "ASSET", "FUND", "TRUST", "MANAGEMENT",
    "INVESTMENT", "SECURITIES", "BANK", "INSURANCE", "GROUP", "PARTNERS",
    "SERVICES", "INTERNATIONAL", "GLOBAL", "LIMITED", "ENTERPRISE", "VENTURES",
    "ADVISORY", "FINANCIAL", "COMPANY", "CORPORATION", "ASSOCIATES",
    "ACCOUNT", "ACCOUNTS", "CLIENT", "BRANCH", "OFFICE", "SETTLEMENT",
}

def _is_foreign_personal_name(name: str) -> bool:
    """Heuristic: 2-4 capitalized ASCII words that don't look like company names."""
    name = name.strip()
    # Must be ASCII only (after stripping punctuation)
    cleaned = re.sub(r"['-.]", "", name)
    if not cleaned.replace(" ", "").isascii():
        return False
    words = name.split()
    if not 2 <= len(words) <= 4:
        return False
    # Each word should look like a proper name (starts uppercase or all caps)
    for w in words:
        w_clean = re.sub(r"[^A-Za-z]", "", w)
        if not w_clean:
            return False
    # No corporate keywords
    upper_words = {w.upper().rstrip(".,-") for w in words}
    if upper_words & _CORP_KEYWORDS_EN:
        return False
    # At least the first word should start with uppercase
    if not words[0][0].isupper():
        return False
    return True


def classify_name(name: str) -> tuple[str, str]:
    """Classify a single shareholder name.

    Returns:
        (type, confidence) tuple.
    """
    if not name or not name.strip():
        return "PRIVATE_CORP", "RULE"

    # Normalize fullwidth Latin to ASCII before any processing
    name_norm = _normalize_fullwidth(name)
    upper = name_norm.upper()
    name_stripped = name_norm.strip()
    # Space-normalized version: 　/\xa0 → ASCII space, for pattern matching
    upper_sp = _ALL_SPACES_RE.sub(" ", upper).strip()

    # --- Priority 1: TRUST_BANK (pure custodian pools) ---
    main = _extract_main_name(name_norm)
    for pat in TRUST_BANK_PATTERNS:
        if pat in main:
            return "TRUST_BANK", "RULE"

    # --- Priority 1.5: Handle 国内代理人/国内連絡先 (domestic agent annotation) ---
    if "国内代理人" in name_norm or "国内連絡先" in name_norm:
        main_entity = _extract_before_domestic_agent(name_norm)
        if main_entity and main_entity != name_norm:
            # Classify the main entity only (strip annotation, no further recursion)
            return classify_name(main_entity)

    # --- Priority 2: Handle "常任代理人" entries ---
    if "常任代理人" in name_norm:
        main = _extract_main_name(name_norm)
        main_upper = _ALL_SPACES_RE.sub(" ", main.upper()).strip()
        # Check if main entity is a known foreign custodian bank
        for bank in FOREIGN_CUSTODIAN_BANKS:
            if bank in main_upper:
                return "FOREIGN_CUSTODIAN", "RULE"
        # Main entity is a real investor (fund/government/institution)
        return "INSTITUTION", "RULE"

    # --- Priority 3: FOREIGN_CUSTODIAN (pure custodian bank names) ---
    # Also check with commas/punctuation removed for comma-separated variants
    upper_clean = re.sub(r"[,，&＆]", " ", upper_sp)
    upper_clean = re.sub(r"\s+", " ", upper_clean).strip()
    for bank in FOREIGN_CUSTODIAN_BANKS:
        if bank in upper or bank in upper_sp or bank in upper_clean:
            return "FOREIGN_CUSTODIAN", "RULE"
    name_nospace = _ALL_SPACES_RE.sub("", name_stripped)
    for kata in FOREIGN_CUSTODIAN_KATA:
        if kata in name_nospace:
            return "FOREIGN_CUSTODIAN", "RULE"

    # --- Priority 4: INSTITUTION ---
    for pat in INSTITUTION_ABBR_PREFIXES:
        if name_stripped.startswith(pat):
            return "INSTITUTION", "RULE"
    for pat in MOCHIKAIKABU_PATTERNS:
        if pat in name_stripped:
            return "INSTITUTION", "RULE"
    for pat in INSTITUTION_ASSOCIATION_PATTERNS:
        if pat in name_stripped:
            return "INSTITUTION", "RULE"
    for pat in INSTITUTION_PATTERNS:
        if pat in name:
            return "INSTITUTION", "RULE"
    for pat in INSTITUTION_SUFFIX_PATTERNS:
        if pat in name:
            return "INSTITUTION", "RULE"
    if _GOVT_SUFFIX_RE.search(name_stripped):
        return "INSTITUTION", "RULE"
    # NPO, 公社 prefixes
    if name_stripped.startswith("NPO法人") or name_stripped.startswith("特定非営利"):
        return "INSTITUTION", "RULE"
    # Full-form legal entity prefixes (公益財団法人 etc.) without abbreviated form
    for prefix in ("公益財団法人", "一般財団法人", "公益社団法人", "一般社団法人",
                   "社会福祉法人", "学校法人", "医療法人", "宗教法人",
                   "独立行政法人", "国立大学法人"):
        if name_stripped.startswith(prefix):
            return "INSTITUTION", "RULE"

    # --- Priority 5: ASSET_MGMT (investment funds/partnerships) ---
    for pat in ASSET_MGMT_PATTERNS:
        if pat in name:
            return "ASSET_MGMT", "RULE"
    for pat in ASSET_MGMT_EN_PATTERNS:
        if pat in upper_sp:
            return "ASSET_MGMT", "RULE"
    # LP without period at end
    if upper_sp.rstrip().endswith(" LP") or upper_sp.rstrip().endswith(",LP"):
        return "ASSET_MGMT", "RULE"
    # C.V. (Dutch commanditaire vennootschap = LP)
    if " C.V." in upper_sp or upper_sp.endswith("C.V."):
        return "ASSET_MGMT", "RULE"
    for kata in KATA_ASSET_MGMT_TERMS:
        if kata in name_nospace:
            return "ASSET_MGMT", "RULE"

    # --- Priority 6: Check for company suffixes ---
    has_corp_suffix = False
    for suffix in CORP_SUFFIXES_JP:
        if suffix in name:
            has_corp_suffix = True
            break
    if not has_corp_suffix:
        # Traditional kanji variants (e.g. 株式會社 with 會 instead of 会)
        name_trad = name.replace("會", "会").replace("社員", "社員")
        for suffix in CORP_SUFFIXES_JP:
            if suffix in name_trad:
                has_corp_suffix = True
                break
    if not has_corp_suffix:
        for suffix in CORP_SUFFIXES_EN:
            if suffix in upper_sp:
                has_corp_suffix = True
                break
    if not has_corp_suffix:
        # Katakana corporate form suffixes
        name_nospace_raw = _ALL_SPACES_RE.sub("", name_stripped)
        for kata_sfx in KATA_CORP_SUFFIXES:
            if name_nospace_raw.endswith(kata_sfx):
                has_corp_suffix = True
                break
    if not has_corp_suffix:
        # SA (S.A.) at end of katakana/mixed names
        if re.search(r"[ＳS][ＡA]$", name_stripped) or upper_sp.endswith(" SA"):
            has_corp_suffix = True

    # --- Priority 7: INDIVIDUAL (Japanese personal name) ---
    if not has_corp_suffix:
        # Association names ending in 会 → INSTITUTION
        if _KAI_SUFFIX_RE.match(name_stripped):
            return "INSTITUTION", "RULE"

        # Match Japanese personal name patterns (family + space(s) + given)
        if _JP_NAME_RE.match(name_stripped):
            return "INDIVIDUAL", "RULE"
        if _KATA_NAME_RE.match(name_stripped):
            return "INDIVIDUAL", "RULE"

        # Katakana personal names with ・ punctuation (e.g. ジョン・マクドナルド)
        if _KATA_PUNCT_NAME_RE.match(name_stripped):
            return "INDIVIDUAL", "RULE"

        # Spaced names: normalize internal spaces and re-check
        name_nospace_raw = _ALL_SPACES_RE.sub("", name_stripped)
        if _JP_SINGLE_NAME_RE.match(name_nospace_raw):
            return "INDIVIDUAL", "RULE"
        # Check space-normalized version for JP name regex
        name_sp_norm = _ALL_SPACES_RE.sub(" ", name_stripped).strip()
        if _JP_NAME_RE.match(name_sp_norm) or _KATA_NAME_RE.match(name_sp_norm):
            return "INDIVIDUAL", "RULE"

        # Foreign personal name heuristic (ASCII only, 2-4 capitalized words)
        if _is_foreign_personal_name(name_sp_norm):
            return "INDIVIDUAL", "RULE"

        # Character-spaced names like "W H I T T E N   D A R R E L": collapse single chars
        # Pattern: single letters separated by spaces form words
        collapsed = re.sub(r"(?<=[A-Z]) (?=[A-Z])", "", name_sp_norm)
        collapsed = re.sub(r"\s+", " ", collapsed).strip()
        if collapsed != name_sp_norm and _is_foreign_personal_name(collapsed):
            return "INDIVIDUAL", "RULE"

    # --- Priority 8: Check for spaced-out corp names (e.g., "C B M 株 式 会 社") ---
    if not has_corp_suffix:
        name_nospace = _ALL_SPACES_RE.sub("", name_stripped)
        for suffix in CORP_SUFFIXES_JP:
            if suffix in name_nospace:
                has_corp_suffix = True
                break

    # --- Priority 9: PRIVATE_CORP (has corporate suffix but not classified above) ---
    if has_corp_suffix:
        if _listed_normalized_names:
            if _normalize_for_listed_check(name_norm) in _listed_normalized_names:
                return "LISTED_CORP", "RULE"
        return "PRIVATE_CORP", "RULE"

    # --- Priority 10: UNCLASSIFIED ---
    return "UNCLASSIFIED", ""


def run_classification(names: list[str]) -> pd.DataFrame:
    """Classify all names and return DataFrame."""
    results = []
    for name in names:
        type_, confidence = classify_name(name)
        results.append({"name": name, "type": type_, "confidence": confidence})
    return pd.DataFrame(results)


def merge_sonnet_results(df: pd.DataFrame) -> pd.DataFrame:
    """Merge Sonnet classification results into the main DataFrame."""
    if not SONNET_CSV.exists():
        logger.error("sonnet_csv_not_found", path=str(SONNET_CSV))
        sys.exit(1)

    sonnet_df = pd.read_csv(SONNET_CSV, encoding="utf-8")
    logger.info("sonnet_loaded", rows=len(sonnet_df))

    sonnet_map = dict(zip(sonnet_df["name"], sonnet_df["type"]))
    updated = 0
    for idx, row in df.iterrows():
        if row["type"] == "UNCLASSIFIED" and row["name"] in sonnet_map:
            df.at[idx, "type"] = sonnet_map[row["name"]]
            df.at[idx, "confidence"] = "SONNET"
            updated += 1

    logger.info("sonnet_merged", updated=updated, remaining_unclassified=int((df["type"] == "UNCLASSIFIED").sum()))
    return df


def main() -> None:
    """Run rule-based classification."""
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true", help="Show stats only, don't write files")
    parser.add_argument("--merge-sonnet", action="store_true", help="Merge Sonnet results into final CSV")
    parser.add_argument(
        "--listed-names-csv",
        type=str,
        default=None,
        help="上場銘柄名CSVパス（data/master/listed_company_names.csv）。省略時は LISTED_CORP 分類をスキップ",
    )
    args = parser.parse_args()

    if not INPUT_CSV.exists():
        logger.error("input_not_found", path=str(INPUT_CSV))
        sys.exit(1)

    names_df = pd.read_csv(INPUT_CSV, encoding="utf-8")
    names = names_df["name"].dropna().tolist()
    logger.info("input_loaded", total_names=len(names))

    if args.listed_names_csv:
        load_listed_names(Path(args.listed_names_csv))
        logger.info("listed_names_loaded", count=len(_listed_normalized_names))

    df = run_classification(names)

    type_counts = df["type"].value_counts()
    for type_, count in type_counts.items():
        pct = count / len(df) * 100
        logger.info("category", type=type_, count=int(count), pct=f"{pct:.1f}%")

    if args.merge_sonnet:
        df = merge_sonnet_results(df)
        type_counts = df["type"].value_counts()
        logger.info("after_merge_stats")
        for type_, count in type_counts.items():
            logger.info("category", type=type_, count=int(count))

    if args.dry_run:
        for type_ in sorted(df["type"].unique()):
            samples = df[df["type"] == type_]["name"].head(8).tolist()
            logger.info("samples", type=type_, examples=samples)
        return

    df.to_csv(OUTPUT_CSV, index=False, encoding="utf-8")
    logger.info("output_written", path=str(OUTPUT_CSV), rows=len(df))

    unclassified = df[df["type"] == "UNCLASSIFIED"][["name"]]
    unclassified.to_csv(UNCLASSIFIED_CSV, index=False, encoding="utf-8")
    logger.info("unclassified_written", path=str(UNCLASSIFIED_CSV), rows=len(unclassified))


if __name__ == "__main__":
    main()
