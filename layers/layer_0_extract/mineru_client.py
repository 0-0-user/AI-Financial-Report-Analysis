"""MinerU API 封装 — 调用 mineru-open-api CLI 提取 PDF

设计约束:
- 通过 CLI 调用（mineru-open-api.exe），非 Python SDK
- 支持批量提取（拆分后的 PDF 分块）
- 环境变量 MINERU_TOKEN 用于鉴权
"""

import json
import logging
import os
import subprocess
import tempfile
import time
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

# MinerU CLI 路径
DEFAULT_CLI_PATH = r"C:\Users\xu\.mineru\bin\mineru-open-api.exe"


def _load_env() -> None:
    """从项目根目录 .env 加载环境变量（幂等）"""
    project_root = Path(__file__).resolve().parent.parent.parent
    env_path = project_root / ".env"
    if env_path.exists():
        try:
            from dotenv import load_dotenv
            load_dotenv(env_path, override=True)
        except ImportError:
            pass


_load_env()


class MineruClient:
    """MinerU API 封装 — 调用 CLI 提取 PDF 内容"""

    def __init__(
        self,
        cli_path: str | Path = DEFAULT_CLI_PATH,
        token: Optional[str] = None,
        timeout: int = 300,
    ):
        self.cli_path = Path(cli_path)
        self.token = token or os.environ.get("MINERU_TOKEN")
        self.timeout = timeout

        if not self.cli_path.exists():
            raise FileNotFoundError(f"MinerU CLI 不存在: {cli_path}")
        if not self.token:
            raise ValueError(
                "MINERU_TOKEN 未设置。请通过环境变量或 token 参数传入。"
            )

    def extract(self, pdf_path: str | Path, output_dir: str | Path | None = None) -> dict:
        """提取单份 PDF，返回完整 JSON 结果

        Args:
            pdf_path: PDF 文件路径
            output_dir: 输出目录（默认临时目录）

        Returns:
            MinerU JSON（content_list 数组，或含 content_list 的 dict）
        """
        pdf_path = Path(pdf_path)
        if not pdf_path.exists():
            raise FileNotFoundError(f"PDF 文件不存在: {pdf_path}")

        out_dir = Path(output_dir or tempfile.mkdtemp())
        out_dir.mkdir(parents=True, exist_ok=True)

        # 缓存命中: 该分块 PDF 已解析过，直接复用输出（同一 PDF 的确定性结果）
        cache_json = out_dir / f"{pdf_path.stem}.json"
        if cache_json.exists():
            with open(cache_json, encoding="utf-8") as f:
                cached = json.load(f)
            cached_list = cached if isinstance(cached, list) else cached.get("content_list", [])
            logger.info(f"MinerU 命中缓存: {cache_json.name} ({len(cached_list)} 元素)")
            return cached

        # 构建命令
        cmd = [
            str(self.cli_path),
            "extract",
            str(pdf_path),
            "-f", "json",
            "-o", str(out_dir),
            "--timeout", str(self.timeout),
        ]

        logger.info(f"MinerU 提取: {pdf_path.name} ({pdf_path.stat().st_size / 1024:.0f} KB)")

        env = None
        if self.token:
            env = {"MINERU_TOKEN": self.token}

        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=self.timeout + 30,
            env=env,
        )

        if result.returncode != 0:
            raise RuntimeError(
                f"MinerU 提取失败 (exit={result.returncode}): "
                f"{result.stderr[:500] or result.stdout[:500]}"
            )

        # 查找输出 JSON：MinerU 输出的文件名与输入 PDF 同名
        out_json = out_dir / f"{pdf_path.stem}.json"
        if not out_json.exists():
            # 可能输出到其他位置，尝试在输出目录下递归查找
            found = list(out_dir.rglob("*.json"))
            if found:
                out_json = found[0]
            else:
                raise FileNotFoundError(
                    f"MinerU 未生成 JSON 输出 (检查 {out_dir})"
                )

        with open(out_json, encoding="utf-8") as f:
            data = json.load(f)

        # 统计元素数量
        content_list = data if isinstance(data, list) else data.get("content_list", [])
        table_count = sum(1 for e in content_list if isinstance(e, dict) and e.get("type") == "table")
        logger.info(
            f"MinerU 完成: {len(content_list)} 元素, {table_count} 张表"
        )

        return data

    def batch_extract(
        self, pdf_paths: list[str | Path], output_dir: str | Path | None = None
    ) -> list[dict]:
        """批量提取多个 PDF 分块，返回合并后的完整数据"""
        all_items: list[dict] = []
        page_offset = 0  # 跨分块累加 page_idx
        span_start = 1   # 当前分块在原始 PDF 中的起始页

        for pdf_path in pdf_paths:
            path = Path(pdf_path)
            # 从文件名推断起始页: chunk_1_p1-89.pdf → 1
            import re
            m = re.search(r"p(\d+)-\d+\.pdf$", path.name)
            if m:
                span_start = int(m.group(1))
            else:
                span_start = page_offset + 1

            data = self.extract(path, output_dir)
            content_list = data if isinstance(data, list) else data.get("content_list", [])

            # 修正 page_idx: 从分块内的相对值映射为原始 PDF 的页码
            for item in content_list:
                if isinstance(item, dict) and "page_idx" in item:
                    original_page = span_start + item["page_idx"]
                    item["page_idx"] = original_page

            all_items.extend(content_list)
            # 更新 offset
            if content_list:
                max_page = max(
                    (item.get("page_idx", 0) for item in content_list if isinstance(item, dict)),
                    default=0,
                )
                page_offset = max_page

        logger.info(f"合并结果: {len(all_items)} 元素")
        return all_items
