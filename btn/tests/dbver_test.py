import contextlib
from typing import cast
from typing import Collection
from typing import Iterator
from typing import List
from typing import Mapping
from typing import Optional
from typing import Tuple
import unittest

import apsw

from btn import dbver


class DummyException(Exception):
    pass


class NullPoolTest(unittest.TestCase):
    def test_close_normal(self) -> None:
        singleton = apsw.Connection(":memory:")
        pool = dbver.null_pool(lambda: singleton)
        with pool() as conn:
            conn.cursor().execute("create table x (x int primary key)")
        # Test the singleton was closed
        with self.assertRaises(apsw.ConnectionClosedError):
            singleton.cursor()

    def test_close_fail(self) -> None:
        singleton = apsw.Connection(":memory:")
        pool = dbver.null_pool(lambda: singleton)
        with self.assertRaises(DummyException):
            with pool():
                raise DummyException()
        # Test the singleton was closed
        with self.assertRaises(apsw.ConnectionClosedError):
            singleton.cursor()


class BeginTest(unittest.TestCase):
    def setUp(self) -> None:
        self.conn = apsw.Connection(":memory:")
        self.conn.cursor().execute("create table x (x int primary key)")

    def do_success(self, lock_mode: dbver.LockMode) -> None:
        self.assertEqual(self.conn.getautocommit(), True)
        with dbver.begin(self.conn, lock_mode):
            self.assertEqual(self.conn.getautocommit(), False)
            self.conn.cursor().execute("insert into x (x) values (1)")
        self.assertEqual(self.conn.getautocommit(), True)
        self.assertEqual(
            self.conn.cursor().execute("select * from x").fetchall(), [(1,)]
        )

    def do_fail(self, lock_mode: dbver.LockMode) -> None:
        self.assertEqual(self.conn.getautocommit(), True)
        with self.assertRaises(DummyException):
            with dbver.begin(self.conn, lock_mode):
                self.assertEqual(self.conn.getautocommit(), False)
                self.conn.cursor().execute("insert into x (x) values (1)")
                raise DummyException()
        self.assertEqual(self.conn.getautocommit(), True)
        self.assertEqual(
            self.conn.cursor().execute("select * from x").fetchall(), []
        )

    def test_deferred_success(self) -> None:
        self.do_success(dbver.LockMode.DEFERRED)

    def test_deferred_fail(self) -> None:
        self.do_fail(dbver.LockMode.DEFERRED)

    def test_immediate_success(self) -> None:
        self.do_success(dbver.LockMode.IMMEDIATE)

    def test_immediate_fail(self) -> None:
        self.do_fail(dbver.LockMode.IMMEDIATE)

    def test_exclusive_success(self) -> None:
        self.do_success(dbver.LockMode.EXCLUSIVE)

    def test_exclusive_fail(self) -> None:
        self.do_fail(dbver.LockMode.EXCLUSIVE)


class BeginPoolTest(unittest.TestCase):
    def setUp(self) -> None:
        self.conn = apsw.Connection(":memory:")
        self.conn.cursor().execute("create table x (x int primary key)")

        @contextlib.contextmanager
        def null_pool() -> Iterator[apsw.Connection]:
            yield self.conn

        self.pool = null_pool

    def test_success(self) -> None:
        self.assertEqual(self.conn.getautocommit(), True)
        with dbver.begin_pool(self.pool, dbver.LockMode.DEFERRED) as conn:
            self.assertEqual(conn.getautocommit(), False)
            conn.cursor().execute("insert into x (x) values (1)")
        self.assertEqual(self.conn.getautocommit(), True)
        self.assertEqual(
            self.conn.cursor().execute("select * from x").fetchall(), [(1,)]
        )

    def test_fail(self) -> None:
        self.assertEqual(self.conn.getautocommit(), True)
        with self.assertRaises(DummyException):
            with dbver.begin_pool(self.pool, dbver.LockMode.DEFERRED) as conn:
                self.assertEqual(conn.getautocommit(), False)
                conn.cursor().execute("insert into x (x) values (1)")
                raise DummyException()
        self.assertEqual(self.conn.getautocommit(), True)
        self.assertEqual(
            self.conn.cursor().execute("select * from x").fetchall(), []
        )


