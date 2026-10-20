"""网页 CI 使用受控的来源检索；API、存储、调度和 Docker 执行使用真实实现。"""

import httpx
from fastapi import Request

from openresearch.api.app import create_app
from openresearch.sources.discovery import SourceDiscovery


def search(request: httpx.Request) -> httpx.Response:
    if request.url.host != "api.github.com" or request.url.path != "/search/repositories":
        return httpx.Response(400)
    if request.url.params.get("q") != "Transformer":
        return httpx.Response(200, json={"items": []})
    return httpx.Response(
        200,
        json={
            "items": [
                {
                    "full_name": "harvardnlp/annotated-transformer",
                    "html_url": "https://github.com/harvardnlp/annotated-transformer",
                    "description": "The Annotated Transformer",
                }
            ]
        },
    )


app = create_app(discovery=SourceDiscovery(httpx.MockTransport(search)))


@app.middleware("http")
async def observed_stream_cursor(request: Request, call_next):
    response = await call_next(request)
    if request.url.path.endswith("/stream"):
        # 仅测试服务回显实际收到的游标，不以浏览器拦截器的精简请求头代替服务端证据。
        response.headers["X-Test-Received-Cursor"] = request.headers.get("last-event-id", "0")
    return response
