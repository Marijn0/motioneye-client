"""Test the motionEye client."""
from __future__ import annotations

import asyncio
from contextlib import closing
import logging
import socket
from typing import Any
from unittest.mock import AsyncMock

import aiohttp
from aiohttp import web
import pytest

from motioneye_client.client import (
    MotionEyeClient,
    MotionEyeClientInvalidAuthError,
    MotionEyeClientPathError,
    MotionEyeClientRequestError,
    MotionEyeClientURLParseError,
)
from motioneye_client.const import (
    KEY_HOST,
    KEY_ID,
    KEY_STREAMING_PORT,
    KEY_VIDEO_STREAMING,
)

_LOGGER = logging.getLogger(__name__)
SESSION_COOKIE = "secure-session"


def _login_response(
    data: dict[str, Any] | None = None, cookie: str = SESSION_COOKIE
) -> web.Response:
    response = web.json_response(data or {})
    response.set_cookie("user", cookie)
    return response


def _assert_session_cookie(request: web.Request) -> None:
    assert request.cookies["user"] == SESSION_COOKIE


async def _create_motioneye_server(aiohttp_server: Any, handlers: list[Any]) -> Any:
    app = web.Application()
    app.add_routes(handlers)

    # Add a login handler unless one is explicitly added (i.e. for testing logins).
    for route in handlers:
        if "/login" == route.path:
            break
    else:
        login_handler = AsyncMock(return_value=_login_response())
        app.add_routes([web.post("/login", login_handler)])
    return await aiohttp_server(app)


@pytest.mark.asyncio
async def test_login_with_credentials(aiohttp_server: Any) -> None:
    """Test login sends credentials as JSON POST."""

    async def login_handler(request: web.Request) -> web.Response:
        body = await request.json()
        assert body["username"] == "admin"
        assert body["password"] == "password"
        return _login_response()

    server = await _create_motioneye_server(
        aiohttp_server, [web.post("/login", login_handler)]
    )
    async with MotionEyeClient(
        str(server.make_url("/")),
        "admin",
        "password",
        "user",
        "user_password",
    ) as client:
        assert client


@pytest.mark.asyncio
async def test_failed_request(caplog: Any, aiohttp_server: Any) -> None:
    """Test a non-JSON response."""

    async def login_handler(request: web.Request) -> web.Response:
        raise web.HTTPNotFound()

    server = await _create_motioneye_server(
        aiohttp_server, [web.post("/login", login_handler)]
    )

    async with MotionEyeClient(str(server.make_url("/"))) as client:
        assert not client
    assert "Unexpected HTTP response status" in caplog.text


@pytest.mark.asyncio
async def test_non_200_response(aiohttp_server: Any) -> None:
    """Test a non-200 response."""

    async def login_handler(request: web.Request) -> web.Response:
        response = web.Response(body="this is not json")
        response.set_cookie("user", SESSION_COOKIE)
        return response

    server = await _create_motioneye_server(
        aiohttp_server, [web.post("/login", login_handler)]
    )

    async with MotionEyeClient(str(server.make_url("/"))) as client:
        assert not client


@pytest.mark.asyncio
async def test_cannot_connect(caplog: Any, aiohttp_server: Any) -> None:
    """Test a failed connection."""

    async def login_handler(request: web.Request) -> web.Response:
        return web.Response(body="this is not json")

    with closing(socket.socket(socket.AF_INET, socket.SOCK_STREAM)) as s:
        s.bind(("localhost", 0))
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        port = s.getsockname()[1]

        async with MotionEyeClient(f"http://localhost:{port}") as client:
            assert not client
    assert "Connection failed" in caplog.text


@pytest.mark.asyncio
async def test_client_login_success(aiohttp_server: Any) -> None:
    """Test successful client login."""

    login_handler = AsyncMock(return_value=_login_response())

    server = await _create_motioneye_server(
        aiohttp_server, [web.post("/login", login_handler)]
    )

    async with MotionEyeClient(str(server.make_url("/"))) as client:
        assert client