class SemverBreakingTest(unittest.TestCase):
    def assert_breaking(
        self, breaking: bool, from_version: int, to_version: int
    ) -> None:
        self.assertEqual(
            dbver.semver_is_breaking(from_version, to_version), breaking
        )
        if breaking:
            with self.assertRaises(dbver.VersionError):
                dbver.semver_check_breaking(from_version, to_version)
        else:
            dbver.semver_check_breaking(from_version, to_version)

    def test_zero_nonbreaking(self) -> None:
        self.assert_breaking(False, 0, 2000000)
        self.assert_breaking(False, 0, 0)

    def test_backward_breaking(self) -> None:
        self.assert_breaking(True, 2000000, 0)
        self.assert_breaking(True, 1001000, 1000000)

    def test_forward_breaking(self) -> None:
        self.assert_breaking(True, 1000000, 2000000)
        self.assert_breaking(True, 1999999, 2000000)

    def test_forward_nonbreaking(self) -> None:
        self.assert_breaking(False, 1000000, 1000001)
        self.assert_breaking(False, 1000000, 1001000)
        self.assert_breaking(False, 1000000, 1999999)


class GetApplicationIdTest(unittest.TestCase):
    def setUp(self) -> None:
        self.conn = apsw.Connection(":memory:")
        self.conn.cursor().execute("attach ':memory:' as ?", ("other schema",))

    def test_get_zero(self) -> None:
        self.assertEqual(dbver.get_application_id(self.conn), 0)
        self.assertEqual(dbver.get_application_id(self.conn, "main"), 0)
        self.assertEqual(
            dbver.get_application_id(self.conn, "other schema"), 0
        )

    def test_get_after_set(self) -> None:
        self.conn.cursor().execute("pragma application_id = 1")
        self.conn.cursor().execute('pragma "other schema".application_id = 2')
        self.assertEqual(dbver.get_application_id(self.conn), 1)
        self.assertEqual(dbver.get_application_id(self.conn, "main"), 1)
        self.assertEqual(
            dbver.get_application_id(self.conn, "other schema"), 2
        )


class SetApplicationIdTest(unittest.TestCase):
    def setUp(self) -> None:
        self.conn = apsw.Connection(":memory:")
        self.conn.cursor().execute("attach ':memory:' as ?", ("other schema",))

    def test_set(self) -> None:
        dbver.set_application_id(1, self.conn)
        self.assertEqual(
            self.conn.cursor().execute("pragma application_id").fetchall(),
            [(1,)],
        )

        dbver.set_application_id(2, self.conn, "main")
        self.assertEqual(
            self.conn.cursor().execute("pragma application_id").fetchall(),
            [(2,)],
        )

        dbver.set_application_id(3, self.conn, "other schema")
        self.assertEqual(
            self.conn.cursor()
            .execute('pragma "other schema".application_id')
            .fetchall(),
            [(3,)],
        )

    def test_set_invalid(self) -> None:
        with self.assertRaises(ValueError):
            dbver.set_application_id(1 << 40, self.conn)

        with self.assertRaises(ValueError):
            dbver.set_application_id(-(1 << 40), self.conn)

        with self.assertRaises(TypeError):
            dbver.set_application_id("str", self.conn)  # type: ignore


class GetUserVersionTest(unittest.TestCase):
    def setUp(self) -> None:
        self.conn = apsw.Connection(":memory:")
        self.conn.cursor().execute("attach ':memory:' as ?", ("other schema",))

    def test_get_zero(self) -> None:
        self.assertEqual(dbver.get_user_version(self.conn), 0)
        self.assertEqual(dbver.get_user_version(self.conn, "main"), 0)
        self.assertEqual(dbver.get_user_version(self.conn, "other schema"), 0)

    def test_get_after_set(self) -> None:
        self.conn.cursor().execute("pragma user_version = 1")
        self.conn.cursor().execute('pragma "other schema".user_version= 2')
        self.assertEqual(dbver.get_user_version(self.conn), 1)
        self.assertEqual(dbver.get_user_version(self.conn, "main"), 1)
        self.assertEqual(dbver.get_user_version(self.conn, "other schema"), 2)


