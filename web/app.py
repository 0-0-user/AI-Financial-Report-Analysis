"""web/app.py — 可视化界面后端（FastAPI）

两个板块：
1. 换钥匙：列厂商 / 存 key / 测连通 / 设默认（= 切换配置档案）
2. 跑分析：收 PDF / 后台分析 / SSE 实时推送思考链 / 返回最终报告

启动：python -m uvicorn web.app:app --port 8000
"""

import json
import os
import threading
import time
from pathlib import Path

from fastapi import FastAPI, UploadFile, File, HTTPException
from fastapi.responses import HTMLResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from llm.client import PROVIDER_CONFIG
from pipeline.runner import AnalysisControl

# 各厂商可选模型（界面下拉选择；不在此列的自定义模型也能填）
PROVIDER_MODELS: dict[str, list[str]] = {
    "deepseek": ["deepseek-v4-flash", "deepseek-v4-pro", "deepseek-chat", "deepseek-reasoner"],
    "glm": ["glm-4.7-flash", "glm-4.5", "glm-4-plus", "glm-4-flash"],
    "qwen": ["qwen-plus", "qwen-max", "qwen-turbo"],
    "openai": ["gpt-4o", "gpt-4o-mini", "gpt-4.1", "gpt-4.1-mini"],
    "anthropic": ["claude-sonnet-5-20250610", "claude-opus-5-20250610", "claude-haiku-4-5-20251001"],
    "moonshot": ["moonshot-v1-8k", "moonshot-v1-32k", "moonshot-v1-128k"],
    "gemini": ["gemini-2.0-flash", "gemini-2.0-pro", "gemini-1.5-flash"],
    "doubao": ["doubao-pro-32k", "doubao-pro-128k", "doubao-lite-32k"],
}

app = FastAPI(title="AI 财报分析", description="配置各家 AI + 拖 PDF 分析")

# ── 路径 ──
BASE_DIR = Path(__file__).resolve().parent.parent
STATIC_DIR = Path(__file__).resolve().parent / "static"
RAW_DIR = BASE_DIR / "data" / "raw"
OUTPUTS_DIR = BASE_DIR / "data" / "outputs"
CHARTS_DIR = OUTPUTS_DIR / "charts"
ENV_PATH = BASE_DIR / ".env"

# ── 任务管理 ──
_lock = threading.Lock()
_tasks: dict[str, dict] = {}          # task_id -> {pdf_path, trace_path, status, done, error}
_active: dict = {"running": False}    # 单任务锁


# ═══════════════════════════════════════
# .env 读写（保留其他变量）
# ═══════════════════════════════════════

def _load_env_map() -> dict[str, str]:
    if not ENV_PATH.exists():
        return {}
    result: dict[str, str] = {}
    for line in ENV_PATH.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and "=" in line and not line.startswith("#"):
            k, v = line.split("=", 1)
            result[k.strip()] = v.strip()
    return result


def _save_env(updates: dict[str, str]) -> None:
    env = _load_env_map()
    env.update(updates)
    for k, v in updates.items():
        os.environ[k] = v
    ENV_PATH.write_text("\n".join(f"{k}={v}" for k, v in env.items()) + "\n", encoding="utf-8")


def _has_key(env_key: str) -> bool:
    val = os.getenv(env_key, "")
    return bool(val) and val != "sk-placeholder"


# ═══════════════════════════════════════
# 页面
# ═══════════════════════════════════════

@app.get("/", response_class=HTMLResponse)
def index():
    html = (STATIC_DIR / "index.html").read_text(encoding="utf-8")
    return HTMLResponse(html)


# ═══════════════════════════════════════
# 板块一：换钥匙
# ═══════════════════════════════════════

@app.get("/api/providers")
def list_providers():
    """列出大模型厂商（第一步）与 MinerU（第二步，独立配置）"""
    current = os.getenv("LLM_PROVIDER", "anthropic")
    llm = []
    for name, cfg in PROVIDER_CONFIG.items():
        llm.append({
            "provider": name,
            "base_url": cfg.get("base_url") or "",
            "default_model": cfg.get("default_model", ""),
            "env_key": cfg["env_key"],
            "has_key": _has_key(cfg["env_key"]),
            "is_default": name == current,
            "models": PROVIDER_MODELS.get(name, [cfg.get("default_model", "")]),
            "current_model": os.getenv(f"LLM_MODEL_{name.upper()}") or cfg.get("default_model", ""),
        })
    mineru = {
        "provider": "mineru",
        "name": "MinerU",
        "base_url": "mineru.net",
        "default_model": "",
        "env_key": "MINERU_TOKEN",
        "has_key": _has_key("MINERU_TOKEN"),
        "models": [],
        "current_model": "",
    }
    return {"llm": llm, "mineru": mineru, "default": current}


