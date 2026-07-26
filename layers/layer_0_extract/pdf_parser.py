"""调用外部 PDF 解析工具提取原始文字和表格数据"""


class PDFParser:
    """封装 PDF 解析逻辑，支持 MinerU / pdfplumber 等后端"""

    def __init__(self, pdf_path: str, backend: str = "mineru"):
        self.pdf_path = pdf_path
        self.backend = backend

    def extract(self) -> dict:
        """解析 PDF，返回原始字典结构"""
        # TODO: 接入 MinerU 或 pdfplumber
        # 当前返回占位结构
        return {
            "raw_text": "",
            "tables": [],
            "metadata": {"page_count": 0, "file_name": self.pdf_path},
        }
