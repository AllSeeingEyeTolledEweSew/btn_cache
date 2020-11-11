import importlib.resources
import io
import os
import pathlib
import re
import unittest

import apsw


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
        return importlib.resources.read_text(  # type: ignore
            "btn.tests.data", f"{self.id()}.golden{suffix}"
        )

    def assert_golden(self, value: str, suffix: str = ".txt") -> None:
        if os.environ.get("GOLDEN_MELD"):
            self.get_meld_path(suffix).write_text(value)
        else:
            second = self.get_data(suffix)
            self.assertEqual(value, second)

    def assert_golden_db(
        self,
        conn: apsw.Connection,
        suffix: str = ".sql",
        include_schema: bool = False,
    ) -> None:
        output_file = io.StringIO()
        shell = apsw.Shell(db=conn, stdout=output_file)
        shell.process_command(".dump")
        output = output_file.getvalue()
        # Remove comments, which include unstable data like timestamps,
        # usernames and hostnames.
        output = re.sub(r"-- (.*?)\n", "", output)
        if not include_schema:
            output = "\n".join(
                line
                for line in output.split("\n")
                if line.startswith("INSERT ")
            )
        self.assert_golden(output, suffix=suffix)
