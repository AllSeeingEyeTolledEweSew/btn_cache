import abc
import collections.abc
import contextlib
import enum
import functools
import logging
from typing import Any
from typing import Callable
from typing import cast
from typing import ContextManager
from typing import Dict
from typing import Generic
from typing import Iterator
from typing import Mapping
from typing import Protocol
from typing import Tuple
from typing import TypeVar

# Support goals:
#  - sqlite, not an abstraction layer
#  - implicit provisioning
#  - the database as an API

# A reader or writer supports some set of formats for an existing db

# Readers and writers should have forward-compatible awareness of breaking
# changes, and throw a helpful error rather than process anything

# A reader or writer dropping support for any version is a breaking change

# A writer may implicitly migrate a schema; any implicit breaking migration is
# a breaking change on the writer

# An empty, unprovisioned database should be considered to be at a "null"
# version. Provisioning is thought of as a migration from null. A migration
# from null to any version should not be considered breaking.

# Readers should normally support version 0 as equivalent to an empty
# provisioned database

# Locks should be held for as little time as possible.

# Should always close explicitly; close on gc interferes with event loops.

# The following things should be application-dependent, not enforced by schema
# management code:
#  - the schema name ("main" vs a name from "ATTACH foo as name")
#  - any pragma that has no effect on sql, like journal_mode
#  - file paths
#  - transaction boundaries or locking modes

# Migration code should persist forever, only deleted if it contain bugs, or if
# optimization from empty case is needed.
# - If there's a migration bug in 1.2.3 -> 1.2.4:
#   - delete this migration in 1.2.5+
#   - add shortcut migration 1.2.3 -> 1.2.5
#   - add 1.2.4 -> 1.2.5 (if bug loses data, may be partial migration)

# Ideas for compatibility in sqlite:
#  - tables as table-or-view
#  - must use "create trigger x instead of" for writer compatibility
#  - client-specific indexes need to consider all major.x table-or-views
#  - what happens if column becomes nullable?
#    - breaking if can't find a value to map for readers
#  - what happens if column becomes not null?
#    - breaking if can't find a value to map for writers
#  - maybe: *readers* optionally do unprompted breaking upgrades?
#    - makes sense if only one reader?
#    - doesn't make sense: still need to upgrade both softwares; if db is
#    upgraded without both sides upgraded, other throws errors
#  - would be nice to know when was the last time a reader of version x read
#  the database

_LOG = logging.getLogger(__name__)


class CursorType(Protocol):
    def execute(self, sql: str) -> Any:
        ...

    def fetchone(self) -> Tuple:
        ...


_Cu_co = TypeVar("_Cu_co", bound=CursorType, covariant=True)


class Connection(Protocol[_Cu_co]):
    def cursor(self) -> _Cu_co:
        ...

    def close(self) -> Any:
        ...


_C = TypeVar("_C", bound=Connection)
Factory = Callable[[], _C]
Pool = Callable[[], ContextManager[_C]]


def null_pool(factory: Factory[_C]) -> Pool[_C]:
    @contextlib.contextmanager
    def func() -> Iterator[_C]:
        conn = factory()
        try:
            yield conn
        finally:
            conn.close()

    return func


class LockMode(str, enum.Enum):
    IMMEDIATE = "immediate"
    DEFERRED = "deferred"
    EXCLUSIVE = "exclusive"


@contextlib.contextmanager
def begin(conn: _C, lock_mode: LockMode) -> Iterator[None]:
    cur = conn.cursor()
    cur.execute(f"begin {lock_mode}")
    try:
        yield
    except Exception:
        # Per https://sqlite.org/lang_transaction.html : some errors may cause
        # an automatic rollback; we should always explicitly rollback and
        # ignore any errors.
        # Currently not ignoring errors, because different adapters use
        # different exception hierarchies. We could try-import a few well-known
        # types though.
        cur.execute("rollback")
        raise
    else:
        cur.execute("commit")


@contextlib.contextmanager
def begin_pool(pool: Pool[_C], lock_mode: LockMode) -> Iterator[_C]:
    with pool() as conn:
        with begin(conn, lock_mode):
            yield conn


class Error(Exception):
    pass


class VersionError(Error):
    pass


def semver_is_breaking(from_version: int, to_version: int) -> bool:
    if from_version == 0:
        return False
    return to_version < from_version or (from_version // 1000000) != (
        to_version // 1000000
    )


def semver_check_breaking(from_version: int, to_version: int) -> None:
    if semver_is_breaking(from_version, to_version):
        raise VersionError(f"{from_version} -> {to_version}: breaking change")


def _check_schema(schema: str) -> None:
    if not isinstance(schema, str):
        raise TypeError("schema must be str")
    if '"' in schema:
        raise ValueError("schema invalid")


def _check_int32(value: int) -> None:
    if not isinstance(value, int):
        raise TypeError("must be int")
    if value > (1 << 31) - 1 or value < -(1 << 31):
        raise ValueError("must be signed int32")


def get_application_id(conn: _C, schema: str = "main") -> int:
    # sqlite doesn't support schema or pragma values as bind parameters.
    # we must do string-formatted sql, and check our inputs
    _check_schema(schema)
    cur = conn.cursor()
    cur.execute(f'pragma "{schema}".application_id')
    (application_id,) = cast(Tuple[int], cur.fetchone())
    return application_id


def set_application_id(
    application_id: int, conn: _C, schema: str = "main"
) -> None:
    # sqlite doesn't support schema or pragma values as bind parameters.
    # we must do string-formatted sql, and check our inputs
    _check_schema(schema)
    _check_int32(application_id)
    cur = conn.cursor()
    cur.execute(f'pragma "{schema}".application_id = {application_id}')


