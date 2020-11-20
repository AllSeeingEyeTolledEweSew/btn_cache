import abc
import contextlib
import sqlite3
import threading
import time
from typing import Any
from typing import cast
from typing import Dict
from typing import Iterator
from typing import Sequence
from typing import TypeVar
import unittest
import unittest.mock

import importlib_resources
import requests
import requests_mock
import requests_mock.adapter

from btn_cache import api as api_lib
from btn_cache import api_types
from btn_cache import metadata_db
from btn_cache import scrape
from btn_cache import site

_T = TypeVar("_T")


@contextlib.contextmanager
def nullcontext(value: _T) -> Iterator[_T]:
    yield value


# request_mock's class decorator is usually a better approach than this.
# However, we want to set up complex mocks in a resuable way and test the
# matchers of each. We could do this with a method that assigns the matchers as
# properties, but static analysis tools want us to only assign properties in
# setUp() or __init__().
class RequestsMockerTestBase(unittest.TestCase):
    def setUp(self) -> None:
        self.requests_mocker = requests_mock.Mocker()
        self.requests_mocker.start()

    def tearDown(self) -> None:
        self.requests_mocker.stop()


class APITestBase(RequestsMockerTestBase):
    def setUp(self) -> None:
        super().setUp()
        self.key = "dummy_key"
        self.api = api_lib.RateLimitedAPI(self.key)

    def mock_api_request(
        self, method: str, params: Sequence[Any], result: Any = None
    ) -> requests_mock.adapter._Matcher:
        def match(req: requests.Request) -> bool:
            json_request = cast(api_types.Request, req.json())
            json_request["id"] = "dummy_id"
            return json_request == api_types.Request(
                jsonrpc="2.0", id="dummy_id", method=method, params=params
            )

        response = api_types.Response(result=result, id="dummy_id")
        return self.requests_mocker.post(
            "https://api.broadcasthe.net/",
            additional_matcher=match,
            json=response,
        )

    def mock_api_error(
        self, message: str, code: api_types.ErrorCode
    ) -> requests_mock.adapter._Matcher:
        error = api_types.Error(message=message, code=code)
        response = api_types.Response(id="dummy_id", error=error)
        return self.requests_mocker.post(
            "https://api.broadcasthe.net/", json=response
        )


class APIErrorsBase(APITestBase, abc.ABC):
    @abc.abstractmethod
    def step(self) -> float:
        raise NotImplementedError

    def test_api_http_error(self) -> None:
        self.requests_mocker.post(
            "https://api.broadcasthe.net/", status_code=500
        )
        with self.assertRaises(scrape.NonFatal):
            self.step()

    def test_api_connection_error(self) -> None:
        self.requests_mocker.post(
            "https://api.broadcasthe.net/", exc=requests.ConnectionError()
        )
        with self.assertRaises(scrape.NonFatal):
            self.step()

    def test_api_invalid_key(self) -> None:
        self.mock_api_error(
            "Invalid API Key", api_types.ErrorCode.INVALID_API_KEY
        )
        with self.assertRaises(api_lib.InvalidAPIKeyError):
            self.step()

    def test_api_call_limit_exceeded(self) -> None:
        self.mock_api_error(
            "Call Limit Exceeded", api_types.ErrorCode.CALL_LIMIT_EXCEEDED
        )
        wait = self.step()
        self.assertLessEqual(wait, 0)


class UserAccessErrorsBase(RequestsMockerTestBase, abc.ABC):
    @abc.abstractmethod
    def step(self) -> float:
        raise NotImplementedError

    def test_site_http_error(self) -> None:
        self.requests_mocker.post(requests_mock.ANY, status_code=500)
        self.requests_mocker.get(requests_mock.ANY, status_code=500)
        with self.assertRaises(scrape.NonFatal):
            self.step()

    def test_site_connection_error(self) -> None:
        self.requests_mocker.post(
            requests_mock.ANY, exc=requests.ConnectionError()
        )
        self.requests_mocker.get(
            requests_mock.ANY, exc=requests.ConnectionError()
        )
        with self.assertRaises(scrape.NonFatal):
            self.step()


