# The author disclaims copyright to this source code. Please see the
# accompanying UNLICENSE file.
from typing import Any
from typing import cast
import logging

import requests

from . import api_types
from . import ratelimit

_LOG = logging.getLogger(__name__)


class Error(Exception):
    pass


class APIError(Error):
    def __init__(self, message: str, code: api_types.ErrorCode) -> None:
        super().__init__(message)
        self.code = code


class CallLimitExceededError(APIError):

    pass


class InvalidAPIKeyError(APIError):

    pass


_ENDPOINT = "https://api.broadcasthe.net/"


def _mk_api_error(message: str, code: api_types.ErrorCode) -> APIError:
    cls = {
        api_types.ErrorCode.INVALID_API_KEY: InvalidAPIKeyError,
        api_types.ErrorCode.CALL_LIMIT_EXCEEDED:
        CallLimitExceededError}.get(code, APIError)
    return cls(message, code)


class API:
    def __init__(self, key: str, *, session: requests.Session = None,
            timeout:float=60) -> None:
        self.key = key
        if session is None:
            session = requests.Session()
        self._session = session
        self._timeout = timeout

    def call(self, method: str, *params: Any) -> Any:
        params = (self.key, ) + params
        request = api_types.Request(jsonrpc="2.0",
                                    id=1,
                                    method=method,
                                    params=params)
        headers = {"Content-Type": "application/json"}

        response = self._session.post(_ENDPOINT, headers=headers, json=request,
                timeout=self._timeout)
        response.raise_for_status()

        api_response = cast(api_types.Response, response.json())
        if "error" in api_response:
            error = api_response["error"]
            _LOG.error("%s: error code %s: %s", method, error["code"],
            error["message"])
            raise _mk_api_error(error["message"], error["code"])

        return api_response["result"]

    def getTorrents(self,
                    results: int = 10,
                    offset: int = 0,
                    **kwargs: Any) -> api_types.GetTorrentsResult:
        result = cast(api_types.GetTorrentsResult,
                    self.call("getTorrents", kwargs, results, offset))
        _LOG.debug("getTorrents: got %d entries, %s total",
                len(result["torrents"]), result["results"])
        return result

    def getUserSnatchlist(
            self,
            results: int = 10,
            offset: int = 0) -> api_types.GetUserSnatchlistResult:
        result = cast(api_types.GetUserSnatchlistResult,
                    self.call("getUserSnatchlist", results, offset))
        _LOG.debug("getUserSnatchlist: got %d entries, %s total",
            len(result["torrents"]), result["results"])
        return result


class RateLimitedAPI(API):
    def __init__(self,
                 key: str,
                 rate_limiter: ratelimit.APIRateLimiter = None,
                 session: requests.Session = None,
                 timeout:float=60) -> None:
        super().__init__(key, session=session, timeout=timeout)
        if rate_limiter is None:
            rate_limiter = ratelimit.APIRateLimiter()
        self._rate_limiter = rate_limiter
        ratelimit.ratelimit_session(self._session, _ENDPOINT,
                                    self._rate_limiter)

    def call(self, method: str, *params: Any) -> Any:
        try:
            return super().call(method, *params)
        except CallLimitExceededError:
            self._rate_limiter.set_remaining(0)
            raise

    def get_rate_limiter(self) -> ratelimit.APIRateLimiter:
        return self._rate_limiter
