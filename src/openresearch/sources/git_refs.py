"""读取 Git smart HTTP 的引用公告；不克隆或执行仓库代码。"""

import re

from openresearch.errors import ContractError


def advertised_head(raw: bytes) -> str:
    packets: list[bytes] = []
    offset = 0
    while offset < len(raw):
        prefix = raw[offset : offset + 4]
        if re.fullmatch(rb"[0-9a-f]{4}", prefix) is None:
            raise ContractError("invalid Git reference packet length")
        size = int(prefix, 16)
        if size == 0:
            packets.append(b"")
            offset += 4
            continue
        if size < 4 or size > 65520 or offset + size > len(raw):
            raise ContractError("invalid or truncated Git reference packet")
        packets.append(raw[offset + 4 : offset + size])
        offset += size
    if (
        len(packets) < 4
        or packets[0].rstrip(b"\n") != b"# service=git-upload-pack"
        or packets[1]
        or packets[-1]
    ):
        raise ContractError("invalid Git reference advertisement")
    # 解析完整公告后再接受唯一 HEAD，不能忽略后续截断或重复引用。
    heads = [
        match[1].decode("ascii")
        for packet in packets[2:-1]
        if (match := re.fullmatch(rb"([0-9a-f]{40}) HEAD", packet.split(b"\0", 1)[0].rstrip(b"\n")))
    ]
    if len(heads) != 1 or heads[0] == "0" * 40:
        raise ContractError("Git did not advertise a unique nonempty HEAD")
    return heads[0]
