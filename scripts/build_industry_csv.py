"""构建申万行业分类 CSV (一次性迁移脚本)

═══════════════════════════════════════════════════════════════
旧表到底长什么样 —— 必须先搞清楚, 否则一定做错
═══════════════════════════════════════════════════════════════

旧表 `data/industry/thf_industry_classification.csv` 有 16843 行, 但它
**不是 16843 家公司**, 而是 **5615 家公司 × 每家 3 行**:
每行装的是同一家公司的**不同层级**。以宁德时代为例 ——

    300750,宁德时代,,,锂电池
    300750,宁德时代,,,电池
    300750,宁德时代,,,电力设备

一级/二级两列几乎全是空, 所有信息都挤在第三列里, 三个级别混在一起。
A1 层按 `cache[股票代码] = entry` 建索引, 后写覆盖先写 —— 于是「拿到哪一级」
完全取决于行序, 是一个纯靠运气的静默降级。

═══════════════════════════════════════════════════════════════
迁移算法
═══════════════════════════════════════════════════════════════

1. 按股票代码聚合, 收集该公司出现过的所有行业名
2. 每个名字去申万树里定位它自己是第几级
3. **取最具体的那个**(层级最大的) 作为该公司的定位点
4. L1/L2 沿树的 `父类编码` 往上走补齐 —— 祖先由树给出, 不需要旧表提供

第 4 步是关键: 旧表里 42 家银行同时有 `银行`(一级) 和 `银行Ⅱ`(无解),
但也有 `城商行Ⅲ`。从 `城商行Ⅲ` 沿树往上走直接得到
`银行 / 城商行Ⅱ / 城商行Ⅲ`, 那个无解的 `银行Ⅱ` 根本用不上。
按这个算法, 5615 家中有 5606 家能拿到完整三级链, 同级冲突为 0。

═══════════════════════════════════════════════════════════════
诚实边界
═══════════════════════════════════════════════════════════════

- **层级结构** (谁是谁的父/子) 来自 cninfo 申万行业分类标准, 通过 akshare
  `stock_industry_category_cninfo("申银万国行业分类标准")`: 512 个节点,
  带 `父类编码` 和 `分级`。
- **「哪只股票属于哪个行业」仍然来自本地旧表。** akshare 目前没有稳定可用的
  接口能按申万逐只列出成分股 —— `sw_index_third_cons` 在 1.18.60 已损坏
  (ValueError: Length mismatch: Expected axis has 18 elements...),
  `stock_industry_change_cninfo` 返回的也不是申万分类。
- **推不出的级别一律留空, 绝不编造。** 旧表里只有 2 个行业名不在树中
  (光伏主材 / 端到端供应链服务, 共 9 处), 这些公司在别的层级上仍可归属。
- **同级出现多个不同值时报冲突并留空那一级**, 而不是按行序挑一个。

用法: python scripts/build_industry_csv.py
"""

import csv
import io
import logging
import sys
import time
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from schemas.tags import strip_level_suffix

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
logger = logging.getLogger(__name__)

_REPO_ROOT = Path(__file__).resolve().parent.parent

_LEGACY_CSV = _REPO_ROOT / "data" / "industry" / "thf_industry_classification.csv"
_OUT_CSV = _REPO_ROOT / "data" / "industry" / "sw_industry_classification.csv"

_OUT_COLUMNS = [
    "股票代码",
    "股票简称",
    "所属申万一级行业",
    "所属申万二级行业",
    "所属申万三级行业",
]

# 树里用罗马数字区分同名不同级的节点 (林业Ⅱ / 林业Ⅲ)。旧表里混着带后缀的名字,
# 精确匹配失败时剥一次再试 (其他建材Ⅲ -> 其他建材, 银行Ⅱ -> 银行)。
# 规则本体在 schemas/tags.py, 与 B+ 查阈值表时用的是同一份。
_strip_roman = strip_level_suffix


