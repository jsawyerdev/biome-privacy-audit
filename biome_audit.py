#!/usr/bin/env python3
"""Audit Apple's Biome activity store (~/Library/Biome).

Biome is the successor to CoreDuet/knowledgeC: over a hundred named event
streams, several plain SQLite databases -- including an on-device entity
graph of people and places built from Mail/Messages/Contacts/Calendar --
and a CloudKit-backed sync layer (sync/sync.db) that replicates a subset
of it to your other Apple devices. Like knowledgeC, it's gated by Full
Disk Access (TCC).

A subset of streams -- ProactiveHarvesting.* and Siri.Remembers.* -- store
verbatim content (actual Notes/Mail/Messages/Safari/Siri-query text), not
just usage metadata. This tool never reads or prints that content: it only
reports whether a content-bearing stream is populated and how large the
live (non-empty) portion is. The entity-graph and sync sections report
counts and, for your own identity row only, your resolved name -- never
other people's names, emails, phone numbers, or raw addresses.

Subcommands:
  report  Read-only. Stream inventory, entity-graph counts, cached-set
          sizes, and sync-peer liveness.
  purge   Deletes the current contents of stream ring-buffer files. Does
          not touch the SQLite entity-graph or sync databases -- safe
          deletion semantics for those aren't established yet.
"""

from __future__ import annotations

import argparse
import logging
import plistlib
import sqlite3
from collections.abc import Sequence
from contextlib import closing
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

DEFAULT_BIOME_PATH = Path.home() / "Library/Biome"
SYSTEM_VERSION_PLIST = Path("/System/Library/CoreServices/SystemVersion.plist")

CACHED_SET_NAMES = [
    "App.InstalledApp",
    "Calendar.Event",
    "Contacts.Contact",
    "HomeKit.Home",
]

CONTENT_BEARING_PREFIXES = ("ProactiveHarvesting.", "Siri.Remembers.")
CONTENT_BEARING_EXACT = {
    "App.Intents.Transcript",
    "IntelligenceFlow.Transcript.Datastream",
    "ToolKit.Transcript",
}

logger = logging.getLogger("biome_audit")


def configure_logging(verbose: bool) -> None:
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.INFO,
        format="%(levelname)s: %(message)s",
    )


def is_content_bearing(stream_name: str) -> bool:
    return (
        stream_name.startswith(CONTENT_BEARING_PREFIXES)
        or stream_name in CONTENT_BEARING_EXACT
    )


def format_bytes(n: int) -> str:
    value = float(n)
    for unit in ("B", "KB", "MB", "GB"):
        if value < 1024 or unit == "GB":
            return f"{value:.0f}{unit}" if unit == "B" else f"{value:.1f}{unit}"
        value /= 1024
    return f"{value:.1f}GB"


def macos_build() -> str:
    if not SYSTEM_VERSION_PLIST.exists():
        return "(unknown)"
    with SYSTEM_VERSION_PLIST.open("rb") as f:
        info = plistlib.load(f)
    return f"{info.get('ProductVersion', '?')} ({info.get('ProductBuildVersion', '?')})"


def connect_sqlite(db_path: Path, read_only: bool = True) -> sqlite3.Connection:
    mode = "ro" if read_only else "rw"
    try:
        conn = sqlite3.connect(f"file:{db_path}?mode={mode}", uri=True, timeout=30)
        conn.execute("SELECT 1 FROM sqlite_master LIMIT 1")
        return conn
    except sqlite3.OperationalError as exc:
        raise SystemExit(
            f"Could not open {db_path} ({mode}): {exc}\n\n"
            "macOS restricts Biome with Full Disk Access (TCC). Grant Full "
            "Disk Access to whatever process is running this script under "
            "System Settings > Privacy & Security > Full Disk Access, then "
            "fully quit and reopen that application."
        ) from exc


def table_exists(conn: sqlite3.Connection, name: str) -> bool:
    (found,) = conn.execute(
        "SELECT COUNT(*) FROM sqlite_master WHERE type='table' AND name=?", (name,)
    ).fetchone()
    return bool(found)