class SetUserVersionTest(unittest.TestCase):
    def setUp(self) -> None:
        self.conn = apsw.Connection(":memory:")
        self.conn.cursor().execute("attach ':memory:' as ?", ("other schema",))

    def test_set(self) -> None:
        dbver.set_user_version(1, self.conn)
        self.assertEqual(
            self.conn.cursor().execute("pragma user_version").fetchall(),
            [(1,)],
        )

        dbver.set_user_version(2, self.conn, "main")
        self.assertEqual(
            self.conn.cursor().execute("pragma user_version").fetchall(),
            [(2,)],
        )

        dbver.set_user_version(3, self.conn, "other schema")
        self.assertEqual(
            self.conn.cursor()
            .execute('pragma "other schema".user_version')
            .fetchall(),
            [(3,)],
        )

    def test_set_invalid(self) -> None:
        with self.assertRaises(ValueError):
            dbver.set_user_version(1 << 40, self.conn)

        with self.assertRaises(ValueError):
            dbver.set_user_version(-(1 << 40), self.conn)

        with self.assertRaises(TypeError):
            dbver.set_user_version("str", self.conn)  # type: ignore


class CheckApplicationIdTest(unittest.TestCase):
    def setUp(self) -> None:
        self.conn = apsw.Connection(":memory:")
        self.conn.cursor().execute("attach ':memory:' as ?", ("other schema",))

    def test_expected_empty(self) -> None:
        self.conn.cursor().execute("pragma application_id = 1")
        dbver.check_application_id(1, self.conn)
        dbver.check_application_id(1, self.conn, "main")

    def test_expected_empty_other(self) -> None:
        self.conn.cursor().execute('pragma "other schema".application_id = 1')
        dbver.check_application_id(1, self.conn, "other schema")

    def test_expected_nonempty(self) -> None:
        self.conn.cursor().execute("pragma application_id = 1")
        self.conn.cursor().execute("create table x (x int primary key)")
        dbver.check_application_id(1, self.conn)
        dbver.check_application_id(1, self.conn, "main")

    def test_expected_nonempty_other(self) -> None:
        self.conn.cursor().execute('pragma "other schema".application_id = 1')
        self.conn.cursor().execute(
            'create table "other schema".x ' "(x int primary key)"
        )
        dbver.check_application_id(1, self.conn, "other schema")

    def test_unexpected(self) -> None:
        self.conn.cursor().execute("pragma application_id = 2")
        with self.assertRaises(dbver.VersionError):
            dbver.check_application_id(1, self.conn)
        with self.assertRaises(dbver.VersionError):
            dbver.check_application_id(1, self.conn, "main")

    def test_unexpected_other(self) -> None:
        self.conn.cursor().execute('pragma "other schema".application_id = 2')
        with self.assertRaises(dbver.VersionError):
            dbver.check_application_id(1, self.conn, "other schema")

    def test_unprovisioned(self) -> None:
        dbver.check_application_id(1, self.conn)
        dbver.check_application_id(1, self.conn, "main")
        dbver.check_application_id(1, self.conn, "other schema")

    def test_zero_nonempty(self) -> None:
        self.conn.cursor().execute("create table x (x int primary key)")
        with self.assertRaises(dbver.VersionError):
            dbver.check_application_id(1, self.conn)
        with self.assertRaises(dbver.VersionError):
            dbver.check_application_id(1, self.conn, "main")

    def test_zero_nonempty_other(self) -> None:
        self.conn.cursor().execute(
            'create table "other schema".x ' "(x int primary key)"
        )
        with self.assertRaises(dbver.VersionError):
            dbver.check_application_id(1, self.conn, "other schema")


