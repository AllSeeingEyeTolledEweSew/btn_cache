# Copyright (c) 2021 AllSeeingEyeTolledEweSew
#
# Permission to use, copy, modify, and/or distribute this software for any
# purpose with or without fee is hereby granted.
#
# THE SOFTWARE IS PROVIDED "AS IS" AND THE AUTHOR DISCLAIMS ALL WARRANTIES WITH
# REGARD TO THIS SOFTWARE INCLUDING ALL IMPLIED WARRANTIES OF MERCHANTABILITY
# AND FITNESS. IN NO EVENT SHALL THE AUTHOR BE LIABLE FOR ANY SPECIAL, DIRECT,
# INDIRECT, OR CONSEQUENTIAL DAMAGES OR ANY DAMAGES WHATSOEVER RESULTING FROM
# LOSS OF USE, DATA OR PROFITS, WHETHER IN AN ACTION OF CONTRACT, NEGLIGENCE OR
# OTHER TORTIOUS ACTION, ARISING OUT OF OR IN CONNECTION WITH THE USE OR
# PERFORMANCE OF THIS SOFTWARE.

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
