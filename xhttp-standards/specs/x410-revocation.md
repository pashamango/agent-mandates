# xHTTP-410: Revocation with Cryptographic Certificate (v0.1.0-draft)

**Status:** Proposed Standard Working Draft  
**Version:** 0.1.0-draft (2026-10-23)  
**Authority:** Principal Pavel Cherkashin (`pavel@mindrock.capital`)  
**Standard Identifier:** `RFC-xHTTP-410`  
**Dependencies:** RFC 8785 (JCS), RFC 7807 (Problem Details), RFC 8615  

---

## 1. Abstract & Problem Statement

In standard HTTP semantics, status code `410 Gone` indicates that access to the target resource is no longer available at the origin server and that this condition is likely to be permanent. 

However, standard HTTP 410 provides zero cryptographic guarantees:
1. **No Authenticity**: A rogue intermediate proxy, malicious CDN edge, or compromised DNS resolver can fabricate a `410 Gone` response to censor a valid resource.
2. **No Audit Trail or Reason Code**: Consumers cannot determine whether a resource was expunged due to a key compromise, legal mandate, scheduled deprecation, or human error.
3. **No Mathematical Non-Repudiation**: The publisher has no way to prove to third parties that a resource was intentionally withdrawn at a specific point in time.

**xHTTP-410** defines a formal, cryptographically signed **Revocation Certificate Envelope** returned whenever a resource has been permanently expunged. The certificate carries the SHA-256 digest of the original resource, a standardized reason code, an ISO 8601 timestamp, a successor URI (if applicable), and an Ed25519 signature from the authorized resource owner.

---

## 2. Protocol Sequence Diagram

```
Auditor / Client Agent                                   Edge Server / Origin
      │                                                           │
      │ 1. GET /mandates/agent-v1-compromised.json HTTP/1.1       │
      │    Host: edge.mindrock.capital                            │
      ├──────────────────────────────────────────────────────────►│
      │                                                           │
      │                                                           │ (Evaluates resource:
      │                                                           │  Permanently revoked.
      │                                                           │  Loads signed tombstone)
      │ 2. HTTP/1.1 410 Gone                                      │
      │    Content-Type: application/xhttp+json                   │
      │    X-Revocation-Id: rev_c4a8d9e2b10f                      │
      │    X-Revocation-Reason: COMPROMISED                       │
      │                                                           │
      │    <410 Revocation Certificate Envelope>                  │
      │    { "resource_uri", "content_hash", "reason", "sig" }    │
      │◄──────────────────────────────────────────────────────────┤
      │                                                           │
      │ (Client extracts certificate:                             │
      │  1. Verifies issuer_did against goDNS / DNS TXT           │
      │  2. Canonicalizes envelope via JCS RFC 8785               │
      │  3. Validates Ed25519 signature                           │
      │  4. Enters permanent tombstone in local agent cache)      │
      │                                                           │
```

---

## 3. Envelope Specifications & Schemas

### 3.1. JSON Schema for Revocation Certificate (`410 Revocation Envelope`)

```json
{
  "$schema": "https://json-schema.org/draft/2020-12/schema",
  "title": "RevocationCertificateEnvelope",
  "type": "object",
  "required": [
    "proto",
    "status_code",
    "revocation_id",
    "resource_uri",
    "original_content_sha256",
    "revocation_reason",
    "revocation_timestamp",
    "issuer_did",
    "godns_anchor",
    "tombstone_policy",
    "signature"
  ],
  "properties": {
    "proto": { "type": "string", "const": "xhttp-410-revocation" },
    "status_code": { "type": "integer", "const": 410 },
    "revocation_id": { "type": "string" },
    "resource_uri": { "type": "string", "format": "uri-reference" },
    "original_content_sha256": { "type": "string", "pattern": "^[0-9a-fA-F]{64}$" },
    "revocation_reason": {
      "type": "string",
      "enum": ["COMPROMISED", "MANDATE_EXPIRED", "LEGAL_TAKEDOWN", "SUPERSEDED", "DECOMMISSIONED"]
    },
    "successor_uri": { "type": "string", "format": "uri-reference" },
    "revocation_timestamp": { "type": "string", "format": "date-time" },
    "issuer_did": { "type": "string" },
    "godns_anchor": { "type": "string" },
    "tombstone_policy": {
      "type": "object",
      "required": ["retain_tombstone_days", "notify_subscribers"],
      "properties": {
        "retain_tombstone_days": { "type": "integer", "minimum": 1 },
        "notify_subscribers": { "type": "boolean" },
        "public_audit_log_url": { "type": "string", "format": "uri" }
      }
    },
    "signature": { "type": "string", "pattern": "^[0-9a-fA-F]{128}$" }
  }
}
```

