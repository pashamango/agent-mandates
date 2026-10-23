# xHTTP-404: Generative Edge Order & Semantic Reconstruction (v0.1.0-draft)

**Status:** Proposed Standard Working Draft  
**Version:** 0.1.0-draft (2026-10-23)  
**Authority:** Principal Pavel Cherkashin (`pavel@mindrock.capital`)  
**Standard Identifier:** `RFC-xHTTP-404`  
**Dependencies:** RFC 7807 (Problem Details for HTTP APIs), RFC 8785 (JCS)  

---

## 1. Abstract & Problem Statement

In the classical architecture of the web, an HTTP `404 Not Found` response signals an unrecoverable failure: the target URI maps to no existing static byte stream, database record, or route handler. 

In the emerging agentic edge ecosystem, however:
1. **Dynamic Synthesis Capability**: Many artifacts (specialized code snippets, format transformations, mathematical proofs, tailored synthetic datasets, API adapters) do not pre-exist in physical storage, but their exact specifications are knowable and generatable on demand.
2. **Redundant Inference Elimination**: Multiple autonomous agents frequently request semantically identical missing resources (e.g. "Python Ed25519 validator matching schema V1"), resulting in duplicate compute expenditures unless an edge-level deduplication mechanism exists.
3. **Deterministic Verification Oracles**: A generated asset is untrusted unless accompanied by explicit verification criteria and cryptographic provenance.

**xHTTP-404** defines an edge-native protocol extension that upgrades `404 Not Found` into a structured **Generative Work Order Envelope**. The response provides the client agent with semantic context, synthesis constraints, execution policies, verification oracle hooks, and a **Semantic Hash (`SemHash`)** for shared edge cache routing.

---

## 2. Protocol Sequence Diagram

```
Autonomous Agent Client                                  Edge Gateway / Synthesizer
      │                                                              │
      │ 1. GET /models/embeddings/bge-m3-quantized.onnx HTTP/1.1     │
      │    Host: edge.mindrock.capital                               │
      │    X-Generative-Intent: "quantize-onnx:int8;arch=arm64"      │
      ├─────────────────────────────────────────────────────────────►│
      │                                                              │
      │                                                              │ (Evaluates local cache:
      │                                                              │  MISS. Evaluates Policy:
      │                                                              │  Generative on-demand OK)
      │ 2. HTTP/1.1 404 Not Found                                    │
      │    Content-Type: application/xhttp+json                      │
      │    X-Generative-Status: Available-For-Synthesis              │
      │    X-Semantic-Hash: a8f4b2c1...d9e0                          │
      │                                                              │
      │    <404 Generative Work Order Envelope>                      │
      │    { "semhash", "synthesis_policy", "verification_oracle" }   │
      │◄─────────────────────────────────────────────────────────────┤
      │                                                              │
      │ (Client inspects envelope:                                   │
      │  Checks token budget, deadline, verification oracle.         │
      │  Decides to trigger generation)                              │
      │                                                              │
      │ 3. POST /models/embeddings/bge-m3-quantized.onnx HTTP/1.1    │
      │    Host: edge.mindrock.capital                               │
      │    X-Action: Execute-Synthesis                               │
      │    X-Semantic-Hash: a8f4b2c1...d9e0                          │
      │    Content-Type: application/json                            │
      │                                                              │
      │    { "agreed_budget": 5000, "oracle_ack": true }             │
      ├─────────────────────────────────────────────────────────────►│
      │                                                              │
      │                                                              │ (Synthesizes artifact,
      │                                                              │  executes verification,
      │                                                              │  stores in SemHash cache)
      │ 4. HTTP/1.1 201 Created (or 200 OK)                          │
      │    Content-Type: application/octet-stream                    │
      │    ETag: "semhash-a8f4b2c1...d9e0"                           │
      │                                                              │
      │    <Synthesized Binary / Document Artifact>                  │
      │◄─────────────────────────────────────────────────────────────┤
```

---

## 3. Envelope Specifications & Schemas

### 3.1. JSON Schema for Generative Work Order (`404 Generative Envelope`)

