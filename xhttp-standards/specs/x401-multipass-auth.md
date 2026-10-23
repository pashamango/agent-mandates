# xHTTP-401: Multipass Authentication Challenge (v0.1.0-draft)

**Status:** Proposed Standard Working Draft  
**Version:** 0.1.0-draft (2026-10-23)  
**Authority:** Principal Pavel Cherkashin (`pavel@mindrock.capital`)  
**Standard Identifier:** `RFC-xHTTP-401`  
**Dependencies:** RFC 8785 (JSON Canonicalization Scheme), RFC 8615, RFC 1035 (DNS)  

---

## 1. Abstract & Problem Statement

Traditional HTTP authentication mechanisms (e.g. Basic, Bearer JWT, OAuth2 Authorization Code flow) assume human-supervised sessions, centralized Identity Providers (IdPs), or preshared long-lived secrets. 

In autonomous agent meshes and federated edge environments:
1. **No Shared Central Authority**: Agents operate across sovereign domains without a universally trusted third-party OAuth2 provider.
2. **Key Rotation & Proof of Possession**: Simple bearer tokens are vulnerable to exfiltration and replay attacks; agents must prove private key possession dynamically for every session.
3. **Domain Anchoring**: Verifying that an autonomous agent is authorized to speak on behalf of a given company or cluster requires an open, decentralized trust anchor.

**xHTTP-401** defines a challenge-response authentication protocol where the server challenges an unauthenticated client with a cryptographically bound challenge envelope. The client responds with a signed **Multipass** envelope proving possession of an Ed25519 private key whose public key and delegation mandate are published openly in the Domain Name System via **goDNS** (`_mandate.<agent>.<domain>`).

---

## 2. Protocol Sequence Diagram

```
Autonomous Agent                                           Edge Server / Gateway
(did:key:z6MknDcbz...)                                    (edge.mindrock.capital)
      │                                                              │
      │ 1. GET /api/v1/compute/dispatch HTTP/1.1                     │
      │    Host: edge.mindrock.capital                               │
      ├─────────────────────────────────────────────────────────────►│
      │                                                              │
      │                                                              │ (Evaluates request:
      │                                                              │  Missing Authorization)
      │ 2. HTTP/1.1 401 Unauthorized                                 │
      │    WWW-Authenticate: Multipass realm="mindrock-fleet"        │
      │    Content-Type: application/xhttp+json                      │
      │                                                              │
      │    <401 Multipass Challenge Envelope>                        │
      │    { "challenge_id", "nonce", "dns_root", "expires_at" }     │
      │◄─────────────────────────────────────────────────────────────┤
      │                                                              │
      │ (Parses challenge, verifies expires_at,                      │
      │  canonicalizes digest via JCS RFC 8785,                      │
      │  signs with Ed25519 private key)                             │
      │                                                              │
      │ 3. POST /api/v1/compute/dispatch HTTP/1.1                    │
      │    Host: edge.mindrock.capital                               │
      │    Authorization: Multipass <Base64URL-Encoded Multipass>    │
      │    Content-Type: application/json                            │
      │                                                              │
      │    { <Target Payload> }                                      │
      ├─────────────────────────────────────────────────────────────►│
      │                                                              │
      │                                                              │ (Extracts Multipass:
      │                                                              │  1. Check nonce replay
      │                                                              │  2. Resolve DNS TXT
      │                                                              │     _mandate.agy.hackt.com
      │                                                              │  3. Verify Ed25519 PoP)
      │                                                              │
      │ 4. HTTP/1.1 200 OK (or 202 Accepted)                         │
      │    X-Multipass-Subject: did:key:z6MknDcbz...                 │
      │    Content-Type: application/json                            │
      │◄─────────────────────────────────────────────────────────────┤
```

---

## 3. Envelope Specifications & Schemas

### 3.1. Server Challenge Envelope (`401 Multipass Challenge`)

When an unauthenticated request arrives, the server returns an HTTP `401 Unauthorized` status with `Content-Type: application/xhttp+json`:

