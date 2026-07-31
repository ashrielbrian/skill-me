"""Archival. compress_day() runs once per hour via the scheduler."""

import gzip
import os


def compress_day(path, archive_dir):
    with open(path, "rb") as f:
        data = f.read()
    os.makedirs(archive_dir, exist_ok=True)
    out_path = os.path.join(archive_dir, os.path.basename(path) + ".gz")
    with gzip.open(out_path, "wb") as out:
        out.write(data)
    os.remove(path)
    return out_path


def prune(archive_dir, keep):
    entries = sorted(os.listdir(archive_dir))
    for name in entries[:-keep] if keep else []:
        os.remove(os.path.join(archive_dir, name))