def count_rows(conn: sqlite3.Connection, table: str) -> int | None:
    if not table_exists(conn, table):
        return None
    (n,) = conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()
    return n


def require_readable(path: Path) -> None:
    try:
        next(path.iterdir(), None)
    except PermissionError as exc:
        raise SystemExit(
            f"Could not read {path}: {exc}\n\n"
            "macOS restricts Biome with Full Disk Access (TCC). Grant Full "
            "Disk Access to whatever process is running this script under "
            "System Settings > Privacy & Security > Full Disk Access, then "
            "fully quit and reopen that application."
        ) from exc


@dataclass(frozen=True)
class StreamStats:
    name: str
    file_count: int
    total_bytes: int
    live_bytes: int
    last_modified: datetime
    content_bearing: bool


def scan_streams(streams_dir: Path) -> list[StreamStats]:
    # Stream files are pre-allocated fixed-size ring buffers, so total size
    # wildly overstates how much is actually stored; live_bytes (non-zero
    # byte count) is what's really there.
    require_readable(streams_dir)
    results = []
    for stream_dir in sorted(p for p in streams_dir.iterdir() if p.is_dir()):
        local_dir = stream_dir / "local"
        if not local_dir.is_dir():
            continue
        files = [f for f in local_dir.iterdir() if f.is_file()]
        if not files:
            continue
        total_bytes = 0
        live_bytes = 0
        latest_mtime = 0.0
        for f in files:
            data = f.read_bytes()
            total_bytes += len(data)
            live_bytes += len(data) - data.count(0)
            latest_mtime = max(latest_mtime, f.stat().st_mtime)
        results.append(
            StreamStats(
                name=stream_dir.name,
                file_count=len(files),
                total_bytes=total_bytes,
                live_bytes=live_bytes,
                last_modified=datetime.fromtimestamp(latest_mtime, tz=timezone.utc),
                content_bearing=is_content_bearing(stream_dir.name),
            )
        )
    return results


@dataclass(frozen=True)
class EntityGraphSummary:
    person_total: int
    person_named: int
    person_with_email: int
    person_with_phone: int
    person_with_employer: int
    self_name: str | None
    location_total: int
    location_named: int
    software_total: int


def entity_graph_summary(databases_dir: Path) -> EntityGraphSummary | None:
    db_path = (
        databases_dir
        / "IntelligencePlatform.Entity"
        / "IntelligencePlatform.Entity.sqlite3"
    )
    if not db_path.exists():
        return None
    with closing(connect_sqlite(db_path)) as conn:
        if not table_exists(conn, "Person"):
            return None
        person_total, named, with_email, with_phone, with_employer = conn.execute(
            "SELECT COUNT(*), SUM(fullName IS NOT NULL), SUM(emailAddresses IS NOT NULL), "
            "SUM(phoneNumbers IS NOT NULL), SUM(employer IS NOT NULL) FROM Person"
        ).fetchone()
        self_row = conn.execute(
            "SELECT fullName FROM Person WHERE isCurrentUser = 1 LIMIT 1"
        ).fetchone()
        location_total, location_named = 0, 0
        if table_exists(conn, "Location"):
            location_total, location_named = conn.execute(
                "SELECT COUNT(*), SUM(name IS NOT NULL AND name != '') FROM Location"
            ).fetchone()
        software_total = count_rows(conn, "software") or 0
    return EntityGraphSummary(
        person_total=person_total or 0,
        person_named=named or 0,
        person_with_email=with_email or 0,
        person_with_phone=with_phone or 0,
        person_with_employer=with_employer or 0,
        self_name=self_row[0] if self_row else None,
        location_total=location_total or 0,
        location_named=location_named or 0,
        software_total=software_total,
    )


