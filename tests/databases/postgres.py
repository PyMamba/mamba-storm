from datetime import datetime, date, time
import os

from storm.databases.postgres import Postgres

from tests.databases.base import DatabaseTest
from tests.helper import TestHelper


class PostgresTest(TestHelper, DatabaseTest):

    def setUp(self):
        TestHelper.setUp(self)
        DatabaseTest.setUp(self)

    def tearDown(self):
        DatabaseTest.setUp(self)
        TestHelper.setUp(self)
    
    def is_supported(self):
        return bool(os.environ.get("STORM_POSTGRES_DBNAME"))

    def create_database(self):
        self.database = Postgres(os.environ["STORM_POSTGRES_DBNAME"])

    def create_tables(self):
        self.connection.execute("CREATE TABLE test "
                                "(id SERIAL PRIMARY KEY, title VARCHAR)")
        self.connection.execute("CREATE TABLE datetime_test "
                                "(id SERIAL PRIMARY KEY,"
                                " dt TIMESTAMP, d DATE, t TIME)")