class ConfigPayload(BaseModel):
    provider: str
    api_key: str = ""
    set_default: bool = False
    model: str = ""


@app.post("/api/config")
def save_config(payload: ConfigPayload):
    """保存某厂商 key / 选择模型 / 设为主力（=切换配置档案）到 .env"""
    if payload.provider == "mineru":
        if not payload.api_key.strip():
            raise HTTPException(400, "key 不能为空")
        _save_env({"MINERU_TOKEN": payload.api_key.strip()})
        return {"ok": True, "message": "MinerU 配置已保存"}

    cfg = PROVIDER_CONFIG.get(payload.provider)
    if not cfg:
        raise HTTPException(400, f"未知厂商: {payload.provider}")
    updates: dict[str, str] = {}
    if payload.api_key.strip():
        updates[cfg["env_key"]] = payload.api_key.strip()
    if payload.model.strip():
        updates[f"LLM_MODEL_{payload.provider.upper()}"] = payload.model.strip()
    if payload.set_default:
        updates["LLM_PROVIDER"] = payload.provider
    if not updates:
        raise HTTPException(400, "无有效配置（请填 key、选模型或设为主力）")
    _save_env(updates)
    return {"ok": True, "message": f"{payload.provider} 配置已保存"}


class TestPayload(BaseModel):
    provider: str
    api_key: str = ""


def _test_mineru(api_key: str = ""):
    """MinerU 连通性测试：POST 一个无效任务，token 有效则通过鉴权返回业务错误码，无效则 401"""
    key = api_key.strip() or os.getenv("MINERU_TOKEN", "")
    if not key:
        return {"ok": False, "message": "请先填写 MinerU API key"}
    try:
        import json as _json
        import urllib.error
        import urllib.request
        body = _json.dumps({"url": "invalid-url-for-test"}).encode()
        req = urllib.request.Request(
            "https://mineru.net/api/v4/extract/task",
            data=body,
            headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=20):
            # 返回业务错误码(-10002)也说明已过鉴权，token 有效
            return {"ok": True, "message": "MinerU 连通成功（token 有效）"}
    except urllib.error.HTTPError as e:
        return {"ok": False, "message": f"MinerU 连通失败：HTTP {e.code}（token 可能无效）"}
    except Exception as e:
        return {"ok": False, "message": f"MinerU 连通失败：{str(e)[:100]}"}


@app.post("/api/test-connection")
def test_connection(payload: TestPayload):
    """连通性测试：发一句话给该厂商，能回话即成功"""
    if payload.provider == "mineru":
        return _test_mineru(payload.api_key)
    cfg = PROVIDER_CONFIG.get(payload.provider)
    if not cfg:
        raise HTTPException(400, f"未知厂商: {payload.provider}")
    key = payload.api_key.strip() or os.getenv(cfg["env_key"], "")
    if not key:
        return {"ok": False, "message": "请先填写 API key"}
    os.environ[cfg["env_key"]] = key
    try:
        from llm.client import LLMClient
        client = LLMClient(provider=payload.provider)
        response = client.chat("test_connection", {}, max_tokens=20)
        return {"ok": True, "message": f"连通成功：{str(response)[:40]}"}
    except Exception as e:
        return {"ok": False, "message": f"连通失败：{str(e)[:120]}"}


# ═══════════════════════════════════════
# 板块二：跑分析
# ═══════════════════════════════════════

@app.post("/api/analyze")
async def analyze(file: UploadFile = File(...)):
    """接收 PDF → 后台线程跑分析 → 返回 task_id"""
    with _lock:
        if _active["running"]:
            raise HTTPException(409, "已有分析在进行中，请等它完成")
        _active["running"] = True

    RAW_DIR.mkdir(parents=True, exist_ok=True)
    filename = Path(file.filename or "report.pdf").name
    dest = RAW_DIR / filename
    dest.write_bytes(await file.read())

    task_id = f"task_{int(time.time())}"
    stem = dest.stem
    task = {
        "pdf_path": str(dest),
        "trace_path": str(BASE_DIR / "data" / "logs" / f"{stem}_trace.jsonl"),
        "status": "running",
        "done": False,
        "error": "",
        "controls": AnalysisControl(),
        "paused": False,
    }
    _tasks[task_id] = task

    def _run():
        try:
            from pipeline.runner import run_single_analysis
            run_single_analysis(str(dest), controls=task["controls"])
            task["status"] = "cancelled" if task["controls"].cancel.is_set() else "success"
        except Exception as e:
            if task["controls"].cancel.is_set():
                task["status"] = "cancelled"
                task["error"] = "分析已取消"
            else:
                task["status"] = "error"
                task["error"] = str(e)
        finally:
            task["done"] = True
            with _lock:
                _active["running"] = False

    threading.Thread(target=_run, daemon=True).start()
    return {"task_id": task_id, "pdf_name": filename, "status": "running"}


