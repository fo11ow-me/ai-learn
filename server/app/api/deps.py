"""路由依赖装配（WHY：生产首次请求时按真实配置惰性初始化；测试可预先写入 app.state 注入 fake）"""
from fastapi import FastAPI

from app.core.config import get_settings
from app.core.sensitive import load_filter
from app.core.tasks import task_store
from app.services.llm import LLMClient


def deps(app: FastAPI):
    """获取依赖四元组 (llm, sensitive, store, settings)。
    逐项惰性装配（WHY：测试可只注入部分依赖，其余仍按真实配置初始化）"""
    if not hasattr(app.state, "settings"):
        app.state.settings = get_settings()
    if not hasattr(app.state, "llm"):
        app.state.llm = LLMClient(app.state.settings)
    if not hasattr(app.state, "sensitive"):
        app.state.sensitive = load_filter(app.state.settings)
    if not hasattr(app.state, "store"):
        app.state.store = task_store
    return app.state.llm, app.state.sensitive, app.state.store, app.state.settings
