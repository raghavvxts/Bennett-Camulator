#!/usr/bin/env python3
import os
from mycamu_api import MyCamuClient

def main():
    print("=" * 60)
    print("   Bennett MyCamu - Instant Outpass Verification")
    print("=" * 60)

    client = MyCamuClient()
    print(f"Parent Account: {client.email}")
    print("Connecting to Bennett portal (student.bennetterp.camu.in)...")

    if client.login():
        print("\n✅ LOGIN SUCCESSFUL!")
        student = client.student_profile or {}
        print(f"👤 Student Name : {student.get('FNa', '')} {student.get('LNa', '')}")
        print(f"🆔 Student ID   : {student.get('StuID', 'N/A')}")
        print(f"🏫 Institute ID : {client.in_id}")

        leaves = client.get_leaves()
        print(f"\n📋 Retrieved {len(leaves)} past leaves / outpasses.")
        if leaves:
            latest = leaves[0]
            print(f"   • Latest Record: {latest.get('LvTy')} ({latest.get('Rson')}) - Status: {latest.get('Stus')}")

        print("\n🎉 Session cached into 'session.json'!")
        print("Your credentials are saved. You won't have to enter them again.")
    else:
        print("\n❌ Login failed. If rate-limited, wait a couple minutes and try again.")

if __name__ == "__main__":
    main()
