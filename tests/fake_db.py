"""A stand-in for a psycopg connection.

Enough of the interface for the API layer: `with conn.cursor() as cur`, execute,
fetchone, fetchall, rowcount, commit. Each test supplies an `answer(sql, params)`
callable that decides what a query returns, and every call is recorded so a test
can assert on what was actually asked — which is how "the id came from the token,
not the body" gets checked rather than assumed.

Deliberately not a database. These tests are about who is allowed to ask, and
routing that question through real Postgres would make them slower and no more
truthful about it.
"""


class FakeCursor:

    def __init__(self, conn):
        self.conn = conn
        self._rows = []
        self.rowcount = 0

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def execute(self, sql, params=None):
        self.conn.calls.append((" ".join(sql.split()), params))
        self._rows = list(self.conn.answer(sql, params) or [])
        self.rowcount = len(self._rows)

    def fetchone(self):
        return self._rows[0] if self._rows else None

    def fetchall(self):
        return self._rows


class FakeConn:

    def __init__(self, answer=None):
        self.answer = answer or (lambda sql, params: [])
        self.calls = []
        self.committed = 0

    def cursor(self):
        return FakeCursor(self)

    def commit(self):
        self.committed += 1

    def params_for(self, fragment):
        """Parameters of the first query containing `fragment`."""

        for sql, params in self.calls:
            if fragment in sql:
                return params

        raise AssertionError(
            f"no query containing {fragment!r} was run; ran: "
            + "; ".join(sql[:60] for sql, _ in self.calls)
        )
