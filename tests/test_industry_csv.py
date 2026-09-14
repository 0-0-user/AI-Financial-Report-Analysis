"""行业分类 CSV 的数据完整性测试

这份 CSV 是 A1(硬标签) / A2(同行池) / B+(行业阈值) 三层共同的地基。
它出问题不会报错，只会让下游悄悄降级 —— 所以这里用一组不依赖网络的断言
把它钉死：

- 列名必须说真话 (数据源是申万, 不是同花顺)
- 每家公司**恰好一行**, 股票代码不得重复
- 层级不得坍缩 (有三级必须有二级和一级)
- 每条 (一级,二级,三级) 必须是申万树里真实存在的祖先链
  —— 对照物是 tests/fixtures/sw_tree_snapshot.json 这份冻结的树快照,
     而不是重新跑一遍生成脚本 (否则就是拿代码证明代码)

关于"每家公司恰好一行": 旧表是 **5615 家公司 × 每家 3 行**, 每行装的是
同一家公司的不同层级 (宁德时代的三行分别是 锂电池 / 电池 / 电力设备)。
A1 按 `cache[股票代码] = entry` 建索引, 后写覆盖先写, 于是拿到哪一级
完全取决于行序 —— 一个纯靠运气的静默降级。所以新版必须一家一行。

树快照来源: akshare stock_industry_category_cninfo("申银万国行业分类标准")
"""

import csv
import io
import json
from collections import Counter, defaultdict
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parent.parent
_CSV_PATH = _REPO_ROOT / "data" / "industry" / "sw_industry_classification.csv"
_TREE_SNAPSHOT = Path(__file__).resolve().parent / "fixtures" / "sw_tree_snapshot.json"

# 迁移前有 5615 家不同的公司。只增不减 —— 少一家就意味着有公司被丢掉了。
_MIN_COMPANIES = 5615

# 申万树按名字建索引。512 个节点名字互不重复, 所以名字可以唯一定位。
def _load_tree() -> dict[str, dict]:
    nodes = json.loads(_TREE_SNAPSHOT.read_text(encoding="utf-8"))
    return {n["类目名称"].strip(): n for n in nodes}


def _load_rows() -> list[dict]:
    if not _CSV_PATH.exists():
        pytest.fail(f"行业分类 CSV 不存在: {_CSV_PATH}")
    text = _CSV_PATH.read_bytes().decode("utf-8-sig")
    return list(csv.DictReader(io.StringIO(text)))


@pytest.fixture(scope="module")
def rows() -> list[dict]:
    return _load_rows()


@pytest.fixture(scope="module")
def tree() -> dict[str, dict]:
    return _load_tree()


class TestHeader:
    def test_columns_say_shenwan_not_tonghuashun(self, rows):
        """列名必须如实反映数据来源。"""
        with open(_CSV_PATH, "rb") as f:
            header = f.readline().decode("utf-8-sig").strip()

        assert "同花顺" not in header, (
            f"列名里仍有「同花顺」: {header}\n"
            f"数据实际是申万分类, 文件名和列名都必须说实话"
        )
        assert "申万" in header

    def test_has_all_three_level_columns(self, rows):
        assert rows, "CSV 为空"
        assert set(rows[0].keys()) >= {
            "股票代码",
            "股票简称",
            "所属申万一级行业",
            "所属申万二级行业",
            "所属申万三级行业",
        }


class TestOneRowPerCompany:
    def test_stock_code_is_unique(self, rows):
        """每家公司恰好一行。

        旧表每家三行、A1 后写覆盖先写, 拿到哪一级纯看行序。一份查表用的
        数据里出现重复主键, 就等于把"结果取决于顺序"写进了数据本身。
        """
        counts = Counter(r["股票代码"].strip() for r in rows)
        dupes = {k: v for k, v in counts.items() if v > 1}
        assert not dupes, f"{len(dupes)} 个股票代码重复出现, 例: {list(dupes)[:5]}"

    def test_company_count_does_not_shrink(self, rows):
        """迁移不得丢公司。"""
        assert len(rows) >= _MIN_COMPANIES, (
            f"迁移前有 {_MIN_COMPANIES} 家公司, 现在只有 {len(rows)}"
        )


