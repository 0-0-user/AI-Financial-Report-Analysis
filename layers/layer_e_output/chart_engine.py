"""E2层: 图表引擎——matplotlib 绘制8张独立折线图

为模块0 (宏观事实) 生成 8 张 PNG 折线图: 
横轴=年份，5条折线=目标公司+top5同行，独立图片。
"""

import logging
import os
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

# ── 8 张图的配置 (列名=东方财富 API 英文缩写) ──
CHART_CONFIGS = [
    {
        "name": "销售毛利率",
        "column": "XSMLL",
        "filename": "chart_01_gross_margin.png",
        "unit": "%",
    },
    {
        "name": "销售净利率",
        "column": "XSJLL",
        "filename": "chart_02_net_profit_margin.png",
        "unit": "%",
    },
    {
        "name": "总资产周转率",
        "column": "TOAZZL",
        "filename": "chart_03_asset_turnover.png",
        "unit": "次",
    },
    {
        "name": "资产负债率",
        "column": "ZCFZL",
        "filename": "chart_04_debt_ratio.png",
        "unit": "%",
    },
    {
        "name": "主营业务收入增长率",
        "column": "YYZSRGDHBZC",
        "filename": "chart_05_revenue_growth.png",
        "unit": "%",
    },
    {
        "name": "经营现金流与净利润比",
        "column": "NCO_NETPROFIT",
        "filename": "chart_06_cash_to_profit.png",
        "unit": "",
    },
    {
        "name": "净资产收益率(ROE)",
        "column": "ROEJQ",
        "filename": "chart_07_roe.png",
        "unit": "%",
    },
    {
        "name": "流动比率",
        "column": "LD",
        "filename": "chart_08_current_ratio.png",
        "unit": "",
    },
]

# 目标公司使用粗红线，同行使用灰色系
_TARGET_COLOR = "#E74C3C"     # 醒目的红
_PEER_COLORS = ["#5D6D7E", "#85929E", "#AEB6BF", "#B2BABB", "#D5D8DC"]


def _setup_chinese_font():
    """尝试设置 matplotlib 中文字体"""
    import matplotlib.pyplot as plt
    import matplotlib
    try:
        # Windows 系统字体
        font_paths = [
            "C:/Windows/Fonts/msyh.ttc",       # 微软雅黑
            "C:/Windows/Fonts/simhei.ttf",      # 黑体
            "C:/Windows/Fonts/simsun.ttc",      # 宋体
        ]
        for fp in font_paths:
            if os.path.exists(fp):
                matplotlib.font_manager.fontManager.addfont(fp)
                plt.rcParams["font.family"] = matplotlib.font_manager.FontProperties(fname=fp).get_name()
                plt.rcParams["axes.unicode_minus"] = False
                logger.info(f"使用中文字体: {fp}")
                return
        # 回退: 让 matplotlib 自动找中文字体
        plt.rcParams["font.sans-serif"] = ["Microsoft YaHei", "SimHei", "SimSun", "WenQuanYi Micro Hei"]
        plt.rcParams["axes.unicode_minus"] = False
    except Exception as e:
        logger.warning(f"设置中文字体失败，图表中文可能显示异常: {e}")