```json
{
  "$schema": "https://json-schema.org/draft/2020-12/schema",
  "title": "GenerativeWorkOrderEnvelope",
  "type": "object",
  "required": [
    "proto",
    "status_code",
    "resource_uri",
    "semantic_hash",
    "synthesis_context",
    "policy",
    "verification_oracle",
    "alternatives"
  ],
  "properties": {
    "proto": { "type": "string", "const": "xhttp-404-generative" },
    "status_code": { "type": "integer", "const": 404 },
    "resource_uri": { "type": "string", "format": "uri-reference" },
    "semantic_hash": { "type": "string", "pattern": "^semhash:[0-9a-fA-F]{64}$" },
    "synthesis_context": {
      "type": "object",
      "required": ["prompt_template", "input_parameters", "target_format", "estimated_compute_ms"],
      "properties": {
        "prompt_template": { "type": "string" },
        "input_parameters": { "type": "object" },
        "target_format": { "type": "string" },
        "estimated_compute_ms": { "type": "integer", "minimum": 1 }
      }
    },
    "policy": {
      "type": "object",
      "required": ["allow_on_demand_synthesis", "max_token_budget", "quota_tier", "cooldown_sec"],
      "properties": {
        "allow_on_demand_synthesis": { "type": "boolean" },
        "max_token_budget": { "type": "integer", "minimum": 0 },
        "quota_tier": { "type": "string" },
        "cooldown_sec": { "type": "integer", "minimum": 0 }
      }
    },
    "verification_oracle": {
      "type": "object",
      "required": ["verifier_type", "test_harness_url", "required_passing_tests"],
      "properties": {
        "verifier_type": { "type": "string", "enum": ["unit_test", "schema_validation", "formal_lean4", "cryptographic_proof"] },
        "test_harness_url": { "type": "string", "format": "uri" },
        "required_passing_tests": { "type": "integer", "minimum": 1 }
      }
    },
    "alternatives": {
      "type": "array",
      "items": {
        "type": "object",
        "required": ["uri", "similarity_score", "reason"],
        "properties": {
          "uri": { "type": "string" },
          "similarity_score": { "type": "number", "minimum": 0.0, "maximum": 1.0 },
          "reason": { "type": "string" }
        }
      }
    }
  }
}
```

### 3.2. Example Response: Generative 404 Work Order

```http
HTTP/1.1 404 Not Found
Date: Fri, 23 Oct 2026 11:47:00 GMT
Content-Type: application/xhttp+json; charset=utf-8
X-Generative-Status: Available-For-Synthesis
X-Semantic-Hash: semhash:a8f4b2c1d9e0f3a76b5c4d3e2f1a0b9c8d7e6f5a4b3c2d1e0f9a8b7c6d5e4f3a

{
  "proto": "xhttp-404-generative",
  "status_code": 404,
  "resource_uri": "/models/embeddings/bge-m3-quantized.onnx",
  "semantic_hash": "semhash:a8f4b2c1d9e0f3a76b5c4d3e2f1a0b9c8d7e6f5a4b3c2d1e0f9a8b7c6d5e4f3a",
  "synthesis_context": {
    "prompt_template": "onnx-quantize-v2:int8",
    "input_parameters": {
      "source_model": "BAAI/bge-m3",
      "quantization_scheme": "symmetric-int8",
      "target_runtime": "onnxruntime-arm64"
    },
    "target_format": "application/octet-stream",
    "estimated_compute_ms": 3200
  },
  "policy": {
    "allow_on_demand_synthesis": true,
    "max_token_budget": 8000,
    "quota_tier": "developer-fleet-free",
    "cooldown_sec": 30
  },
  "verification_oracle": {
    "verifier_type": "unit_test",
    "test_harness_url": "https://hackt.com/harnesses/onnx_quant_verify.py",
    "required_passing_tests": 8
  },
  "alternatives": [
    {
      "uri": "/models/embeddings/bge-m3-fp16.onnx",
      "similarity_score": 0.94,
      "reason": "Full FP16 baseline is cached and ready for immediate download."
    },
    {
      "uri": "/models/embeddings/bge-small-int8.onnx",
      "similarity_score": 0.78,
      "reason": "Lower parameter model with identical interface."
    }
  ]
}
```

### 3.3. Example Request: Synthesis Confirmation & Initiation (Wire Example)

When the client agent accepts the terms of the generative work order, it initiates synthesis via an authenticated HTTP POST:

