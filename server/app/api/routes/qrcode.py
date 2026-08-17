"""二维码路由（海报分享：返回可扫描的真二维码 PNG；上线阶段替换为微信 getUnlimited 小程序码）"""
import io

import qrcode
from fastapi import APIRouter, Query, Request
from fastapi.responses import Response

from app.api.deps import deps

router = APIRouter()

MAX_TEXT_LENGTH = 256


@router.get("/qrcode")
def qrcode_image(request: Request, text: str = Query(default="", max_length=MAX_TEXT_LENGTH)) -> Response:
    """生成二维码 PNG：text 为空时使用配置的默认分享地址（settings.share_url）"""
    settings = deps(request.app)[3]
    target = text or settings.share_url
    img = qrcode.make(target)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return Response(content=buf.getvalue(), media_type="image/png")