### 3.2. Example Response: 410 Revocation Certificate

```http
HTTP/1.1 410 Gone
Date: Fri, 23 Oct 2026 11:48:00 GMT
Content-Type: application/xhttp+json; charset=utf-8
X-Revocation-Id: rev_c4a8d9e2b10f
X-Revocation-Reason: COMPROMISED
Cache-Control: public, max-age=31536000, immutable

{
  "proto": "xhttp-410-revocation",
  "status_code": 410,
  "revocation_id": "rev_c4a8d9e2b10f",
  "resource_uri": "/mandates/agent-v1-compromised.json",
  "original_content_sha256": "8a3e9c1b2f4d6e8a0b1c2d3e4f5a6b7c8d9e0f1a2b3c4d5e6f7a8b9c0d1e2f3a",
  "revocation_reason": "COMPROMISED",
  "successor_uri": "/mandates/agent-v2-active.json",
  "revocation_timestamp": "2026-10-23T11:47:45Z",
  "issuer_did": "did:key:z6MknDcbzMCiFLLdxKQboCqVGPYZsJbwp27o37gYqBEqJ4NE#agy@wolfram",
  "godns_anchor": "_mandate.agy.hackt.com",
  "tombstone_policy": {
    "retain_tombstone_days": 365,
    "notify_subscribers": true,
    "public_audit_log_url": "https://hackt.com/audit/revocations/rev_c4a8d9e2b10f"
  },
  "signature": "3e9b7a1c8d5f2a4e6b8c0d2e4f6a8b0c2d4e6f8a0b2c4d6e8f0a2b4c6d8e0f2a4b6c8d0e2f4a6b8c0d2e4f6a8b0c2d4e6f8a0b2c4d6e8f0a2b4c6d8e0f2a4b6c"
}
```

---

## 4. Verification Algorithm for Client Agents

Upon receiving an HTTP 410 response with an `xhttp-410-revocation` body, the client agent executes the following verification procedure before purging local caches:

1. **Extract Issuer Identity**: Parse `issuer_did` and `godns_anchor` from the certificate.
2. **Anchor Validation**: Perform a secure DNS lookup for `godns_anchor` (e.g. `_mandate.agy.hackt.com`). Validate DNSSEC signatures. Confirm that the public key matches `issuer_did`.
3. **Digest Construction**:
   - Construct a copy of the revocation envelope omitting the `signature` field.
   - Canonicalize the copy using RFC 8785 (JCS).
   - Compute $\text{Digest} = \text{SHA256}(\text{CanonicalJSON})$.
4. **Signature Verification**: Verify the 64-byte Ed25519 `signature` using the issuer's public key against $\text{Digest}$.
5. **Tombstone Commitment**:
   - If valid, mark the resource URI as **Permanently Expunged** in the client's internal database.
   - Store the signed certificate for audit compliance.
   - If `successor_uri` is present, update pointers to query the successor on subsequent operations.
   - If invalid, flag the response as an untrusted proxy anomaly and alert the security governor.

---

## 5. Security Considerations

* **Denial of Service via Forged Revocation**: Adversaries might attempt to inject fake 410 responses to convince agents that critical APIs or assets are gone. Strict cryptographic verification against DNSSEC-anchored public keys guarantees that only the authorized owner can successfully revoke a resource.
* **Key Compromise Recovery**: If the signing key itself is compromised, the revocation certificate must be signed by the **Principal master authority** (anchored in `_mandate.<root-domain>`), revoking both the resource and the delegated agent key simultaneously.
* **Immutable Caching Headers**: Servers must emit long-lived cache headers (`Cache-Control: public, max-age=31536000, immutable`). Because the resource is permanently gone, edge CDNs can cache the tombstone indefinitely without re-querying the origin.

---

## 6. Interoperability Notes & Limitations

* **Permanent Storage Overhead**: Storing tombstones indefinitely can induce storage bloat for systems with millions of ephemeral items. The `retain_tombstone_days` field allows operators to specify an SLA (e.g. 365 days), after which the server may purge the tombstone and return a generic 404.
* **Search Engine Crawlers**: Search crawlers (e.g. Googlebot) respect standard HTTP 410 status codes to de-index URLs from web search indices. The presence of the JSON envelope does not impede standard crawler de-indexing behavior.
* **Partial Revocations**: xHTTP-410 does not support temporary suspension; temporary downtime must be signaled via standard `503 Service Unavailable` or `xHTTP-420 Patience`.
