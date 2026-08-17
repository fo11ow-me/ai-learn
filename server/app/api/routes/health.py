"""健康检查路由（方案文档步骤 0 验证标准：GET /health 返回 200）"""
from fastapi import APIRouter

router = APIRouter()


@router.get("/health")
def health() -> dict:
    """进程存活探针：服务可用即返回 ok"""
    return {"status": "ok"}