def _draw_single_chart(
    years: list[str],
    target_code: str,
    target_values: list[Optional[float]],
    peer_data: list[dict],  # [{"code": ..., "name": ..., "values": [...]}, ...]
    company_names: dict[str, str],
    chart_cfg: dict,
    output_dir: Path,
) -> Optional[str]:
    """绘制一张折线图，保存为 PNG，返回文件名"""
    import matplotlib.pyplot as plt
    import matplotlib.ticker as ticker

    fig, ax = plt.subplots(figsize=(10, 5.5))

    # 绘制目标公司折线 (粗红) 
    valid_x = [i for i, v in enumerate(target_values) if v is not None]
    valid_y = [target_values[i] for i in valid_x]
    valid_year_labels = [years[i] for i in valid_x]
    target_name = company_names.get(target_code, target_code)

    ax.plot(
        valid_year_labels, valid_y,
        color=_TARGET_COLOR, linewidth=2.5, marker="o", markersize=6,
        label=f"★ {target_name}", zorder=5,
    )

    # 绘制同行折线 (灰色系) 
    for idx, peer in enumerate(peer_data):
        pv = peer["values"]
        valid_px = [i for i, v in enumerate(pv) if v is not None]
        valid_py = [pv[i] for i in valid_px]
        if not valid_py:
            continue
        color = _PEER_COLORS[idx % len(_PEER_COLORS)]
        pname = company_names.get(peer["code"], peer["code"])
        ax.plot(
            [years[i] for i in valid_px], valid_py,
            color=color, linewidth=1.5, marker=".", markersize=4,
            label=pname, alpha=0.8,
        )

    # 标题和标签
    unit_suffix = f" ({chart_cfg['unit']})" if chart_cfg["unit"] else ""
    ax.set_title(chart_cfg["name"], fontsize=14, fontweight="bold", pad=12)
    ax.set_ylabel(chart_cfg["name"] + unit_suffix, fontsize=11)
    ax.set_xlabel("年份", fontsize=11)

    # 图例
    ax.legend(loc="best", fontsize=9, framealpha=0.8)

    # 网格
    ax.grid(True, linestyle="--", alpha=0.3)
    ax.set_axisbelow(True)

    # 紧凑布局
    fig.tight_layout()

    # 保存
    output_path = output_dir / chart_cfg["filename"]
    fig.savefig(output_path, dpi=150, bbox_inches="tight")
    plt.close(fig)
    logger.info(f"  已生成: {output_path}")
    return chart_cfg["filename"]


def generate_all_charts(
    multi_year_data: dict[str, list[dict]],
    company_names: dict[str, str],
    target_stock_code: str,
    output_dir: str | Path = "data/outputs/charts",
) -> list[dict]:
    """生成全部 8 张折线图

    Args:
        multi_year_data: {stock_code: [{year, col1, col2, ...}, ...]}
        company_names: {stock_code: company_name}
        target_stock_code: 目标公司股票代码
        output_dir: 输出目录

    Returns:
        [{"name": "销售毛利率", "filename": "chart_01_...png", "column": "..."}, ...]
    """
    import matplotlib
    matplotlib.use("Agg")  # 非交互模式
    _setup_chinese_font()

    output_path = Path(output_dir)
    output_path.mkdir(parents=True, exist_ok=True)

    # 构建 {stock_code: {year: {col: val}}} 索引
    indexed: dict[str, dict[str, dict]] = {}
    for code, records in multi_year_data.items():
        indexed[code] = {rec.get("year", ""): rec for rec in records if "year" in rec}

    # 收集所有可用年份 (按年份排序) 
    all_years: set[str] = set()
    for code, year_data in indexed.items():
        all_years.update(year_data.keys())
    sorted_years = sorted(all_years)

    if not sorted_years:
        logger.warning("无可用年份数据，跳过图表生成")
        return []

    # 确定目标公司 + top5 同行 (multi_year_data 中除 target 外的前5个) 
    peer_codes = [c for c in multi_year_data if c != target_stock_code][:5]

    result = []
    for cfg in CHART_CONFIGS:
        col = cfg["column"]

        # 提取目标公司值
        target_values: list[Optional[float]] = []
        for yr in sorted_years:
            rec = indexed.get(target_stock_code, {}).get(yr, {})
            target_values.append(rec.get(col))

        # 提取同行值
        peer_data = []
        for pc in peer_codes:
            pv = []
            for yr in sorted_years:
                rec = indexed.get(pc, {}).get(yr, {})
                pv.append(rec.get(col))
            peer_data.append({"code": pc, "values": pv})

        # 如果有足够数据则绘图
        has_target = any(v is not None for v in target_values)
        has_peer = any(
            any(v is not None for v in p["values"])
            for p in peer_data
        )
        if not has_target and not has_peer:
            logger.debug(f"  跳过 {cfg['name']}: 无数据")
            continue

        fname = _draw_single_chart(
            years=sorted_years,
            target_code=target_stock_code,
            target_values=target_values,
            peer_data=peer_data,
            company_names=company_names,
            chart_cfg=cfg,
            output_dir=output_path,
        )
        if fname:
            result.append({
                "name": cfg["name"],
                "column": col,
                "filename": fname,
            })

    logger.info(f"图表生成完成: {len(result)}/8 张")
    return result
