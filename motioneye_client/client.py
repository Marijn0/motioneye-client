#!/usr/bin/env python
"""Client for motionEye."""
from __future__ import annotations

import json
import logging
from pathlib import PurePath
from types import TracebackType
from typing import Any, Callable
from urllib.parse import urlencode, urljoin, urlsplit, urlunsplit

import aiohttp

from .const import (
    DEFAULT_ADMIN_USERNAME,
    DEFAULT_URL_SCHEME,
    KEY_ID,
    KEY_STREAMING_PORT,
    KEY_VIDEO_STREAMING,
)

_LOGGER = logging.getLogger(__name__)


class MotionEyeClientError(Exception):
    """General MotionEyeClient error."""


class MotionEyeClientInvalidAuthError(MotionEyeClientError):
    """Invalid motionEye authentication."""


class MotionEyeClientConnectionError(MotionEyeClientError):
    """Connection failure."""


class MotionEyeClientRequestError(MotionEyeClientError):
    """Request failure."""


class MotionEyeClientURLParseError(MotionEyeClientError):
    """Unable to parse the URL."""


class MotionEyeClientPathError(MotionEyeClientError):
    """Invalid path provided."""


class MotionEyeClient:
    """MotionEye Client."""

    def __init__(
        self,
        url: str,
        admin_username: str | None = None,
        admin_password: str | None = None,
        surveillance_username: str | None = None,
        surveillance_password: str | None = None,
        session: aiohttp.ClientSession | None = None,
    ):
        """Construct a new motionEye client."""
        parsed = urlsplit(url)
        if not parsed.scheme or not parsed.netloc:
            raise MotionEyeClientURLParseError(
                "Invalid URL, must have a URL scheme and host: %s" % url
            )

        self._url = url
        if session:
            self._session = session
            self._created_session = False
        else:
            # DummyCookieJar disables automatic cookie handling; we manage the
            # session cookie explicitly so it works for any session, including
            # those backed by a jar that rejects IP-address hosts (the default).
            self._session = aiohttp.ClientSession(
                cookie_jar=aiohttp.DummyCookieJar()
            )
            self._created_session = True
        self._session_cookie: str | None = None
        self._admin_username = admin_username or DEFAULT_ADMIN_USERNAME
        self._admin_password = admin_password or ""

    async def __aenter__(self) -> "MotionEyeClient" | None:
        """Enter context manager and connect the client."""
        try:
            await self.async_client_login()
        except MotionEyeClientError:
            return None
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: type[BaseException] | None,
        traceback: TracebackType | None,
    ) -> None:
        """Leave context manager and close the client."""
        await self.async_client_close()

    def _build_url(
        self,
        path: str,
        params: dict[str, Any] | None = None,
    ) -> str:
        """Build a motionEye URL."""
        params = params or {}
        if params:
            return urljoin(self._url, path + "?" + urlencode(params))
        return urljoin(self._url, path)

    async def _async_request(
        self,
        path: str,
        params: dict[str, Any] | None = None,
        data: dict[str, Any] | None = None,
        method: str = "GET",
        on_response: Callable[[aiohttp.ClientResponse], None] | None = None,
        retry_auth: bool = True,
        _raw: bool = False,
    ) -> dict[str, Any] | bytes | None:
        """Fetch return code and JSON, or raw bytes when requested, from motionEye."""

        serialized_json = json.dumps(data) if data is not None else None
        url = self._build_url(path, params=params)

        headers: dict[str, str] = {}
        if serialized_json:
            headers["Content-Type"] = "application/json"
        if self._session_cookie:
            headers["Cookie"] = f"user={self._session_cookie}"

        if method == "GET":
            func = self._session.get
        else:
            func = self._session.post

        coro = func(url, data=serialized_json, headers=headers)

        try:
            async with coro as response:
                _LOGGER.debug("%s %s -> %i", method, url, response.status)
                if response.status in (401, 403):
                    if retry_auth and path != "/login":
                        _LOGGER.debug(
                            "Authentication failed in request to %s; "
                            "refreshing session",
                            url,
                        )
                        await self.async_client_login()
                        return await self._async_request(
                            path,
                            params=params,
                            data=data,
                            method=method,
                            on_response=on_response,
                            retry_auth=False,
                            _raw=_raw,
                        )
                    _LOGGER.warning(
                        f"Authentication failed in request to {url} : {response}"
                    )
                    raise MotionEyeClientInvalidAuthError(response)
                elif not response.ok:
                    _LOGGER.warning(
                        "Unexpected HTTP response status code %s for request: %s",
                        response.status,
                        url,
                    )
                    raise MotionEyeClientRequestError(response)
                if on_response is not None:
                    on_response(response)
                if _raw:
                    return await response.read()
                try:
                    return_value: dict[str, Any] | None = await response.json(
                        content_type=None
                    )
                    return return_value
                except (json.decoder.JSONDecodeError, UnicodeDecodeError) as exc:
                    _LOGGER.error(f"Could not JSON decode: {await response.read()!r}")
                    raise MotionEyeClientRequestError(response) from exc
        except aiohttp.client_exceptions.ClientConnectorError as exc:
            _LOGGER.warning(f"Connection failed to motionEye: {exc}")
            raise MotionEyeClientConnectionError(exc) from exc
        except aiohttp.client_exceptions.ClientError as exc:
            _LOGGER.warning(f"Request failed to motionEye: {exc}")
            raise MotionEyeClientRequestError(exc) from exc

    async def async_client_login(self) -> dict[str, Any] | None:
        """Login to the motionEye server."""

        self._session_cookie = None
        return await self._async_request(
            "/login",
            data={"username": self._admin_username, "password": self._admin_password},
            method="POST",
            on_response=self._store_session_cookie,
            retry_auth=False,
        )

    def _store_session_cookie(self, response: aiohttp.ClientResponse) -> None:
        """Store the secure session cookie returned by motionEye."""
        if morsel := response.cookies.get("user"):
            self._session_cookie = morsel.value
            return

        _LOGGER.warning(
            "Authentication failed: login response did not set a user cookie"
        )
        raise MotionEyeClientInvalidAuthError(response)

    async def async_client_close(self) -> bool:
        """Disconnect from the MotionEye server."""
        if self._created_session:
            await self._session.close()
        return True

    async def async_get_manifest(self) -> dict[str, Any] | None:
        """Get the motionEye manifest."""
        return await self._async_request("/manifest.json")

    async def async_get_server_config(self) -> dict[str, Any] | None:
        """Get the motionEye server config ."""
        return await self._async_request("/config/main/get")

    async def async_get_cameras(self) -> dict[str, Any] | None:
        """Get all motionEye cameras config."""
        return await self._async_request("/config/list")

    async def async_get_camera(self, camera_id: int) -> dict[str, Any] | None:
        """Get a motionEye camera config."""
        return await self._async_request(f"/config/{camera_id}/get")

    async def async_set_camera(
        self, camera_id: int, config: dict[str, Any]
    ) -> dict[str, Any] | None:
        """Set a motionEye camera config."""
        return await self._async_request(
            f"/config/{camera_id}/set",
            method="POST",
            data=config,
        )

    async def async_action(self, camera_id: int, action: str) -> dict[str, Any] | None:
        """Trigger a motionEye action."""
        return await self._async_request(
            f"/action/{camera_id}/{action}",
            method="POST",
            data={},
        )

    @classmethod
    def is_camera_streaming(cls, camera: dict[str, Any] | None) -> bool:
        """Determine if a given camera is streaming."""
        return bool(
            camera
            and KEY_STREAMING_PORT in camera
            and camera.get(KEY_VIDEO_STREAMING, False)
        )

    def get_camera_stream_url(self, camera: dict[str, Any]) -> str | None:
        """Get the camera stream URL."""
        if MotionEyeClient.is_camera_streaming(camera):
            # Remote motionEye instances will provide a host in their camera
            # dictionary, use that if specified, otherwise extract the hostname
            # from the URL (removing the port if present). Url validity is
            # checked on construction so this will always succeed.
            host = camera.get("host", urlsplit(self._url).netloc.split(":")[0])

            # motion (the process underlying motionEye) cannot natively do https on the
            # stream port, it will always be http regardless of what protocol is used to
            # talk to motionEye itself.
            return urlunsplit(
                (
                    DEFAULT_URL_SCHEME,
                    f"{host}:{camera[KEY_STREAMING_PORT]}",
                    "/",
                    "",
                    "",
                )
            )
        return None

    def get_camera_snapshot_url(self, camera: dict[str, Any]) -> str | None:
        """Get the camera snapshot URL."""
        if MotionEyeClient.is_camera_streaming(camera) and KEY_ID in camera:
            return urljoin(self._url, f"/picture/{camera[KEY_ID]}/current/")
        return None

    def _strip_leading_slash(self, path: str) -> str:
        """Strip leading slash from a path."""
        pure_path = PurePath(path)
        if not pure_path.parts:
            raise MotionEyeClientPathError("Could not parse empty path")
        if pure_path.parts[0] == "/":
            path = str(PurePath(*pure_path.parts[1:]))
        return path

    def get_movie_url(self, camera_id: int, path: str, preview: bool = False) -> str:
        """Get the movie playback URL."""
        action = "preview" if preview else "playback"
        return urljoin(
            self._url,
            f"/movie/{camera_id}/{action}/{self._strip_leading_slash(path)}",
        )

    def get_image_url(self, camera_id: int, path: str, preview: bool = False) -> str:
        """Get the image URL."""
        action = "preview" if preview else "download"
        return urljoin(
            self._url,
            f"/picture/{camera_id}/{action}/{self._strip_leading_slash(path)}",
        )

    @classmethod
    def is_file_type_image(self, file_type: int) -> bool:
        """Determine if a file_type represents an image."""
        # It's an image if the event file_type is <8.
        # See: https://github.com/Motion-Project/motion/blob/master/src/motion.h#L177
        return file_type < 8

    @classmethod
    def is_file_type_movie(self, file_type: int) -> bool:
        """Determine if a file_type represents an image."""
        return not self.is_file_type_image(file_type)

    async def async_get_movies(
        self, camera_id: int, prefix: str | None = None
    ) -> dict[str, Any] | None:
        """Get a motionEye camera config."""
        return await self._async_request(
            f"/movie/{camera_id}/list", params={"prefix": prefix} if prefix else None
        )

    async def async_get_images(
        self, camera_id: int, prefix: str | None = None
    ) -> dict[str, Any] | None:
        """Get a motionEye camera config."""
        return await self._async_request(
            f"/picture/{camera_id}/list", params={"prefix": prefix} if prefix else None
        )

    async def async_get_snapshot_image(self, camera_id: int) -> bytes | None:
        """Fetch the current snapshot image using the authenticated session."""
        result = await self._async_request(
            f"/picture/{camera_id}/current/", _raw=True
        )
        return result if isinstance(result, bytes) else None
