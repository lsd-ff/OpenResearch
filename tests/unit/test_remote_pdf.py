import asyncio
import gzip

import pytest
from pdf_network import PDF, PUBLIC, Wire, response

from openresearch.errors import ContractError
from openresearch.sources.pdf_transport import PdfTransport
from openresearch.sources.public_network import PublicNetworkBackend, public_address
from openresearch.sources.remote_pdf import RemotePdfReader, pdf_filename, pdf_location


def reader(settings, replies, *, answers=None, proxy=""):
    wire = Wire(replies)
    resolutions = []

    async def resolve(host, port):
        resolutions.append((host, port))
        return answers(host, len(resolutions)) if answers else [PUBLIC]

    transport = PdfTransport(PublicNetworkBackend(proxy, resolver=resolve, network=wire))
    return RemotePdfReader(settings, transport), wire, resolutions


@pytest.mark.parametrize(
    "url",
    [
        "file:///paper.pdf",
        "ftp://example.com/paper.pdf",
        "https:///paper.pdf",
        "https://user:pass@example.com/a.pdf",
        "https://@example.com/a.pdf",
        "https://example.com:0/a.pdf",
        "https://example.com:65536/a.pdf",
        "https://example.com/\nprivate.pdf",
    ],
)
def test_invalid_urls_cannot_reach_network(url):
    with pytest.raises(ContractError):
        pdf_location(url)


@pytest.mark.parametrize(
    "address",
    [
        "127.0.0.1",
        "10.0.0.1",
        "172.16.1.1",
        "192.168.0.1",
        "169.254.169.254",
        "0.0.0.0",
        "100.64.0.1",
        "224.0.0.1",
        "255.255.255.255",
        "192.0.2.1",
        "::1",
        "::",
        "fc00::1",
        "fe80::1",
        "ff02::1",
        "::ffff:127.0.0.1",
        "64:ff9b::7f00:1",
        "2002:7f00:1::",
        "not-an-address",
    ],
)
def test_private_and_special_addresses_are_blocked(address):
    assert not public_address(address)


def test_public_addresses_and_names_preserve_supported_sources():
    assert public_address(PUBLIC) and public_address("2606:4700:4700::1111")
    assert public_address("::ffff:8.8.8.8")
    assert (
        str(pdf_location("https://arxiv.org/abs/1706.03762v7"))
        == "https://arxiv.org/pdf/1706.03762v7"
    )
    assert pdf_filename("https://example.com/paper.pdf?access_token=secret#page=2") == "paper.pdf"
    assert pdf_filename("https://example.com/") == "paper.pdf"


async def test_direct_connection_pins_ip_but_retains_host_and_verified_tls(settings):
    source, wire, resolutions = reader(settings, [[response()]])
    assert await source.fetch("https://paper.example/paper.pdf?download=1") == PDF
    assert resolutions == [("paper.example", 443)]
    assert wire.calls == [(PUBLIC, 443)] and wire.tls == ["paper.example"]
    sent = b"".join(wire.writes)
    assert b"GET /paper.pdf?download=1 HTTP/1.1" in sent and b"Host: paper.example" in sent
    assert all(stream._closed for stream in wire.streams)


@pytest.mark.parametrize("proxy_scheme", ["http", "https"])
async def test_proxy_connect_pins_public_target_and_never_sends_proxy_credentials_to_source(
    settings, proxy_scheme
):
    source, wire, _ = reader(
        settings,
        [[b"HTTP/1.1 200 Connection established\r\n\r\n", response()]],
        proxy=f"{proxy_scheme}://proxy-user:proxy-pass@proxy.example:8080",
    )
    assert await source.fetch("https://paper.example/a.pdf") == PDF
    assert wire.calls == [("proxy.example", 8080)]
    assert wire.tls == (
        ["proxy.example", "paper.example"] if proxy_scheme == "https" else ["paper.example"]
    )
    sent = b"".join(wire.writes)
    assert f"CONNECT {PUBLIC}:443 HTTP/1.1".encode() in sent
    assert b"Proxy-Authorization: Basic " in sent
    assert b"Proxy-Authorization" not in sent.split(b"GET ", 1)[1]
    assert all(stream._closed for stream in wire.streams)


