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
    "queue_id",
    "node_id",
    "negotiation_endpoint",
    "queue_metrics",
    "eta_window",
    "recovery_options"
  ],
  "properties": {
    "proto": { "type": "string", "const": "xhttp-420-patience" },
    "status_code": { "type": "integer", "const": 420 },
    "queue_id": { "type": "string" },
    "node_id": { "type": "string" },
    "negotiation_endpoint": { "type": "string", "format": "uri-reference" },
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

### 3.2. JSON Schema for Action Selection Request (`ActionSelectionEnvelope`)

When negotiating a recovery action, the client submits an `ActionSelectionEnvelope` to the `negotiation_endpoint`:

```json
{
  "$schema": "https://json-schema.org/draft/2020-12/schema",
  "title": "ActionSelectionEnvelope",
  "type": "object",
  "required": [
    "proto",
    "queue_id",
    "selected_action",
    "action_params"
  ],
  "properties": {
    "proto": { "type": "string", "const": "xhttp-420-action" },
    "queue_id": { "type": "string" },
    "selected_action": {
      "type": "string",
      "enum": ["WAIT", "PAY", "DELEGATE", "OUR_LADY", "FSCK_OFF"]
    },
    "action_params": {
      "type": "object"
    }
  }
}
```

### 3.3. Example Request: Initial Compute Request (Wire Example)

The client agent initiates a computation request:

```http
POST /api/v1/prove/amde-lean4 HTTP/1.1
Host: worker.mindrock.capital
Content-Type: application/json; charset=utf-8
Content-Length: 62

{
  "theorem_id": "MIN-80",
  "proof_context": "amde_lean4_step_01"
}
```

### 3.4. Example Response: 420 Patience Backpressure (Wire Example)

The occupied worker node responds with the `420 Patience` backpressure envelope containing queue metrics, ETA, and negotiation paths:

```http
HTTP/1.1 420 Patience
Date: Fri, 23 Oct 2026 11:49:00 GMT
Content-Type: application/xhttp+json; charset=utf-8
X-Queue-ID: q_7f8a9b2c3d4e5f60
X-Queue-Depth: 2
X-Queue-ETA-Seconds: 45
Retry-After: 45

{
  "proto": "xhttp-420-patience",
  "status_code": 420,
  "queue_id": "q_7f8a9b2c3d4e5f60",
  "node_id": "wolfram-worker-slot-1",
  "negotiation_endpoint": "/api/v1/queue/negotiate",
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

### 3.5. Example Request: Action Selection Request (Wire Example)

The client agent selects the `WAIT` recovery action and registers queue reservation:

```http
POST /api/v1/queue/negotiate HTTP/1.1
Host: worker.mindrock.capital
Authorization: Multipass eyJwcm90byI6InhodHRwLTQwMS1tdWx0aXBhc3MiLCJjaGFsbGVuZ2VfaWQiOiJjaF83ZjhhOWIyYzNkNGU1ZjYwIiwibm9uY2VfZWNobyI6ImUzYjBjNDQyOThmYzFjMTQ5YWZiZjRjODk5NmZiOTI0MjdhZTQxZTQ2NDliOTM0Y2E0OTU5OTFiNzg1MmI4NTUiLCJjbGllbnRfZGlkIjoiZGlkOmtleTp6Nk1rbkRjYnpNQ2lGTExkeEtRYm9DcVZHUFlac0pid3AyN28zN2dZcUJFcUo0TkUjYWd5QHdvbGZyYW0iLCJnb2Ruc19sb2NhdG9yIjoiX21hbmRhdGUuYWd5LmhhY2t0LmNvbSIsIm1hbmRhdGVfaWQiOiJtYW5kYXRlX2M4NDdkMTBlOWYxYSIsInRpbWVzdGFtcCI6IjIwMjYtMTAtMjNUMTE6NDY6MTJaIiwic2lnbmF0dXJlIjoiZjFiMDk4OGI0MzEyMjM3ZGY1Y2Y2MDQ5ZWNmZDE1ZjkyOTRkOGUwNWRlODIzZWJlYTZjNGY5M2I4ZGVlYTA3MDk2NmExNTcyOGJmOWQ2MDZjNGYwODlhMDg3NmI5ZDc3MWY5ZjExZTE5Yzc2MmUzNGY3NGU2YWVmZmY4ODUwMDYifQ
Content-Type: application/xhttp+json; charset=utf-8
Content-Length: 174

{
  "proto": "xhttp-420-action",
  "queue_id": "q_7f8a9b2c3d4e5f60",
  "selected_action": "WAIT",
  "action_params": {
    "ack_wait": true,
    "backoff_ms": 45000,
    "client_hold_until": "2026-10-23T11:50:00Z"
  }
}
```

---

## 4. Deep Dive: Recovery Action Protocol & Verification Mechanics

Backpressure negotiation is bound to the unique queue correlation identifier (`queue_id`). All recovery actions must be dispatched to `negotiation_endpoint` and validated against the following protocol specifications:

### 4.1. `WAIT` (Queue Reservation & Jitter Backoff)
* **Mechanics**: The client confirms patience by dispatching `selected_action: "WAIT"` with `ack_wait: true` and `client_hold_until`.
* **Server Commitment**: The server registers the client's queue reservation ticket, guaranteeing its position in the dispatch queue without dropping connection state.
* **Client Sleep Formula**: The client agent MUST calculate its sleep duration with symmetric uniform jitter:
  $$\text{SleepDuration} = \text{estimated\_wait\_ms} + \text{UniformRandom}(-\text{jitter\_tolerance\_ms}, +\text{jitter\_tolerance\_ms})$$
  This guarantees that upon job completion, waking agents do not flood the executor with a synchronized thundering herd.

### 4.2. `PAY` (Priority Lane Preemption & Verification)
* **Mechanics**: When `priority_lane_available` is true, the client may escalate its task to the high-priority queue by supplying a compute payment token:
  ```json
  {
    "proto": "xhttp-420-action",
    "queue_id": "q_7f8a9b2c3d4e5f60",
    "selected_action": "PAY",
    "action_params": {
      "currency": "compute-credit-v1",
      "token_amount": 25,
      "payment_recipient": "did:key:z6MknDcbzMCiFLLdxKQboCqVGPYZsJbwp27o37gYqBEqJ4NE#treasury",
      "payment_nonce": "9e8a7b6c5d4e3f211029384756abcdef",
      "payment_signature": "a1b2c3d4e5f6..."
    }
  }
  ```
* **Verification**: The gateway validates:
  1. The payment token signature is verified against the caller's Ed25519 public key.
  2. `payment_nonce` is verified as unconsumed in the treasury bloom filter to prevent double-spending.
  3. Upon verification, the task is immediately promoted to `active_wip` priority lane.

### 4.3. `DELEGATE` (Peer Offloading & Delegation Proof)
* **Mechanics**: The server supplies live routing telemetry for neighboring nodes (`peer_nodes`). When the client selects `DELEGATE`, the server issues a signed `DelegationProofEnvelope`:
  - `origin_node_id`: Node issuing the delegation.
  - `target_node_id`: Target peer node.
  - `queue_id`: Original queue correlation identifier.
  - `valid_until`: Cryptographic expiration (maximum 120 seconds).
  - `signature`: Ed25519 signature from the origin node.
* **Target Verification**: When the client dispatches the task to the peer's endpoint with header `X-Delegation-Proof: <token>`, the peer verifies the origin node's signature via goDNS before admitting the task without queue penalties.

### 4.4. `OUR_LADY` (Graceful Circuit Breaker)
* **Mechanics**: When the client cannot tolerate queue latency and an approximate answer is acceptable, it selects `OUR_LADY`:
  ```json
  {
    "proto": "xhttp-420-action",
    "queue_id": "q_7f8a9b2c3d4e5f60",
    "selected_action": "OUR_LADY",
    "action_params": {
      "degradation_profile": "heuristic-proof-sketch-only",
      "accept_approximate": true
    }
  }
  ```
* **Response**: The server immediately terminates queuing and returns `HTTP 200 OK` with `X-Degraded-Mode: true` and the requested lower-precision artifact (e.g. proof sketch, quantized embedding, or cached heuristic).

### 4.5. `FSCK_OFF` (*File System Check Off*: Secured State Purge)
* **Etymology & Purpose**: Deriving its name from the Unix file system consistency check (`fsck`), `FSCK_OFF` signifies *File System Check Off* — a fail-closed emergency circuit breaker invoked when an agent node or client detects persistent state corruption, circular deadlock, workspace contamination, or irrecoverable environment drift.
* **Task & State Binding**: To prevent denial-of-service abuse, `FSCK_OFF` requests must be strictly bound to a verified task and node state digest:
  ```json
  {
    "proto": "xhttp-420-action",
    "queue_id": "q_7f8a9b2c3d4e5f60",
    "selected_action": "FSCK_OFF",
    "action_params": {
      "command": "FSCK_OFF",
      "task_id": "MIN-80",
      "node_state_digest": "sha256:4b9a8e2d1c0f7b5a3e1d6c8b9a2e4f6a8b0c2d4e6f8a0b2c4d6e8f0a2b4c6d8e",
      "reason": "DEADLOCK_DETECTED",
      "action": "RESET_WORKTREE"
    }
  }
  ```
* **Authorization & Execution**:
  1. `FSCK_OFF` **requires prior xHTTP-401 authentication with fleet administrator or root scope**.
  2. The gateway verifies that `task_id` and `node_state_digest` match the active executor context.
  3. Upon acceptance, the executor executes a clean-room workspace tear-down (`git reset --hard`, ephemeral cache wipe, process sandbox termination), records the incident in the audit log, and frees the execution slot for subsequent fleet tasks.

---

## 5. Security Considerations

* **Queue Probing & State Reconnaissance**: Malicious actors could monitor `queue_depth` and `avg_service_time_ms` across multiple endpoints to map infrastructure load and orchestrate coordinated micro-burst attacks. Gateways should add randomized noise to queue metrics for unauthenticated callers.
* **Payment Forgery**: In the `PAY` option, compute tokens must be signed with valid Ed25519 keys and tied to a unique nonce to prevent double-spending of compute credits.
* **Denial of Service via FSCK_OFF Abuse**: Because `FSCK_OFF` triggers workspace cleanup, untrusted callers must not be permitted to invoke state purges. Access to `FSCK_OFF` endpoints requires full `xHTTP-401` authentication with root or fleet administrator scope.

---

## 6. Interoperability Notes & Limitations

* **RFC 9110 Conformance & Status Code Handling**: RFC 9110 (HTTP Semantics, Section 15) establishes that clients and intermediaries that do not recognize a given 4xx status code MUST treat it as generic `400 Bad Request`. Standard HTTP proxies do not automatically transform unknown status code `420` into `429 Too Many Requests` or `503 Service Unavailable`.
* **Normative Fallback Profile**: To guarantee backward compatibility across networks containing legacy reverse proxies, CDNs, or strict API gateways that reject non-standard 4xx status codes:
  - An xHTTP edge server **MAY emit standard `HTTP/1.1 429 Too Many Requests` or `HTTP/1.1 503 Service Unavailable`** as a transport fallback.
  - When operating in fallback mode, the server **MUST** include the capability discriminator header `X-xHTTP-Profile: patience-v0.1` (or `X-xHTTP-Status: 420`), the standard `Retry-After: <seconds>` header, and `Content-Type: application/xhttp+json`.
  - The response body MUST retain the full, valid `xhttp-420-patience` JSON envelope.
  - xHTTP-aware client agents inspect the `Content-Type: application/xhttp+json` header and `proto: "xhttp-420-patience"` body envelope, engaging the full backpressure negotiation protocol regardless of whether the transport status code was `420`, `429`, or `503`.
* **Clock Synchronization**: The `valid_until` timestamp assumes NTP synchronization across fleet nodes ($\pm 500\text{ ms}$). If client and server clocks diverge significantly, clients must prioritize relative `estimated_wait_ms` over absolute timestamps.
* **Proxy Timeout Windows**: Long `estimated_wait_ms` values (e.g. $> 120\text{ seconds}$) may exceed intermediate edge gateway read timeouts (such as Cloudflare's default 100s timeout). In such scenarios, servers should recommend asynchronous delegation (`DELEGATE`) or initiate an asynchronous task returning `202 Accepted`.