class NamedFormatMigrations(dbver.Migrations[Optional[str], dbver.Connection]):
    def get_format_unchecked(
        self, conn: dbver.Connection, schema: str = "main"
    ) -> Optional[str]:
        if dbver.get_application_id(conn, schema) == 0:
            return None
        cur = conn.cursor()
        cur.execute(f'select name from "{schema}".format')
        (name,) = cast(Tuple[str], cur.fetchone())
        return name

    def set_format(
        self,
        new_format: Optional[str],
        conn: dbver.Connection,
        schema: str = "main",
    ) -> None:
        assert new_format is not None
        super().set_format(new_format, conn, schema=schema)
        cur = conn.cursor()
        cur.execute(f'drop table if exists "{schema}".format')
        cur.execute(f'create table "{schema}".format (name text not null)')
        cur.execute(
            f'insert into "{schema}".format (name) values (?)', (new_format,)
        )


class NamedFormatMigrationsTest(unittest.TestCase):
    def setUp(self) -> None:
        self.migrations = NamedFormatMigrations(application_id=1)

        @self.migrations.migrates(None, "A")
        def migrate_null_a(
            conn: dbver.Connection, schema: str = "main"
        ) -> None:
            conn.cursor().execute(
                f'create table "{schema}".a (a int primary key)'
            )

        @self.migrations.migrates(None, "B")
        def migrate_null_b(
            conn: dbver.Connection, schema: str = "main"
        ) -> None:
            conn.cursor().execute(
                f'create table "{schema}".b (b int primary key)'
            )

        @self.migrations.migrates("A", "B")
        def migrate_a_b(conn: dbver.Connection, schema: str = "main") -> None:
            cur = conn.cursor()
            cur.execute(f'create table "{schema}".b (b int primary key)')
            cur.execute(f'insert into "{schema}".b select * from "{schema}".a')
            cur.execute(f'drop table "{schema}".a')

        @self.migrations.migrates("B", "A")
        def migrate_b_a(conn: dbver.Connection, schema: str = "main") -> None:
            cur = conn.cursor()
            cur.execute(f'create table "{schema}".a (a int primary key)')
            cur.execute(f'insert into "{schema}".a select * from "{schema}".b')
            cur.execute(f'drop table "{schema}".b')

        self.conn = apsw.Connection(":memory:")
        self.conn.cursor().execute("attach ':memory:' as ?", ("other schema",))

    def assert_migration_map(
        self,
        mapping: Mapping[Optional[str], dbver.Migration],
        targets: Collection[Optional[str]],
    ) -> None:
        self.assertEqual(set(mapping.keys()), set(targets))
        for migration in mapping.values():
            self.assertTrue(callable(migration))

    def test_mapping(self) -> None:
        self.assert_migration_map(self.migrations[None], {"A", "B"})
        self.assert_migration_map(self.migrations["A"], {"B"})
        self.assert_migration_map(self.migrations["B"], {"A"})

        self.assertEqual(self.migrations.get("does_not_exist"), None)

    def test_unprovisioned(self) -> None:
        self.assertIsNone(self.migrations.get_format(self.conn))
        self.assertIsNone(self.migrations.get_format(self.conn, "main"))
        self.assertIsNone(
            self.migrations.get_format(self.conn, "other schema")
        )

    def test_invalid_application_id(self) -> None:
        self.conn.cursor().execute("pragma application_id = 2")
        with self.assertRaises(dbver.VersionError):
            self.migrations.get_format(self.conn)

    def test_nonempty_db(self) -> None:
        self.conn.cursor().execute("create table x (x int primary key)")
        with self.assertRaises(dbver.VersionError):
            self.migrations.get_format(self.conn)

    def test_provision_a(self) -> None:
        self.migrations[None]["A"](self.conn, "main")
        self.assertEqual(self.migrations.get_format(self.conn), "A")
        self.conn.cursor().execute("insert into a (a) values (?)", (1,))

        self.migrations[None]["A"](self.conn, "other schema")
        self.assertEqual(
            self.migrations.get_format(self.conn, "other schema"), "A"
        )
        self.conn.cursor().execute(
            'insert into "other schema".a (a) values (?)', (1,)
        )

    def test_provision_b(self) -> None:
        self.migrations[None]["B"](self.conn, "main")
        self.assertEqual(self.migrations.get_format(self.conn), "B")
        self.conn.cursor().execute("insert into b (b) values (?)", (1,))

        self.migrations[None]["B"](self.conn, "other schema")
        self.assertEqual(
            self.migrations.get_format(self.conn, "other schema"), "B"
        )
        self.conn.cursor().execute(
            'insert into "other schema".b (b) values (?)', (1,)
        )

    def test_migrate_a_b(self) -> None:
        self.migrations[None]["A"](self.conn, "main")
        self.assertEqual(self.migrations.get_format(self.conn), "A")
        cur = self.conn.cursor()
        cur.execute("insert into a (a) values (?)", (1,))
        self.migrations["A"]["B"](self.conn, "main")
        self.assertEqual(self.migrations.get_format(self.conn), "B")
        cur.execute("select * from b")
        self.assertEqual(cast(List[Tuple[int]], cur.fetchall()), [(1,)])

        self.migrations[None]["A"](self.conn, "other schema")
        self.assertEqual(
            self.migrations.get_format(self.conn, "other schema"), "A"
        )
        cur = self.conn.cursor()
        cur.execute('insert into "other schema".a (a) values (?)', (1,))
        self.migrations["A"]["B"](self.conn, "other schema")
        self.assertEqual(
            self.migrations.get_format(self.conn, "other schema"), "B"
        )
        cur.execute('select * from "other schema".b')
        self.assertEqual(cast(List[Tuple[int]], cur.fetchall()), [(1,)])

    def test_migrate_b_a(self) -> None:
        self.migrations[None]["B"](self.conn, "main")
        self.assertEqual(self.migrations.get_format(self.conn), "B")
        cur = self.conn.cursor()
        cur.execute("insert into b (b) values (?)", (1,))
        self.migrations["B"]["A"](self.conn, "main")
        self.assertEqual(self.migrations.get_format(self.conn), "A")
        cur.execute("select * from a")
        self.assertEqual(cast(List[Tuple[int]], cur.fetchall()), [(1,)])

        self.migrations[None]["B"](self.conn, "other schema")
        self.assertEqual(
            self.migrations.get_format(self.conn, "other schema"), "B"
        )
        cur = self.conn.cursor()
        cur.execute('insert into "other schema".b (b) values (?)', (1,))
        self.migrations["B"]["A"](self.conn, "other schema")
        self.assertEqual(
            self.migrations.get_format(self.conn, "other schema"), "A"
        )
        cur.execute('select * from "other schema".a')
        self.assertEqual(cast(List[Tuple[int]], cur.fetchall()), [(1,)])


