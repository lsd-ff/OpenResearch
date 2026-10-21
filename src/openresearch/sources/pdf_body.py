"""流式校验大小、gzip 及 PDF 签名，不解析不可信 PDF 内容。"""

import zlib

import httpx

from openresearch.errors import ContractError


async def pdf_body(response: httpx.Response, limit: int) -> bytes:
    if response.headers.get("content-type", "").split(";")[0].strip().lower() != "application/pdf":
        raise ContractError("remote response is not application/pdf")
    encoding = response.headers.get("content-encoding", "identity").strip().lower()
    if encoding not in {"identity", "gzip"}:
        raise ContractError("remote PDF uses an unsupported content encoding")
    encoded_limit = limit if encoding == "identity" else limit * 2
    declared = response.headers.get("content-length")
    if declared is not None:
        try:
            length = int(declared)
        except ValueError:
            raise ContractError("remote PDF has an invalid content length") from None
        if length < 0 or length > encoded_limit:
            raise ContractError("remote PDF exceeds configured size limit")
    output = bytearray()
    encoded_size = 0
    decoder = zlib.decompressobj(31) if encoding == "gzip" else None
    try:
        async for raw in response.aiter_raw(chunk_size=65536):
            encoded_size += len(raw)
            if encoded_size > encoded_limit:
                raise ContractError("remote PDF exceeds configured size limit")
            pending = raw
            while pending:
                chunk = decoder.decompress(pending, limit - len(output) + 1) if decoder else pending
                if len(output) + len(chunk) > limit:
                    raise ContractError("remote PDF exceeds configured size limit")
                output.extend(chunk)
                pending = decoder.unused_data if decoder else b""
                if pending:
                    decoder = zlib.decompressobj(31)
        if decoder and not decoder.eof:
            raise ContractError("remote PDF gzip body is incomplete")
    except zlib.error:
        raise ContractError("remote PDF gzip body is invalid") from None
    if not output.startswith(b"%PDF-"):
        raise ContractError("remote response does not contain a PDF signature")
    return bytes(output)
