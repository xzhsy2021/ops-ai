from __future__ import annotations

import argparse
import base64
import json
import os
import sqlite3
import sys
import time

DB_PATHS = [
    os.path.expanduser("~/.local/share/opencode/opencode.db"),
    os.path.expanduser("~/.config/opencode/opencode.db"),
]

IMAGE_MIME_PREFIX = "data:image/"


def find_db() -> str:
    for p in DB_PATHS:
        if os.path.isfile(p):
            return p
    raise RuntimeError("opencode DB not found. Looked at: " + ", ".join(DB_PATHS))


def connect() -> sqlite3.Connection:
    return sqlite3.connect(find_db())


def list_image_parts(db, limit: int, session_id: str | None = None):
    cur = db.cursor()
    where = "p.data LIKE '%data:image/%'"
    params: list = []
    if session_id:
        where += " AND p.session_id = ?"
        params.append(session_id)
    cur.execute(
        f"""
        SELECT p.id, p.session_id, p.message_id, length(p.data) AS sz,
               p.data, p.time_created
        FROM part p
        WHERE {where}
        ORDER BY p.time_created DESC
        LIMIT ?
        """,
        params + [limit],
    )
    out = []
    for part_id, session_id, msg_id, sz, data, ts in cur.fetchall():
        try:
            d = json.loads(data)
        except ValueError:
            continue
        if d.get("type") != "file":
            continue
        url = d.get("url", "")
        if not url.startswith(IMAGE_MIME_PREFIX):
            continue
        out.append((part_id, session_id, msg_id, sz, d.get("mime", ""), ts))
    return out


def extract(db, part_id: str, out_dir: str, verbose: bool = True):
    cur = db.cursor()
    cur.execute("SELECT data FROM part WHERE id = ?", (part_id,))
    row = cur.fetchone()
    if not row:
        raise RuntimeError(f"part not found: {part_id}")
    d = json.loads(row[0])
    if d.get("type") != "file":
        raise RuntimeError(f"part {part_id} is type={d.get('type')!r}, not a file")
    url = d.get("url", "")
    if not url.startswith(IMAGE_MIME_PREFIX):
        raise RuntimeError(f"part {part_id} has no data:image url")
    mime = d.get("mime") or "application/octet-stream"
    ext = mime.split("/")[1] if "/" in mime else "bin"
    if ext in ("octet-stream", "png;base64", "jpg"):
        ext = "png"
    filename = d.get("filename") or f"part_{part_id}.{ext}"
    out_path = os.path.join(out_dir, os.path.basename(filename))
    if not out_path.endswith("." + ext) and os.path.exists(out_path):
        out_path = os.path.join(out_dir, f"{part_id}.{ext}")
    b64 = url.split(",", 1)[1]
    with open(out_path, "wb") as f:
        f.write(base64.b64decode(b64))
    if verbose:
        print(f"[OK] part={part_id} mime={mime} size={os.path.getsize(out_path)} -> {out_path}")
    return out_path


def main():
    parser = argparse.ArgumentParser(
        description="Extract images pasted into opencode chats from the opencode SQLite DB."
    )
    parser.add_argument("--list", action="store_true", help="list recent image parts (default)")
    parser.add_argument("--part", help="part id to extract")
    parser.add_argument("--latest", action="store_true", help="extract the most recent image part")
    parser.add_argument("--session", help="only consider parts from this session id")
    parser.add_argument("--limit", type=int, default=10, help="list limit (default 10)")
    parser.add_argument("--out", default=os.path.expanduser("~/AppData/Local/Temp/opencode"),
                        help="output dir for extracted images")
    args = parser.parse_args()

    db = connect()
    if args.part:
        os.makedirs(args.out, exist_ok=True)
        extract(db, args.part, args.out)
        return

    rows = list_image_parts(db, 2000 if args.latest else args.limit, args.session)
    if not rows:
        print("[INFO] no image parts found in opencode DB")
        return

    if args.latest:
        if not rows:
            print("[INFO] no image parts found in opencode DB")
            return
        part_id = rows[0][0]
        os.makedirs(args.out, exist_ok=True)
        extract(db, part_id, args.out)
        return

    print(f"{'PART_ID':<34} {'SIZE':>8}  {'TIME':<16} SESSION")
    for part_id, session_id, msg_id, sz, mime, ts in rows:
        tstr = time.strftime("%m-%d %H:%M", time.localtime(ts / 1000)) if ts else "?"
        print(f"{part_id:<34} {sz:>8}  {tstr:<16} {session_id[:24]}")


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print(f"[ERROR] {e}", file=sys.stderr)
        sys.exit(1)
