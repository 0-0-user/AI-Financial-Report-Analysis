"""A1层：语义提取与打标签——LLM 给公司打定性标签

职责：
- 读取年报 JSON 中的公司基本情况和业务描述
- 调用 LLM 打硬标签（行业分类）+ 软标签（商业模式特征）
- 硬标签支持多套分类体系（同花顺/申万/证监会/GICS）
- 软标签从 config/industry_tags.yaml 的枚举选项中选取

LLM 不可用时的降级策略：
- 基于关键词规则进行基础标签推断（不依赖外部 API）
"""

from typing import Optional
import logging
import yaml
from pathlib import Path

from schemas.tags import CompanyTags, HardTag, SoftTag
from schemas.raw_doc import RawDocument

logger = logging.getLogger(__name__)

# 加载软标签定义（用于构建 prompt 和降级匹配）
_TAGS_CONFIG_PATH = Path("config/industry_tags.yaml")

# ────────────────────────────────────────────
# 同花顺行业分类 CSV 查表（硬标签优先路径）
# ────────────────────────────────────────────

_INDUSTRY_CSV_PATH = Path("data/industry/thf_industry_classification.csv")
_INDUSTRY_CACHE: dict[str, dict] | None = None  # {stock_code: row}
_NAME_INDEX: list[tuple[str, dict]] | None = None


def _load_industry_csv() -> dict[str, dict]:
    """惰性加载同花顺行业分类 CSV，返回 {stock_code_norm: row_dict} 字典

    股票代码标准化：去除 .SH/.SZ/.BJ 后缀
    加载时做校验：列名正确 + 行数 > 5000
    """
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
        # 标准化：600519.SH → 600519
        code_norm = code.split(".")[0]
        entry = {
            "code": code,
            "name": row["股票简称"].strip(),
            "level1": row["所属同花顺一级行业"].strip(),
            "level2": row["所属同花顺二级行业"].strip(),
            "level3": row["所属同花顺三级行业"].strip(),
        }
        cache[code_norm] = entry
        cache[code] = entry  # 同时保留原始带后缀的 key
        name_index.append((entry["name"], entry))
        row_count += 1

    if row_count < 5000:
        logger.warning(f"行业分类 CSV 行数异常: {row_count}（期望 > 5000），可能被截断")

    _INDUSTRY_CACHE = cache
    _NAME_INDEX = name_index
    logger.info(f"已加载行业分类 CSV: {row_count} 只股票, {len(set(e['level3'] for e in cache.values()))} 个三级行业")
    return cache


def _csv_lookup_by_code(stock_code: str) -> dict | None:
    """按股票代码精确匹配（代码优先路径）

    支持入参格式：
    - "600519" → 匹配 CSV 中的 600519.SH
    - "600519.SH" → 匹配 CSV 中的 600519.SH
    """
    if not stock_code:
        return None
    cache = _load_industry_csv()
    return cache.get(stock_code.strip())


def _csv_lookup_by_name(company_name: str) -> dict | None:
    """按公司名模糊匹配（股票代码缺失时的备选路径）

    匹配策略：CSV 中的股票简称 是否 包含在公司全称中
    例如 company_name="贵州茅台酒股份有限公司" → 匹配简称"贵州茅台"
    """
    if not company_name or _NAME_INDEX is None:
        return None
    for short_name, entry in _NAME_INDEX:
        if short_name in company_name or company_name in short_name:
            return entry
    return None


def _csv_lookup_hard_tags(stock_code: str, company_name: str) -> list[HardTag] | None:
    """CSV 查表获取硬标签，返回三级+二级+一级的 HardTag 列表

    优先级：股票代码精确 → 公司名模糊
    """
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


def _soft_tags_to_str(hard_tags: list[HardTag]) -> str:
    """将 HardTag 列表转为简短的描述文字，用于 prompt 变量"""
    return ", ".join(f"{t.system}={t.value}" for t in hard_tags)


def _load_soft_tags_config() -> list[dict]:
    """加载软标签配置"""
    if not _TAGS_CONFIG_PATH.exists():
        return []
    with open(_TAGS_CONFIG_PATH, encoding="utf-8") as f:
        config = yaml.safe_load(f)
    return config.get("soft_tags", [])


