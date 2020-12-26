# Copyright (c) 2020 AllSeeingEyeTolledEweSew
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

import json
import pathlib

from . import site


class Storage:
    def __init__(self, path: pathlib.Path):
        self.path = path

    @property
    def auth_file_path(self) -> pathlib.Path:
        return self.path.joinpath("auth.json")

    def get_user_auth(self) -> site.UserAuth:
        data = json.loads(self.auth_file_path.read_text())
        return site.UserAuth(**data)

    @property
    def metadata_db_path(self) -> pathlib.Path:
        return self.path.joinpath("metadata.db")

    @property
    def user_db_path(self) -> pathlib.Path:
        return self.path.joinpath("user.db")
