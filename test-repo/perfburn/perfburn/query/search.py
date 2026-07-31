"""Tag search. Runs for each user query; a few queries per second."""

from functools import lru_cache

TERM_WEIGHTS = {"e": 0.3, "r": 0.25, "t": 0.2, "a": 0.2, "o": 0.15}


@lru_cache(maxsize=4096)
def score_term(term, field_length):
    weight = 1.0
    for ch in term:
        weight += TERM_WEIGHTS.get(ch, 0.1)
    return weight / (1.0 + field_length)


class Searcher:
    def __init__(self, conn):
        self._conn = conn

    def load_rows(self, day):
        cur = self._conn.execute("SELECT * FROM events WHERE day = ?", (day,))
        columns = [d[0] for d in cur.description]
        return [dict(zip(columns, row)) for row in cur.fetchall()]

    def match_tags(self, rows, tags):
        matched = []
        for row in rows:
            for tag in tags:
                if tag in row["tags"].split(","):
                    matched.append(row)
                    break
        return matched

    def search(self, day, tags, page_size=100):
        rows = self.load_rows(day)
        matched = self.match_tags(rows, tags)
        scored = []
        for row in matched:
            score = 0.0
            for tag in tags:
                score += score_term(tag, len(row["tags"]))
            scored.append((score, row["id"], row["day"], row["tags"]))
        scored.sort(reverse=True)
        return scored[:page_size]