```json
{
  "$schema": "https://json-schema.org/draft/2020-12/schema",
  "title": "MultipassChallengeEnvelope",
  "type": "object",
  "required": [
    "proto",
    "challenge_id",
    "nonce",
    "realm",
    "dns_authority",
    "required_scope",
    "issued_at",
    "expires_at"
  ],
  "properties": {
    "proto": { "type": "string", "const": "xhttp-401-challenge" },
    "challenge_id": { "type": "string" },
    "nonce": { "type": "string", "pattern": "^[0-9a-fA-F]{64}$" },
    "realm": { "type": "string" },
    "dns_authority": { "type": "string" },
    "required_scope": { "type": "string" },
    "issued_at": { "type": "string", "format": "date-time" },
    "expires_at": { "type": "string", "format": "date-time" }
  }
}
```

#### Example Challenge Response:
```http
HTTP/1.1 401 Unauthorized
Date: Fri, 23 Oct 2026 11:46:10 GMT
WWW-Authenticate: Multipass realm="mindrock-fleet", authority="hackt.com"
Content-Type: application/xhttp+json; charset=utf-8

{
  "proto": "xhttp-401-challenge",
  "challenge_id": "ch_7f8a9b2c3d4e5f60",
  "nonce": "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855",
  "realm": "mindrock-fleet",
  "dns_authority": "hackt.com",
  "required_scope": "compute:dispatch",
  "issued_at": "2026-10-23T11:46:10Z",
  "expires_at": "2026-10-23T11:47:10Z"
}
```

---

### 3.2. Client Presentation Envelope (`Multipass Credentials`)

The client encodes the following JSON object, canonicalizes it according to RFC 8785 (JCS), signs the resulting bytes with its Ed25519 private key, and supplies it as a Base64URL string in the `Authorization: Multipass <token>` header:

```json
{
  "$schema": "https://json-schema.org/draft/2020-12/schema",
  "title": "MultipassPresentationEnvelope",
  "type": "object",
  "required": [
    "proto",
    "challenge_id",
    "nonce_echo",
    "client_did",
    "godns_locator",
    "mandate_id",
    "timestamp",
    "signature"
  ],
  "properties": {
    "proto": { "type": "string", "const": "xhttp-401-multipass" },
    "challenge_id": { "type": "string" },
    "nonce_echo": { "type": "string" },
    "client_did": { "type": "string" },
    "godns_locator": { "type": "string" },
    "mandate_id": { "type": "string" },
    "timestamp": { "type": "string", "format": "date-time" },
    "signature": { "type": "string", "pattern": "^[0-9a-fA-F]{128}$" }
  }
}
```

#### Example Multipass Presentation Payload (Decoded):
```json
{
  "proto": "xhttp-401-multipass",
  "challenge_id": "ch_7f8a9b2c3d4e5f60",
  "nonce_echo": "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855",
  "client_did": "did:key:z6MknDcbzMCiFLLdxKQboCqVGPYZsJbwp27o37gYqBEqJ4NE#agy@wolfram",
  "godns_locator": "_mandate.agy.hackt.com",
  "mandate_id": "mandate_c847d10e9f1a",
  "timestamp": "2026-10-23T11:46:12Z",
  "signature": "f1b0988b4312237df5cf6049ecfd15f9294d8e05de823ebea6c4f93b8deea070966a15728bf9d606c4f089a0876b9d771f9f11e19c762e34f74e6aefff885006"
}
```

#### Example Request: Authenticated Multipass Request (Wire Example):
```http
POST /api/v1/compute/dispatch HTTP/1.1
Host: edge.mindrock.capital
Authorization: Multipass eyJwcm90byI6InhodHRwLTQwMS1tdWx0aXBhc3MiLCJjaGFsbGVuZ2VfaWQiOiJjaF83ZjhhOWIyYzNkNGU1ZjYwIiwibm9uY2VfZWNobyI6ImUzYjBjNDQyOThmYzFjMTQ5YWZiZjRjODk5NmZiOTI0MjdhZTQxZTQ2NDliOTM0Y2E0OTU5OTFiNzg1MmI4NTUiLCJjbGllbnRfZGlkIjoiZGlkOmtleTp6Nk1rbkRjYnpNQ2lGTExkeEtRYm9DcVZHUFlac0pid3AyN28zN2dZcUJFcUo0TkUjYWd5QHdvbGZyYW0iLCJnb2Ruc19sb2NhdG9yIjoiX21hbmRhdGUuYWd5LmhhY2t0LmNvbSIsIm1hbmRhdGVfaWQiOiJtYW5kYXRlX2M4NDdkMTBlOWYxYSIsInRpbWVzdGFtcCI6IjIwMjYtMTAtMjNUMTE6NDY6MTJaIiwic2lnbmF0dXJlIjoiZjFiMDk4OGI0MzEyMjM3ZGY1Y2Y2MDQ5ZWNmZDE1ZjkyOTRkOGUwNWRlODIzZWJlYTZjNGY5M2I4ZGVlYTA3MDk2NmExNTcyOGJmOWQ2MDZjNGYwODlhMDg3NmI5ZDc3MWY5ZjExZTE5Yzc2MmUzNGY3NGU2YWVmZmY4ODUwMDYifQ
Content-Type: application/json; charset=utf-8
Content-Length: 53

{
  "task": "proof-verification",
  "priority": "standard"
}
```

