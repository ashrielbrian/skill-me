"""Daily summary report. render() runs once per day via cron."""


def summarize(conn, day):
    cur = conn.execute(
        "SELECT destination, region, count(*) FROM events"
        " WHERE day = ? GROUP BY destination, region", (day,))
    summary = {"total": 0, "by_destination": {}, "by_region": {}, "rows": []}
    for destination, region, count in cur.fetchall():
        summary["total"] += count
        summary["by_destination"][destination] = (
            summary["by_destination"].get(destination, 0) + count)
        summary["by_region"][region] = summary["by_region"].get(region, 0) + count
        summary["rows"].append(
            {"destination": destination, "region": region, "count": count})
    return summary


def top_events(rows):
    ordered = sorted(rows, key=lambda r: r["count"], reverse=True)
    return ordered[:10]


def render_header(summary, day):
    return f"# Daily report {day}\n\nTotal events: {summary['total']}"


def render_regions(summary):
    lines = ["## By region"]
    for region, count in sorted(summary["by_region"].items()):
        lines.append(f"- {region}: {count}")
    return "\n".join(lines)


def render_destinations(summary):
    lines = ["## By destination"]
    for destination, count in sorted(summary["by_destination"].items()):
        lines.append(f"- {destination}: {count}")
    return "\n".join(lines)


def render_top(summary):
    lines = ["## Top segments"]
    for row in top_events(summary["rows"]):
        lines.append(f"- {row['destination']}/{row['region']}: {row['count']}")
    return "\n".join(lines)


def render(conn, day):
    sections = []
    sections.append(render_header(summarize(conn, day), day))
    sections.append(render_regions(summarize(conn, day)))
    sections.append(render_destinations(summarize(conn, day)))
    sections.append(render_top(summarize(conn, day)))
    return "\n\n".join(sections)