class UserVersionMigrationsTest(unittest.TestCase):
    def setUp(self) -> None:
        self.migrations = dbver.UserVersionMigrations[dbver.Connection](
            application_id=1
        )

        @self.migrations.migrates(0, 1)
        def migrate_0_1(conn: dbver.Connection, schema: str) -> None:
            conn.cursor().execute(
                f'create table "{schema}".a (a int primary key)'
            )

        @self.migrations.migrates(1, 2)
        def migrate_1_2(conn: dbver.Connection, schema: str) -> None:
            cur = conn.cursor()
            cur.execute(f'create table "{schema}".a2 (a int primary key)')
            cur.execute(
                f'insert into "{schema}".a2 select * from "{schema}".a'
            )
            cur.execute(f'drop table "{schema}".a')

        self.conn = apsw.Connection(":memory:")
        self.conn.cursor().execute("attach ':memory:' as ?", ("other schema",))

    def test_unprovisioned(self) -> None:
        self.assertEqual(self.migrations.get_format(self.conn), 0)
        self.assertEqual(self.migrations.get_format(self.conn, "main"), 0)
        self.assertEqual(
            self.migrations.get_format(self.conn, "other schema"), 0
        )

    def test_invalid_application_id(self) -> None:
        self.conn.cursor().execute("pragma application_id = 2")
        with self.assertRaises(dbver.VersionError):
            self.migrations.get_format(self.conn)

    def test_nonempty_db(self) -> None:
        self.conn.cursor().execute("create table x (x int primary key)")
        with self.assertRaises(dbver.VersionError):
            self.migrations.get_format(self.conn)

    def test_provision(self) -> None:
        version = self.migrations.upgrade(self.conn)
        self.assertEqual(version, 2)
        self.assertEqual(self.migrations.get_format(self.conn), 2)

        version = self.migrations.upgrade(self.conn, "other schema")
        self.assertEqual(version, 2)
        self.assertEqual(
            self.migrations.get_format(self.conn, "other schema"), 2
        )

    def test_upgrade_nonbreaking(self) -> None:
        self.migrations[0][1](self.conn, "main")
        self.assertEqual(self.migrations.get_format(self.conn), 1)

        version = self.migrations.upgrade(self.conn)
        self.assertEqual(version, 1)

    def test_upgrade_breaking(self) -> None:
        self.migrations[0][1](self.conn, "main")
        self.assertEqual(self.migrations.get_format(self.conn), 1)

        version = self.migrations.upgrade(self.conn, breaking=True)
        self.assertEqual(version, 2)

    def test_upgrade_condition(self) -> None:
        def condition(orig: int, new: int) -> bool:
            return new <= 1

        version = self.migrations.upgrade(self.conn, condition=condition)
        self.assertEqual(version, 1)


