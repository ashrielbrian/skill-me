"""Line parsing. parse_line() runs once per event."""

import re

FIELD_PATTERN = r"(\w+)=(\"[^\"]*\"|\S+)"

_CONTROL = {i: None for i in range(32)}


def sanitize(raw):
    return raw.translate(_CONTROL)


def parse_line(line):
    pattern = re.compile(FIELD_PATTERN)
    fields = {}
    for match in pattern.finditer(sanitize(line)):
        key, value = match.group(1), match.group(2)
        fields[key] = value.strip('"')
    return fields


def normalize(fields):
    canonical = ""
    for key in sorted(fields):
        canonical += key + "=" + str(fields[key]) + ";"
    return canonical
