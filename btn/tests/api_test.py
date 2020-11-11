import abc
from typing import Any
from typing import cast
from typing import Sequence
import unittest

import requests
import requests_mock

from btn import api as api_lib
from btn import api_types
from btn import ratelimit


def mock_request(
    mock: requests_mock.Mocker,
    method: str,
    params: Sequence[Any],
    result: Any = None,
) -> None:
    def match(req: requests.Request) -> bool:
        json_request = cast(api_types.Request, req.json())
        json_request["id"] = "dummy_id"
        return json_request == api_types.Request(
            jsonrpc="2.0", id="dummy_id", method=method, params=params
        )

    response = api_types.Response(result=result, id="dummy_id")
    mock.post(
        "https://api.broadcasthe.net/", additional_matcher=match, json=response
    )


def mock_api_error(
    mock: requests_mock.Mocker, message: str, code: api_types.ErrorCode
) -> None:
    error = api_types.Error(message=message, code=code)
    response = api_types.Response(id="dummy_id", error=error)
    mock.post("https://api.broadcasthe.net/", json=response)


class APICallTestBase(unittest.TestCase, abc.ABC):
    def setUp(self) -> None:
        self.key = "dummy_key"
        self.api = api_lib.API(self.key)

    @abc.abstractmethod
    def call(self) -> None:
        pass

    def test_http_error(self, mock: requests_mock.Mocker) -> None:
        mock.post("https://api.broadcasthe.net/", status_code=500)
        with self.assertRaises(requests.HTTPError):
            self.call()

    def test_connection_error(self, mock: requests_mock.Mocker) -> None:
        mock.post(
            "https://api.broadcasthe.net/", exc=requests.ConnectionError()
        )
        with self.assertRaises(requests.ConnectionError):
            self.call()

    def test_invalid_key(self, mock: requests_mock.Mocker) -> None:
        mock_api_error(
            mock, "Invalid API Key", api_types.ErrorCode.INVALID_API_KEY
        )
        with self.assertRaises(api_lib.InvalidAPIKeyError):
            self.call()

    def test_call_limit_exceeded(self, mock: requests_mock.Mocker) -> None:
        mock_api_error(
            mock,
            "Call Limit Exceeded",
            api_types.ErrorCode.CALL_LIMIT_EXCEEDED,
        )
        with self.assertRaises(api_lib.CallLimitExceededError):
            self.call()


@requests_mock.Mocker()
class GetTorrentsTest(APICallTestBase, unittest.TestCase):
    def call(self):
        self.api.getTorrents()

    def test_call(self, mock: requests_mock.Mocker) -> None:
        result = api_types.GetTorrentsResult(results="123", torrents={})
        mock_request(mock, "getTorrents", [self.key, {}, 10, 0], result=result)

        self.assertEqual(self.api.getTorrents(), result)

    def test_limit_offset(self, mock: requests_mock.Mocker) -> None:
        result = api_types.GetTorrentsResult(results="123", torrents={})
        mock_request(
            mock, "getTorrents", [self.key, {}, 100, 50], result=result
        )

        self.assertEqual(self.api.getTorrents(results=100, offset=50), result)

    def test_filter(self, mock: requests_mock.Mocker) -> None:
        result = api_types.GetTorrentsResult(results="123", torrents={})
        mock_request(
            mock,
            "getTorrents",
            [self.key, {"Series": "Example"}, 10, 0],
            result=result,
        )

        self.assertEqual(self.api.getTorrents(Series="Example"), result)


@requests_mock.Mocker()
class GetUserSnatchlistTest(APICallTestBase, unittest.TestCase):
    def call(self):
        self.api.getUserSnatchlist()

    def test_call(self, mock: requests_mock.Mocker) -> None:
        result = api_types.GetUserSnatchlistResult(results="123", torrents={})
        mock_request(
            mock, "getUserSnatchlist", [self.key, 10, 0], result=result
        )

        self.assertEqual(self.api.getUserSnatchlist(), result)

    def test_limit_offset(self, mock: requests_mock.Mocker) -> None:
        result = api_types.GetUserSnatchlistResult(results="123", torrents={})
        mock_request(
            mock, "getUserSnatchlist", [self.key, 100, 50], result=result
        )

        self.assertEqual(
            self.api.getUserSnatchlist(results=100, offset=50), result
        )


class RateLimitedAPITest(unittest.TestCase):
    def setUp(self) -> None:
        self.session = requests.Session()
        self.dummy_response = api_types.Response(
            id="dummy_id",
            result=api_types.GetTorrentsResult(results="123", torrents={}),
        )
        self.mock_adapter = requests_mock.adapter.Adapter()
        self.mock_adapter.register_uri(
            "post", "https://api.broadcasthe.net/", json=self.dummy_response
        )
        self.session.mount("http://", self.mock_adapter)
        self.session.mount("https://", self.mock_adapter)

    def test_nonblocking(self) -> None:
        self.mock_adapter.register_uri(
            "post", "https://api.broadcasthe.net/", json=self.dummy_response
        )
        rate_limiter = ratelimit.APIRateLimiter(blocking=False)
        api = api_lib.RateLimitedAPI(
            "dummy_key", rate_limiter, session=self.session
        )

        # Shouldn't block for first N calls
        for _ in range(150):
            api.getTorrents()

        # Next call should block
        with self.assertRaises(ratelimit.RequestWouldBlock):
            api.getTorrents()

    def test_call_limit_exceeded(self) -> None:
        error_response = api_types.Response(
            id="dummy_id",
            error=api_types.Error(
                message="Call Limit Exceeded",
                code=api_types.ErrorCode.CALL_LIMIT_EXCEEDED,
            ),
        )
        self.mock_adapter.register_uri(
            "post", "https://api.broadcasthe.net/", json=error_response
        )
        rate_limiter = ratelimit.APIRateLimiter(blocking=False)
        api = api_lib.RateLimitedAPI(
            "dummy_key", rate_limiter, session=self.session
        )

        with self.assertRaises(api_lib.CallLimitExceededError):
            api.getTorrents()

        # Next call should block
        with self.assertRaises(ratelimit.RequestWouldBlock):
            api.getTorrents()


# Delete abstract test bases so they're not picked up by the loader
del APICallTestBase
