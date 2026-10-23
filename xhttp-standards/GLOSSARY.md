# xHTTP Unified Glossary & Specification Versioning

**Status:** Canonical Reference Document  
**Version:** 0.1.0-draft (2026-10-23)  
**Authority:** Principal Pavel Cherkashin (`pavel@mindrock.capital`)  
**Scope:** Universal terminology and versioning rules across all xHTTP standards.

---

## 1. Unified Terminology & Definitions

### 1.1. Identity & Cryptography
* **Agent DID (`did:key`)**: A decentralized identifier format based on the W3C DID Core specification, representing an autonomous software entity by prefixing its multicodec-encoded Ed25519 public key (e.g. `did:key:z6MknDcbzMCiFLLdxKQboCqVGPYZsJbwp27o37gYqBEqJ4NE#agy@wolfram`).
* **Proof of Possession (PoP)**: A cryptographic assertion demonstrating that the entity presenting an identity possesses the corresponding private key without revealing the secret key itself. In xHTTP, PoP is provided by signing a server-issued challenge nonce.
* **goDNS / Mandate TXT Record**: A decentralized trust anchor leveraging DNS `TXT` records under the naming convention `_mandate.<agent-identifier>.<root-domain>`. Contains the public key, authorization scope, expiration timestamp, and the Principal's Ed25519 signature.
* **Multipass**: A cryptographically verifiable authentication token containing the agent's DID, challenge nonce echo, timestamp, and Ed25519 signature, transmitted in the `Authorization: Multipass <token>` HTTP header.
* **Canonical JSON Digest**: The SHA-256 hash computed over an RFC 8785 (JSON Canonicalization Scheme - JCS) normalized representation of a payload, ensuring deterministic signatures across disparate language runtimes.
* **Principal Root Anchor**: The apex domain DNS TXT record (`_mandate-root.<root-domain>`) or pinned keystore holding the Principal authority's master Ed25519 public key, establishing the root of trust for all delegated agent mandates.

### 1.2. Generative Edge & Synthesis
* **Generative 404 (`x404`)**: An enhanced HTTP status code and response envelope indicating that while the target static artifact does not exist on disk or cache, the edge compute layer possesses the prompt, context, or generative pipeline to synthesize it on demand.
* **Semantic Hash (`SemHash`)**: A deterministic 64-character hexadecimal digest derived from the normalized semantic intent, schema requirements, and prompt parameters of a request. Used to detect semantic duplicates and route identical synthesis requests to shared edge cache buckets.
* **Synthesis Work Order**: A structured JSON envelope detailing the parameters of an edge generation task, including model family, maximum token budget, deadline, constraints, and verification criteria.
* **Verification Oracle**: An automated validation harness (such as a unit test suite, schema validator, or cryptographic checker) specified within an `x404` envelope to independently certify the correctness of synthesized output prior to client delivery.
* **Semantic Normalization Pipeline**: The sequential 5-phase normalization process (path canonicalization, volatile parameter exclusion, Unicode NFC normalization, media-type normalization, and domain-separated JCS serialization) ensuring that semantically identical requests yield matching SemHash digests.

### 1.3. Revocation & Provenance
* **Cryptographic Revocation Certificate (`x410`)**: An immutable, signed JSON object attesting that a specific URI or content digest was deliberately and permanently expunged by its authorized owner.
* **Tombstone**: A persistent record stored in edge caches or indexers that responds to subsequent requests for a revoked resource, carrying the revocation certificate and reason code.
* **Active Fast-Path SLA**: The time window defined by `retain_tombstone_days` during which an edge node guarantees immediate tombstone response before delegating to the permanent audit archive.
* **Revocation Reason Code**: A standardized enumerator indicating the cause of removal:
  * `COMPROMISED`: Key compromise or critical security defect.
  * `MANDATE_EXPIRED`: Authority window elapsed.
  * `LEGAL_TAKEDOWN`: Compliance, copyright, or regulatory obligation.
  * `SUPERSEDED`: Resource replaced by a newer canonical version (includes successor URI).
  * `DECOMMISSIONED`: Planned end-of-life retirement.
* **Non-Repudiation**: The mathematical inability of the issuing authority to deny having generated and signed the revocation certificate.

