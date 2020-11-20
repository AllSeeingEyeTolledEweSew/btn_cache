import enum
from typing import Any
from typing import Dict
from typing import Sequence

from typing_extensions import TypedDict


class TorrentEntry(TypedDict):
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


class GetTorrentsResult(TypedDict):
    results: str
    torrents: Dict[str, TorrentEntry]


class SnatchEntryTorrentInfo(TypedDict):
    GroupName: str
    Series: str
    Year: str
    Source: str
    Container: str
    Codec: str
    Resolution: str


class SnatchEntry(TypedDict):
    Downloaded: str
    IsSeeding: str
    Ratio: str
    Seedtime: str
    SnatchTime: str
    TorrentID: str
    TorrentInfo: SnatchEntryTorrentInfo
    Uploaded: str


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


class GetUserSnatchlistResult(TypedDict):
    results: str
    torrents: Dict[str, SnatchEntry]


class Request(TypedDict):
    jsonrpc: str
    id: Any
    method: str
    params: Sequence[Any]


class ErrorCode(enum.IntEnum):
    INVALID_API_KEY = -32001
    CALL_LIMIT_EXCEEDED = -32002


class Error(TypedDict):
    message: str
    code: ErrorCode


class Response(TypedDict, total=False):
    id: Any
    result: Any
    error: Error
