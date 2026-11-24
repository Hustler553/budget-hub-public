"""Clear an instance's password so its owner can set a new one."""

import argparse
import sys
from datetime import datetime

from budget import config, db
from budget.services import auth


class NotConfirmed(Exception):
    pass


def clear_password(confirm=False):
    if not confirm:
        raise NotConfirmed(
            "clear_password() erases the stored password; pass confirm=True")
    stamp = datetime.now().strftime("%Y%m%dT%H%M%S%f")
    snapshot = db.backup_db(label=f"prereset-{stamp}")
    if snapshot is None:
        raise RuntimeError(
            f"Refusing to reset: no snapshot was written for prereset-{stamp}. "
            f"A file of that name already exists in {config.BACKUP_DIR}.")
    auth.clear_password()
    return snapshot


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--force", action="store_true",
                        help="skip the confirmation prompt")
    args = parser.parse_args(argv)

    print(f"Instance database: {config.DB_PATH}")
    if not auth.password_is_set():
        print("No password is set — this instance is already awaiting setup.")
        print("\nIt is standing open: anyone who can reach it can claim")
        print("it at /setup. If that was not expected, have the owner set")
        print("their password now.")
        return 0

    if not args.force:
        print("\nThis will erase the stored password and revoke every")
        print("remembered device. The owner then sets a new password the next")
        print("time they open the app.")
        print("\nIt does NOT end browser sessions: a browser")
        print("that has opened the app recently may carry straight on")
        print("without the new password. This is for a forgotten")
        print("password, not for shutting someone out.")
        print("\nWhile no password is set, ANYONE who can reach this instance")
        print("can claim it at /setup. Do this while the owner is ready to")
        print("set their new password, not before.")
        if input("\nType 'reset' to continue: ").strip() != "reset":
            print("Cancelled; nothing changed.")
            return 1

    snapshot = clear_password(confirm=True)
    print(f"\nDone. A recovery copy was saved as {snapshot.name}")
    print(f"in {config.BACKUP_DIR}.")
    print("Tell the owner to open the app and set a new password now.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