TEST_ENTRY = api_types.TorrentEntry(
    Category="Episode",
    Codec="H.264",
    Container="MKV",
    DownloadURL="https://example.com/unused",
    GroupID="234",
    GroupName="S01E01",
    ImdbID="1234567",
    InfoHash="F" * 40,
    Leechers="1",
    Origin="P2P",
    ReleaseName="example.s01e01.coolkids",
    Resolution="1080p",
    Seeders="10",
    Series="Example",
    SeriesBanner="https://example.com/banner.jpg",
    SeriesID="345",
    SeriesPoster="https://example.com/poster.jpg",
    Size="12345678",
    Snatched="100",
    Source="HDTV",
    Time="123456789",
    TorrentID="123",
    TvdbID="456",
    TvrageID="567",
    YoutubeTrailer="https://www.youtube.com/v/abcdefghijk",
)

TEST_AUTH = site.UserAuth(
    user_id=12345,
    auth="dummy_auth",
    authkey="dummy_authkey",
    passkey="dummy_passkey",
)


class MetadataScraperTest(APIErrorsBase):
    def setUp(self) -> None:
        super().setUp()
        self.metadata_conn = sqlite3.Connection(
            ":memory:", isolation_level=None
        )
        self.metadata_pool = lambda: nullcontext(self.metadata_conn)

        self.scraper = scrape.MetadataScraper(
            api=self.api, metadata_pool=self.metadata_pool
        )

        torrents: Dict[str, api_types.TorrentEntry] = {}
        for i in range(5, 10):
            entry = TEST_ENTRY.copy()
            entry["TorrentID"] = str(i)
            entry["Time"] = str(i)
            torrents[str(i)] = entry
        result1 = api_types.GetTorrentsResult(results="9", torrents=torrents)
        torrents = {}
        for i in range(1, 6):
            entry = TEST_ENTRY.copy()
            entry["TorrentID"] = str(i)
            entry["Time"] = str(i)
            torrents[str(i)] = entry
        result2 = api_types.GetTorrentsResult(results="9", torrents=torrents)
        self.mock1 = self.mock_api_request(
            "getTorrents", [self.key, {}, 2 ** 31, 0], result1
        )
        self.mock2 = self.mock_api_request(
            "getTorrents", [self.key, {}, 2 ** 31, 4], result2
        )

    def step(self) -> float:
        return self.scraper.step()

    def test_manually_step(self) -> None:
        # First step should start scraping at offset zero
        wait = self.step()
        self.assertLessEqual(wait, 0)
        self.assertEqual(self.mock1.call_count, 1)
        cur = self.metadata_conn.cursor().execute(
            "select id from torrent_entry"
        )
        self.assertEqual({i for i, in cur}, {5, 6, 7, 8, 9})
        # Second step should scrape at further offset
        wait = self.step()
        self.assertLessEqual(wait, 0)
        self.assertEqual(self.mock2.call_count, 1)
        cur = self.metadata_conn.cursor().execute(
            "select id from torrent_entry"
        )
        self.assertEqual({i for i, in cur}, {1, 2, 3, 4, 5, 6, 7, 8, 9})
        # Third step should detect end of list, and restart at zero
        wait = self.step()
        self.assertLessEqual(wait, 0)
        self.assertEqual(self.mock1.call_count, 2)

    def test_run_terminate(self) -> None:
        def thread():
            time.sleep(0.15)
            self.scraper.terminate()

        threading.Thread(target=thread).start()
        self.scraper.run()


class MetadataTipScraperTest(APIErrorsBase):
    def setUp(self) -> None:
        super().setUp()
        self.metadata_conn = sqlite3.Connection(
            ":memory:", isolation_level=None
        )
        self.metadata_pool = lambda: nullcontext(self.metadata_conn)

        self.scraper = scrape.MetadataTipScraper(
            api=self.api,
            metadata_pool=self.metadata_pool,
            user_access=site.UserAccess(auth=TEST_AUTH),
        )

        # Set up mock response for feed
        feed_content = importlib_resources.read_binary(
            "btn_cache.tests", "test_feed.xml"
        )
        # Don't need full query string for match
        self.requests_mocker.get(
            "https://broadcasthe.net/feeds.php",
            content=feed_content,
            headers={"Content-Type": "application/xml"},
        )

        self.torrents: Dict[str, api_types.TorrentEntry] = {}
        for i in range(1, 6):
            entry = TEST_ENTRY.copy()
            entry["TorrentID"] = str(i)
            entry["Time"] = str(i)
            self.torrents[str(i)] = entry
        result = api_types.GetTorrentsResult(
            results="5", torrents=self.torrents
        )
        self.api_mock = self.mock_api_request(
            "getTorrents", [self.key, {}, 2 ** 31, 0], result
        )

    def step(self) -> float:
        return self.scraper.step()

    def test_step_empty_db(self) -> None:
        wait = self.step()
        self.assertGreater(wait, 0)
        cur = self.metadata_conn.cursor()
        cur.execute("select id from torrent_entry")
        self.assertEqual({i for i, in cur}, {1, 2, 3, 4, 5})

    def test_step_no_changes(self) -> None:
        wait = self.step()
        self.assertGreater(wait, 0)
        self.assertEqual(self.api_mock.call_count, 1)
        wait = self.step()
        self.assertGreater(wait, 0)
        self.assertEqual(self.api_mock.call_count, 1)

    def test_step_some_data_in_db(self) -> None:
        metadata_db.upgrade(self.metadata_conn)
        metadata_db.TorrentEntriesUpdate(self.torrents["3"]).apply(
            self.metadata_conn
        )
        wait = self.step()
        self.assertGreater(wait, 0)
        self.assertEqual(self.api_mock.call_count, 1)

    def test_run_and_terminate(self) -> None:
        def thread():
            time.sleep(0.25)
            self.scraper.terminate()

        threading.Thread(target=thread).start()
        self.scraper.run()


