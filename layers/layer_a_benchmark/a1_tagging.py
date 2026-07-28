"""A1层：语义提取——硬标签 + 财务数字画像

职责：
- 优先从同花顺行业分类 CSV 查表获取硬标签
- CSV 查到硬标签 → LLM 只输出 6 维财务等级
- CSV 查不到 → LLM 同时输出硬标签 + 等级
- LLM 不可用 → 关键词规则降级硬标签，数字画像设为 None
"""

import logging
from pathlib import Path

from schemas.tags import CompanyTags, HardTag, FinancialProfile
from schemas.raw_doc import RawDocument

logger = logging.getLogger(__name__)

# ────────────────────────────────────────────
# 同花顺行业分类 CSV 查表（硬标签优先路径）
# ────────────────────────────────────────────

_INDUSTRY_CSV_PATH = Path("data/industry/thf_industry_classification.csv")
_INDUSTRY_CACHE: dict[str, dict] | None = None
_NAME_INDEX: list[tuple[str, dict]] | None = None


def _load_industry_csv() -> dict[str, dict]:
    """惰性加载同花顺行业分类 CSV"""
    global _INDUSTRY_CACHE, _NAME_INDEX
    if _INDUSTRY_CACHE is not None:
        return _INDUSTRY_CACHE

    import csv
    import io

    if not _INDUSTRY_CSV_PATH.exists():
        logger.warning(f"行业分类 CSV 不存在: {_INDUSTRY_CSV_PATH}")
        _INDUSTRY_CACHE = {}
        _NAME_INDEX = []
        return _INDUSTRY_CACHE

    with open(_INDUSTRY_CSV_PATH, "rb") as f:
        raw = f.read()
    text = raw.decode("utf-8-sig")

    reader = csv.DictReader(io.StringIO(text))
    required_cols = {"股票代码", "股票简称", "所属同花顺一级行业", "所属同花顺二级行业", "所属同花顺三级行业"}
    if not required_cols.issubset(reader.fieldnames or []):
        logger.warning(f"行业分类 CSV 列名不匹配: {reader.fieldnames}")
        _INDUSTRY_CACHE = {}
        _NAME_INDEX = []
        return _INDUSTRY_CACHE

    cache: dict[str, dict] = {}
    name_index: list[tuple[str, dict]] = []
    row_count = 0

    for row in reader:
        code = row["股票代码"].strip()
        code_norm = code.split(".")[0]
        entry = {
            "code": code,
            "name": row["股票简称"].strip(),
            "level1": row["所属同花顺一级行业"].strip(),
            "level2": row["所属同花顺二级行业"].strip(),
            "level3": row["所属同花顺三级行业"].strip(),
        }
        cache[code_norm] = entry
        cache[code] = entry
        name_index.append((entry["name"], entry))
        row_count += 1

    if row_count < 5000:
        logger.warning(f"行业分类 CSV 行数异常: {row_count}")

    _INDUSTRY_CACHE = cache
    _NAME_INDEX = name_index
    logger.info(f"已加载行业分类 CSV: {row_count} 只股票")
    return cache


def _csv_lookup_by_code(stock_code: str) -> dict | None:
    """按股票代码精确匹配"""
    if not stock_code:
        return None
    cache = _load_industry_csv()
    return cache.get(stock_code.strip())


def _csv_lookup_by_name(company_name: str) -> dict | None:
    """按公司名模糊匹配"""
    if not company_name or _NAME_INDEX is None:
        return None
    for short_name, entry in _NAME_INDEX:
        if short_name in company_name or company_name in short_name:
            return entry
    return None


def _csv_lookup_hard_tags(stock_code: str, company_name: str) -> list[HardTag] | None:
    """CSV 查表获取硬标签（三级+二级+一级）"""
    result = _csv_lookup_by_code(stock_code)
    if not result:
        result = _csv_lookup_by_name(company_name)
    if result:
        return [
            HardTag(system="同花顺三级行业", value=result["level3"]),
            HardTag(system="同花顺二级行业", value=result["level2"]),
            HardTag(system="同花顺一级行业", value=result["level1"]),
        ]
    return None


def _hard_tags_to_str(hard_tags: list[HardTag]) -> str:
    """HardTag 列表 → 描述文字"""
    return ", ".join(f"{t.system}={t.value}" for t in hard_tags)