### 1.4. Backpressure & Concurrency Control
* **Patience Protocol (`x420`)**: A real-time backpressure signaling standard used when an edge node or compute slot is executing at capacity (`WIP=1`) and cannot accept new jobs immediately.
* **Queue Correlation ID (`queue_id`)**: A unique identifier linking an in-flight backpressured compute request to subsequent action selection negotiations.
* **Action Selection Envelope (`xhttp-420-action`)**: The standardized client request payload submitted to `negotiation_endpoint` to choose a recovery branch (`WAIT`, `PAY`, `DELEGATE`, `OUR_LADY`, `FSCK_OFF`).
* **ETA Window & Drift Tolerance**: The server's estimated completion time for current in-flight operations, accompanied by a maximum expected drift margin in milliseconds.
* **Backpressure Action Codes**: Standardized recovery pathways offered in an `x420` envelope:
  * `WAIT`: Instructs the client to pause and retry after the specified ETA window.
  * `PAY`: Permits the client to bypass the queue or enter a high-priority lane via cryptographic compute token or micropayment deposit.
  * `DELEGATE`: Directs the client to alternate federated worker nodes that currently possess free capacity.
  * `OUR_LADY`: Invokes an emergency circuit breaker that returns a lower-fidelity, cached, or heuristic response rather than waiting for primary synthesis.
  * `FSCK_OFF` (*File System Check Off*): Instructs the client to terminate requests immediately and trigger an automated clean-room reset or cache purge because the server node's state is corrupted or under catastrophic overload.

### 1.5. Interoperability & Transport
* **Normative Fallback Profile**: The backward-compatible transport mechanism whereby an edge server emits standard HTTP 429 or 503 status with `X-xHTTP-Profile: patience-v0.1` and `Content-Type: application/xhttp+json` to navigate legacy HTTP intermediaries.
* **`/.well-known/xhttp`**: The canonical metadata discovery endpoint defined under RFC 8615, allowing clients to query server capabilities, supported status codes, cryptographic suites, and queue telemetry.
* **Protocol Phi (`Φ-0.1`)**: The underlying multi-agent task handoff and messaging protocol operating across Git coordinates, Linear comments, and xHTTP edge gateways.

---

## 2. Specification Versioning Policy

xHTTP follows **Semantic Versioning 2.0.0 (SemVer)** with strict draft lifecycle states:

```
[EXPERIMENTAL] ──► [DRAFT (v0.1.x)] ──► [CANDIDATE (v1.0.0-rc)] ──► [STANDARD (v1.0.0)]
```

### 2.1. Version Format: `MAJOR.MINOR.PATCH-STAGE`
* **`MAJOR` (e.g. `1.x.x`)**: Incremented when backward-incompatible envelope schema changes, signature algorithm deprecations, or breaking protocol flow shifts occur.
* **`MINOR` (e.g. `0.2.x`)**: Incremented when backward-compatible protocol capabilities, new action codes, or new discovery fields are introduced.
* **`PATCH` (e.g. `0.1.1`)**: Incremented for editorial clarifications, non-breaking schema corrections, and security guidance updates.
* **`STAGE`**: Pre-release tags: `-draft`, `-rc1`, `-rc2`.

### 2.2. Deprecation & Migration Policy
* Any field marked for deprecation must remain supported for a minimum of two minor releases or six months.
* Servers must continue to accept older minor versions unless a critical cryptographic vulnerability requires immediate revocation.

---

## 3. Specification Changelog

### Version `0.1.0-draft` (2026-10-23)
* **Initial Public Working Draft Release**: Formulated under Mindrock Architectural Directive 2026-10-20.
* **`specs/x401-multipass-auth.md`**:
  * Formalized `401 Multipass Challenge` envelope schema.
  * Defined DNS TXT `goDNS` record syntax and Ed25519 proof-of-possession handshake.
  * Integrated replay protection with monotonic timestamps and nonce expiration windows.
* **`specs/x404-generative.md`**:
  * Established `404 Generative Work Order` envelope schema.
  * Defined Semantic Hash (`SemHash`) algorithm for cross-agent cache deduplication.
  * Added verification oracle constraints and token budget fields.
* **`specs/x410-revocation.md`**:
  * Defined immutable Ed25519-signed revocation tombstone envelope.
  * Established 5 standardized revocation reason codes.
  * Specified client verification algorithm and non-repudiation guarantees.
* **`specs/x420-patience.md`**:
  * Formalized `420 Patience Protocol` backpressure envelope.
  * Defined queue telemetry, ETA window calculations, and jitter bounds.
  * Specified deterministic action codes: `WAIT`, `PAY`, `DELEGATE`, `OUR_LADY`, and `FSCK_OFF` (*File System Check Off*).
* **`WELL-KNOWN.md`**:
  * Defined discovery endpoint `/.well-known/xhttp` schema, caching headers, and rate limits.
* **`ANNOUNCEMENT.md` & `README.md`**:
  * Authored developer-focused announcement and repository index.