class SemverMigrationsTest(unittest.TestCase):
    def setUp(self) -> None:
        self.migrations = dbver.SemverMigrations[dbver.Connection](
            application_id=1
        )

        @self.migrations.migrates(0, 1000000)
        def migrate_1(conn: dbver.Connection, schema: str) -> None:
            conn.cursor().execute(
                f'create table "{schema}".a (a int primary key)'
            )

        @self.migrations.migrates(1000000, 1001000)
        def migrate_1dot1(conn: dbver.Connection, schema: str) -> None:
            cur = conn.cursor()
            cur.execute(f'alter table "{schema}".a add column t text')

        @self.migrations.migrates(1001000, 2000000)
        def migrate_2(conn: dbver.Connection, schema: str) -> None:
            cur = conn.cursor()
            cur.execute(
                f'create table "{schema}".a2 (a int primary key, t text)'
            )
            cur.execute(
                f'insert into "{schema}".a2 select * from "{schema}".a'
            )
            cur.execute(f'drop table "{schema}".a')

        self.conn = apsw.Connection(":memory:")
        self.conn.cursor().execute("attach ':memory:' as ?", ("other schema",))

    def test_unprovisioned(self) -> None:
        self.assertEqual(self.migrations.get_format(self.conn), 0)
        self.assertEqual(self.migrations.get_format(self.conn, "main"), 0)
        self.assertEqual(
            self.migrations.get_format(self.conn, "other schema"), 0
        )

    def test_invalid_application_id(self) -> None:
        self.conn.cursor().execute("pragma application_id = 2")
        with self.assertRaises(dbver.VersionError):
            self.migrations.get_format(self.conn)

    def test_nonempty_db(self) -> None:
        self.conn.cursor().execute("create table x (x int primary key)")
        with self.assertRaises(dbver.VersionError):
            self.migrations.get_format(self.conn)

    def test_provision(self) -> None:
        version = self.migrations.upgrade(self.conn)
        self.assertEqual(version, 2000000)
        self.assertEqual(self.migrations.get_format(self.conn), 2000000)

        version = self.migrations.upgrade(self.conn, "other schema")
        self.assertEqual(version, 2000000)
        self.assertEqual(
            self.migrations.get_format(self.conn, "other schema"), 2000000
        )

    def test_upgrade_nonbreaking(self) -> None:
        self.migrations[0][1000000](self.conn, "main")
        self.assertEqual(self.migrations.get_format(self.conn), 1000000)

        version = self.migrations.upgrade(self.conn)
        self.assertEqual(version, 1001000)

    def test_upgrade_breaking(self) -> None:
        self.migrations[0][1000000](self.conn, "main")
        self.assertEqual(self.migrations.get_format(self.conn), 1000000)

        version = self.migrations.upgrade(self.conn, breaking=True)
        self.assertEqual(version, 2000000)

    def test_upgrade_condition(self) -> None:
        def condition(orig: int, new: int) -> bool:
            return new <= 1001000

        version = self.migrations.upgrade(self.conn, condition=condition)
        self.assertEqual(version, 1001000)