def _fetch_tree(retries: int = 4) -> list[dict]:
    """从 cninfo 拉申万行业分类树。网络抖动时重试。"""
    import akshare as ak

    last_err: Exception | None = None
    for attempt in range(1, retries + 1):
        try:
            df = ak.stock_industry_category_cninfo("申银万国行业分类标准")
            rows = df.to_dict("records")
            if len(rows) < 100:
                raise ValueError(f"树只有 {len(rows)} 个节点, 明显不完整")
            logger.info(f"申万树获取成功: {len(rows)} 个节点 (第 {attempt} 次)")
            return rows
        except Exception as e:  # noqa: BLE001 - 网络/解析错误都要重试
            last_err = e
            logger.warning(f"申万树获取失败 (第 {attempt} 次): {e}")
            if attempt < retries:
                time.sleep(2 * attempt)
    raise RuntimeError(f"申万树获取失败, 已重试 {retries} 次") from last_err


def _build_indexes(tree: list[dict]) -> tuple[dict[str, dict], dict[str, dict]]:
    """建两个索引: 按名字 (定位用) 和按编码 (查父类用)。

    树里 512 个节点的名字互不重复。这里断言这一点 —— 若哪天不成立了,
    按名字定位会静默挑错节点。
    """
    by_name: dict[str, dict] = {}
    by_code: dict[str, dict] = {}
    for node in tree:
        name = str(node["类目名称"]).strip()
        code = str(node["类目编码"]).strip()
        if name in by_name:
            raise ValueError(f"申万树里出现重名节点: {name} —— 按名字定位不再可靠")
        by_name[name] = node
        by_code[code] = node
    return by_name, by_code


def _lookup(value: str, by_name: dict) -> dict | None:
    """行业名 -> 树节点。精确匹配失败时剥一次罗马数字后缀再试。"""
    if not value:
        return None
    node = by_name.get(value)
    if node is None:
        stripped = _strip_roman(value)
        if stripped != value:
            node = by_name.get(stripped)
    return node


def _ancestor_chain(node: dict, by_code: dict) -> tuple[str, str, str]:
    """沿父类编码往上走, 返回 (一级, 二级, 三级)。节点不在的级别就是空串。"""
    names: dict[int, str] = {}
    cur: dict | None = node
    seen: set[str] = set()
    while cur is not None:
        code = str(cur["类目编码"]).strip()
        if code in seen:  # 防御: 树里有环的话别转死
            break
        seen.add(code)
        level = int(cur["分级"])
        if level >= 1:
            names[level] = str(cur["类目名称"]).strip()
        cur = by_code.get(str(cur.get("父类编码") or "").strip())
    return names.get(1, ""), names.get(2, ""), names.get(3, "")


def _resolve_company(
    values: set[str], by_name: dict, by_code: dict
) -> tuple[str, str, str, list[str], list[str]]:
    """把一家公司出现过的所有行业名归并成 (一级, 二级, 三级)。

    返回 (l1, l2, l3, 无法解析的名字, 同级冲突的名字)。
    """
    nodes: list[dict] = []
    unmatched: list[str] = []
    for value in sorted(values):
        node = _lookup(value, by_name)
        if node is None:
            unmatched.append(value)
        else:
            nodes.append(node)

    if not nodes:
        return "", "", "", unmatched, []

    # 取最具体的那一级作为定位点
    top_level = max(int(n["分级"]) for n in nodes)
    tops = {str(n["类目名称"]).strip(): n for n in nodes if int(n["分级"]) == top_level}

    if len(tops) > 1:
        # 同一级别上出现多个不同名字 = 真冲突。祖先若一致就照填粗的两级,
        # 有争议的那一级留空 —— 不按行序挑一个。
        chains = {_ancestor_chain(n, by_code) for n in tops.values()}
        common = next(iter(chains)) if len(chains) == 1 else ("", "", "")
        return common[0], common[1], "", unmatched, sorted(tops)

    return (*_ancestor_chain(next(iter(tops.values())), by_code), unmatched, [])


