#!/usr/bin/env python3
"""
Add or update Rabithole login credentials.

Usage:
    python manage_users.py list
    python manage_users.py add --username bob --display-name "Bob"
    python manage_users.py update --username admin --new-username owner
    python manage_users.py delete --username bob

For add/update, if --password is omitted you will be prompted for it
interactively (so it never ends up in shell history). --new-password
works the same way for update.
"""

import argparse
import getpass
import hashlib
import secrets
import sqlite3
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent
DB_PATH = ROOT / "db" / "rabithole.db"


def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def hash_password(password: str, salt: str):
    dk = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt.encode("utf-8"), 100000)
    return dk.hex()


def cmd_list(args, conn):
    rows = conn.execute("SELECT username, display_name, created_at FROM users ORDER BY created_at").fetchall()
    if not rows:
        print("No users found.")
        return
    for r in rows:
        print(f"{r['username']}  (display name: {r['display_name'] or '-'})")


def cmd_add(args, conn):
    existing = conn.execute("SELECT id FROM users WHERE username = ?", (args.username,)).fetchone()
    if existing:
        print(f"ERROR: username '{args.username}' already exists. Use 'update' instead.")
        return

    password = args.password or getpass.getpass(f"Password for new user '{args.username}': ")
    if not password:
        print("ERROR: password is required to add a user.")
        return

    salt = secrets.token_hex(16)
    password_hash = hash_password(password, salt)
    user_id = f"user-{args.username}"
    now = int(time.time() * 1000)

    conn.execute(
        "INSERT INTO users (id, username, password_hash, salt, display_name, created_at) VALUES (?,?,?,?,?,?)",
        (user_id, args.username, password_hash, salt, args.display_name, now),
    )
    conn.commit()
    print(f"Added user '{args.username}'.")


def cmd_update(args, conn):
    user = conn.execute("SELECT * FROM users WHERE username = ?", (args.username,)).fetchone()
    if not user:
        print(f"ERROR: no user found with username '{args.username}'")
        return

    new_username = args.new_username or user["username"]
    if new_username != user["username"]:
        clash = conn.execute("SELECT id FROM users WHERE username = ?", (new_username,)).fetchone()
        if clash:
            print(f"ERROR: username '{new_username}' is already taken.")
            return

    password = args.new_password
    if password is None and args.set_password:
        password = getpass.getpass(f"New password for '{user['username']}' (leave blank to keep current): ")

    display_name = args.new_display_name if args.new_display_name is not None else user["display_name"]

    if password:
        salt = secrets.token_hex(16)
        password_hash = hash_password(password, salt)
        conn.execute(
            "UPDATE users SET username=?, password_hash=?, salt=?, display_name=? WHERE id=?",
            (new_username, password_hash, salt, display_name, user["id"]),
        )
    else:
        conn.execute(
            "UPDATE users SET username=?, display_name=? WHERE id=?",
            (new_username, display_name, user["id"]),
        )

    # Log out all existing sessions for this user so old tokens can't be used post-change
    conn.execute("DELETE FROM sessions WHERE user_id = ?", (user["id"],))
    conn.commit()

    print(f"Updated user '{user['username']}' -> '{new_username}'.")
    if password:
        print("Password changed. All existing sessions for this user were logged out.")


def cmd_delete(args, conn):
    user = conn.execute("SELECT * FROM users WHERE username = ?", (args.username,)).fetchone()
    if not user:
        print(f"ERROR: no user found with username '{args.username}'")
        return
    remaining = conn.execute("SELECT COUNT(*) AS c FROM users").fetchone()["c"]
    if remaining <= 1:
        print("ERROR: refusing to delete the last remaining user account.")
        return
    conn.execute("DELETE FROM sessions WHERE user_id = ?", (user["id"],))
    conn.execute("DELETE FROM users WHERE id = ?", (user["id"],))
    conn.commit()
    print(f"Deleted user '{args.username}'.")


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)

    p_list = sub.add_parser("list", help="List existing usernames")
    p_list.set_defaults(func=cmd_list)

    p_add = sub.add_parser("add", help="Add a new user")
    p_add.add_argument("--username", required=True)
    p_add.add_argument("--password")
    p_add.add_argument("--display-name")
    p_add.set_defaults(func=cmd_add)

    p_update = sub.add_parser("update", help="Update an existing user's username/password/display name")
    p_update.add_argument("--username", required=True, help="Current username of the account to update")
    p_update.add_argument("--new-username")
    p_update.add_argument("--new-password")
    p_update.add_argument("--new-display-name")
    p_update.add_argument(
        "--set-password", action="store_true",
        help="Prompt for a new password interactively if --new-password wasn't given",
    )
    p_update.set_defaults(func=cmd_update)

    p_delete = sub.add_parser("delete", help="Delete a user")
    p_delete.add_argument("--username", required=True)
    p_delete.set_defaults(func=cmd_delete)

    args = parser.parse_args()

    if not DB_PATH.exists():
        print(f"ERROR: Database not found at {DB_PATH}")
        return

    conn = get_db()
    args.func(args, conn)
    conn.close()


if __name__ == "__main__":
    main()
