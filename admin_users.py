"""Local server-console account inspection; not an HTTP or Telegram endpoint."""
import argparse
import json

import database


def main():
    parser = argparse.ArgumentParser(description='List linked bot accounts from the local database.')
    parser.add_argument('--show-passwords', action='store_true',
                        help='Explicitly reveal passwords in this local terminal. Do not share the output.')
    args = parser.parse_args()
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