def build(legacy_csv: Path = _LEGACY_CSV, out_csv: Path = _OUT_CSV) -> dict:
    if not legacy_csv.exists():
        raise FileNotFoundError(
            f"旧表不存在: {legacy_csv}\n"
            f"本脚本是一次性迁移, 需要旧表作为「哪只股票属于哪个行业」的来源。"
        )

    text = legacy_csv.read_bytes().decode("utf-8-sig")
    legacy_rows = list(csv.DictReader(io.StringIO(text)))
    if not legacy_rows:
        raise ValueError(f"旧表为空: {legacy_csv}")

    industry_cols = [c for c in legacy_rows[0].keys() if "行业" in c]
    if not industry_cols:
        raise ValueError(f"旧表里找不到行业列: {list(legacy_rows[0].keys())}")
    logger.info(
        f"旧表 {len(legacy_rows)} 行, 行业列: {industry_cols} (三列都读, 因为"
        f"同一家公司的不同层级散落在不同行里)"
    )

    # ── 按股票代码聚合, 收集该公司出现过的所有行业名 ──
    names_by_code: dict[str, set[str]] = defaultdict(set)
    display: dict[str, str] = {}
    for row in legacy_rows:
        code = (row.get("股票代码") or "").strip()
        if not code:
            continue
        display.setdefault(code, (row.get("股票简称") or "").strip())
        for col in industry_cols:
            value = (row.get(col) or "").strip()
            if value:
                names_by_code[code].add(value)

    if not names_by_code:
        raise ValueError("旧表里一行有效数据都没有")

    logger.info(
        f"聚合得到 {len(names_by_code)} 家公司 "
        f"(旧表 {len(legacy_rows)} 行 -> 平均 {len(legacy_rows)/len(names_by_code):.2f} 行/家)"
    )

    by_name, by_code = _build_indexes(_fetch_tree())

    out_rows: list[dict] = []
    unmatched_values: dict[str, int] = defaultdict(int)
    conflicts: list[tuple[str, list[str]]] = []
    level_counts = {1: 0, 2: 0, 3: 0}

    for code in sorted(names_by_code):
        l1, l2, l3, unmatched, conflict = _resolve_company(
            names_by_code[code], by_name, by_code
        )
        for value in unmatched:
            unmatched_values[value] += 1
        if conflict:
            conflicts.append((code, conflict))
        for level, name in ((1, l1), (2, l2), (3, l3)):
            if name:
                level_counts[level] += 1
        out_rows.append({
            "股票代码": code,
            "股票简称": display.get(code, ""),
            "所属申万一级行业": l1,
            "所属申万二级行业": l2,
            "所属申万三级行业": l3,
        })

    out_csv.parent.mkdir(parents=True, exist_ok=True)
    with open(out_csv, "w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=_OUT_COLUMNS)
        writer.writeheader()
        writer.writerows(out_rows)

    total = len(out_rows)
    logger.info(f"已写出 {out_csv} ({total} 家公司, 每家一行)")
    for level in (1, 2, 3):
        logger.info(
            f"  {level} 级覆盖: {level_counts[level]}/{total} = {level_counts[level]/total:.1%}"
        )
    if conflicts:
        detail = "; ".join(f"{c}:{v}" for c, v in conflicts[:5])
        logger.warning(f"  {len(conflicts)} 家公司同级出现冲突值 (该级已留空): {detail}")
    if unmatched_values:
        detail = ", ".join(f"{k}x{v}" for k, v in sorted(unmatched_values.items()))
        logger.warning(f"  无法在申万树中定位的行业名 (已跳过): {detail}")

    return {
        "out": str(out_csv),
        "companies": total,
        "level1": level_counts[1],
        "level2": level_counts[2],
        "level3": level_counts[3],
        "conflicts": len(conflicts),
        "unmatched_values": dict(unmatched_values),
    }


if __name__ == "__main__":
    sys.exit(0 if build() else 1)
