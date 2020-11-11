import enum
import sys
import typing
from typing import Any
from typing import Dict
from typing import Sequence

if sys.version_info >= (3, 8):

    class TorrentEntry(typing.TypedDict):
        Category: str
        Codec: str
        Container: str
        DownloadURL: str
        GroupID: str
        GroupName: str
        ImdbID: str
        InfoHash: str
        Leechers: str
        Origin: str
        ReleaseName: str
        Resolution: str
        Seeders: str
        Series: str
        SeriesBanner: str
        SeriesID: str
        SeriesPoster: str
        Size: str
        Snatched: str
        Source: str
        Time: str
        TorrentID: str
        TvdbID: str
        TvrageID: str
        YoutubeTrailer: str


else:
    TorrentEntry = Dict[str, Any]

TORRENT_ENTRY_KEYS = {
    "Category",
    "Codec",
    "Container",
    "DownloadURL",
    "GroupID",
    "GroupName",
    "ImdbID",
    "InfoHash",
    "Leechers",
    "Origin",
    "ReleaseName",
    "Resolution",
    "Seeders",
    "Series",
    "SeriesBanner",
    "SeriesID",
    "SeriesPoster",
    "Size",
    "Snatched",
    "Source",
    "Time",
    "TorrentID",
    "TvdbID",
    "TvrageID",
    "YoutubeTrailer",
}


if sys.version_info >= (3, 8):

    class GetTorrentsResult(typing.TypedDict):
        results: str
        torrents: Dict[str, TorrentEntry]


else:
    GetTorrentsResult = Dict[str, Any]


if sys.version_info >= (3, 8):

    class SnatchEntryTorrentInfo(typing.TypedDict):
        GroupName: str
        Series: str
        Year: str
        Source: str
        Container: str
        Codec: str
        Resolution: str


else:
    SnatchEntryTorrentInfo = Dict[str, Any]


if sys.version_info >= (3, 8):

    class SnatchEntry(typing.TypedDict):
        Downloaded: str
        IsSeeding: str
        Ratio: str
        Seedtime: str
        SnatchTime: str
        TorrentID: str
        TorrentInfo: SnatchEntryTorrentInfo
        Uploaded: str


else:
    SnatchEntry = Dict[str, Any]

SNATCH_ENTRY_KEYS = {
    "Downloaded",
    "IsSeeding",
    "Ratio",
    "Seedtime",
    "SnatchTime",
    "TorrentID",
    "TorrentInfo",
    "Uploaded",
}


if sys.version_info >= (3, 8):

    class GetUserSnatchlistResult(typing.TypedDict):
        results: str
        torrents: Dict[str, SnatchEntry]


else:
    GetUserSnatchlistResult = Dict[str, Any]


if sys.version_info >= (3, 8):

    class Request(typing.TypedDict):
        jsonrpc: str
        id: Any
        method: str
        params: Sequence[Any]


else:
    Request = Dict[str, Any]


class ErrorCode(enum.IntEnum):
    INVALID_API_KEY = -32001
    CALL_LIMIT_EXCEEDED = -32002


if sys.version_info >= (3, 8):

    class Error(typing.TypedDict):
        message: str
        code: ErrorCode


else:
    Error = Dict[str, Any]


if sys.version_info >= (3, 8):

    class Response(typing.TypedDict, total=False):
        id: Any
        result: Any
        error: Error


else:
    Response = Dict[str, Any]