def run_tagging(raw_doc: RawDocument) -> CompanyTags:
    """读取公司基本信息和行业背景，输出硬标签 + 软标签

    输入：
        raw_doc: 第0层的 RawDocument

    流程：
        1. 优先从同花顺行业分类 CSV 查表获取硬标签（代码路径）
        2. CSV 查到硬标签 → 只问 LLM 软标签（减少 token 消耗 + 消除硬标签幻觉）
        3. CSV 查不到 → LLM 同时输出硬标签 + 软标签（原路径）
        4. LLM 也不可用 → 关键词规则降级（原路径）

    输出：
        CompanyTags（含公司名、股票代码、硬标签列表、软标签列表）
    """
    company_name = raw_doc.metadata.get("company_name", "")
    stock_code = raw_doc.metadata.get("stock_code", "")
    business_desc = raw_doc.company_overview.business_description or ""
    industry_text = raw_doc.company_overview.industry_classification or ""
    full_text = f"{company_name}\n{industry_text}\n{business_desc}"

    # 第一步：CSV 查硬标签（优先路径，最准确）
    csv_hard_tags = _csv_lookup_hard_tags(stock_code, company_name)
    if csv_hard_tags:
        logger.info(f"CSV 查到硬标签: {_soft_tags_to_str(csv_hard_tags)}")
        try:
            soft_tags = _llm_tagging(
                company_name, stock_code, business_desc,
                existing_hard_tags=csv_hard_tags,
            )
            return CompanyTags(
                company_name=company_name,
                stock_code=stock_code,
                hard_tags=csv_hard_tags,
                soft_tags=soft_tags,
            )
        except Exception as e:
            logger.warning(f"CSV 查表后 LLM 软标签失败，用关键词降级: {e}")
            soft_tags = _infer_soft_tags(full_text)
            return CompanyTags(
                company_name=company_name,
                stock_code=stock_code,
                hard_tags=csv_hard_tags,
                soft_tags=soft_tags,
            )

    # 第二步：CSV 查不到 → LLM 硬标签 + 软标签（原完整路径）
    try:
        return _llm_tagging(company_name, stock_code, business_desc)
    except Exception as e:
        logger.warning(f"LLM 打标签失败，回退到规则匹配: {e}")
        return _rule_based_tagging(company_name, stock_code, full_text)


# ────────────────────────────────────────────
# LLM 路径
# ────────────────────────────────────────────

def _llm_tagging(
    company_name: str,
    stock_code: str,
    business_desc: str,
    existing_hard_tags: list[HardTag] | None = None,
) -> CompanyTags | list[SoftTag]:
    """通过 LLM 进行标签分类

    两种模式：
    - existing_hard_tags=None（默认）：LLM 输出硬标签 + 软标签，返回 CompanyTags
    - existing_hard_tags 有值：硬标签已由 CSV 确定，LLM 只输出软标签，返回 list[SoftTag]
    """
    from llm.client import LLMClient

    soft_tags_config = _load_soft_tags_config()
    soft_tags_for_prompt = [{"name": t["name"], "options": t["options"]} for t in soft_tags_config]

    variables = {
        "company_name": company_name,
        "business_description": business_desc,
        "soft_tags": soft_tags_for_prompt,
    }

    # 如果已有硬标签（来自 CSV），传给 prompt 让 LLM 只输出软标签
    if existing_hard_tags:
        variables["existing_hard_tags"] = _soft_tags_to_str(existing_hard_tags)

    client = LLMClient()
    response = client.chat("a1_tagging", variables)

    import json
    data = _extract_json(response if isinstance(response, str) else str(response))

    # 模式1：CSV 已提供硬标签 → 只解析软标签
    if existing_hard_tags:
        soft_tag_list = []
        for st in data.get("soft_tags", []):
            soft_tag_list.append(SoftTag(dimension=st["dimension"], value=st["value"]))
        return soft_tag_list

    # 模式2：LLM 同时输出硬标签 + 软标签
    hard_tag_data = data.get("hard_tag", {})
    hard_tags = [HardTag(
        system=hard_tag_data.get("system", "同花顺三级行业"),
        value=hard_tag_data.get("value", ""),
    )]

    extra_hard_tags = data.get("extra_hard_tags", [])
    for ht in extra_hard_tags:
        hard_tags.append(HardTag(system=ht.get("system", ""), value=ht.get("value", "")))

    soft_tag_list = []
    for st in data.get("soft_tags", []):
        soft_tag_list.append(SoftTag(dimension=st["dimension"], value=st["value"]))

    return CompanyTags(
        company_name=company_name,
        stock_code=stock_code,
        hard_tags=hard_tags,
        soft_tags=soft_tag_list,
    )