# ────────────────────────────────────────────
# 主入口
# ────────────────────────────────────────────

def run_tagging(raw_doc: RawDocument) -> CompanyTags:
    """读取公司信息，输出硬标签 + 财务数字画像

    流程：
        1. CSV 查硬标签（股票代码 → 公司名 → 降级）
        2. CSV 查到 → LLM 只出 6 维等级
        3. CSV 查不到 → LLM 同时出硬标签 + 等级
        4. LLM 不可用 → 关键词降级，画像=None
    """
    company_name = raw_doc.metadata.get("company_name", "")
    stock_code = raw_doc.metadata.get("stock_code", "")
    business_desc = raw_doc.company_overview.business_description or ""
    industry_text = raw_doc.company_overview.industry_classification or ""
    full_text = f"{company_name}\n{industry_text}\n{business_desc}"

    # 第一步：CSV 查硬标签
    csv_hard_tags = _csv_lookup_hard_tags(stock_code, company_name)
    if csv_hard_tags:
        logger.info(f"CSV 查到硬标签: {_hard_tags_to_str(csv_hard_tags)}")
        try:
            fp = _llm_tagging(
                company_name, business_desc,
                existing_hard_tags=csv_hard_tags,
            )
            return CompanyTags(
                company_name=company_name,
                stock_code=stock_code,
                hard_tags=csv_hard_tags,
                financial_profile=fp,
            )
        except Exception as e:
            logger.warning(f"CSV 后 LLM 画像失败: {e}")
            return CompanyTags(
                company_name=company_name,
                stock_code=stock_code,
                hard_tags=csv_hard_tags,
                financial_profile=None,
            )

    # 第二步：LLM 同时出硬标签 + 数字画像
    try:
        return _llm_tagging(company_name, business_desc)
    except Exception as e:
        logger.warning(f"LLM 打标签失败，回退到规则匹配: {e}")
        return _rule_based_tagging(company_name, stock_code, full_text)


# ────────────────────────────────────────────
# LLM 路径
# ────────────────────────────────────────────

def _llm_tagging(
    company_name: str,
    business_desc: str,
    existing_hard_tags: list[HardTag] | None = None,
) -> CompanyTags | FinancialProfile:
    """通过 LLM 获取财务数字画像

    返回：
    - existing_hard_tags 有值 → FinancialProfile（仅等级数组）
    - 无 → CompanyTags（硬标签 + 等级数组）
    """
    from llm.client import LLMClient

    variables: dict[str, str] = {
        "company_name": company_name,
        "business_description": business_desc,
    }
    if existing_hard_tags:
        variables["existing_hard_tags"] = _hard_tags_to_str(existing_hard_tags)

    client = LLMClient()
    response = client.chat("a1_tagging", variables)
    data = _extract_json(response if isinstance(response, str) else str(response))

    levels = data.get("levels", [])
    if not levels or len(levels) != 6:
        logger.warning(f"LLM 返回 levels 格式异常: {levels}")
        levels = ["中", "中", "中", "中", "中", "中"]

    if existing_hard_tags:
        return FinancialProfile(levels=levels)

    # 模式2：同时出硬标签
    hard_tag_val = data.get("hard_tag", "")
    hard_tags = [HardTag(value=hard_tag_val)] if hard_tag_val else [HardTag(value="未知行业")]
    return CompanyTags(
        company_name=company_name,
        stock_code="",
        hard_tags=hard_tags,
        financial_profile=FinancialProfile(levels=levels),
    )


def _extract_json(text: str) -> dict:
    """从 LLM 文本中提取 JSON"""
    import json
    import re

    text = text.strip()
    fence_match = re.search(r'```(?:json)?\s*([\s\S]*?)\s*```', text)
    if fence_match:
        text = fence_match.group(1)
    brace_match = re.search(r'\{[\s\S]*\}', text)
    if brace_match:
        text = brace_match.group(0)
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        try:
            return json.loads(text.replace("'", '"'))
        except json.JSONDecodeError:
            logger.warning(f"无法解析 JSON: {text[:200]}")
            return {}


# ────────────────────────────────────────────
# 关键词规则降级（LLM 不可用时）
# ────────────────────────────────────────────

