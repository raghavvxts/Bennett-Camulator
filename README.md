# Bennett Camulator

A multi-user Telegram bot and FastAPI service for Bennett University's MyCamu parent portal.

## Day Out Pass

- Account access is restricted to private Telegram chats.
- The default outing reason is **Going out**. Users can change it before selecting **Create pass**.
- The bot checks the linked student's leave and gate records first. A valid leave covering the selected date is reused; otherwise it submits `Leave`, a reason, and the same date in both leave-date fields.
- It creates a real gate pass using the portal's Day Out Pass configuration (`DOP`), requests parent approval separately, and checks that pass's final status before reporting success.
- A new pass is allowed only after the current gate pass is marked `Completed`, even if the student is already inside campus. Existing submitted/approved day passes are reused (and pending parent approval can be completed) without creating another pass. Other non-completed statuses block creation; a current unfinished pass also blocks scheduling another pass for tomorrow. A contradictory scanned-out record still blocks creation until resolved.
- Departure defaults to the next full minute **after** the portal checks. Return is 7 PM the same day. User-selected departures are revalidated before submission.
- Before 6 AM, the bot asks for a departure time. At closing it asks whether to apply for tomorrow, then asks for a time from 6 AM to before 7 PM. It never silently changes the selected day.
- No documents are requested for Day Out Pass. Multi-day Leave Out Pass is outside this implementation.
- One progress message shows checks, leave/gate-pass submission, and approval verification.

Leave and gate-pass statuses are separate: a leave may be `Submitted` while the gate pass is `Approved`. Old malformed `LEAVE_OUT_PASS` leave records are left untouched.

Draft prompts are stored in SQLite. After a restart, `/pass` or the main-menu pass button resumes today's saved prompt for confirmation. Credentials and incomplete login conversations are not stored in drafts. Requests are never automatically submitted on restart.

The API and bot share a database lock per account. A request that may have reached MyCamu is marked uncertain before transmission. After a crash or timeout, an existing gate pass can be reconciled and approved; if no matching pass is found, further creation is blocked for manual inspection. Do not blindly retry or clear this flag without checking the portal, including any partially created leave. An operator can clear a verified-safe record in `pass_operations` after confirming its hashed account key. There is no automatic retry of mutations.

## Live menu and attendance

`/menu` and meal buttons fetch the linked student's MyCamu Cafeteria feed each time, using IST for the day. Dishes, facility, and serving times come from that feed. The default is the active meal or next serving; **Today's full menu** shows all meals. After the final serving the bot labels the displayed meal as finished. After midnight, requests fetch the new day's feed. Missing menus and fetch failures are reported without a static fallback.

`/attendance` shows overall and subject statistics and advice for a 75% threshold. Counts are validated, decimal percentages are supported, and bunk advice uses class counts rather than rounded percentages. Long reports are split into plain-text messages.

Portal network calls run outside Telegram's event loop. A chat-aware update processor keeps each conversation ordered while allowing up to 16 users' updates to proceed concurrently. A dedicated 16-thread portal pool keeps slow university requests separate from the heartbeat executor. Larger bursts queue; they are not sent to MyCamu all at once. A local test runs 100 independent linked accounts through submission and approval against mocked portal responses, plus 100 personal-key API requests. This verifies account isolation and queue handling, not live portal capacity or a guarantee that every request succeeds.

## Local setup

Python 3.11 or newer:

```sh
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
```

Configure `.env` in this repository:

- `TELEGRAM_BOT_TOKEN`: enables the Telegram worker.
- `OWNER_TELEGRAM_ID`: your numeric Telegram ID from `/myid`; enables private owner-only `/users`. Leave unset to disable the owner directory. Never hardcode this ID in source.
- `API_SECRET_KEY`: optional owner-only shortcut API key for the environment-configured parent account. Other users generate their own key with `/shortcut`. No public default key is accepted. Generate a strong value, for example with `python -c 'import secrets; print(secrets.token_urlsafe(32))'`.
- `PARENT_EMAIL`, `PARENT_PASSWORD`: optional single-account shortcut credentials. Telegram users link their own accounts.
- `CAMULATOR_DATA_DIR`: persistent directory for the database, encryption key, drafts, locks and backups; defaults to this repository locally.
- `CREDENTIAL_KEY`: optional Fernet key supplied outside the database. If omitted, `.credential.key` is generated with owner-only permissions in the data directory when credentials are first saved.

Run `bash start.sh`. It loads `.env` and supervises the API plus the optional Telegram worker. If either child exits, the other is stopped and the service exits unsuccessfully so the host can restart it. Shutdown signals stop both processes. The API uses `$PORT`, defaulting to 8000.

`/health` checks the Telegram event-loop heartbeat when the bot is enabled and returns HTTP 503 when it is missing or stale. Without a Telegram token it checks the API alone. Health checks do not log into MyCamu or guarantee the university portal is available.