def _extract_json(text: str) -> dict:
    """从 LLM 返回文本中提取 JSON（处理 markdown fence、单引号等）"""
    import json
    import re

    text = text.strip()
    # 去掉 ```json ... ``` 包裹
    fence_match = re.search(r'```(?:json)?\s*([\s\S]*?)\s*```', text)
    if fence_match:
        text = fence_match.group(1)
    # 尝试找最外层的 {...}
    brace_match = re.search(r'\{[\s\S]*\}', text)
    if brace_match:
        text = brace_match.group(0)
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        # 单引号 → 双引号（常见于某些 LLM 输出）
        try:
            return json.loads(text.replace("'", '"'))
        except json.JSONDecodeError:
            logger.warning(f"无法解析 LLM 输出的 JSON: {text[:200]}")
            return {}


# ────────────────────────────────────────────
# 规则匹配降级路径
# ────────────────────────────────────────────

# 关键词 → 硬标签映射（基于同花顺三级行业）
_KEYWORD_HARD_TAG_MAP: dict[str, str] = {
    # 食品饮料
    "白酒": "白酒", "啤酒": "啤酒", "红酒": "其他酒类", "葡萄酒": "其他酒类",
    "乳制品": "乳制品", "乳业": "乳制品", "牛奶": "乳制品",
    "酱油": "调味品", "调味品": "调味品", "醋": "调味品", "味精": "调味品",
    "休闲食品": "休闲食品", "零食": "休闲食品", "坚果": "休闲食品",
    "速冻": "速冻食品", "预制菜": "预制菜",
    # 新能源
    "光伏": "光伏制造设备", "太阳能": "光伏发电", "硅片": "光伏制造设备", "电池片": "光伏制造设备",
    "锂电池": "锂电池", "锂电": "锂电池", "正极材料": "锂电池", "负极材料": "锂电池", "电解液": "锂电池",
    "新能源汽车": "新能源汽车", "电动车": "新能源汽车", "新势力": "新能源汽车",
    "储能": "储能", "逆变器": "光伏制造设备",
    # 半导体
    "芯片设计": "半导体设计", "IC设计": "半导体设计", "集成电路设计": "半导体设计",
    "晶圆": "半导体制造", "芯片制造": "半导体制造", "代工": "半导体制造", "fab": "半导体制造",
    "封装测试": "半导体封测", "封测": "半导体封测", "先进封装": "半导体封测",
    "半导体设备": "半导体设备", "光刻": "半导体设备", "刻蚀": "半导体设备",
    # 医药
    "创新药": "化学制药", "仿制药": "化学制药", "制药": "化学制药",
    "中药": "中药", "中成药": "中药", "中药材": "中药",
    "CXO": "CXO/医药外包", "CRO": "CXO/医药外包", "CDMO": "CXO/医药外包", "医药外包": "CXO/医药外包",
    "医疗器械": "医疗器械", "医疗设备": "医疗器械", "耗材": "医疗器械", "体外诊断": "医疗器械",
    "疫苗": "生物制品", "血制品": "生物制品", "生物药": "生物制品", "抗体": "生物制品",
    # 金融
    "证券": "证券", "券商": "证券", "投行": "证券",
    "保险": "保险", "寿险": "保险", "财险": "保险",
    "银行": "银行", "城商行": "城商行", "农商行": "城商行",
    # 消费电子
    "手机": "消费电子", "智能穿戴": "消费电子", "耳机": "消费电子",
    # 互联网/软件
    "云计算": "云计算", "云服务": "云计算", "SaaS": "云计算",
    "网络安全": "网络安全", "信息安全": "网络安全",
    "游戏": "游戏", "手游": "游戏", "电竞": "游戏",
    "广告": "广告营销", "营销": "广告营销",
    # 军工
    "军工": "军工电子", "航空": "航空装备", "航天": "航空装备", "导弹": "军工电子",
    # 能源
    "煤炭": "煤炭开采", "煤矿": "煤炭开采",
    "石油": "石油开采", "天然气": "石油开采", "油田": "石油开采",
    "火电": "火电", "水电": "水电", "核电": "核电", "风电": "风力发电",
    # 养殖
    "生猪": "生猪养殖", "养猪": "生猪养殖", "猪": "生猪养殖",
    "饲料": "饲料", "鸡": "白羽肉鸡", "肉鸡": "白羽肉鸡",
    # 地产
    "房地产": "房地产开发", "地产开发": "房地产开发", "住宅": "房地产开发",
    "物业": "物业管理", "物业服务": "物业管理",
    # 交通
    "航运": "航运", "海运": "航运", "集装箱": "航运",
    "快递": "快递", "物流": "快递",
    "高速公路": "高速公路", "铁路": "铁路运输", "机场": "机场",
    # 消费
    "旅游": "旅游景区", "景区": "旅游景区", "酒店": "酒店",
    "免税": "免税", "运动": "运动服饰", "服饰": "运动服饰", "家纺": "家纺",
    "化妆品": "化妆品", "医美": "医美服务",
    # 教育
    "教育": "教育培训", "培训": "教育培训", "学校": "教育培训",
}

