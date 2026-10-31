const encoder = new TextEncoder();
export function base64(bytes: Uint8Array): string {
  return btoa(String.fromCharCode(...bytes));
}
export function unbase64(value: string): Uint8Array<ArrayBuffer> {
  return Uint8Array.from(atob(value), (c) => c.charCodeAt(0));
}
export function randomToken(): string {
  return base64(crypto.getRandomValues(new Uint8Array(32)))
    .replace(/\+/g, "-")
    .replace(/\//g, "_")
    .replace(/=+$/, "");
}
export async function hash(value: string): Promise<string> {
  return Array.from(
    new Uint8Array(
      await crypto.subtle.digest("SHA-256", encoder.encode(value)),
    ),
    (x) => x.toString(16).padStart(2, "0"),
  ).join("");
}
export async function equal(a: string, b: string): Promise<boolean> {
  const key = await crypto.subtle.importKey(
    "raw",
    encoder.encode("camulator-constant-time-comparison"),
    { name: "HMAC", hash: "SHA-256" },
    false,
    ["sign", "verify"],
  );
  const signature = await crypto.subtle.sign("HMAC", key, encoder.encode(a));
  return crypto.subtle.verify("HMAC", key, signature, encoder.encode(b));
}
export async function seal(
  value: unknown,
  secret: string,
  context: string,
): Promise<string> {
  const key = await crypto.subtle.importKey(
    "raw",
    unbase64(secret),
    "AES-GCM",
    false,
    ["encrypt"],
  );
  const iv = crypto.getRandomValues(new Uint8Array(12));
  const ciphertext = await crypto.subtle.encrypt(
    { name: "AES-GCM", iv, additionalData: encoder.encode(context) },
    key,
    encoder.encode(JSON.stringify(value)),
  );
  return `v1:${base64(iv)}:${base64(new Uint8Array(ciphertext))}`;
}
export async function open<T>(
  value: string,
  secret: string,
  context: string,
): Promise<T> {
  const [version, iv, ciphertext] = value.split(":");
  if (version !== "v1" || !iv || !ciphertext)
    throw new Error("Invalid encrypted record");
  const key = await crypto.subtle.importKey(
    "raw",
    unbase64(secret),
    "AES-GCM",
    false,
    ["decrypt"],
  );
  const plain = await crypto.subtle.decrypt(
    {
      name: "AES-GCM",
      iv: unbase64(iv),
      additionalData: encoder.encode(context),
    },
    key,
    unbase64(ciphertext),
  );
  return JSON.parse(new TextDecoder().decode(plain)) as T;
}
export async function ownerVerifier(
  password: string,
  pepper: string,
  salt = base64(crypto.getRandomValues(new Uint8Array(16))),
): Promise<string> {
  if (password.length < 12)
    throw new Error("Owner password must be at least 12 characters");
  // Workers WebCrypto caps PBKDF2 at 100,000 iterations. Pepper is a separate hosting secret.
  const key = await crypto.subtle.importKey(
    "raw",
    encoder.encode(password + "\0" + pepper),
    "PBKDF2",
    false,
    ["deriveBits"],
  );
  const bits = await crypto.subtle.deriveBits(
    {
      name: "PBKDF2",
      salt: unbase64(salt),
      iterations: 100000,
      hash: "SHA-256",
    },
    key,
    256,
  );
  return `pbkdf2$100000$${salt}$${base64(new Uint8Array(bits))}`;
}
export async function verifyOwner(
  password: string,
  verifier: string,
  pepper: string,
): Promise<boolean> {
  if (!verifier.startsWith("pbkdf2$100000$") || password.length < 12)
    return false;
  return equal(
    await ownerVerifier(password, pepper, verifier.split("$")[2]),
    verifier,
  );
}