```http
POST /models/embeddings/bge-m3-quantized.onnx HTTP/1.1
Host: edge.mindrock.capital
Authorization: Multipass eyJwcm90byI6InhodHRwLTQwMS1tdWx0aXBhc3MiLCJjaGFsbGVuZ2VfaWQiOiJjaF83ZjhhOWIyYzNkNGU1ZjYwIiwibm9uY2VfZWNobyI6ImUzYjBjNDQyOThmYzFjMTQ5YWZiZjRjODk5NmZiOTI0MjdhZTQxZTQ2NDliOTM0Y2E0OTU5OTFiNzg1MmI4NTUiLCJjbGllbnRfZGlkIjoiZGlkOmtleTp6Nk1rbkRjYnpNQ2lGTExkeEtRYm9DcVZHUFlac0pid3AyN28zN2dZcUJFcUo0TkUjYWd5QHdvbGZyYW0iLCJnb2Ruc19sb2NhdG9yIjoiX21hbmRhdGUuYWd5LmhhY2t0LmNvbSIsIm1hbmRhdGVfaWQiOiJtYW5kYXRlX2M4NDdkMTBlOWYxYSIsInRpbWVzdGFtcCI6IjIwMjYtMTAtMjNUMTE6NDY6MTJaIiwic2lnbmF0dXJlIjoiZjFiMDk4OGI0MzEyMjM3ZGY1Y2Y2MDQ5ZWNmZDE1ZjkyOTRkOGUwNWRlODIzZWJlYTZjNGY5M2I4ZGVlYTA3MDk2NmExNTcyOGJmOWQ2MDZjNGYwODlhMDg3NmI5ZDc3MWY5ZjExZTE5Yzc2MmUzNGY3NGU2YWVmZmY4ODUwMDYifQ
X-Action: Execute-Synthesis
X-Semantic-Hash: semhash:a8f4b2c1d9e0f3a76b5c4d3e2f1a0b9c8d7e6f5a4b3c2d1e0f9a8b7c6d5e4f3a
Content-Type: application/json; charset=utf-8
Content-Length: 138

{
  "agreed_budget": 5000,
  "oracle_ack": true,
  "client_instance_id": "agy-wolfram-01",
  "callback_url": "https://agy.mindrock.capital/hooks/synthesis"
}
```

---

## 4. Semantic Normalization & Deduplication Algorithm (`SemHash`)

To prevent duplicate generative synthesis cycles, edge gateways compute a deterministic `SemHash`. Rather than relying on naive text hashing, `SemHash` applies a strict normalization pipeline that maps semantically equivalent requests to the identical hash digest.

### 4.1. Step-by-Step Normalization Pipeline

The normalization process comprises five sequential phases:

```
[Incoming Request]
       │
       ▼ Phase 1: URI Path Canonicalization (RFC 3986 path normalization, lowercased segments)
       │
       ▼ Phase 2: Volatile Query Stripping (Remove timestamps, trace IDs, auth signatures)
       │
       ▼ Phase 3: Unicode & Whitespace Normalization (NFC normalization, space collapsing)
       │
       ▼ Phase 4: Media-Type & Parameter Canonicalization (Sort semantic params, canonical media type)
       │
       ▼ Phase 5: Domain Separation & JCS Hashing (xhttp-semhash-v1: prefix + RFC 8785 JCS)
       │
[Deterministic SemHash]
```

1. **Phase 1: URI Path Canonicalization**:
   - Strip scheme (`http://`, `https://`) and authority/host.
   - Resolve relative path segments (`.` and `..`) per RFC 3986 section 5.2.4.
   - Collapse consecutive forward slashes into a single slash `/`.
   - Strip trailing slashes unless path is root `/`.
   - Decode unreserved percent-encoded octets; lowercase all percent-encoded hex sequences.

2. **Phase 2: Query Parameter Filtering (Volatile vs. Semantic)**:
   - Query parameters that represent ephemeral runtime state, tracing, or client identity MUST be completely stripped from the hash calculation.
   - **Volatile Parameters (Always Excluded)**:
     `timestamp`, `t`, `_`, `nonce`, `request_id`, `trace_id`, `span_id`, `client_id`, `session_id`, `sig`, `signature`, `callback`, `utm_*`, `cache_buster`.
   - **Semantic Parameters (Retained)**:
     Parameters dictating resource attributes (e.g. `target_arch`, `quantization`, `precision`, `dialect`).
   - Retained parameters are sorted lexicographically by parameter name in ascending ASCII order. Both names and values are percent-encoded in lowercase.

