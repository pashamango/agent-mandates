# xHTTP-420: Patience Protocol & Agent Backpressure Negotiation (v0.1.0-draft)

**Status:** Proposed Standard Working Draft  
**Version:** 0.1.0-draft (2026-10-23)  
**Authority:** Principal Pavel Cherkashin (`pavel@mindrock.capital`)  
**Standard Identifier:** `RFC-xHTTP-420`  
**Dependencies:** RFC 6585 (Additional HTTP Status Codes), RFC 7807, RFC 8785  

---

## 1. Abstract & Problem Statement

Autonomous agent clusters and specialized hardware runners (such as LLM inference engines, Lean 4 theorem provers, and formal verification nodes) frequently operate under strict concurrency constraints (e.g. Work-In-Progress $\text{WIP} = 1$ per GPU or executor slot). 

When such a node receives a new computational request while occupied:
1. **Uninformative Polling**: Standard HTTP `429 Too Many Requests` or `503 Service Unavailable` with a raw `Retry-After: 30` header treats the caller as a human browser, providing no insight into queue depth, execution stage, or realistic completion deadlines.
2. **Deadlock & Thundering Herds**: Agents polling blindly on fixed intervals waste network bandwidth, exacerbate server contention, and risk distributed deadlocks.
3. **Lack of Negotiation Paths**: A busy node might be capable of delegating to a federated peer, escalating to a paid priority lane, offering a lower-precision fallback, or requesting a catastrophic abort.

**xHTTP-420** defines the **Patience Protocol**, introducing an intelligent backpressure negotiation handshake. The server returns an HTTP `420 Patience` response containing precise queue telemetry, estimated time of arrival (ETA), jitter tolerances, and five actionable recovery branches: `WAIT`, `PAY`, `DELEGATE`, `OUR_LADY`, and `FSCK_OFF` (*File System Check Off*).

---

## 2. Protocol Sequence Diagram

```
Autonomous Client Agent                                   Specialized Edge Worker
(agy@wolfram)                                            (executor slot occupied, WIP=1)
      │                                                              │
      │ 1. POST /api/v1/prove/amde-lean4 HTTP/1.1                    │
      │    Host: worker.mindrock.capital                             │
      │    Content-Type: application/json                            │
      │                                                              │
      │    { "theorem_id": "MIN-80", "proof_context": "..." }        │
      ├─────────────────────────────────────────────────────────────►│
      │                                                              │
      │                                                              │ (Evaluates executor state:
      │                                                              │  Slot occupied, WIP=1.
      │                                                              │  Calculates Queue & ETA)
      │ 2. HTTP/1.1 420 Patience                                     │
      │    Content-Type: application/xhttp+json                      │
      │    X-Queue-Position: 3                                       │
      │    X-Queue-ETA-Seconds: 42                                   │
      │                                                              │
      │    <420 Patience Backpressure Envelope>                      │
      │    { "queue_depth": 3, "eta_ms": 42000, "action_options" }   │
      │◄─────────────────────────────────────────────────────────────┤
      │                                                              │
      │ (Client evaluates options:                                   │
      │  Option A: WAIT (Sleep 42s + jitter)                         │
      │  Option B: PAY (Escalate to priority lane)                   │
      │  Option C: DELEGATE (Redirect to darwin-slot-2)              │
      │  Option D: OUR_LADY (Graceful heuristic degradation)         │
      │  Option E: FSCK_OFF (Purge corrupt workspace & abort)        │
      │                                                              │
      │ Client chooses DELEGATE)                                     │
      │                                                              │
      │ 3. POST /api/v1/prove/amde-lean4 HTTP/1.1                    │
      │    Host: worker-secondary.mindrock.capital                  │
      │    X-Delegation-Proof: del_8f9a2b1c0e...                     │
      │                                                              │
      │    { "theorem_id": "MIN-80", "proof_context": "..." }        │
      ├─────────────────────────────────────────────────────────────►│
```

---

## 3. Envelope Specifications & Schemas

### 3.1. JSON Schema for Patience Protocol (`420 Patience Envelope`)

