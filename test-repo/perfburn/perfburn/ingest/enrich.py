"""Event enrichment. enrich() runs once per event."""

import copy
import csv
import io


class Enricher:
    def __init__(self, conn, region_csv_text):
        self._conn = conn
        self._region_csv_text = region_csv_text

    def lookup_source(self, source_id):
        cur = self._conn.execute(
            "SELECT name, tier FROM sources WHERE id = ?", (source_id,))
        row = cur.fetchone()
        if row is None:
            return {"name": "unknown", "tier": 0}
        return {"name": row[0], "tier": row[1]}

    def tag_region(self, event):
        regions = {}
        reader = csv.reader(io.StringIO(self._region_csv_text))
        for row in reader:
            if len(row) == 2:
                name, prefix = row
                regions[prefix.strip()] = name.strip()
        address = event["fields"].get("ip", "0.0.0.0")
        octet = address.split(".")[0]
        return regions.get(octet, "other")

    def enrich(self, event):
        enriched = copy.deepcopy(event)
        enriched["source"] = self.lookup_source(event["fields"].get("source_id", ""))
        enriched["region"] = self.tag_region(event)
        enriched["day"] = event["fields"].get("ts", "")[:10]
        return enriched
