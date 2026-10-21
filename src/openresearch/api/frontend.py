from fastapi.staticfiles import StaticFiles
from starlette.exceptions import HTTPException
from starlette.responses import Response
from starlette.types import Scope


class WorkbenchFiles(StaticFiles):
    async def get_response(self, path: str, scope: Scope) -> Response:
        path = path.replace("\\", "/").lstrip("/")
        if path == "api" or path.startswith("api/") or path == "ping":
            raise HTTPException(404)
        try:
            return await super().get_response(path, scope)
        except HTTPException as error:
            if error.status_code != 404:
                raise
            # 前端路由刷新回到同一个入口；不存在的 API 仍保持 404。
            return await super().get_response("index.html", scope)