@pytest.mark.asyncio
async def test_client_login_without_cookie_failure(aiohttp_server: Any) -> None:
    """Test login fails when motionEye does not return a session cookie."""

    login_handler = AsyncMock(return_value=web.json_response({}))

    server = await _create_motioneye_server(
        aiohttp_server, [web.post("/login", login_handler)]
    )

    client = MotionEyeClient(str(server.make_url("/")))
    with pytest.raises(MotionEyeClientInvalidAuthError):
        await client.async_client_login()
    await client.async_client_close()


@pytest.mark.asyncio
async def test_client_login_failure(caplog: Any, aiohttp_server: Any) -> None:
    """Test failed client login."""

    login_handler = AsyncMock(
        return_value=web.json_response(
            {"error": "invalid credentials"}, status=401
        )
    )

    server = await _create_motioneye_server(
        aiohttp_server, [web.post("/login", login_handler)]
    )

    async with MotionEyeClient(str(server.make_url("/"))) as client:
        assert not client
    assert "Authentication failed" in caplog.text


@pytest.mark.asyncio
async def test_get_manifest(aiohttp_server: Any) -> None:
    """Test getting the motionEye manifest."""

    manifest = {"key": "value"}

    async def manifest_handler(request: web.Request) -> web.Response:
        _assert_session_cookie(request)
        return web.json_response(manifest)

    server = await _create_motioneye_server(
        aiohttp_server, [web.get("/manifest.json", manifest_handler)]
    )

    async with MotionEyeClient(str(server.make_url("/"))) as client:
        assert client
        assert await client.async_get_manifest() == manifest


@pytest.mark.asyncio
async def test_get_server_config(aiohttp_server: Any) -> None:
    """Test getting the motionEye server config."""

    server_config = {"key": "value"}

    async def server_config_handler(request: web.Request) -> web.Response:
        _assert_session_cookie(request)
        return web.json_response(server_config)

    server = await _create_motioneye_server(
        aiohttp_server, [web.get("/config/main/get", server_config_handler)]
    )

    async with MotionEyeClient(str(server.make_url("/"))) as client:
        assert client
        assert await client.async_get_server_config() == server_config


@pytest.mark.asyncio
async def test_get_cameras(aiohttp_server: Any) -> None:
    """Test getting the motionEye cameras."""

    cameras = {"key": "value"}

    async def list_cameras_handler(request: web.Request) -> web.Response:
        _assert_session_cookie(request)
        return web.json_response(cameras)

    server = await _create_motioneye_server(
        aiohttp_server, [web.get("/config/list", list_cameras_handler)]
    )

    async with MotionEyeClient(str(server.make_url("/"))) as client:
        assert client
        assert await client.async_get_cameras() == cameras


@pytest.mark.asyncio
async def test_passed_in_session_uses_session_cookie(aiohttp_server: Any) -> None:
    """Test a caller-provided aiohttp session uses the stored session cookie."""

    cameras = {"key": "value"}

    async def list_cameras_handler(request: web.Request) -> web.Response:
        _assert_session_cookie(request)
        return web.json_response(cameras)

    server = await _create_motioneye_server(
        aiohttp_server, [web.get("/config/list", list_cameras_handler)]
    )

    session = aiohttp.ClientSession()
    try:
        client = MotionEyeClient(str(server.make_url("/")), session=session)
        await client.async_client_login()
        assert await client.async_get_cameras() == cameras
        await client.async_client_close()
        assert not session.closed
    finally:
        await session.close()


