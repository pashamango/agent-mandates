# xHTTP Open Standard Suite (v0.1.0-draft)

**Status:** Public Working Draft  
**Version:** 0.1.0-draft (2026-10-23)  
**Authority:** Principal Pavel Cherkashin (`pavel@mindrock.capital`)  
**Originating Directive:** Mindrock Architectural Directive 2026-10-20  
**Target Repository:** `agent-mandates` / `xhttp-standards`  
**License:** MIT License  

---

## 1. Executive Summary & Vision

The modern World Wide Web was engineered around human attention spans, document retrieval, and centralized authentication authorities. Standard HTTP status codes (such as `401 Unauthorized`, `404 Not Found`, `410 Gone`, and `429 Too Many Requests`) reflect this synchronous, browser-centric design:
* They assume interactive humans can authenticate via interactive login redirects, cookies, or OAuth2 consent screens.
* They treat a missing resource as a terminal dead-end (`404`), oblivious to edge generative intelligence capable of on-demand synthesis.
* They signal deletion (`410`) as an untrusted statement without non-repudiation proofs, vulnerability to adversarial proxy spoofing, or auditable lineage.
* They signal capacity exhaustion (`429`) with rudimentary `Retry-After` integer headers that fail to convey queue position, cost escalation, delegation paths, or graceful backpressure recovery.

**xHTTP** is an open protocol extension family engineered from first principles for the **Autonomous Agent Web** and **Generative Edge Computing**. It defines standardized, cryptographically signed JSON envelopes, decentralized domain-anchored identities via DNS (`goDNS`), generative fallback workflows, and backpressure negotiation mechanisms.

```
                      ┌─────────────────────────────────┐
                      │    Autonomous Agent / Client    │
                      └──────────────┬──────────────────┘
                                     │
           ┌─────────────────────────┼─────────────────────────┐
           │ x401                    │ x404                    │ x410
           ▼                         ▼                         ▼
┌─────────────────────┐   ┌─────────────────────┐   ┌─────────────────────┐
│ Multipass Challenge │   │ Generative Order    │   │ Revocation Cert     │
│ Proof of Possession │   │ Semantic Hash Dedupe│   │ Cryptographic Proof │
│ DNS TXT Anchored    │   │ On-Demand Synthesis │   │ Immutable Tombstone │
└─────────────────────┘   └─────────────────────┘   └─────────────────────┘
           │                         │                         │
           └─────────────────────────┼─────────────────────────┘
                                     │ x420
                                     ▼
                          ┌─────────────────────┐
                          │  Patience Protocol  │
                          │ Backpressure Queue  │
                          │ WAIT/PAY/DELEGATE/  │
                          │ OUR_LADY/FSCK_OFF   │
                          └─────────────────────┘
```

---

## 2. Protocol Specifications Matrix

The xHTTP suite comprises four core specification modules and a global discovery framework:

| Specification | Status | RFC File | Core Mechanism |
| :--- | :---: | :--- | :--- |
| **`x401`** | `DRAFT` | [`specs/x401-multipass-auth.md`](file:///Users/pavelcherkashin/ChitAkasha-Wolfram/worktrees/MIN-148-XHTTP-SPECS-0001/xhttp-standards/specs/x401-multipass-auth.md) | Multipass Auth Challenge: Nonce-based challenge envelope, decentralized agent identity presentation (`did:key`), and proof-of-possession verification via DNS TXT records (`goDNS`). |
| **`x404`** | `DRAFT` | [`specs/x404-generative.md`](file:///Users/pavelcherkashin/ChitAkasha-Wolfram/worktrees/MIN-148-XHTTP-SPECS-0001/xhttp-standards/specs/x404-generative.md) | Generative 404: Edge-first generative error envelope with Semantic Hash (`SemHash`) deduplication, synthesis constraints, token budgets, and verification oracles. |
| **`x410`** | `DRAFT` | [`specs/x410-revocation.md`](file:///Users/pavelcherkashin/ChitAkasha-Wolfram/worktrees/MIN-148-XHTTP-SPECS-0001/xhttp-standards/specs/x410-revocation.md) | Revocation with Certificate: Immutable Ed25519-signed revocation tombstones, reason codes, content hashes, and non-repudiation verification. |
| **`x420`** | `DRAFT` | [`specs/x420-patience.md`](file:///Users/pavelcherkashin/ChitAkasha-Wolfram/worktrees/MIN-148-XHTTP-SPECS-0001/xhttp-standards/specs/x420-patience.md) | Patience Protocol: Dynamic agent backpressure envelope providing queue position, ETA window, and recovery action branches (`WAIT`, `PAY`, `DELEGATE`, `OUR_LADY`, `FSCK_OFF`). |
| **`/.well-known`** | `DRAFT` | [`WELL-KNOWN.md`](file:///Users/pavelcherkashin/ChitAkasha-Wolfram/worktrees/MIN-148-XHTTP-SPECS-0001/xhttp-standards/WELL-KNOWN.md) | Canonical Edge Discovery: `/.well-known/xhttp` capabilities profile, protocol negotiation, supported cryptography, and edge runtime endpoints. |

---

## 3. Directory Layout

```
xhttp-standards/
├── README.md                 # This root document & architectural index
├── ANNOUNCEMENT.md           # Public announcement & portfolio presentation
├── GLOSSARY.md               # Unified glossary of terms & SemVer changelog
├── WELL-KNOWN.md             # Specification for /.well-known/xhttp endpoint
└── specs/
    ├── x401-multipass-auth.md # Specification: Multipass Auth Challenge
    ├── x404-generative.md     # Specification: Generative 404 & Semantic Hash
    ├── x410-revocation.md     # Specification: Revocation with Certificate
    └── x420-patience.md       # Specification: Patience Protocol Backpressure
```

---

## 4. Key Architectural Tenets

1. **Self-Describing JSON Envelopes**: All xHTTP protocols exchange machine-readable, schema-valid JSON payloads encapsulated within HTTP response bodies or RFC 7807 problem details.
2. **Zero Central Trust Anchor**: Identities are anchored in existing DNS infrastructure (`goDNS` / DKIM-for-agents), requiring no proprietary PKI or centralized OAuth2 provider.
3. **Fail-Closed Security**: Missing signatures, expired nonces, invalid semantic hashes, or unverified mandate records trigger deterministic protocol rejections.
4. **Deterministic Agent Decisions**: Instead of unstructured error messages, agents receive actionable choice trees with discrete recovery parameters.
5. **Backwards HTTP Compatibility**: Every xHTTP response degrades gracefully into standard HTTP/1.1 and HTTP/2 clients while conveying rich operational envelopes to xHTTP-aware agents.

---

## 5. Conformance & Implementation Gates

Implementations of xHTTP servers, proxies, and agent clients must satisfy four acceptance criteria:

* **Gate 1 (Envelopes & Flow):** Complete JSON schema definitions, valid example payloads, and deterministic ASCII message sequence charts.
* **Gate 2 (Security & Interop):** Dedicated threat models, replay prevention, side-channel mitigations, and honest interoperability constraints.
* **Gate 3 (Semantics & Versioning):** Adherence to the unified vocabulary in [`GLOSSARY.md`](file:///Users/pavelcherkashin/ChitAkasha-Wolfram/worktrees/MIN-148-XHTTP-SPECS-0001/xhttp-standards/GLOSSARY.md) and SemVer 2.0.0.
* **Gate 4 (Public Readiness):** Clear developer documentation, test suites with zero parsing failures, and open-source contribution guidelines.
