"""Repeatable public research with URL-keyed incremental SQLite storage."""

import argparse
import hashlib
import json
import sqlite3
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path


def persist(rows, out, query):
    out = Path(out)
    out.mkdir(parents=True, exist_ok=True)
    now = datetime.now(timezone.utc).isoformat()
    new = updated = 0
    with closing(sqlite3.connect(out / "posts.sqlite3")) as db, db:
        db.execute(
            "CREATE TABLE IF NOT EXISTS posts(url TEXT PRIMARY KEY,data TEXT NOT NULL,hash TEXT NOT NULL,first_seen TEXT NOT NULL,last_seen TEXT NOT NULL)"
        )
        db.execute(
            "CREATE TABLE IF NOT EXISTS runs(at TEXT,query TEXT,seen INTEGER,new INTEGER,updated INTEGER)"
        )
        for row in rows:
            payload = json.dumps(row, ensure_ascii=False, sort_keys=True)
            digest = hashlib.sha256(payload.encode()).hexdigest()
            old = db.execute("SELECT hash FROM posts WHERE url=?", (row["url"],)).fetchone()
            if old is None:
                new += 1
            elif old[0] != digest:
                updated += 1
            db.execute(
                "INSERT INTO posts VALUES(?,?,?,?,?) ON CONFLICT(url) DO UPDATE SET data=excluded.data,hash=excluded.hash,last_seen=excluded.last_seen",
                (row["url"], payload, digest, now, now),
            )
        db.execute("INSERT INTO runs VALUES(?,?,?,?,?)", (now, query, len(rows), new, updated))
        total = db.execute("SELECT COUNT(*) FROM posts").fetchone()[0]
    result = {
        "checkedAt": now,
        "query": query,
        "seen": len(rows),
        "new": new,
        "updated": updated,
        "totalUnique": total,
        "rows": rows,
    }
    (out / "latest.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("query")
    parser.add_argument("--limit", type=int, default=10)
    parser.add_argument("--since")
    parser.add_argument("--until")
    parser.add_argument(
        "--out", default=str(Path.cwd() / "threads-results")
    )
    args = parser.parse_args()
    from agent_reach.channels import get_channel
    from agent_reach.channels.threads import ThreadsChannel

    channel = get_channel("threads")
    if not isinstance(channel, ThreadsChannel):
        raise RuntimeError("Threads channel is not registered")
    rows = channel.search(args.query, args.limit, args.since, args.until)
    result = persist(rows, args.out, args.query)
    print(
        json.dumps({k: v for k, v in result.items() if k != "rows"}, ensure_ascii=False, indent=2)
    )


if __name__ == "__main__":
    main()