# 软标签各维度的关键词映射
_SOFT_TAG_KEYWORD_MAP: dict[str, dict[str, list[str]]] = {
    "资产结构": {
        "重资产": ["重资产", "固定资产", "产能", "生产线", "工厂", "矿山", "钻井", "船舶", "高炉", "设备密集"],
        "轻资产": ["轻资产", "轻量化", "品牌", "渠道", "IP", "知识产权", "研发驱动", "互联网", "平台"],
    },
    "毛利率水平": {
        "高毛利": ["毛利率", "高毛利", "高附加值", "差异化", "技术壁垒", "品牌溢价"],
        "低毛利": ["低毛利", "成本控制", "薄利多销", "规模化", "大宗商品", "原材料"],
    },
    "客户类型": {
        "To-B": ["企业客户", "B端", "企业级", "产业客户", "B2B", "供应链", "配套", "工业"],
        "To-C": ["消费者", "C端", "零售", "个人", "品牌", "B2C", "直营", "电商", "门店"],
        "To-G": ["政府", "国企", "央企", "政府采购", "公共事业", "政务", "军工", "国防"],
    },
    "竞争壁垒": {
        "品牌护城河": ["品牌", "百年", "老字号", "高端", "奢侈", "名酒", "消费心智"],
        "技术壁垒": ["研发", "专利", "技术领先", "know-how", "稀缺", "独家", "仿制壁垒"],
        "规模效应": ["规模", "产能", "市占率", "集中度", "龙头", "份额"],
        "特许经营": ["牌照", "特许", "专营", "许可", "资质", "审批", "配额"],
        "成本优势": ["成本", "低成本", "一体化", "资源禀赋", "地理位置"],
        "网络效应": ["平台", "双边", "多边", "网络", "生态", "用户规模", "数据"],
    },
    "现金流特征": {
        "奶牛型": ["现金流", "稳定", "分红", "日常消费", "公用事业"],
        "烧钱型": ["亏损", "研发投入", "资本开支", "扩张", "补贴", "投入期"],
        "周期波动型": ["周期", "波动", "大宗", "供需", "价格波动"],
    },
    "研发强度": {
        "高研发": ["研发", "创新", "博士后", "实验室", "研究院"],
        "低研发": ["传统", "成熟工艺", "稳定生产"],
    },
    "商业模式": {
        "产品型": ["产品", "制造", "生产", "销售"],
        "平台型": ["平台", "撮合", "交易", "连接", "匹配", "双边市场"],
        "项目型": ["工程", "项目", "定制", "合同", "EPC", "交付"],
        "服务型": ["SaaS", "订阅", "续费", "服务", "咨询", "外包"],
    },
    "受政策影响度": {
        "强政策驱动": ["政策", "补贴", "监管", "审批", "牌照", "配额", "双碳", "环保"],
        "政策不敏感": ["消费品", "日用品", "零售", "服务", "可选消费"],
    },
    "周期性": {
        "强周期": ["周期", "大宗商品", "原材料", "航运", "钢铁", "煤炭", "化工"],
        "抗周期/防御型": ["消费", "医药", "必需", "公用事业", "食品", "饮料"],
    },
}


def _rule_based_tagging(company_name: str, stock_code: str, text: str) -> CompanyTags:
    """基于关键词规则推断标签（不依赖 LLM 的降级方案）"""
    hard_tags = _infer_hard_tags(text)
    soft_tags = _infer_soft_tags(text)

    return CompanyTags(
        company_name=company_name,
        stock_code=stock_code,
        hard_tags=hard_tags,
        soft_tags=soft_tags,
    )


def _infer_hard_tags(text: str) -> list[HardTag]:
    """从文本中基于关键词推断硬标签"""
    matched_industry = None
    for keyword, industry in _KEYWORD_HARD_TAG_MAP.items():
        if keyword in text:
            matched_industry = industry
            break

    if matched_industry:
        return [HardTag(system="同花顺三级行业", value=matched_industry)]
    return [HardTag(system="同花顺三级行业", value="未识别")]


def _infer_soft_tags(text: str) -> list[SoftTag]:
    """从文本中基于关键词推断软标签"""
    tags = []
    for dimension, value_map in _SOFT_TAG_KEYWORD_MAP.items():
        for value, keywords in value_map.items():
            if any(kw in text for kw in keywords):
                tags.append(SoftTag(dimension=dimension, value=value))
                break  # 一个维度只取第一个匹配值
    return tags