def get_user_version(conn: _C, schema: str = "main") -> int:
    # sqlite doesn't support schema or pragma values as bind parameters.
    # we must do string-formatted sql, and check our inputs
    _check_schema(schema)
    cur = conn.cursor()
    cur.execute(f'pragma "{schema}".user_version')
    (user_version,) = cast(Tuple[int], cur.fetchone())
    return user_version


def set_user_version(
    user_version: int, conn: _C, schema: str = "main"
) -> None:
    # sqlite doesn't support schema or pragma values as bind parameters.
    # we must do string-formatted sql, and check our inputs
    _check_schema(schema)
    _check_int32(user_version)
    cur = conn.cursor()
    cur.execute(f'pragma "{schema}".user_version = {user_version}')


def has_tables(conn: _C, schema: str = "main") -> bool:
    # sqlite doesn't support schema or pragma values as bind parameters.
    # we must do string-formatted sql, and check our inputs
    _check_schema(schema)
    cur = conn.cursor()
    cur.execute(
        f"select 1 from \"{schema}\".sqlite_master where type = 'table'"
    )
    return cur.fetchone() is not None


def check_application_id(
    application_id: int, conn: _C, schema: str = "main"
) -> None:
    have_id = get_application_id(conn, schema=schema)
    if have_id == 0:
        if has_tables(conn, schema=schema):
            raise VersionError("database is not empty")
    elif have_id != application_id:
        raise VersionError(
            "wrong application_id: " f"{have_id} != {application_id}"
        )


Migration = Callable[[_C, str], None]
_T = TypeVar("_T")


class Migrations(abc.ABC, collections.abc.Mapping, Generic[_T, _C]):
    def __init__(self, *, application_id: int = 0) -> None:
        self._forward: Dict[_T, Dict[_T, Migration]] = {}
        self._application_id = application_id

    def __getitem__(self, key: _T) -> Mapping[_T, Migration[_C]]:
        return self._forward[key]

    def __iter__(self) -> Iterator[_T]:
        return iter(self._forward)

    def __len__(self) -> int:
        return len(self._forward)

    def check(self, conn: _C, schema: str = "main") -> None:
        if self._application_id != 0:
            check_application_id(self._application_id, conn, schema=schema)

    def get_format(self, conn: _C, schema: str = "main") -> _T:
        self.check(conn, schema=schema)
        return self.get_format_unchecked(conn, schema=schema)

    @abc.abstractmethod
    def get_format_unchecked(self, conn: _C, schema: str = "main") -> _T:
        raise NotImplementedError

    @abc.abstractmethod
    def set_format(
        self, new_format: _T, conn: _C, schema: str = "main"
    ) -> None:
        if self._application_id != 0:
            set_application_id(self._application_id, conn, schema=schema)

    def add(
        self, from_format: _T, to_format: _T, migration: Migration[_C]
    ) -> Migration[_C]:
        @functools.wraps(migration)
        def wrapped(conn: _C, schema: str = "main") -> None:
            migration(conn, schema)
            self.set_format(to_format, conn, schema=schema)

        self._forward.setdefault(from_format, {})
        self._forward[from_format][to_format] = wrapped
        return wrapped

    def migrates(
        self, from_format: _T, to_format: _T
    ) -> Callable[[Migration[_C]], Migration[_C]]:
        def wrap(migration: Migration[_C]) -> Migration[_C]:
            return self.add(from_format, to_format, migration)

        return wrap


class _SupportsLessThan(Protocol):
    def __lt__(self, __other: Any) -> bool:
        ...


_LT = TypeVar("_LT", bound=_SupportsLessThan)


class VersionMigrations(Migrations[_LT, _C]):
    def is_breaking(self, from_format: _LT, to_format: _LT) -> bool:
        return bool(from_format)

    def add(
        self, from_format: _LT, to_format: _LT, migration: Migration[_C]
    ) -> Migration[_C]:
        if not from_format < to_format:
            raise AssertionError(
                f"{from_format} -> {to_format}: version does not increase"
            )
        return super().add(from_format, to_format, migration)

    def upgrade(
        self,
        conn: _C,
        schema: str = "main",
        *,
        condition: Callable[[_LT, _LT], Any] = None,
        breaking=False,
    ) -> _LT:
        if condition is None:

            def condition(orig: _LT, new: _LT) -> bool:
                return breaking or not self.is_breaking(orig, new)

        assert condition is not None  # makes mypy happy
        orig = self.get_format(conn, schema=schema)
        cur = orig
        while True:
            migrations = self.get(cur, {})
            candidates = migrations.keys()
            allowed = [fmt for fmt in candidates if condition(orig, fmt)]
            if not allowed:
                break
            new = max(allowed)
            _LOG.debug("upgrading to version %s", new)
            migrations[new](conn, schema)
            cur = new
        return cur


class UserVersionMigrations(VersionMigrations[int, _C]):
    def get_format_unchecked(self, conn: _C, schema: str = "main") -> int:
        return get_user_version(conn, schema=schema)

    def set_format(
        self, new_format: int, conn: _C, schema: str = "main"
    ) -> None:
        super().set_format(new_format, conn, schema=schema)
        set_user_version(new_format, conn, schema=schema)


class SemverMigrations(UserVersionMigrations[_C]):
    def is_breaking(self, from_format: int, to_format: int) -> bool:
        return semver_is_breaking(from_format, to_format)
