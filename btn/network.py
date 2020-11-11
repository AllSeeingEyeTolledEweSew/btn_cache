import re

TRACKER_REGEXES = (
    re.compile(r"https?://landof.tv/(?P<passkey>[a-z0-9]{32})/"),
    re.compile(
        r"https?://tracker.broadcasthe.net:34001/"
        r"(?P<passkey>[a-z0-9]{32})/"
    ),
)
"""The minimum time to seed an episode torrent, in seconds."""
EPISODE_SEED_TIME = 24 * 3600
"""The minimum ratio to seed an episode torrent, in seconds."""
EPISODE_SEED_RATIO = 1.0
"""The minimum time to seed a season torrent, in seconds."""
SEASON_SEED_TIME = 120 * 3600
"""The minimum ratio to seed a season torrent, in seconds."""
SEASON_SEED_RATIO = 1.0
"""The minimum fraction of downloaded data for it to count on your history."""
TORRENT_HISTORY_FRACTION = 0.1
