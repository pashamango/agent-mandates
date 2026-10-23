# Announcing xHTTP: The Open Standard Suite for the Autonomous Agent Web

**Publication Date:** October 23, 2026  
**Status:** Public RFC Draft (v0.1.0-draft)  
**Authors:** Mindrock Autonomous Fleet Engineering (`agy@mindrock.capital`, `codex@mindrock.capital`)  
**Authority:** Principal Pavel Cherkashin (`pavel@mindrock.capital`)  
**Reference Codebase:** [`https://github.com/pashamango/agent-mandates`](https://github.com/pashamango/agent-mandates)  

---

## The Paradigm Shift: HTTP for Machines, Not Just Browsers

When HTTP was drafted in the early 1990s, the client was an interactive human operating a desktop browser. If authentication failed, a login screen was presented. If a page was missing, an untyped HTML page informed the user to look elsewhere. If a server was overloaded, the browser stalled or showed a cryptic connection timeout.

Thirty-five years later, the primary consumers and producers of network traffic are rapidly becoming **autonomous software agents**, **inference pipelines**, and **edge compute nodes**. These systems do not navigate with mice or read visual HTML error pages. They require:
1. **Cryptographic Proofs of Identity** that operate peer-to-peer across administrative domains without central Identity Providers (IdPs).
2. **Generative Resolution** for missing resources, transforming traditional `404 Not Found` dead-ends into verifiable on-demand synthesis pipelines.
3. **Cryptographic Non-Repudiation** for deleted content, replacing unauthenticated `410 Gone` responses with immutable, signed revocation tombstones.
4. **Structured Backpressure Negotiation**, upgrading crude `429 Too Many Requests` responses with dynamic queue metrics, ETA windows, and multi-path recovery commands (`WAIT`, `PAY`, `DELEGATE`, `OUR_LADY`, and `FSCK_OFF`).

Today, we are publishing the first four open working draft specifications of the **xHTTP Open Standard Suite (v0.1.0-draft)**.

---

## The Four Pillars of xHTTP

### 1. `x401`: Multipass Auth Challenge (`specs/x401-multipass-auth.md`)
Traditional HTTP authentication forces agents into OAuth2 consent loops or brittle bearer tokens tied to corporate servers. **`x401`** introduces a decentralized, challenge-response authentication handshake:
* **Decentralized Trust via goDNS:** Agent public keys and capability mandates are anchored directly in DNS TXT records (`_mandate.<agent>.<domain>`), leveraging the resilient global DNS infrastructure.
* **Proof of Possession (PoP):** When challenging an agent, the server returns an `x401` challenge envelope containing a cryptographic nonce and realm. The agent responds with a canonical Ed25519 signature over the challenge digest.
* **Zero IdP Lock-in:** Any agent domain with DNS control can authenticate to any edge node in under 5 milliseconds with zero external API calls.

### 2. `x404`: Generative 404 & Semantic Hashing (`specs/x404-generative.md`)
On the legacy web, a 404 error is an unrecoverable failure. In an agentic architecture, many missing resources can be generated deterministically on-demand:
* **Semantic Hash (`SemHash`):** Deduplicates identical or equivalent generation requests across edge caches, avoiding redundant inference cost.
* **Work Order Envelope:** When a resource is absent, the edge issues a structured work order containing semantic context, token budgets, execution policy, and verification oracle constraints.
* **On-Demand Synthesis:** Allows client agents to trigger synchronous edge generation or receive verifiable synthetic alternatives with mathematical quality guarantees.

### 3. `x410`: Revocation with Certificate (`specs/x410-revocation.md`)
How does an agent know that a deleted resource was genuinely revoked by its rightful owner rather than censored by an intermediate proxy?
* **Signed Revocation Tombstones:** Every `x410` response returns an immutable certificate signed by the resource authority's Ed25519 private key.
* **Provenance & Reason Codes:** The certificate records the SHA-256 hash of the revoked content, timestamp, expiration, and standardized reason code (`COMPROMISED`, `MANDATE_EXPIRED`, `LEGAL_TAKEDOWN`, `SUPERSEDED`, or `DECOMMISSIONED`).
* **Non-Repudiation:** Once a revocation certificate is issued, downstream nodes and auditors possess permanent mathematical proof of revocation.

### 4. `x420`: Patience Protocol (`specs/x420-patience.md`)
Modern high-performance agent nodes execute with strict concurrency guarantees (e.g. `WIP=1` per specialized slot). Under peak load, existing `429` responses with `Retry-After` force blind, wasteful polling loops.
* **Queue Visibility:** The `x420` envelope exposes current queue depth, estimated completion time (ETA), and jitter bounds.
* **Actionable Negotiation Paths:** Instead of a binary drop, the client is offered five deterministic recovery branches:
  * `WAIT`: Coordinated backoff based on server ETA.
  * `PAY`: Priority lane escalation via compute token or micropayment deposit.
  * `DELEGATE`: Peer-to-peer offloading to an alternate federated edge node.
  * `OUR_LADY`: Circuit-breaker fallback to lower-fidelity cached models.
  * `FSCK_OFF` (*File System Check Off*): Deterministic node self-quarantine and queue purge when local state integrity fails.

---

## Global Discovery: `/.well-known/xhttp`

To prevent speculative probing and protocol mismatch, participating edge nodes publish their capabilities at `/.well-known/xhttp`. This standardized manifest declares supported protocol versions, public key registries, rate limiting policies, and cryptographic algorithms.

---

## Who This Is For

* **Agent Systems Engineers:** Developers building autonomous multi-agent networks, autonomous coding loops, and federated agent meshes who need deterministic error handling and zero-trust handoffs.
* **Edge & Cloud Infrastructure Teams:** Teams operating Cloudflare Workers, Fastly Compute, Envoy, or NGINX edge instances seeking to serve generative models with structured backpressure.
* **Partner Founders & Cryptographic Protocols:** Innovators building next-generation Web3, AI, and sovereign computing stacks who recognize that network protocols must evolve for autonomous intelligence.

---

## How to Get Involved

The specifications are hosted publicly in the `agent-mandates` repository under the permissive MIT License:
* **Repository:** [`https://github.com/pashamango/agent-mandates`](https://github.com/pashamango/agent-mandates)
* **Specifications Folder:** [`xhttp-standards/specs/`](file:///Users/pavelcherkashin/ChitAkasha-Wolfram/worktrees/MIN-148-XHTTP-SPECS-0001/xhttp-standards/specs/)
* **RFC Feedback:** We welcome pull requests, security audits, and issue debates on GitHub and GitLab.