@pytest.mark.parametrize("addresses", [["127.0.0.1"], [PUBLIC, "10.0.0.1"]])
async def test_all_resolved_addresses_are_checked_before_any_connection(settings, addresses):
    source, wire, _ = reader(settings, [], answers=lambda host, number: addresses)
    with pytest.raises(ContractError, match="non-public"):
        await source.fetch("https://paper.example/a.pdf")
    assert wire.calls == []


@pytest.mark.parametrize("redirect", ["http://internal.example/private.pdf", "/next.pdf"])
async def test_redirect_rechecks_dns_and_cannot_rebind_to_a_private_destination(settings, redirect):
    source, wire, resolutions = reader(
        settings,
        [[response(b"", status=302, headers={"Location": redirect})]],
        answers=lambda host, number: [PUBLIC] if number == 1 else ["127.0.0.1"],
    )
    with pytest.raises(ContractError, match="non-public"):
        await source.fetch("https://paper.example/a.pdf")
    assert len(resolutions) == 2 and wire.calls == [(PUBLIC, 443)]
    assert all(stream._closed for stream in wire.streams)


async def test_valid_redirects_keep_correct_hosts_and_do_not_forward_cookies(settings):
    source, wire, resolutions = reader(
        settings,
        [
            [
                response(
                    b"",
                    status=302,
                    headers={"Location": "../final.pdf", "Set-Cookie": "key=private"},
                )
            ],
            [response()],
        ],
    )
    assert await source.fetch("http://paper.example/folder/start.pdf") == PDF
    assert resolutions == [("paper.example", 80), ("paper.example", 80)]
    sent = b"".join(wire.writes)
    assert b"GET /final.pdf HTTP/1.1" in sent and b"Cookie:" not in sent


async def test_redirect_limit_is_bounded(settings):
    source, wire, _ = reader(
        settings,
        [[response(b"", status=302, headers={"Location": "/again.pdf"})] for _ in range(6)],
    )
    with pytest.raises(ContractError, match="redirect limit"):
        await source.fetch("https://paper.example/a.pdf")
    assert len(wire.calls) == 6 and all(stream._closed for stream in wire.streams)


@pytest.mark.parametrize(
    "body,headers",
    [
        (b"<html>login</html>", {"Content-Type": "text/html"}),
        (b"not-a-pdf", {}),
        (PDF, {"Content-Length": "9999999"}),
        (b"%PDF-" + b"x" * 1024, {}),
        (gzip.compress(b"%PDF-" + b"x" * 1000000), {"Content-Encoding": "gzip"}),
        (gzip.compress(PDF)[:-3], {"Content-Encoding": "gzip"}),
    ],
)
async def test_wrong_type_signature_size_and_gzip_expansion_are_rejected(settings, body, headers):
    settings.upload_max_bytes = 1024
    source, wire, _ = reader(settings, [[response(body, headers=headers)]])
    with pytest.raises(ContractError):
        await source.fetch("https://paper.example/a.pdf")
    assert all(stream._closed for stream in wire.streams)


async def test_gzip_is_bounded_and_supports_multiple_members(settings):
    source, _, _ = reader(
        settings,
        [
            [
                response(
                    gzip.compress(PDF[:10]) + gzip.compress(PDF[10:]),
                    headers={"Content-Encoding": "gzip"},
                )
            ]
        ],
    )
    assert await source.fetch("https://paper.example/a.pdf") == PDF


async def test_transient_response_retries_and_persistent_error_does_not_expose_signed_url(settings):
    source, wire, _ = reader(settings, [[response(b"", status=503)], [response()]])
    assert await source.fetch("https://paper.example/a.pdf") == PDF
    assert len(wire.calls) == 2
    source, wire, _ = reader(settings, [[response(b"private response", status=403)]])
    with pytest.raises(ContractError) as caught:
        await source.fetch("https://paper.example/a.pdf?secret_token=must-not-leak")
    assert "must-not-leak" not in str(caught.value) and "private response" not in str(caught.value)


async def test_dns_and_all_attempts_share_one_download_deadline(settings):
    settings.source_download_timeout_seconds = 0.01

    async def slow(host, port):
        await asyncio.sleep(1)
        return [PUBLIC]

    wire = Wire([])
    source = RemotePdfReader(
        settings, PdfTransport(PublicNetworkBackend(resolver=slow, network=wire))
    )
    with pytest.raises(ContractError, match="source unavailable"):
        await source.fetch("https://paper.example/a.pdf")
    assert not wire.calls