_KEYWORD_HARD_TAG_MAP: dict[str, str] = {
    "白酒": "白酒", "啤酒": "啤酒", "红酒": "其他酒类", "葡萄酒": "其他酒类",
    "乳制品": "乳制品", "乳业": "乳制品", "牛奶": "乳制品",
    "酱油": "调味品", "调味品": "调味品", "醋": "调味品", "味精": "调味品",
    "休闲食品": "休闲食品", "零食": "休闲食品", "坚果": "休闲食品",
    "速冻": "速冻食品", "预制菜": "预制菜",
    "光伏": "光伏制造设备", "太阳能": "光伏发电", "硅片": "光伏制造设备", "电池片": "光伏制造设备",
    "锂电池": "锂电池", "锂电": "锂电池", "正极材料": "锂电池", "负极材料": "锂电池", "电解液": "锂电池",
    "新能源汽车": "新能源汽车", "电动车": "新能源汽车", "新势力": "新能源汽车",
    "储能": "储能", "逆变器": "光伏制造设备",
    "芯片设计": "半导体设计", "IC设计": "半导体设计", "集成电路设计": "半导体设计",
    "晶圆": "半导体制造", "芯片制造": "半导体制造", "代工": "半导体制造", "fab": "半导体制造",
    "封装测试": "半导体封测", "封测": "半导体封测", "先进封装": "半导体封测",
    "半导体设备": "半导体设备", "光刻": "半导体设备", "刻蚀": "半导体设备",
    "创新药": "化学制药", "仿制药": "化学制药", "制药": "化学制药",
    "中药": "中药", "中成药": "中药", "中药材": "中药",
    "CXO": "CXO/医药外包", "CRO": "CXO/医药外包", "CDMO": "CXO/医药外包", "医药外包": "CXO/医药外包",
    "医疗器械": "医疗器械", "医疗设备": "医疗器械", "耗材": "医疗器械", "体外诊断": "医疗器械",
    "疫苗": "生物制品", "血制品": "生物制品", "生物药": "生物制品", "抗体": "生物制品",
    "证券": "证券", "券商": "证券", "保险": "保险", "银行": "银行", "城商行": "城商行",
    "手机": "消费电子", "智能穿戴": "消费电子", "耳机": "消费电子",
    "云计算": "云计算", "云服务": "云计算", "SaaS": "云计算",
    "网络安全": "网络安全", "信息安全": "网络安全",
    "游戏": "游戏", "手游": "游戏", "电竞": "游戏",
    "广告": "广告营销", "营销": "广告营销",
    "军工": "军工电子", "航空": "航空装备", "航天": "航空装备", "导弹": "军工电子",
    "煤炭": "煤炭开采", "煤矿": "煤炭开采",
    "石油": "石油开采", "天然气": "石油开采", "油田": "石油开采",
    "火电": "火电", "水电": "水电", "核电": "核电", "风电": "风力发电",
    "生猪": "生猪养殖", "养猪": "生猪养殖", "猪": "生猪养殖",
    "饲料": "饲料", "鸡": "白羽肉鸡", "肉鸡": "白羽肉鸡",
    "房地产": "房地产开发", "地产开发": "房地产开发", "住宅": "房地产开发",
    "物业": "物业管理", "物业服务": "物业管理",
    "航运": "航运", "海运": "航运", "集装箱": "航运",
    "快递": "快递", "物流": "快递",
    "高速公路": "高速公路", "铁路": "铁路运输", "机场": "机场",
    "旅游": "旅游景区", "景区": "旅游景区", "酒店": "酒店",
    "免税": "免税", "运动": "运动服饰", "服饰": "运动服饰", "家纺": "家纺",
    "化妆品": "化妆品", "医美": "医美服务",
    "教育": "教育培训", "培训": "教育培训", "学校": "教育培训",
}


def _rule_based_tagging(company_name: str, stock_code: str, text: str) -> CompanyTags:
    """关键词规则降级（LLM 不可用时）"""
    matched = None
    for keyword, industry in _KEYWORD_HARD_TAG_MAP.items():
        if keyword in text:
            matched = industry
            break

    hard_tags = [HardTag(value=matched or "未识别")]
    return CompanyTags(
        company_name=company_name,
        stock_code=stock_code,
        hard_tags=hard_tags,
        financial_profile=None,
    )