Telegram commands: `/start`, `/login`, `/pass`, `/cancel`, `/menu`, `/attendance`, `/restart`, `/features`, `/shortcut`.

## Owner account directory

In a private chat, use `/myid`, then set `OWNER_TELEGRAM_ID` to that value in hosting secrets and restart the service. `/users` lists linked accounts with student names, Telegram IDs, emails, decrypted passwords and linked dates; `/users 2` shows the next page. It is denied to other Telegram IDs and in group chats. This lists currently linked accounts, not live activity or users who have logged out.

Passwords are sent as plain text only to the configured owner in a private chat; session cookies are excluded. Anyone with access to that owner chat can read the displayed passwords. For local console inspection, run `python admin_users.py --show-passwords` in your private server console with access to the database and its original encryption key. Without that flag, `python admin_users.py` shows identifiers only. This is a local operator tool, not a network-accessible credential export. Do not share console output containing passwords. Account linking tells users that the server operator can access stored credentials.

Making the source repository private does not move or delete the runtime database. The hosting provider still needs authorized access to the private repository for future builds and deployments.

## Storage, migration and backups

SQLite encrypts stored login emails, parent passwords and session cookies with Fernet. Existing plaintext credentials are migrated transactionally at startup; database compaction removes old plaintext from its live pages/WAL. A wrong or missing key for existing encrypted accounts causes startup to fail rather than reset their data. Basic student metadata and Telegram IDs remain unencrypted; database files and backups have owner-only permissions. The private, owner-only `/users` command displays stored passwords; there is no HTTP credential-export endpoint. Runtime and privileged hosting administrators with the encryption key can still decrypt credentials; filesystem owner permissions do not prevent privileged access.

**Preserve the original credential key.** Encrypted backups cannot be restored without it. Keep a separate secure copy of the key or supply `CREDENTIAL_KEY` through hosting secrets. Local file-key encryption does not protect against someone who can read both the database and its key. Bot error logging omits incoming updates and raw exception messages to reduce accidental credential disclosure.

The supervisor creates an online SQLite backup at startup and then once per day, retaining the seven latest daily snapshots under `backups/` in the data directory. These are local recovery snapshots, not off-site backups; copy snapshots and the recovery key to separate protected storage for protection against disk loss. Logout deletes the live linked account and its draft; retained backups expire with the seven-snapshot rotation.

`render.yaml` declares a 1 GB persistent disk mounted at `/var/data`. Review hosting plan and disk charges before deployment. Existing deployments must copy their current database and original credential key to the configured data directory before switching. No deployment or remote migration is performed by these source changes.

Use one deployment instance and one Telegram polling worker. The database coordinates the two local processes; it is not a distributed database for multiple hosts. Docker excludes credentials, database files, local keys, backups and virtual environments from the build context.

## Shortcut API

Each linked Telegram user runs `/shortcut` in private chat to generate a personal key for their own account. Only a hash of that key is stored. Running `/shortcut` again rotates it; logout or account relinking revokes it. A personal key works without `API_SECRET_KEY` or any environment parent credentials. The optional owner key still uses only the environment-configured parent account.

Only `X-API-Key` is accepted. Query-string keys and mutating `GET /quick-pass` are removed. Use `POST /quick-pass`:

```json
{"reason": "Going out"}
```

An empty JSON body uses the same default reason. To specify a departure, supply an ISO datetime with a timezone:

```json
{"reason": "Going out", "departure_at": "2026-11-01T09:00:00+05:30"}
```

Only today or tomorrow is accepted. After closing, a request without a departure returns `needs_next_day: true`; the shortcut must ask before sending tomorrow's selected time. `force` cannot bypass gate/overlap checks. Portal refusals and uncertain submissions return `success: false`; callers must inspect that field, not only HTTP status.

Other protected endpoints: `/active-pass`, `/attendance`, `/leaves`. All protected endpoints select the parent account from the supplied personal key. Clients cannot select another account by passing a Telegram/student ID in the request body. The optional owner key selects only the environment-configured account.

## Project privacy

The source and bot text do not hardcode the operator’s personal name or account credentials. This is not anonymity: public GitHub ownership and commit authorship, hosting records, Telegram configuration, and MyCamu’s account/request logs can identify or associate activity. Source cleanup does not erase public history or third-party records.

## Verification

```sh
python -m unittest discover -v
```

Tests use temporary databases and mocked portal requests. They cover timing boundaries, slow portal checks, missing/existing leave, approval verification, encrypted credential migration, shared-process locks, uncertain-request recovery, saved prompts, live menu selection, decimal attendance API access controls, 100-user account isolation, and personal-key rotation/revocation.

On 31 October 2026, live read-only checks verified the menu feed and reuse of an approved pass. A real first-outing request also created the missing same-day leave and produced a verified, parent-approved gate pass. The later hardening changes are tested locally without further real pass creation. No Telegram token is bundled, and a real Telegram deployment still requires an end-to-end check with a configured bot.
