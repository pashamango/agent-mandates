# xHTTP Edge Discovery: `/.well-known/xhttp` (v0.1.0-draft)

**Status:** Canonical Discovery Standard  
**Version:** 0.1.0-draft (2026-10-23)  
**Authority:** Principal Pavel Cherkashin (`pavel@mindrock.capital`)  
**Specification Type:** Well-Known URI Protocol Handshake (RFC 8615)  

---

## 1. Abstract & Motivation

As autonomous agents traverse the decentralized edge web, speculative probing of uncoordinated endpoints induces unnecessary latency, network bandwidth consumption, and error storms. 

The `/.well-known/xhttp` endpoint provides a deterministic, lightweight, cacheable discovery interface that allows client agents to inspect an edge node's supported xHTTP protocol extensions, active cryptographic cipher suites, identity verification anchors, capacity limits, and operational status prior to dispatching stateful or compute-heavy requests.

---

## 2. Protocol Discovery Flow

```
Agent Client                                           Edge Gateway
    │                                                        │
    │  GET /.well-known/xhttp HTTP/1.1                      │
    │  Host: edge.mindrock.capital                           │
    │  Accept: application/json, application/xhttp+json     │
    ├───────────────────────────────────────────────────────►│
    │                                                        │ (Evaluate edge health,
    │                                                        │  load, and capabilities)
    │  HTTP/1.1 200 OK                                       │
    │  Content-Type: application/xhttp+json                  │
    │  ETag: "w/38f9a2b1c4e7"                                │
    │  Cache-Control: public, max-age=60, s-maxage=300       │
    │                                                        │
    │  <xHTTP Capability Envelope>                           │
    │◄───────────────────────────────────────────────────────┤
    │                                                        │
```

---

## 3. Envelope Specification & Schema

### 3.1. JSON Schema (draft 2020-12)

```json
{
  "$schema": "https://json-schema.org/draft/2020-12/schema",
  "title": "xHTTPDiscoveryEnvelope",
  "type": "object",
  "required": [
    "proto",
    "version",
    "node_id",
    "supported_extensions",
    "cryptography",
    "trust_anchors",
    "telemetry"
  ],
  "properties": {
    "proto": { "type": "string", "const": "xhttp-discovery" },
    "version": { "type": "string", "pattern": "^0\\.[0-9]+\\.[0-9]+(-[a-z0-9]+)?$" },
    "node_id": { "type": "string" },
    "operator": { "type": "string" },
    "supported_extensions": {
      "type": "array",
      "items": { "type": "string", "enum": ["x401", "x404", "x410", "x420"] }
    },
    "cryptography": {
      "type": "object",
      "required": ["signature_algorithms", "hash_functions", "canonicalization"],
      "properties": {
        "signature_algorithms": {
          "type": "array",
          "items": { "type": "string" }
        },
        "hash_functions": {
          "type": "array",
          "items": { "type": "string" }
        },
        "canonicalization": { "type": "string" }
      }
    },
    "trust_anchors": {
      "type": "object",
      "required": ["godns_domain_root", "mandatory_dnssec"],
      "properties": {
        "godns_domain_root": { "type": "string" },
        "mandatory_dnssec": { "type": "boolean" },
        "public_key_registry": { "type": "string", "format": "uri" }
      }
    },
    "telemetry": {
      "type": "object",
      "required": ["status", "active_wip", "max_concurrency", "uptime_sec"],
      "properties": {
        "status": { "type": "string", "enum": ["healthy", "degraded", "draining", "overloaded"] },
        "active_wip": { "type": "integer", "minimum": 0 },
        "max_concurrency": { "type": "integer", "minimum": 1 },
        "uptime_sec": { "type": "integer", "minimum": 0 }
      }
    }
  }
}
```

### 3.2. Example Discovery Response

```http
HTTP/1.1 200 OK
Date: Fri, 23 Oct 2026 11:45:00 GMT
Content-Type: application/xhttp+json; charset=utf-8
ETag: "xhttp-disc-9f82d1c4b7"
Cache-Control: public, max-age=60, stale-while-revalidate=120
Server: xHTTP-Edge/0.1.0

{
  "proto": "xhttp-discovery",
  "version": "0.1.0-draft",
  "node_id": "wolfram-edge-01.mindrock.capital",
  "operator": "Mindrock Autonomous Fleet",
  "supported_extensions": ["x401", "x404", "x410", "x420"],
  "cryptography": {
    "signature_algorithms": ["ed25519"],
    "hash_functions": ["sha256", "blake3"],
    "canonicalization": "RFC-8785-JCS"
  },
  "trust_anchors": {
    "godns_domain_root": "hackt.com",
    "mandatory_dnssec": true,
    "public_key_registry": "https://hackt.com/.well-known/agent-keys.json"
  },
  "telemetry": {
    "status": "healthy",
    "active_wip": 1,
    "max_concurrency": 2,
    "uptime_sec": 864200
  }
}
```

---

## 4. Edge Caching Semantics

Edge gateways hosting `/.well-known/xhttp` must configure HTTP headers to prevent thundering herd requests while reflecting load shifts promptly:
1. **`Cache-Control: public, max-age=60, stale-while-revalidate=120`**: Clients may cache capability profiles locally for 60 seconds. In the event of network jitter, stale cached profiles may be used for an additional 120 seconds while a background fetch refreshes the manifest.
2. **Conditional Validation (`If-None-Match`)**: Clients must supply the cached `ETag` on recurring discovery polls. If node capabilities and status remain invariant, the server responds with `304 Not Modified` without retransmitting the body.
3. **Emergency Degradation**: When a node transitions to `draining` or `overloaded`, it invalidates downstream edge cache keys via CDN cache-tag purge or sets `max-age=0`.

---

## 5. Security Considerations

* **Information Disclosure & Fingerprinting**: Exposing internal node identifiers or capacity limits (`active_wip`, `max_concurrency`) could allow adversaries to infer cluster size or schedule targeted volumetric attacks. Operators may omit granular telemetry fields or aggregate metrics across edge pools.
* **Denial of Service via Discovery Polling**: Adversarial agents might flood `/.well-known/xhttp` to exhaust connection state. Edge proxies must rate-limit unauthenticated discovery requests by client IP subnet (e.g. 60 requests/minute per `/64` IPv6 or `/24` IPv4).
* **Cache Poisoning**: Discovery documents must be delivered exclusively over TLS 1.3 with valid certificates. Intermediate CDNs must not alter the canonical JSON serialization.

---

## 6. Interoperability Notes & Limitations

* **RFC 8615 Conformance**: The URI path `/.well-known/xhttp` is strictly reserved under the IANA Well-Known URIs registry conventions.
* **Content Negotiation**: Servers must accept both `application/json` and `application/xhttp+json`. If a client specifies `Accept: text/html`, the server may return a human-readable diagnostic status page or fall back to JSON.
* **Dynamic Status vs. Static Hosting**: In environments utilizing static object storage (e.g. AWS S3, Cloudflare Pages), the dynamic `telemetry` block may be set to static default values (`status: "healthy"`, `active_wip: 0`) with longer TTLs (`max-age=3600`).