```json
{
  "$schema": "https://json-schema.org/draft/2020-12/schema",
  "title": "PatienceBackpressureEnvelope",
  "type": "object",
  "required": [
    "proto",
    "status_code",
    "node_id",
    "queue_metrics",
    "eta_window",
    "recovery_options"
  ],
  "properties": {
    "proto": { "type": "string", "const": "xhttp-420-patience" },
    "status_code": { "type": "integer", "const": 420 },
    "node_id": { "type": "string" },
    "queue_metrics": {
      "type": "object",
      "required": ["queue_depth", "active_wip", "max_capacity", "avg_service_time_ms"],
      "properties": {
        "queue_depth": { "type": "integer", "minimum": 0 },
        "active_wip": { "type": "integer", "minimum": 1 },
        "max_capacity": { "type": "integer", "minimum": 1 },
        "avg_service_time_ms": { "type": "integer", "minimum": 1 }
      }
    },
    "eta_window": {
      "type": "object",
      "required": ["estimated_wait_ms", "jitter_tolerance_ms", "valid_until"],
      "properties": {
        "estimated_wait_ms": { "type": "integer", "minimum": 0 },
        "jitter_tolerance_ms": { "type": "integer", "minimum": 0 },
        "valid_until": { "type": "string", "format": "date-time" }
      }
    },
    "recovery_options": {
      "type": "object",
      "required": ["WAIT", "PAY", "DELEGATE", "OUR_LADY", "FSCK_OFF"],
      "properties": {
        "WAIT": {
          "type": "object",
          "required": ["recommended_backoff_ms", "max_retries"],
          "properties": {
            "recommended_backoff_ms": { "type": "integer" },
            "max_retries": { "type": "integer" }
          }
        },
        "PAY": {
          "type": "object",
          "required": ["priority_lane_available", "token_cost", "payment_recipient"],
          "properties": {
            "priority_lane_available": { "type": "boolean" },
            "token_cost": { "type": "integer" },
            "payment_recipient": { "type": "string" }
          }
        },
        "DELEGATE": {
          "type": "object",
          "required": ["peer_nodes"],
          "properties": {
            "peer_nodes": {
              "type": "array",
              "items": {
                "type": "object",
                "required": ["node_id", "endpoint", "estimated_rtt_ms"],
                "properties": {
                  "node_id": { "type": "string" },
                  "endpoint": { "type": "string", "format": "uri" },
                  "estimated_rtt_ms": { "type": "integer" }
                }
              }
            }
          }
        },
        "OUR_LADY": {
          "type": "object",
          "required": ["fallback_available", "degradation_profile"],
          "properties": {
            "fallback_available": { "type": "boolean" },
            "degradation_profile": { "type": "string" }
          }
        },
        "FSCK_OFF": {
          "type": "object",
          "required": ["command", "action", "state_purge_endpoint"],
          "properties": {
            "command": { "type": "string", "const": "FSCK_OFF" },
            "description": { "type": "string" },
            "action": { "type": "string", "enum": ["RESET_WORKTREE", "PURGE_CACHE", "TERMINATE_SESSION"] },
            "state_purge_endpoint": { "type": "string", "format": "uri-reference" }
          }
        }
      }
    }
  }
}
```

### 3.2. Example Response: 420 Patience Envelope

```http
HTTP/1.1 420 Patience
Date: Fri, 23 Oct 2026 11:49:00 GMT
Content-Type: application/xhttp+json; charset=utf-8
X-Queue-Depth: 2
X-Queue-ETA-Seconds: 45
Retry-After: 45

{
  "proto": "xhttp-420-patience",
  "status_code": 420,
  "node_id": "wolfram-worker-slot-1",
  "queue_metrics": {
    "queue_depth": 2,
    "active_wip": 1,
    "max_capacity": 1,
    "avg_service_time_ms": 22500
  },
  "eta_window": {
    "estimated_wait_ms": 45000,
    "jitter_tolerance_ms": 3000,
    "valid_until": "2026-10-23T11:50:00Z"
  },
  "recovery_options": {
    "WAIT": {
      "recommended_backoff_ms": 45000,
      "max_retries": 3
    },
    "PAY": {
      "priority_lane_available": true,
      "token_cost": 25,
      "payment_recipient": "did:key:z6MknDcbzMCiFLLdxKQboCqVGPYZsJbwp27o37gYqBEqJ4NE#treasury"
    },
    "DELEGATE": {
      "peer_nodes": [
        {
          "node_id": "darwin-worker-slot-1",
          "endpoint": "https://darwin-node.mindrock.capital/api/v1/prove/amde-lean4",
          "estimated_rtt_ms": 14
        }
      ]
    },
    "OUR_LADY": {
      "fallback_available": true,
      "degradation_profile": "heuristic-proof-sketch-only"
    },
    "FSCK_OFF": {
      "command": "FSCK_OFF",
      "description": "File System Check Off: Clean-room workspace reset and abort when node state is invalid or blocked.",
      "action": "RESET_WORKTREE",
      "state_purge_endpoint": "/api/v1/node/reset"
    }
  }
}
```