---

## 4. Decentralized DNS Trust Chain & Verification (`goDNS`)

To guarantee non-repudiation and resist DNS resolver poisoning, the verification pipeline anchors the agent's delegation certificate back to the organization's Principal Public Key.

```
[Apex Domain Anchor] ──► DNS TXT: _mandate-root.hackt.com ──► Principal Pubkey (p_root)
                                                                     │
[Agent Locator]      ──► DNS TXT: _mandate.agy.hackt.com     ──► Canonicalizes tags
                                                                     │ Validates sig with p_root
                                                                     ▼
[Challenge Digest]   ──► JCS(Challenge + Nonce)              ──► Validates PoP with p_agent
```

### 4.1. Principal Root Public Key Discovery
The edge server acquires the trusted Principal public key ($p_{root}$) using one of three deterministic methods:
1. **Apex DNS TXT Root Record**: The verifier queries the DNS TXT record at `_mandate-root.<dns_authority>` (e.g. `_mandate-root.hackt.com`), which contains:
   `v=mndroot1; k=ed25519; p=z6MkpTHR8VNsBxYAAWHut2Geadd9jSwuBV8xRoAnwWsdvKtH;`
2. **Canonical Well-Known Discovery**: The verifier fetches `https://<dns_authority>/.well-known/agent-keys.json` over TLS 1.3 with validated certificates.
3. **Local Pre-Configured Keystore**: Edge gateways operating in air-gapped or high-security mesh environments maintain pinned Principal public keys for authorized partner domains.

### 4.2. Mandate TXT Record Syntax & Encoding
The agent mandate record published at `godns_locator` (e.g. `_mandate.agy.hackt.com`) consists of semicolon-separated key-value tags:
```text
v=mnd1; k=ed25519; p=z6MknDcbzMCiFLLdxKQboCqVGPYZsJbwp27o37gYqBEqJ4NE; scope=compute:dispatch,models:read; exp=2026-12-31T23:59:59Z; sig=e8b7c4a1d2e3f405162738495a6b7c8d9e0f1a2b3c4d5e6f7a8b9c0d1e2f3a4b5c6d7e8f9a0b1c2d3e4f5a6b7c8d9e0f1a2b3c4d5e6f7a8b9c0d1e2f3a4b5c6d
```

Field specifications and encodings:
* `v`: Protocol format version (`mnd1`).
* `k`: Cryptographic algorithm (`ed25519`).
* `p`: Multicodec-encoded Ed25519 public key formatted in multibase `base58btc` with standard `z` prefix (matching W3C `did:key`).
* `scope`: Comma-separated list of authorized capabilities (e.g. `compute:dispatch,models:read`).
* `exp`: ISO 8601 UTC timestamp defining mandate expiration (e.g. `2026-12-31T23:59:59Z`).
* `sig`: 64-byte Ed25519 signature generated by the Principal private key, encoded as a 128-character lowercase hexadecimal string.

### 4.3. Canonical Signing Input Serialization
To eliminate parsing ambiguity across different DNS servers and runtimes, the signed payload of the TXT record is canonicalized as follows:
1. Extract all key-value tags from the TXT record, **strictly excluding** the `sig` tag.
2. Trim leading and trailing whitespace from each key and value.
3. Escape reserved characters in values using percent-encoding: `;` (`%3B`), `=` (`%3D`), and `%` (`%25`).
4. Sort the tags alphabetically by tag name in ascending ASCII order (`exp`, `k`, `p`, `scope`, `v`).
5. Format each pair as `key=value;` with no whitespace.
6. Concatenate the pairs in sorted order into a single UTF-8 string:
   $$\text{CanonicalTXT} = \text{"exp=2026-12-31T23:59:59Z;k=ed25519;p=z6MknDcbz...;scope=compute:dispatch,models:read;v=mnd1;"}$$