@app.get("/api/analyze/{task_id}/events")
def stream_events(task_id: str):
    """SSE：里程碑事件（供左栏小窗）+ 精简 Markdown 实时渲染（供右栏，人读）"""
    task = _tasks.get(task_id)
    if not task:
        raise HTTPException(404, "任务不存在")
    trace_path = Path(task["trace_path"])
    from pipeline.tracer import tracer

    def gen():
        offset = 0
        last_md = ""
        last_paused = False
        waited = 0
        # 等待 trace 文件出现
        while not trace_path.exists():
            if task["done"]:
                yield "event: done\ndata: {}\n\n"
                return
            time.sleep(0.2)
            waited += 1
            if waited > 60:
                yield f"event: error\ndata: {json.dumps({'message': 'trace 文件超时未生成'})}\n\n"
                return
        # 循环：推里程碑事件 + 定期推精简 Markdown + 暂停状态
        while True:
            # 0) 暂停/继续状态推送
            is_paused = bool(task["controls"].pause.is_set()) and not task["done"]
            if is_paused != last_paused:
                last_paused = is_paused
                yield f"data: {json.dumps({'type': 'control', 'state': 'paused' if is_paused else 'resumed'})}\n\n"
            # 1) tail jsonl 拿里程碑事件（供左栏小窗）
            try:
                size = trace_path.stat().st_size
                if size > offset:
                    with open(trace_path, encoding="utf-8") as f:
                        f.seek(offset)
                        new_lines = f.read().splitlines()
                    offset = trace_path.stat().st_size
                    for line in new_lines:
                        if not line.strip():
                            continue
                        try:
                            ev = json.loads(line)
                        except json.JSONDecodeError:
                            continue
                        if ev.get("type") == "milestone":
                            yield f"data: {json.dumps({'type': 'milestone', 'layer': ev.get('layer'), 'status': ev.get('status')})}\n\n"
            except OSError:
                pass
            # 2) 定期推精简 Markdown（概览+思考过程+里程碑，不含输入/输出 JSON）
            md = tracer.render_markdown(summary_only=True)
            if md != last_md:
                last_md = md
                yield f"data: {json.dumps({'type': 'markdown', 'text': md})}\n\n"
            if task["done"]:
                if task["status"] == "cancelled":
                    yield f"data: {json.dumps({'type': 'control', 'state': 'cancelled'})}\n\n"
                yield "event: done\ndata: {}\n\n"
                break
            time.sleep(0.3)

    return StreamingResponse(gen(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@app.get("/api/analyze/{task_id}/result")
def get_result(task_id: str):
    """返回分析最终结果（报告 Markdown + JSON）"""
    task = _tasks.get(task_id)
    if not task:
        raise HTTPException(404, "任务不存在")
    if not task["done"]:
        return {"status": "running"}
    if task["status"] == "error":
        return {"status": "error", "message": task["error"]}

    stem = Path(task["pdf_path"]).stem
    md_path = OUTPUTS_DIR / f"{stem}_report.md"
    json_path = OUTPUTS_DIR / f"{stem}_report.json"
    return {
        "status": "success",
        "markdown": md_path.read_text(encoding="utf-8") if md_path.exists() else "",
        "report": json.loads(json_path.read_text(encoding="utf-8")) if json_path.exists() else None,
        "trace_md": (BASE_DIR / "data" / "logs" / f"{stem}_trace.md").read_text(encoding="utf-8")
        if (BASE_DIR / "data" / "logs" / f"{stem}_trace.md").exists() else "",
    }


@app.post("/api/analyze/{task_id}/cancel")
def cancel_task(task_id: str):
    """取消分析：真正终止，不后台跑完"""
    task = _tasks.get(task_id)
    if not task:
        raise HTTPException(404, "任务不存在")
    task["controls"].cancel.set()
    task["controls"].pause.clear()
    task["paused"] = False
    return {"ok": True, "status": "cancelled"}


@app.post("/api/analyze/{task_id}/pause")
def pause_task(task_id: str):
    """暂停分析：管线在层间等待，可继续接上"""
    task = _tasks.get(task_id)
    if not task:
        raise HTTPException(404, "任务不存在")
    if task["done"]:
        return {"ok": True, "status": "done"}
    task["controls"].pause.set()
    task["paused"] = True
    return {"ok": True, "status": "paused"}


@app.post("/api/analyze/{task_id}/resume")
def resume_task(task_id: str):
    """继续分析：从暂停处接上"""
    task = _tasks.get(task_id)
    if not task:
        raise HTTPException(404, "任务不存在")
    task["controls"].pause.clear()
    task["paused"] = False
    return {"ok": True, "status": "running"}


# 图表静态目录
if CHARTS_DIR.exists():
    app.mount("/charts", StaticFiles(directory=str(CHARTS_DIR)), name="charts")
