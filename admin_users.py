"""Local server-console account inspection; not an HTTP or Telegram endpoint."""
import argparse
import json
from getpass import getpass
import os
from pathlib import Path
import owner_access

import database


def main():
    parser = argparse.ArgumentParser(description='List linked bot accounts from the local database.')
    modes = parser.add_mutually_exclusive_group()
    modes.add_argument('--set-password', action='store_true', help='Configure the separate owner password in this private console.')
    modes.add_argument('--show-passwords', action='store_true',
                        help='Explicitly reveal passwords in this local terminal. Do not share the output.')
    args = parser.parse_args()
    try:
        if args.set_password:
            if os.getenv('OWNER_PASSWORD_HASH') or (Path(database.DB_PATH).parent/'.owner-password.hash').exists():
                if not owner_access.authenticate(getpass('Current owner password: ')):
                    parser.error('Incorrect current owner password.')
            password = getpass('New owner password (at least 12 characters): ')
            if password != getpass('Confirm new owner password: '):
                parser.error('Passwords do not match.')
            owner_access.set_password(password)
            print('Owner password configured. No plaintext password was stored.')
            return
        if not owner_access.authenticate(getpass('Owner password: ')):
            parser.error('Incorrect owner password.')
    except ValueError as exc:
        parser.error(str(exc))
    page = 1
    while True:
        total, users = database.user_directory(page, 100)
        for user in users:
            if args.show_passwords:
                user['password'] = database.get_user(user['telegram_id'])['password']
            print(json.dumps(user, ensure_ascii=False))
        if page*100 >= total:
            break
        page += 1


if __name__ == '__main__':
    main()