---

## 4. Deep Dive: Recovery Action Semantics

### 4.1. `WAIT`
Instructs the caller to enter an asynchronous sleep state. The client must calculate sleep duration as:
$$\text{SleepDuration} = \text{estimated\_wait\_ms} + \text{UniformRandom}(-\text{jitter\_tolerance\_ms}, +\text{jitter\_tolerance\_ms})$$
This eliminates thundering herds when a long-running batch job finishes.

### 4.2. `PAY`
When enabled, the client can attach a signed computational micropayment or credit token (`X-Compute-Credit: <token>`) to immediately preempt lower-priority tasks and enter a dedicated high-priority execution queue.

### 4.3. `DELEGATE`
The server provides a live routing table of neighboring peer nodes in the mesh that currently advertise available capacity for the same capability. The client agent can immediately redispatch its request to the specified endpoint without contacting a central coordinator.

### 4.4. `OUR_LADY` (Graceful Circuit Breaker)
Inspired by resilient naval circuit-breaker protocols, `OUR_LADY` signals that the client can request an immediate, reduced-fidelity response (e.g. an unverified proof sketch, a cached approximation, or a quantized embedding) rather than blocking on full-precision compute.

### 4.5. `FSCK_OFF` (*File System Check Off*)
The **`FSCK_OFF`** command represents a deliberate fail-closed escape hatch for autonomous agent networks:
* **Etymology & Purpose**: Deriving its name from the Unix file system consistency check (`fsck`), `FSCK_OFF` signifies *File System Check Off* — an explicit directive issued when an agent node or client detects persistent state corruption, circular deadlock, workspace contamination, or irrecoverable environment drift.
* **Mechanism**: When a client executes `FSCK_OFF`, it terminates the current task queue, cancels pending leases, and dispatches a POST request to `state_purge_endpoint`. The node executes a clean-room workspace tear-down (`git reset --hard`, `/tmp` cache wipe), freeing locked resources for subsequent fleet operations.

---

## 5. Security Considerations

* **Queue Probing & State Reconnaissance**: Malicious actors could monitor `queue_depth` and `avg_service_time_ms` across multiple endpoints to map infrastructure load and orchestrate coordinated micro-burst attacks. Gateways should add randomized noise to queue metrics for unauthenticated callers.
* **Payment Forgery**: In the `PAY` option, compute tokens must be signed with valid Ed25519 keys and tied to a unique nonce to prevent double-spending of compute credits.
* **Denial of Service via FSCK_OFF Abuse**: Because `FSCK_OFF` triggers workspace cleanup, untrusted callers must not be permitted to invoke state purges. Access to `FSCK_OFF` endpoints requires full `xHTTP-401` authentication with root or fleet administrator scope.

---

## 6. Interoperability Notes & Limitations

* **Mapping to Standard HTTP 429**: Standard HTTP proxies or legacy reverse proxies that do not recognize status code 420 may automatically convert it to `429 Too Many Requests` or `503 Service Unavailable`. To ensure compatibility:
  - The server emits both `X-Queue-ETA-Seconds` and the standard `Retry-After: <seconds>` header.
  - The response body remains fully intact, allowing xHTTP-aware clients to parse the JSON envelope regardless of status code rewrites.
* **Clock Synchronization**: The `valid_until` timestamp assumes NTP synchronization across fleet nodes ($\pm 500\text{ ms}$). If client and server clocks diverge significantly, clients must prioritize relative `estimated_wait_ms` over absolute timestamps.
* **Proxy Timeout Windows**: Long `estimated_wait_ms` values (e.g. $> 120\text{ seconds}$) may exceed intermediate edge gateway read timeouts (such as Cloudflare's default 100s timeout). In such scenarios, servers should recommend asynchronous delegation (`DELEGATE`) or initiate an asynchronous task returning `202 Accepted`.
