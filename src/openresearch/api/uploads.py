from typing import Any
from urllib.parse import quote

from fastapi import APIRouter, Response, UploadFile

from openresearch.api.dependencies import IdentityDep, RuntimeDep
from openresearch.api.payloads import upload_payload
from openresearch.storage.upload_files import UploadSubmission

router = APIRouter(prefix="/api/uploads", tags=["uploads"])


@router.post("", status_code=201)
def create_upload(file: UploadFile, runtime: RuntimeDep, identity: IdentityDep) -> dict[str, Any]:
    """上传仅保存为数据，不在宿主机执行文件或解析 PDF。"""
    record = runtime.uploads.create(
        UploadSubmission(file.filename or "attachment", file.file, identity)
    )
    return upload_payload(record)


@router.get("/{upload_id}")
def get_upload(upload_id: str, runtime: RuntimeDep, identity: IdentityDep) -> dict[str, Any]:
    return upload_payload(runtime.repository.uploads.get(upload_id, identity.user_id))


@router.get("/{upload_id}/content")
def upload_content(upload_id: str, runtime: RuntimeDep, identity: IdentityDep) -> Response:
    record, content = runtime.uploads.content(upload_id, identity.user_id)
    return Response(
        content,
        media_type=record.content_type,
        headers={
            "ETag": f'"{record.sha256}"',
            "X-Content-Type-Options": "nosniff",
            "Content-Disposition": f"inline; filename*=UTF-8''{quote(record.name, safe='')}",
        },
    )
