"""从 akshare 在线构建同花顺行业分类 CSV

用法: python scripts/build_industry_csv.py
输出: data/industry/thf_industry_classification.csv

数据来源: akshare stock_board_industry_name_em() + stock_board_industry_cons_em()
覆盖: 约 500 个板块，5000+ 只 A 股
"""

import csv
import logging
import sys
import time
from pathlib import Path

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
logger = logging.getLogger(__name__)

ROOT = Path(__file__).parent.parent
OUTPUT_PATH = ROOT / "data/industry/thf_industry_classification.csv"


def main():
    try:
        import akshare as ak
        import pandas as pd
    except ImportError:
        logger.error("需要 akshare: pip install akshare")
        sys.exit(1)

    # 1. 获取所有行业板块列表
    logger.info("获取行业板块列表...")
    boards = ak.stock_board_industry_name_em()
    logger.info(f"共 {len(boards)} 个行业板块")

    # 2. 遍历每个板块获取成分股
    all_rows = []
    failed = 0
    for i, (_, row) in enumerate(boards.iterrows()):
        board_name = row["板块名称"]
        if (i + 1) % 50 == 0:
            logger.info(f"进度: {i+1}/{len(boards)} ({board_name}), 已收集 {len(all_rows)} 条")

        try:
            cons = ak.stock_board_industry_cons_em(symbol=board_name)
            if cons is None or cons.empty:
                failed += 1
                continue

            for _, stock in cons.iterrows():
                code = str(stock.get("代码", stock.get("股票代码", "")))
                name = str(stock.get("名称", stock.get("股票简称", "")))
                if not code or not name:
                    continue

                # 同花顺没有直接的三级分类，这里用板块名作为三级行业
                all_rows.append({
                    "股票代码": code,
                    "股票简称": name,
                    "所属同花顺一级行业": "",
                    "所属同花顺二级行业": "",
                    "所属同花顺三级行业": board_name,
                })
        except Exception:
            failed += 1
            continue

        time.sleep(1.5)  # 限速，避免被封

    # 3. 保存 CSV
    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(OUTPUT_PATH, "w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=[
            "股票代码", "股票简称",
            "所属同花顺一级行业", "所属同花顺二级行业", "所属同花顺三级行业",
        ])
        writer.writeheader()
        writer.writerows(all_rows)

    logger.info(f"完成: {len(all_rows)} 条记录, {len(boards) - failed}/{len(boards)} 板块成功")
    logger.info(f"输出: {OUTPUT_PATH}")


if __name__ == "__main__":
    main()
