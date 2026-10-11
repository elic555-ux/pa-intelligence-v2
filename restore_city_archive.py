"""Restore archived inventory records for one city or county without a network scan."""

import argparse
import json
import os
from pathlib import Path


FILES = ("properties.json", "sheriff_listings.json")
ARCHIVE = Path("COMPS_REPORTS/inventory_city_archive")


def normalized(value):
    return " ".join(str(value or "").strip().casefold().split())


def read_rows(path):
    rows = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(rows, list) or any(not isinstance(row, dict) for row in rows):
        raise ValueError(f"Invalid inventory: {path}")
    return rows


def plan(root, kind, name):
    if not normalized(name):
        raise ValueError("City or county must not be empty")
    key = "city" if kind == "city" else "county"
    requested = normalized(name).removesuffix(" county") if kind == "county" else normalized(name)
    results = {}
    for filename in FILES:
        active_path, archive_path = root / filename, root / ARCHIVE / filename
        active, archived = read_rows(active_path), read_rows(archive_path)
        matched = [row for row in archived if
                   (normalized(row.get(key)).removesuffix(" county") if kind == "county"
                    else normalized(row.get(key))) == requested]
        remaining = [row for row in archived if row not in matched]
        ids = {str(row.get("id")): row for row in active if row.get("id")}
        for row in matched:
            identifier = str(row.get("id") or "")
            if identifier and identifier in ids and ids[identifier] != row:
                raise ValueError(f"Existing property ID conflicts with archived record in {filename}")
        restored = [row for row in matched if not row.get("id") or str(row["id"]) not in ids]
        results[filename] = (active_path, archive_path, active + restored, remaining,
                             len(restored), len(matched))
    return results


def restore(root, kind, name, check=False):
    root = Path(root)
    results = plan(root, kind, name)
    counts = {name: {"restored": row[4], "matched": row[5]}
              for name, row in results.items()}
    if check or not any(row[5] for row in results.values()):
        return counts
    originals = {path: path.read_bytes() for row in results.values() for path in row[:2]}
    staged = []
    try:
        for active_path, archive_path, active, archived, _, _ in results.values():
            for path, rows in ((active_path, active), (archive_path, archived)):
                temporary = path.with_name(path.name + ".restore-tmp")
                with temporary.open("w", encoding="utf-8") as stream:
                    json.dump(rows, stream, ensure_ascii=False, indent=2)
                    stream.write("\n")
                    stream.flush()
                    os.fsync(stream.fileno())
                staged.append((temporary, path))
        if any(path.read_bytes() != body for path, body in originals.items()):
            raise ValueError("Inventory changed during restore")
        for temporary, path in staged:
            os.replace(temporary, path)
    finally:
        for temporary, _ in staged:
            temporary.unlink(missing_ok=True)
    return counts


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    scope = parser.add_mutually_exclusive_group(required=True)
    scope.add_argument("--city")
    scope.add_argument("--county")
    parser.add_argument("--repo", default=".")
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    print(json.dumps(restore(args.repo, "city" if args.city else "county",
                             args.city or args.county, args.check), ensure_ascii=False))
