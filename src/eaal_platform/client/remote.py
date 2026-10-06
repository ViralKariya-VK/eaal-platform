"""Talking to a CAVY server over HTTP."""

from __future__ import annotations

import contextlib
from typing import Any

import httpx

_TIMEOUT = httpx.Timeout(60.0, connect=5.0)  # AI replies and scoring can be slow
_POLL_TIMEOUT = httpx.Timeout(40.0, connect=5.0)  # the server holds a poll open for ~25 s


class ServerUnreachableError(ConnectionError):
    """The server couldn't be contacted (wrong address, offline, firewall...)."""


class RemoteBackend:
    """Same method names as ``CavyApi``, executed on the server."""

    def __init__(self, base_url: str, http: httpx.Client | None = None) -> None:
        self.base_url = base_url.rstrip("/")
        self._http = http or httpx.Client(timeout=_TIMEOUT)
        self._token: str | None = None
        # Why the server ended our session (e.g. "signed out by an administrator"),
        # kept so every later call explains itself instead of failing vaguely.
        self._signed_out_reason: str | None = None

    # -- plumbing --------------------------------------------------------

    def _post(
        self,
        path: str,
        body: dict[str, Any],
        *,
        auth: bool = True,
        timeout: httpx.Timeout | None = None,
    ) -> httpx.Response:
        if auth and self._token is None and self._signed_out_reason:
            raise PermissionError(self._signed_out_reason)
        headers = {"Authorization": f"Bearer {self._token}"} if auth and self._token else {}
        try:
            if timeout is None:
                return self._http.post(f"{self.base_url}{path}", json=body, headers=headers)
            return self._http.post(
                f"{self.base_url}{path}", json=body, headers=headers, timeout=timeout
            )
        except httpx.HTTPError as exc:
            raise ServerUnreachableError(
                f"Can't reach the CAVY server at {self.base_url}. "
                "Check that it is running and you are on the same network."
            ) from exc

    @staticmethod
    def _error_message(response: httpx.Response) -> str:
        try:
            data = response.json()
        except ValueError:
            return f"The server returned an error ({response.status_code})."
        return str(data.get("error") or data.get("detail") or "The server returned an error.")

    def health(self) -> dict[str, Any]:
        try:
            response = self._http.get(f"{self.base_url}/api/health")
            data: dict[str, Any] = response.json()
        except (httpx.HTTPError, ValueError) as exc:
            raise ServerUnreachableError(f"Can't reach a CAVY server at {self.base_url}.") from exc
        if data.get("service") != "cavy":
            raise ServerUnreachableError(f"{self.base_url} isn't a CAVY server.")
        return data

    # -- calls ---------------------------------------------------------------

    def login(self, role: str, email: str, password: str) -> dict[str, Any]:
        response = self._post(
            "/api/login", {"role": role, "email": email, "password": password}, auth=False
        )
        if response.status_code != 200:
            raise ValueError(self._error_message(response))
        result: dict[str, Any] = response.json()
        self._token = result.pop("token", None)
        self._signed_out_reason = None
        return result

    def logout(self) -> None:
        if self._token is not None:
            # Signing out must always succeed locally, even if the server is gone.
            with contextlib.suppress(ServerUnreachableError):
                self._post("/api/logout", {})
        self._token = None
        self._signed_out_reason = None

    def create_account(self, *args: Any) -> dict[str, Any]:
        response = self._post("/api/create_account", {"args": list(args)}, auth=False)
        if response.status_code != 200:
            raise ValueError(self._error_message(response))
        result: dict[str, Any] = response.json()["result"]
        return result

    def get_academic_options(self) -> list[dict[str, Any]]:
        """The sign-up form's dropdown lists (needs no sign-in)."""
        try:
            response = self._http.get(f"{self.base_url}/api/academic")
        except httpx.HTTPError as exc:
            raise ServerUnreachableError(
                f"Can't reach the CAVY server at {self.base_url}."
            ) from exc
        if response.status_code != 200:
            raise ValueError(self._error_message(response))
        options: list[dict[str, Any]] = response.json()
        return options

    def get_signup_info(self) -> dict[str, Any]:
        """Whether this server emails students their first login (needs no sign-in)."""
        try:
            response = self._http.get(f"{self.base_url}/api/signup_info")
        except httpx.HTTPError as exc:
            raise ServerUnreachableError(
                f"Can't reach the CAVY server at {self.base_url}."
            ) from exc
        info: dict[str, Any] = response.json() if response.status_code == 200 else {}
        return {"email_signup": bool(info.get("email_signup"))}

    def request_login(self, email: str) -> dict[str, Any]:
        """Ask the server to email a first-login password to this (approved) address."""
        response = self._post("/api/request_login", {"email": email}, auth=False)
        if response.status_code != 200:
            raise ValueError(self._error_message(response))
        result: dict[str, Any] = response.json()["result"]
        return result

    def call(self, method: str, *args: Any) -> Any:
        response = self._post(f"/api/call/{method}", {"args": list(args)})
        self._raise_if_signed_out(response)
        if response.status_code != 200:
            raise ValueError(self._error_message(response))
        return response.json()["result"]

    def _raise_if_signed_out(self, response: httpx.Response) -> None:
        if response.status_code == 401:
            self._token = None
            self._signed_out_reason = self._error_message(response)
            raise PermissionError(self._signed_out_reason)

    def updates(self, versions: dict[str, int], wait: bool = True) -> dict[str, Any]:
        """Wait (up to ~25 s) until the server reports something new."""
        response = self._post(
            "/api/updates", {"versions": versions, "wait": wait}, timeout=_POLL_TIMEOUT
        )
        self._raise_if_signed_out(response)
        if response.status_code != 200:
            raise ValueError(self._error_message(response))
        result: dict[str, Any] = response.json()
        return result

    def __getattr__(self, name: str) -> Any:
        if name.startswith("_"):
            raise AttributeError(name)

        def method(*args: Any) -> Any:
            return self.call(name, *args)

        return method