def cached_set_counts(sets_dir: Path) -> dict[str, int]:
    counts: dict[str, int] = {}
    for name in CACHED_SET_NAMES:
        db_path = sets_dir / name / "Database" / "Set.db"
        if not db_path.exists():
            continue
        with closing(connect_sqlite(db_path)) as conn:
            n = count_rows(conn, "content")
            if n is not None:
                counts[name] = n

    pet_dir = sets_dir / "Photos.PetRelationship"
    if pet_dir.is_dir():
        total = 0
        for source_dir in pet_dir.iterdir():
            db_path = source_dir / "Database" / "Set.db"
            if db_path.exists():
                with closing(connect_sqlite(db_path)) as conn:
                    total += count_rows(conn, "content") or 0
        if total:
            counts["Photos.PetRelationship"] = total
    return counts


def recently_focused_count(databases_dir: Path) -> int | None:
    db_path = databases_dir / "Games.RecentlyPlayed" / "Games.RecentlyPlayed.sqlite3"
    if not db_path.exists():
        return None
    with closing(connect_sqlite(db_path)) as conn:
        return count_rows(conn, "AppsRecentlyFocused")


@dataclass(frozen=True)
class SyncPeer:
    device_id_prefix: str
    is_self: bool
    model: str
    platform: int
    last_sync: datetime | None


def sync_peers(sync_dir: Path) -> tuple[list[SyncPeer], int]:
    """Returns (peers, ck_atom_count). ck_atom_count is the row count of the
    CloudKit-sync-primitive table (CKAtom) -- present only if this Biome
    instance actually syncs through CloudKit rather than staying local."""
    db_path = sync_dir / "sync.db"
    if not db_path.exists():
        return [], 0
    with closing(connect_sqlite(db_path)) as conn:
        peers = []
        if table_exists(conn, "DevicePeer"):
            rows = conn.execute(
                "SELECT device_identifier, me, model, platform, last_sync_date FROM DevicePeer"
            ).fetchall()
            for device_id, me, model, platform, last_sync in rows:
                peers.append(
                    SyncPeer(
                        device_id_prefix=(device_id or "")[:8],
                        is_self=bool(me),
                        model=model or "(unknown)",
                        platform=platform if platform is not None else -1,
                        last_sync=(
                            datetime.fromtimestamp(last_sync, tz=timezone.utc)
                            if last_sync
                            else None
                        ),
                    )
                )
        ck_atom_count = count_rows(conn, "CKAtom") or 0
    return peers, ck_atom_count


def print_table(headers: Sequence[str], rows: Sequence[Sequence[str]]) -> None:
    widths = [len(h) for h in headers]
    for row in rows:
        for i, cell in enumerate(row):
            widths[i] = max(widths[i], len(str(cell)))
    print("  ".join(h.ljust(w) for h, w in zip(headers, widths)))
    print("  ".join("-" * w for w in widths))
    for row in rows:
        print("  ".join(str(cell).ljust(w) for cell, w in zip(row, widths)))


