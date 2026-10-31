#!/usr/bin/env python3
"""Read-only profile/history diagnostic; never creates or approves a pass."""
import os
from dotenv import load_dotenv
from mycamu_api import MyCamuClient


def main():
    load_dotenv()
    email, password = os.getenv("PARENT_EMAIL"), os.getenv("PARENT_PASSWORD")
    if not email or not password:
        print("Set PARENT_EMAIL and PARENT_PASSWORD before running this diagnostic.")
        return
    ok, error, profile = MyCamuClient.test_and_fetch_profile(email, password)
    if not ok:
        print(f"Login failed: {error}")
        return
    client = MyCamuClient(email=email, password=password, cookies=profile["cookies"],
                         stu_id=profile["stu_id"], in_id=profile["in_id"], student_name=profile["student_name"])
    print(f"Login successful. Account type: {profile['account_type']}")
    try:
        records = client.get_leaves()
        print(f"Retrieved {len(records)} leave/gate-pass records.")
        for record in records[:5]:
            print(f"{client.record_type(record)}: {client.record_status(record)}")
    except Exception:
        print("Could not retrieve records. Check MyCamu before retrying.")


if __name__ == "__main__":
    main()
