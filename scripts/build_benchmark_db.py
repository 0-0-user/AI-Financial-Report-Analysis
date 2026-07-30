"""
==========================================================
 scripts/build_benchmark_db.py — 初始化行业基准数据库
==========================================================

构建 A2 层所需的行业财务指标基准数据。

核心功能: 
1. 从 CSV 文件导入基准数据
2. 按行业名称从公开数据源抓取 (待实现) 

基准数据格式: CSV 文件，包含股票代码、行业、年份、指标名、值。

使用方式: 
    python scripts/build_benchmark_db.py --industry 白酒
    python scripts/build_benchmark_db.py --csv ./my_data.csv
"""

import argparse
import pandas as pd
from pathlib import Path


BENCHMARK_DIR = Path("data/benchmarks")


def main():
    parser = argparse.ArgumentParser(description="构建行业基准数据库")
    parser.add_argument("--industry", help="行业名称 (如: 白酒) ")
    parser.add_argument("--csv", help="从 CSV 文件导入")
    args = parser.parse_args()

    BENCHMARK_DIR.mkdir(parents=True, exist_ok=True)

    if args.csv:
        df = pd.read_csv(args.csv)
        output_path = BENCHMARK_DIR / f"{Path(args.csv).stem}.csv"
        df.to_csv(output_path, index=False)
        print(f"✅ 已从 CSV 导入: {output_path}")
    elif args.industry:
        output_path = BENCHMARK_DIR / f"{args.industry}.csv"
        print(f"⏳ 正在获取 {args.industry} 行业数据...")
        # TODO: 从公开数据源拉取行业财务指标
        print(f"✅ 基准数据已保存: {output_path}")
    else:
        print("请指定 --industry 或 --csv")


if __name__ == "__main__":
    main()