class MetadataTipScraperSiteErrorTest(APITestBase, UserAccessErrorsBase):
    def setUp(self) -> None:
        super().setUp()
        self.metadata_conn = sqlite3.Connection(
            ":memory:", isolation_level=None
        )
        self.metadata_pool = lambda: nullcontext(self.metadata_conn)

        self.scraper = scrape.MetadataTipScraper(
            api=self.api,
            metadata_pool=self.metadata_pool,
            user_access=site.UserAccess(auth=TEST_AUTH),
        )

    def step(self) -> float:
        return self.scraper.step()


TEST_SNATCH = api_types.SnatchEntry(
    TorrentID="100",
    Downloaded="1000",
    Uploaded="2000",
    Ratio="---",
    Seedtime="86400",
    IsSeeding="1",
    SnatchTime="2000-01-01 01:02:03",
    TorrentInfo=api_types.SnatchEntryTorrentInfo(
        GroupName="S01E01",
        Series="Example",
        Year="2000",
        Source="HDTV",
        Container="MKV",
        Codec="H.264",
        Resolution="1080p",
    ),
)


class SnatchlistScraperTest(APIErrorsBase):
    def setUp(self) -> None:
        super().setUp()
        self.user_conn = sqlite3.Connection(":memory:", isolation_level=None)
        self.user_pool = lambda: nullcontext(self.user_conn)

        self.scraper = scrape.SnatchlistScraper(
            api=self.api, user_pool=self.user_pool
        )

        self.result1 = api_types.GetUserSnatchlistResult(
            results="10", torrents={}
        )
        for i in range(6, 11):
            snatch = TEST_SNATCH.copy()
            snatch["TorrentID"] = str(i)
            self.result1["torrents"][str(i)] = snatch
        self.mock1 = self.mock_api_request(
            "getUserSnatchlist", [self.key, 10000, 0], self.result1
        )

        self.result2 = api_types.GetUserSnatchlistResult(
            results="10", torrents={}
        )
        for i in range(1, 6):
            snatch = TEST_SNATCH.copy()
            snatch["TorrentID"] = str(i)
            self.result2["torrents"][str(i)] = snatch
        self.mock2 = self.mock_api_request(
            "getUserSnatchlist", [self.key, 10000, 5], self.result2
        )

    def step(self) -> float:
        return self.scraper.step()

    def test_manually_step(self) -> None:
        wait = self.step()
        self.assertLessEqual(wait, 0)
        cur = self.user_conn.cursor().execute("select id from snatchlist")
        self.assertEqual({i for i, in cur}, {6, 7, 8, 9, 10})

        wait = self.step()
        self.assertGreater(wait, 0)
        cur = self.user_conn.cursor().execute("select id from snatchlist")
        self.assertEqual({i for i, in cur}, {1, 2, 3, 4, 5, 6, 7, 8, 9, 10})

        # Should restart to 0
        self.assertEqual(self.mock1.call_count, 1)
        wait = self.step()
        self.assertLessEqual(wait, 0)
        self.assertEqual(self.mock1.call_count, 2)

    def test_run_and_terminate(self) -> None:
        def thread():
            time.sleep(0.25)
            self.scraper.terminate()

        threading.Thread(target=thread).start()
        self.scraper.run()


# Remove abstract test bases from globals, so loaders don't instantiate them
del APITestBase
del APIErrorsBase
del UserAccessErrorsBase
