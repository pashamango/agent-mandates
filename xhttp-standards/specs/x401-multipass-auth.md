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

#### Example Challenge Payload:
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

The client encodes the following JSON object, canonicalizes it according to RFC 8785 (JCS), signs the resulting bytes with its Ed25519 private key, and supplies it either as a Base64URL string in the `Authorization: Multipass <token>` header or as a JSON body:

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

#### Example Multipass Presentation:
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

---

## 4. Decentralized DNS Verification (`goDNS`)

The receiving edge server verifies the client's assertion through a 3-step offline/DNS pipeline:

```
[Extract godns_locator] ──► DNS TXT Query: _mandate.agy.hackt.com
                                   │
                                   ▼
             Parse TXT: "v=mnd1; k=ed25519; p=z6MknDcbz...; exp=2026-12-31;"
                                   │
           ┌───────────────────────┴───────────────────────┐
           ▼                                               ▼
Match client_did with p=                       Verify Multipass signature
(Public Key Anchor Confirmed)                  (Proof of Possession Validated)
```

1. **DNS Query**: The server performs a DNS query for `TXT` records at `godns_locator` (e.g. `_mandate.agy.hackt.com`).
2. **Mandate Parsing**: The record must conform to the format:
   `v=mnd1; k=ed25519; p=<multicodec-pubkey>; scope=<authorized-scopes>; exp=<iso8601>; sig=<principal-sig>`
3. **Signature Verification**:
   - The server verifies that `p` matches the public key embedded in `client_did`.
   - The server verifies `signature` over the canonical JCS serialization of the Multipass payload (excluding `signature`) using the public key `p`.
   - The server verifies that the challenge `nonce` matches the server's cache and has not expired or been reused.

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