@pytest.mark.asyncio
async def test_expired_session_cookie_is_refreshed(aiohttp_server: Any) -> None:
    """Test an expired session cookie is refreshed and the request is retried."""

    expired_cookie = "expired-session"
    login_cookies = [expired_cookie, SESSION_COOKIE]
    login_requests = 0
    cameras = {"key": "value"}

    async def login_handler(request: web.Request) -> web.Response:
        nonlocal login_requests
        login_requests += 1
        return _login_response(cookie=login_cookies.pop(0))

    async def list_cameras_handler(request: web.Request) -> web.Response:
        if request.cookies.get("user") == expired_cookie:
            return web.json_response({"error": "unauthorized"}, status=403)
        _assert_session_cookie(request)
        return web.json_response(cameras)

    server = await _create_motioneye_server(
        aiohttp_server,
        [
            web.post("/login", login_handler),
            web.get("/config/list", list_cameras_handler),
        ],
    )

    async with MotionEyeClient(str(server.make_url("/"))) as client:
        assert client
        assert await client.async_get_cameras() == cameras
    assert login_requests == 2


@pytest.mark.asyncio
async def test_get_camera(aiohttp_server: Any) -> None:
    """Test getting a motionEye camera."""

    camera = {"key": "value"}

    async def get_camera_handler(request: web.Request) -> web.Response:
        _assert_session_cookie(request)
        return web.json_response(camera)

    server = await _create_motioneye_server(
        aiohttp_server, [web.get("/config/100/get", get_camera_handler)]
    )
    async with MotionEyeClient(str(server.make_url("/"))) as client:
        assert client
        assert await client.async_get_camera(100) == camera


@pytest.mark.asyncio
async def test_set_camera(aiohttp_server: Any) -> None:
    """Test setting a motionEye camera."""

    camera = {"key": "value"}

    async def set_camera_handler(request: web.Request) -> web.Response:
        _assert_session_cookie(request)
        assert await request.json() == camera
        return web.json_response({})

    server = await _create_motioneye_server(
        aiohttp_server, [web.post("/config/100/set", set_camera_handler)]
    )

    async with MotionEyeClient(str(server.make_url("/"))) as client:
        assert client
        assert await client.async_set_camera(100, config=camera) == {}


@pytest.mark.asyncio
async def test_is_camera_streaming() -> None:
    """Test whether a camera is streaming."""
    assert MotionEyeClient.is_camera_streaming(
        {KEY_STREAMING_PORT: 8000, KEY_VIDEO_STREAMING: True}
    )

    assert not MotionEyeClient.is_camera_streaming(
        {KEY_STREAMING_PORT: 8000, KEY_VIDEO_STREAMING: False}
    )

    assert not MotionEyeClient.is_camera_streaming({KEY_VIDEO_STREAMING: True})
    assert not MotionEyeClient.is_camera_streaming({})


@pytest.mark.asyncio
async def test_get_camera_stream_url(aiohttp_server: Any) -> None:
    """Test retrieving the stream URL."""
    client = MotionEyeClient("http://host:8000")
    assert (
        client.get_camera_stream_url(
            {KEY_STREAMING_PORT: 8000, KEY_VIDEO_STREAMING: True}
        )
        == "http://host:8000/"
    )

    assert not client.get_camera_stream_url({})


@pytest.mark.asyncio
async def test_get_camera_stream_url_remote_motioneye(aiohttp_server: Any) -> None:
    """Test retrieving the stream URL for a remote motioneye."""
    client = MotionEyeClient("http://host:8000")
    assert (
        client.get_camera_stream_url(
            {KEY_STREAMING_PORT: 8001, KEY_VIDEO_STREAMING: True, KEY_HOST: "foobar"}
        )
        == "http://foobar:8001/"
    )

    assert not client.get_camera_stream_url({})


@pytest.mark.asyncio
async def test_get_camera_snapshot_url(aiohttp_server: Any) -> None:
    """Test retrieving the snapshot URL."""
    client = MotionEyeClient("http://host:8000")
    assert (
        client.get_camera_snapshot_url(
            {KEY_STREAMING_PORT: 8000, KEY_VIDEO_STREAMING: True, KEY_ID: 100}
        )
        == "http://host:8000/picture/100/current/"
    )

    assert not client.get_camera_snapshot_url(
        {KEY_STREAMING_PORT: 8000, KEY_VIDEO_STREAMING: True}
    )