class TestCoverage:
    def test_level1_is_essentially_complete(self, rows):
        """一级行业是最后一道防线, 必须几乎全覆盖。"""
        filled = sum(1 for r in rows if r["所属申万一级行业"].strip())
        ratio = filled / len(rows)
        assert ratio > 0.999, (
            f"一级行业覆盖率仅 {ratio:.2%} ({filled}/{len(rows)})。"
            f"一级是自适应选池的最后兜底, 它空掉下游就没法比了"
        )

    def test_full_three_level_chain_for_almost_everyone(self, rows):
        """99% 以上的公司应当拿到完整三级链。

        旧表里那 5615 家公司的三级链其实是齐的 —— 只是被摊成了三行。
        按股票代码聚合、取最具体的那个值、再从树里补祖先, 就都回来了。
        覆盖率掉下来意味着聚合逻辑退化了。
        """
        l3 = sum(1 for r in rows if r["所属申万三级行业"].strip()) / len(rows)
        assert l3 > 0.99, (
            f"三级行业覆盖率仅 {l3:.2%}。旧表按公司聚合后应有 99% 以上, "
            f"掉下来说明没有正确合并同一公司的多行"
        )


class TestHierarchy:
    def test_no_level_collapse(self, rows):
        """有细的就必须有粗的 —— 否则下游按细一级查池子会查空。"""
        bad = [
            r for r in rows
            if (r["所属申万三级行业"].strip() and not r["所属申万二级行业"].strip())
            or (r["所属申万二级行业"].strip() and not r["所属申万一级行业"].strip())
        ]
        assert not bad, f"{len(bad)} 行层级坍缩, 例: {bad[:3]}"

    def test_each_level3_maps_to_a_single_parent_chain(self, rows):
        """同一个三级名不得挂到两个不同的二级下 —— 这是映射出错的强信号。"""
        parents: dict[str, set[tuple[str, str]]] = defaultdict(set)
        for r in rows:
            l3 = r["所属申万三级行业"].strip()
            if l3:
                parents[l3].add(
                    (r["所属申万一级行业"].strip(), r["所属申万二级行业"].strip())
                )
        ambiguous = {k: v for k, v in parents.items() if len(v) > 1}
        assert not ambiguous, f"三级行业挂到了多个父链: {ambiguous}"

    def test_each_level2_maps_to_a_single_level1(self, rows):
        parents: dict[str, set[str]] = defaultdict(set)
        for r in rows:
            l2 = r["所属申万二级行业"].strip()
            if l2:
                parents[l2].add(r["所属申万一级行业"].strip())
        ambiguous = {k: v for k, v in parents.items() if len(v) > 1}
        assert not ambiguous, f"二级行业挂到了多个一级: {ambiguous}"


class TestChainIsRealInTheTree:
    """每条 (一级,二级,三级) 必须是申万树里真实存在的祖先链。

    对照物是冻结的树快照。测试不重跑生成逻辑, 所以它能抓到
    「生成脚本自洽但整体错位」这类 bug。
    """

    def test_chain_matches_tree(self, rows, tree):
        violations = []
        for r in rows:
            l1 = r["所属申万一级行业"].strip()
            l2 = r["所属申万二级行业"].strip()
            l3 = r["所属申万三级行业"].strip()
            if not l1:
                continue

            node = tree.get(l1)
            if node is None or node["分级"] != 1:
                violations.append((r["股票代码"], l1, "一级名不是树里的一级节点"))
                continue

            if l2:
                n2 = tree.get(l2)
                if n2 is None:
                    violations.append((r["股票代码"], l2, "树里没有这个二级名"))
                elif n2["分级"] != 2:
                    violations.append((r["股票代码"], l2, f"分级={n2['分级']} 不是二级"))
                elif n2["父类编码"] != node["类目编码"]:
                    violations.append((r["股票代码"], l2, f"父类不是 {l1}"))

            if l3 and l2:
                n3 = tree.get(l3)
                n2 = tree.get(l2)
                if n3 is None:
                    violations.append((r["股票代码"], l3, "树里没有这个三级名"))
                elif n3["分级"] != 3:
                    violations.append((r["股票代码"], l3, f"分级={n3['分级']} 不是三级"))
                elif n2 is not None and n3["父类编码"] != n2["类目编码"]:
                    violations.append((r["股票代码"], l3, f"父类不是 {l2}"))

        assert not violations, (
            f"{len(violations)} 行的行业链与申万树不符, 例: {violations[:5]}"
        )


class TestUnresolvedIsHonest:
    def test_unresolved_rows_are_rare_and_left_empty(self, rows):
        """推不出层级的公司必须留空, 不许拿猜测填上。

        旧表里只有 2 个行业名不在申万树中 (光伏主材 / 端到端供应链服务,
        共 9 处)。而这些公司还有别的可解析的层级, 所以实际无归属的公司更少。
        """
        unresolved = [
            r for r in rows
            if not r["所属申万一级行业"].strip()
            and not r["所属申万二级行业"].strip()
            and not r["所属申万三级行业"].strip()
        ]
        assert len(unresolved) <= 5, (
            f"{len(unresolved)} 家公司完全无法归属, 超出预期, 迁移逻辑可能退化了"
        )
