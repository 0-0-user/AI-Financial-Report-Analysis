from pydantic import BaseModel


class PeerCompany(BaseModel):
    """匹配到的同行公司"""
    name: str
    stock_code: str
    similarity_score: float
    financials: dict[str, float]


class Benchmark(BaseModel):
    """横向 + 纵向基准"""
    peer_median: dict[str, float]
    historical_mean: dict[str, float]
    peer_companies: list[PeerCompany]
