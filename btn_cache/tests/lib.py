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

import io
import os
import pathlib
import re
import sqlite3
from typing import Any
from typing import cast
from typing import Iterator
from typing import TextIO
from typing import Tuple
import unittest

import importlib_resources


def dump_value(out: TextIO, value: Any) -> None:
    if value is None:
        out.write("NULL")
    elif isinstance(value, (int, float)):
        out.write(str(value))
    elif isinstance(value, bytes):
        out.write("X'")
        for byte in value:
            out.write(format(byte, "02X"))
        out.write("'")
    elif isinstance(value, str):
        out.write("'")
        value = re.sub(r"'", "''", value)
        value = re.sub("\0", "'||X'00'||'", value)
        out.write(value)
        out.write("'")
    else:
        raise TypeError(f"unsupported value: {value}")


def dump_conn(out: TextIO, conn: sqlite3.Connection, include_schema=False) -> None:
    schema_cur = conn.cursor()
    schema_cur.execute("select name, sql from sqlite_master where type = 'table'")
    for name, sql in cast(Iterator[Tuple[str, str]], schema_cur):
        # Write CREATE TABLE first
        if include_schema:
            out.write(sql)
            out.write(";\n")
        # Write INSERTs
        cur = conn.cursor()
        cur.execute(f"select * from {name}")
        for row in cur:
            out.write("INSERT INTO ")
            out.write(name)
            out.write(" VALUES (")
            for i, col in enumerate(row):
                if i != 0:
                    out.write(", ")
                dump_value(out, col)
            out.write(");\n")
        # Write indexes and triggers
        if include_schema:
            cur.execute(
                "select sql from sqlite_master where tbl_name = ? "
                "and type in ('index', 'trigger')"
            )
            for (sql,) in cast(Iterator[Tuple[str]], cur):
                out.write(sql)
                out.write(";\n")


class BaseTest(unittest.TestCase):

    maxDiff = None

    def get_meld_path(self, suffix: str) -> pathlib.Path:
        # importlib.resources doesn't provide any way for updating files
        # that are assumed to be individually accessible on the filesystem. So
        # for updating golden data, we use the "naive" approach of referencing
        # a file based off of the __file__ path.
        return (
            pathlib.Path(__file__)
            .parent.joinpath("data", f"{self.id()}.golden.dummy")
            .with_suffix(suffix)
        )

    def get_data(self, suffix: str) -> str:
        return importlib_resources.read_text(
            "btn_cache.tests.data", f"{self.id()}.golden{suffix}"
        )

    def assert_golden(self, value: str, suffix: str = ".txt") -> None:
        if os.environ.get("GOLDEN_MELD"):
            self.get_meld_path(suffix).write_text(value)
        else:
            second = self.get_data(suffix)
            self.assertEqual(value, second)

    def assert_golden_db(
        self,
        conn: sqlite3.Connection,
        include_schema: bool = False,
        suffix: str = ".sql",
    ) -> None:
        writer = io.StringIO()
        dump_conn(writer, conn, include_schema=include_schema)
        self.assert_golden(writer.getvalue(), suffix=suffix)