3. **Phase 3: Unicode & Text Normalization**:
   - All string literals in prompt templates and parameters MUST be normalized to **Unicode Normalization Form C (NFC)**.
   - Whitespace in non-code prompts is normalized: leading and trailing spaces are trimmed, and runs of whitespace (spaces, tabs, newlines) are collapsed to a single space character (`0x20`).

4. **Phase 4: Media-Type Normalization**:
   - Target format and MIME type strings are lowercased and stripped of optional parameter spacing (e.g. `application/octet-stream` or `application/json; charset=utf-8`).

5. **Phase 5: Canonical Intent Object Construction**:
   The normalized components are assembled into a canonical dictionary:
   ```json
   {
     "canonical_path": "/models/embeddings/bge-m3-quantized.onnx",
     "format": "application/octet-stream",
     "parameters": {
       "quantization_scheme": "symmetric-int8",
       "source_model": "BAAI/bge-m3",
       "target_runtime": "onnxruntime-arm64"
     },
     "prompt_template": "onnx-quantize-v2:int8",
     "schema_version": "v1"
   }
   ```

### 4.2. Hash Derivation Formula with Domain Separation

To protect against cross-protocol collision attacks, the normalized object is prefixed with a static domain separator string before SHA-256 calculation:

$$\text{SemHash} = \text{"semhash:"} + \text{HEX}(\text{SHA256}(\text{"xhttp-semhash-v1:"} \parallel \text{JCS}(\text{CanonicalIntentObject})))$$

Where:
* $\text{"xhttp-semhash-v1:"}$ is the ASCII domain separator.
* $\text{JCS}(\cdot)$ is the RFC 8785 JSON Canonicalization Scheme serializer.
* $\text{SHA256}(\cdot)$ computes the 32-byte cryptographic digest.
* $\text{HEX}(\cdot)$ encodes the digest as 64 lowercase hexadecimal characters.

### 4.3. Equivalence Recognition & Edge Coalescing

Consider two distinct client requests dispatched by separate agents:
* **Client A**:
  `GET /models/embeddings/bge-m3-quantized.onnx?trace_id=987654321&arch=arm64&nonce=a1b2c3`
* **Client B**:
  `GET /models/embeddings/bge-m3-quantized.onnx?arch=arm64&session_id=sess_fedcba&t=1729684000`

Because `trace_id`, `nonce`, `session_id`, and `t` are classified as volatile parameters, both requests produce the exact same `CanonicalIntentObject` and yield the identical `SemHash`:
`semhash:a8f4b2c1d9e0f3a76b5c4d3e2f1a0b9c8d7e6f5a4b3c2d1e0f9a8b7c6d5e4f3a`

When Client B's request arrives while Client A's synthesis is actively processing in the GPU slot, the edge gateway coalesces Client B into the existing job promise, eliminating redundant inference and double-billing.

---

## 5. Security Considerations

* **Compute Denial of Service (Economic DoS)**: Malicious or misconfigured agents could deliberately generate millions of unique, high-entropy synthetic requests to exhaust server GPU/CPU capacity. Mitigations:
  - Synthesis execution **requires prior xHTTP-401 authentication**.
  - Strict wall-clock limits (e.g. max 120 seconds) and weekly synthesis caps (e.g. 50 tasks per account) enforced by the gateway.
* **Model Poisoning & Adversarial Prompts**: If client-supplied prompt inputs are directly concatenated into generative pipelines, prompt injection attacks could compromise synthesized code. Synthesis pipelines must run in sandboxed, unprivileged wasm/container boundaries with network isolation.
* **Semantic Hash Collision**: Attackers could craft colliding inputs to pollute the edge cache with malicious synthetic artifacts. Semantic hashes must use collision-resistant 256-bit hash functions (SHA-256 or BLAKE3) over canonicalized JSON.

---

## 6. Interoperability Notes & Limitations

* **Legacy HTTP Client Handling**: Standard web browsers and legacy crawlers will interpret the HTTP 404 response normally as a missing page. Browsers do not execute the synthesis action unless explicit JavaScript client libraries are present.
* **Cold-Start Latency**: Edge generation introduces wall-clock latencies (hundreds of milliseconds to several minutes). Clients requesting synchronous synthesis must negotiate appropriate connection read timeouts or support asynchronous polling (`202 Accepted` with `Location` header).
* **Non-Deterministic Outputs**: Generative models may produce subtly different artifacts across runs. To guarantee repeatability, edge nodes must pin model weights, temperature ($T=0.0$), and random seeds, or store the first verified artifact permanently under its content hash.