7. The byte stream to verify against `sig` using $p_{root}$ is the exact UTF-8 encoding of $\text{CanonicalTXT}$.

### 4.4. Complete Verification Algorithm
Upon receiving an HTTP request with `Authorization: Multipass <token>`:
1. **Decode Multipass**: Base64URL-decode `<token>` and parse as `MultipassPresentationEnvelope`.
2. **Replay & Lifetime Check**:
   - Verify that `challenge_id` matches an issued, unexpired challenge in the server's cache.
   - Verify that `nonce_echo` matches the challenge's random nonce.
   - Immediately mark the nonce as consumed in the server's atomic bloom filter / cache to prevent replay.
3. **DNS Query**: Query the DNS TXT record at `godns_locator` with DNSSEC validation enabled (`do=true`).
4. **Principal Signature Verification**:
   - Parse the TXT record tags.
   - Construct $\text{CanonicalTXT}$ as defined in Section 4.3.
   - Verify the `sig` tag using Principal root public key $p_{root}$. If signature verification fails, abort immediately with `HTTP 401 Unauthorized (error="invalid_principal_signature")`.
5. **Mandate Expiry Verification**:
   - Parse `exp` as an ISO 8601 UTC timestamp.
   - If $\text{CurrentTime} > \text{exp}$, reject with `HTTP 401 Unauthorized (error="mandate_expired")`.
6. **Scope Authorization Check**:
   - Parse `scope` tags into an array of allowed scopes.
   - Match `required_scope` from the challenge against allowed scopes. Sub-scopes are permitted if a prefix wildcard is present (e.g. `compute:*` authorizes `compute:dispatch`).
   - If the required scope is missing, reject with `HTTP 403 Forbidden (error="insufficient_scope")`.
7. **Proof of Possession (PoP)**:
   - Ensure the public key `p` extracted from the DNS record matches the multibase key inside `client_did`.
   - Reconstruct the Multipass presentation payload omitting `signature`.
   - Canonicalize the payload via RFC 8785 (JCS) and compute its SHA-256 digest.
   - Verify `signature` against the digest using the agent's public key `p`.
   - If valid, bind the request context to `client_did` and allow processing.

---

## 5. Security Considerations

* **Replay Attacks**: Every challenge incorporates a high-entropy 256-bit cryptographically secure pseudorandom nonce with a strict validity window (typically 60 seconds). Servers maintain an in-memory or Redis-backed ephemeral bloom filter / cache to ensure every nonce is consumed exactly once.
* **DNS Spoofing & Cache Poisoning**: Attackers could forge DNS responses to insert malicious public keys. Consequently:
  - Production implementations **MUST enforce DNSSEC** validation on all `goDNS` lookups.
  - Edge gateways may query DNS-over-HTTPS (DoH) endpoints (e.g. Cloudflare `1.1.1.1` or Google `8.8.8.8`) with `do=true` (DNSSEC OK flag).
* **Principal Mandate Signature**: The DNS TXT record itself includes an Ed25519 signature from the root organization Principal (`sig=...`), guaranteeing that compromised DNS management credentials cannot bind unauthorized agents without the root offline master key.
* **Side-Channel & Timing Attacks**: Cryptographic signature validation and nonce comparisons must execute in constant time (`crypto_verify64`).

---

## 6. Interoperability Notes & Limitations

* **DNS Propagation Latency**: Updates to DNS TXT records are subject to DNS TTL caching (recommended TTL: 300 seconds). Newly enrolled agents may experience a propagation delay of up to 5 minutes across global resolvers before initial authentication succeeds.
* **DNS UDP MTU Limits**: DNS TXT strings exceeding 255 bytes must be formatted as concatenated strings according to RFC 1035 section 3.3.14. Resolvers must support EDNS0 (RFC 6891) with a minimum buffer size of 1220 bytes.
* **HTTP Header Size Constraints**: When transmitted as a header, the Base64URL-encoded Multipass token consumes approximately 350-450 bytes, well within standard web server header boundaries (8 KB).