@pytest.mark.asyncio
async def test_async_get_snapshot_image(aiohttp_server: Any) -> None:
    """Test getting a current snapshot image through the authenticated client."""

    image = b"\xff\xd8jpeg-data\xff\xd9"

    async def snapshot_handler(request: web.Request) -> web.Response:
        _assert_session_cookie(request)
        return web.Response(body=image, content_type="image/jpeg")

    server = await _create_motioneye_server(
        aiohttp_server, [web.get("/picture/100/current/", snapshot_handler)]
    )

    async with MotionEyeClient(str(server.make_url("/"))) as client:
        assert client
        assert await client.async_get_snapshot_image(100) == image


@pytest.mark.asyncio
async def test_action(aiohttp_server: Any) -> None:
    """Test performing an action on a motionEye camera."""

    async def action_handler(request: web.Request) -> web.Response:
        _assert_session_cookie(request)
        assert await request.json() == {}
        return web.json_response({})

    action = "foo"
    server = await _create_motioneye_server(
        aiohttp_server, [web.post(f"/action/100/{action}", action_handler)]
    )

    async with MotionEyeClient(str(server.make_url("/"))) as client:
        assert client
        assert await client.async_action(100, action) == {}


@pytest.mark.asyncio
async def test_invalid_urls(aiohttp_server: Any) -> None:
    """Test invalid URLs."""
    server = await _create_motioneye_server(aiohttp_server, [])

    with pytest.raises(MotionEyeClientURLParseError):
        async with MotionEyeClient(f"{server.host}:{server.port}/"):
            pass

    with pytest.raises(MotionEyeClientURLParseError):
        async with MotionEyeClient("http://"):
            pass


@pytest.mark.asyncio
async def test_get_movie_url(aiohttp_server: Any) -> None:
    """Test retrieving a movie URL."""
    client = MotionEyeClient("http://host:8000")
    for path in ["/foo", "foo"]:
        assert (
            client.get_movie_url(1, path)
            == "http://host:8000/movie/1/playback/foo"
        )
        assert (
            client.get_movie_url(1, path, preview=True)
            == "http://host:8000/movie/1/preview/foo"
        )

    with pytest.raises(MotionEyeClientPathError):
        client.get_movie_url(1, "")


@pytest.mark.asyncio
async def test_get_image_url(aiohttp_server: Any) -> None:
    """Test retrieving an image URL."""
    client = MotionEyeClient("http://host:8000")
    for path in ["/foo", "foo"]:
        assert (
            client.get_image_url(1, path)
            == "http://host:8000/picture/1/download/foo"
        )
        assert (
            client.get_image_url(1, path, preview=True)
            == "http://host:8000/picture/1/preview/foo"
        )

    with pytest.raises(MotionEyeClientPathError):
        client.get_image_url(1, "")


@pytest.mark.asyncio
async def test_async_get_movies(aiohttp_server: Any) -> None:
    """Test getting motionEye movies."""

    async def get_movies_handler(request: web.Request) -> web.Response:
        _assert_session_cookie(request)
        assert "prefix" not in request.query
        return web.json_response({"one": "two"})

    async def get_movies_prefix_handler(request: web.Request) -> web.Response:
        _assert_session_cookie(request)
        assert request.query["prefix"] == "moo"
        return web.json_response({"three": "four"})

    server = await _create_motioneye_server(
        aiohttp_server,
        [
            web.get("/movie/100/list", get_movies_handler),
            web.get("/movie/101/list", get_movies_prefix_handler),
        ],
    )
    async with MotionEyeClient(str(server.make_url("/"))) as client:
        assert client
        assert await client.async_get_movies(100) == {"one": "two"}
        assert await client.async_get_movies(101, prefix="moo") == {"three": "four"}


