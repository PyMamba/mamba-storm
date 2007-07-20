#
# Copyright (c) 2006, 2007 Canonical
#
# Written by Gustavo Niemeyer <gustavo@niemeyer.net>
#
# This file is part of Storm Object Relational Mapper.
#
# Storm is free software; you can redistribute it and/or modify
# it under the terms of the GNU Lesser General Public License as
# published by the Free Software Foundation; either version 2.1 of
# the License, or (at your option) any later version.
#
# Storm is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU Lesser General Public License for more details.
#
# You should have received a copy of the GNU Lesser General Public License
# along with this program.  If not, see <http://www.gnu.org/licenses/>.
#
from datetime import datetime, date, time
from time import sleep, time as now
import sys
import re

from storm.databases import dummy

try:
    from pysqlite2 import dbapi2 as sqlite
except ImportError:
    sqlite = dummy

from storm.variables import Variable, RawStrVariable
from storm.database import *
from storm.exceptions import install_exceptions, DatabaseModuleError
from storm.expr import (
    Select, SELECT, Undef, SQLRaw, SetExpr, Union, Except, Intersect,
    compile, compile_select, compile_set_expr)


install_exceptions(sqlite)


compile = compile.create_child()

@compile.when(Select)
def compile_select_sqlite(compile, select, state):
    if select.offset is not Undef and select.limit is Undef:
        select.limit = sys.maxint
    statement = compile_select(compile, select, state)
    if state.context is SELECT:
        # SQLite breaks with (SELECT ...) UNION (SELECT ...), so we
        # do SELECT * FROM (SELECT ...) instead.  This is important
        # because SELECT ... UNION SELECT ... ORDER BY binds the ORDER BY
        # to the UNION instead of SELECT.
        return "SELECT * FROM (%s)" % statement
    return statement

# Considering the above, selects have a greater precedence.
compile.set_precedence(5, Union, Except, Intersect)


class SQLiteResult(Result):

    def get_insert_identity(self, primary_key, primary_variables):
        return SQLRaw("(OID=%d)" % self._raw_cursor.lastrowid)

    @staticmethod
    def set_variable(variable, value):
        if isinstance(variable, RawStrVariable):
            # pysqlite2 may return unicode.
            value = str(value)
        variable.set(value, from_db=True)

    @staticmethod
    def _from_database(row):
        for value in row:
            if isinstance(value, buffer):
                yield str(value)
            else:
                yield value


class SQLiteConnection(Connection):

    _result_factory = SQLiteResult
    _compile = compile
    _statement_re = re.compile("^\s*(?:select|(insert|update|delete|"
                               "replace))\s", re.IGNORECASE)
    _in_transaction = False

    @staticmethod
    def _to_database(params):
        for param in params:
            if isinstance(param, Variable):
                param = param.get(to_db=True)
            if isinstance(param, (datetime, date, time)):
                yield str(param)
            elif isinstance(param, str):
                yield buffer(param)
            else:
                yield param

    def commit(self):
        self._in_transaction = False
        super(SQLiteConnection, self).commit()

    def rollback(self):
        self._in_transaction = False
        super(SQLiteConnection, self).rollback()

    def _enforce_transaction(self, statement):
        """Make PySQLite behave slightly better regarding transactions.

        PySQLite does some very dirty tricks to control the moment in
        which transactions begin and end.  It actually *changes* the
        transactional behavior of SQLite.
 
        The real behavior of SQLite is that transactions are SERIALIZABLE
        by default.  That is, any reads are repeatable, and changes in
        other threads or processes won't modify data for already started
        transactions that have issued any reading or writing statements.

        PySQLite changes that in a very unpredictable way.  First, it will
        only actually begin a transaction if a INSERT/UPDATE/DELETE/REPLACE
        operation is executed (yes, it will parse the statement).  This
        means that any SELECTs executed *before* one of the former mentioned
        operations are seen, will be operating in READ COMMITTED mode.  Then,
        if after that a INSERT/UPDATE/DELETE/REPLACE is seen, the transaction
        actually begins, and so it moves into SERIALIZABLE mode.

        Another pretty surprising behavior is that it will *commit* any
        on-going transaction if any other statement besides
        SELECT/INSERT/UPDATE/DELETE/REPLACE is seen.

        In an ORM we're really dealing with cached data, so working on top
        of a system like that means that cache validity is pretty random.

        So what we do in this method is track the arbitrary transaction
        starting/ending points of PySQLite, and force it to begin a real
        transaction rather than operating in autocommit mode when it
        promised a transaction.  Unfortunately we can't improve the
        unrequested commits on unknown statements, so we just make sure
        that a new transaction is started again after that's done.

        References:
            http://www.sqlite.org/lockingv3.html
            http://docs.python.org/lib/sqlite3-Controlling-Transactions.html
        """
        match = self._statement_re.match(statement)
        if not match:
            # Something else.
            self._in_transaction = False
        elif not self._in_transaction:
            self._in_transaction = True
            if match.group(1):
                # INSERT/UPDATE/DELETE/REPLACE, PySQLite will give us a
                # real transaction. Thank you.
                pass
            else:
                # SELECT, please give us the transaction we asked for.
                try:
                    self._raw_connection.execute("DELETE FROM sqlite_master "
                                                 "WHERE NULL")
                except sqlite.OperationalError:
                    pass

    def _raw_execute(self, statement, params=None, _started=None):
        """Execute a raw statement with the given parameters.

        This method will automatically retry on locked database errors.
        This should be done by pysqlite, but it doesn't work with
        versions < 2.3.4, so we make sure the timeout is respected
        here.
        """
        self._enforce_transaction(statement)
        while True:
            try:
                return Connection._raw_execute(self, statement, params)
            except sqlite.OperationalError, e:
                if str(e) != "database is locked":
                    raise
                if _started is None:
                    _started = now()
                elif now() - _started < self._database._timeout:
                    sleep(0.1)
                else:
                    raise


class SQLite(Database):

    _connection_factory = SQLiteConnection

    def __init__(self, uri):
        if sqlite is dummy:
            raise DatabaseModuleError("'pysqlite2' module not found")
        self._filename = uri.database or ":memory:"
        self._timeout = float(uri.options.get("timeout", 5))

    def connect(self):
        raw_connection = sqlite.connect(self._filename, timeout=self._timeout)
        return self._connection_factory(self, raw_connection)


create_from_uri = SQLite
