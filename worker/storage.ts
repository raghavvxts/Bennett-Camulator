import { open, seal, hash, randomToken } from "./security.ts";
export interface Profile {
  email: string;
  password: string;
  student_name: string;
  stu_id: string;
  in_id: string;
  logged_in_id: string;
  account_type: string;
  cookies: Record<string, string>;
}
export interface State {
  step: "email" | "password" | "tomorrow" | "time" | "confirm" | "owner";
  email?: string;
  date?: string;
  departure?: string;
  hour?: number;
  minute?: number;
  page?: number;
}
export class Store {
  // Store instances live for one request; never share decrypted accounts globally.
  private accounts = new Map<number, Promise<Profile | null>>();
  private snapshots = new Map<number, string>();
  constructor(readonly env: Env) {}
  async account(id: number): Promise<Profile | null> {
    const cached = this.accounts.get(id);
    if (cached) return cached;
    const pending = this.loadAccount(id);
    this.accounts.set(id, pending);
    return pending;
  }
  private async loadAccount(id: number): Promise<Profile | null> {
    const row = await this.env.DB.prepare(
      "SELECT sealed FROM accounts WHERE id=?",
    )
      .bind(id)
      .first<{ sealed: string }>();
    if (!row) return null;
    const profile = await open<Profile>(
      row.sealed,
      this.env.CREDENTIAL_KEY,
      `account:${id}`,
    );
    this.snapshots.set(id, JSON.stringify(profile));
    return profile;
  }
  async saveAccount(
    id: number,
    profile: Profile,
    relink = false,
  ): Promise<void> {
    const serialized = JSON.stringify(profile);
    if (!relink && this.snapshots.get(id) === serialized) return;
    const sealed = await seal(
      profile,
      this.env.CREDENTIAL_KEY,
      `account:${id}`,
    );
    await this.env.DB.prepare(
      `INSERT INTO accounts(id,sealed,linked_at) VALUES(?,?,?) ON CONFLICT(id) DO UPDATE SET sealed=excluded.sealed${relink ? ",token_hash=NULL,linked_at=excluded.linked_at" : ""}`,
    )
      .bind(id, sealed, new Date().toISOString())
      .run();
    this.snapshots.set(id, serialized);
    this.accounts.set(id, Promise.resolve(profile));
  }
  async forget(id: number): Promise<void> {
    await this.env.DB.batch([
      this.env.DB.prepare("DELETE FROM accounts WHERE id=?").bind(id),
      this.env.DB.prepare("DELETE FROM conversations WHERE id=?").bind(id),
    ]);
    this.accounts.delete(id);
    this.snapshots.delete(id);
  }
  async state(id: number): Promise<State | null> {
    const row = await this.env.DB.prepare(
      "SELECT sealed FROM conversations WHERE id=? AND expires>?",
    )
      .bind(id, Date.now())
      .first<{ sealed: string }>();
    return row
      ? open<State>(row.sealed, this.env.CREDENTIAL_KEY, `state:${id}`)
      : null;
  }
  async setState(id: number, state: State | null): Promise<void> {
    if (!state) {
      await this.env.DB.prepare("DELETE FROM conversations WHERE id=?")
        .bind(id)
        .run();
      return;
    }
    const sealed = await seal(state, this.env.CREDENTIAL_KEY, `state:${id}`);
    await this.env.DB.prepare(
      "INSERT INTO conversations(id,sealed,expires) VALUES(?,?,?) ON CONFLICT(id) DO UPDATE SET sealed=excluded.sealed,expires=excluded.expires",
    )
      .bind(
        id,
        sealed,
        Date.now() + (state.step === "owner" ? 120000 : 1800000),
      )
      .run();
  }
  async lease(key: string): Promise<string | null> {
    const token = randomToken();
    const row = await this.env.DB.prepare(
      "INSERT INTO leases(key,token,expires) VALUES(?,?,?) ON CONFLICT(key) DO UPDATE SET token=excluded.token,expires=excluded.expires WHERE leases.expires<? RETURNING token",
    )
      .bind(key, token, Date.now() + 300000, Date.now())
      .first<{ token: string }>();
    return row?.token === token ? token : null;
  }
  async release(key: string, token: string): Promise<void> {
    await this.env.DB.prepare("DELETE FROM leases WHERE key=? AND token=?")
      .bind(key, token)
      .run();
  }
  async uncertain(account: string, value?: boolean): Promise<boolean> {
    if (value !== undefined) {
      await this.env.DB.prepare(
        "INSERT INTO operations(account,uncertain) VALUES(?,?) ON CONFLICT(account) DO UPDATE SET uncertain=excluded.uncertain",
      )
        .bind(account, value ? 1 : 0)
        .run();
      return value;
    }
    return Boolean(
      (
        await this.env.DB.prepare(
          "SELECT uncertain FROM operations WHERE account=?",
        )
          .bind(account)
          .first<{ uncertain: number }>()
      )?.uncertain,
    );
  }
  async shortcut(id: number): Promise<string> {
    const token = randomToken();
    await this.env.DB.prepare("UPDATE accounts SET token_hash=? WHERE id=?")
      .bind(await hash(token), id)
      .run();
    return token;
  }
  async shortcutId(token: string): Promise<number | null> {
    return (
      (
        await this.env.DB.prepare("SELECT id FROM accounts WHERE token_hash=?")
          .bind(await hash(token))
          .first<{ id: number }>()
      )?.id ?? null
    );
  }
}