@pytest.mark.asyncio
async def test_async_get_images(aiohttp_server: Any) -> None:
    """Test getting motionEye images."""

    async def get_images_handler(request: web.Request) -> web.Response:
        _assert_session_cookie(request)
        assert "prefix" not in request.query
        return web.json_response({"one": "two"})

    async def get_images_prefix_handler(request: web.Request) -> web.Response:
        _assert_session_cookie(request)
        assert request.query["prefix"] == "moo"
        return web.json_response({"three": "four"})

    server = await _create_motioneye_server(
        aiohttp_server,
        [
            web.get("/picture/100/list", get_images_handler),
            web.get("/picture/101/list", get_images_prefix_handler),
        ],
    )
    async with MotionEyeClient(str(server.make_url("/"))) as client:
        assert client
        assert await client.async_get_images(100) == {"one": "two"}
        assert await client.async_get_images(101, prefix="moo") == {"three": "four"}


@pytest.mark.asyncio
async def test_client_response_error(aiohttp_server: Any) -> None:
    """Test invalid server."""

    async def not_motioneye_server(
        reader: asyncio.StreamReader, writer: asyncio.StreamWriter
    ) -> None:
        writer.close()

    with closing(socket.socket(socket.AF_INET, socket.SOCK_STREAM)) as s:
        s.bind(("localhost", 0))
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        port = s.getsockname()[1]

        await asyncio.start_server(not_motioneye_server, "127.0.0.1", port)

        with pytest.raises(MotionEyeClientRequestError):
            client = MotionEyeClient(f"http://localhost:{port}/")
            await client.async_client_login()

        await client.async_client_close()


@pytest.mark.asyncio
async def test_unicode_decode_error(aiohttp_server: Any) -> None:
    """Test unicode bytes that cannot be decoded."""

    app = web.Application()
    login_response = web.Response(
        body=b"\xff", charset="UTF-8", content_type="application/octet-stream"
    )
    login_response.set_cookie("user", SESSION_COOKIE)
    login_handler = AsyncMock(return_value=login_response)
    app.add_routes([web.post("/login", login_handler)])
    server = await aiohttp_server(app)

    with pytest.raises(MotionEyeClientRequestError):
        client = MotionEyeClient(str(server.make_url("/")))
        await client.async_client_login()
    await client.async_client_close()


@pytest.mark.asyncio
async def test_json_decode_error(aiohttp_server: Any) -> None:
    """Test JSON that cannot be decoded."""

    app = web.Application()
    login_response = web.Response(body="this is not valid json")
    login_response.set_cookie("user", SESSION_COOKIE)
    login_handler = AsyncMock(return_value=login_response)
    app.add_routes([web.post("/login", login_handler)])
    server = await aiohttp_server(app)

    with pytest.raises(MotionEyeClientRequestError):
        client = MotionEyeClient(str(server.make_url("/")))
        await client.async_client_login()
    await client.async_client_close()


@pytest.mark.asyncio
async def test_is_file_type_image() -> None:
    """Test is_file_type_image."""

    client = MotionEyeClient("http://localhost")
    assert client.is_file_type_image(0)
    assert client.is_file_type_image(7)
    assert not client.is_file_type_image(8)
    assert not client.is_file_type_image(100)


@pytest.mark.asyncio
async def test_is_file_type_movie() -> None:
    """Test is_file_type_image."""

    client = MotionEyeClient("http://localhost")
    assert not client.is_file_type_movie(0)
    assert not client.is_file_type_movie(7)
    assert client.is_file_type_movie(8)
    assert client.is_file_type_movie(100)


@pytest.mark.asyncio
async def test_session_passed_in_is_not_closed() -> None:
    """Test a session passed in is not closed."""

    session = AsyncMock()
    client = MotionEyeClient("http://localhost", session=session)
    await client.async_client_close()
    assert not session.close.called