def print_report(biome_path: Path, top_n: int) -> None:
    print(f"Biome report -- {biome_path}")
    print(f"macOS {macos_build()}")
    print("=" * 72)

    streams = scan_streams(biome_path / "streams" / "restricted")
    populated = len(streams)
    print(f"\n{populated} of the streams this OS defines are currently populated.")

    ranked = sorted(streams, key=lambda s: s.live_bytes, reverse=True)[:top_n]
    print(f"\nTop {len(ranked)} populated streams by live (non-empty) data:")
    print_table(
        ["stream", "live", "of allocated", "last written"],
        [
            [
                s.name + (" *" if s.content_bearing else ""),
                format_bytes(s.live_bytes),
                format_bytes(s.total_bytes),
                f"{s.last_modified:%Y-%m-%d}",
            ]
            for s in ranked
        ],
    )
    if any(s.content_bearing for s in ranked):
        print(
            "\n* content-bearing: this stream can hold verbatim text (Notes/Mail/"
            "Messages/Safari/Siri queries), not just usage metadata. This tool "
            "does not read or print stream payloads, so it cannot show you what "
            "text, if any, is inside -- only that the stream has live data."
        )

    entity = entity_graph_summary(biome_path / "databases")
    if entity:
        print(
            "\nOn-device entity graph (databases/IntelligencePlatform.Entity.sqlite3):"
        )
        print(
            f"  {entity.person_total} people known to this Mac's identity graph "
            f"({entity.person_named} with a resolved name, "
            f"{entity.person_with_email} with a captured email, "
            f"{entity.person_with_phone} with a captured phone number, "
            f"{entity.person_with_employer} with an employer)."
        )
        if entity.self_name:
            print(f"  Your own resolved identity: {entity.self_name}")
        print(
            f"  {entity.location_total} location(s) in the graph "
            f"({entity.location_named} with a name label). Addresses and "
            "coordinates are deliberately not shown by this tool."
        )
        print(f"  {entity.software_total} distinct software/executable entities.")

    set_counts = cached_set_counts(biome_path / "sets" / "Default")
    if set_counts:
        print("\nCached entity sets (sets/Default/*, counts only, no content):")
        print_table(
            ["set", "rows"],
            [[name, str(n)] for name, n in set_counts.items()],
        )

    focused = recently_focused_count(biome_path / "databases")
    if focused is not None:
        print(
            f"\n{focused} apps in the AppsRecentlyFocused table (databases/Games.RecentlyPlayed)."
        )

    peers, ck_atom_count = sync_peers(biome_path / "sync")
    if peers:
        print(
            f"\nSync: {len(peers)} device(s) known to this Biome instance via CloudKit:"
        )
        print_table(
            ["device id", "self", "model/build", "platform", "last sync"],
            [
                [
                    p.device_id_prefix + "...",
                    "yes" if p.is_self else "no",
                    p.model,
                    str(p.platform),
                    f"{p.last_sync:%Y-%m-%d %H:%M UTC}" if p.last_sync else "unknown",
                ]
                for p in peers
            ],
        )
        print(f"  {ck_atom_count:,} CloudKit sync atoms recorded (CKAtom table).")


def purge_streams(streams_dir: Path, assume_yes: bool) -> None:
    require_readable(streams_dir)
    targets = [
        f
        for stream_dir in streams_dir.iterdir()
        if stream_dir.is_dir()
        for f in (stream_dir / "local").glob("*")
        if f.is_file()
    ]
    if not targets:
        logger.info("nothing to purge")
        return

    total_bytes = sum(f.stat().st_size for f in targets)
    logger.info("%d stream file(s), %s total", len(targets), format_bytes(total_bytes))

    if not assume_yes:
        reply = input(f"Delete {len(targets)} Biome stream file(s)? [y/N] ")
        if reply.strip().lower() != "y":
            logger.info("aborted, no changes made")
            return

    deleted = 0
    for f in targets:
        try:
            f.unlink()
            deleted += 1
        except OSError as exc:
            logger.warning("could not delete %s: %s", f, exc)
    logger.info("deleted %d of %d file(s)", deleted, len(targets))


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="biome_audit",
        description="Inspect and optionally clear Apple's Biome activity store.",
    )
    parser.add_argument(
        "--biome-path",
        type=Path,
        default=DEFAULT_BIOME_PATH,
        help=f"Path to the Biome directory (default: {DEFAULT_BIOME_PATH})",
    )
    parser.add_argument("--verbose", action="store_true")
    subparsers = parser.add_subparsers(dest="command", required=True)

    report_parser = subparsers.add_parser(
        "report", help="Read-only summary of what's logged."
    )
    report_parser.add_argument(
        "--top",
        type=int,
        default=20,
        help="Number of streams to show, ranked by live data size.",
    )

    purge_parser = subparsers.add_parser(
        "purge",
        help="Delete current stream ring-buffer contents (not the SQLite databases).",
    )
    purge_parser.add_argument(
        "--yes", action="store_true", help="Skip the confirmation prompt."
    )

    args = parser.parse_args()
    configure_logging(args.verbose)

    if not args.biome_path.exists():
        raise SystemExit(f"{args.biome_path} does not exist on this Mac.")

    if args.command == "report":
        print_report(args.biome_path, args.top)
    elif args.command == "purge":
        purge_streams(args.biome_path / "streams" / "restricted", args.yes)


if __name__ == "__main__":
    main()
