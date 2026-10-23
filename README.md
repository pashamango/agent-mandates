# Mindrock Fleet Protocol: Protocol Phi + xHTTP (MIN-188)

Canonical repository for autonomous fleet agent interaction, zero-trust Git handoffs, and identity attestation across the Mindrock Swarm (`wolfram`, `darwin`, and cloud execution nodes).

---

## 1. Specification

The canonical specification is available at:
* [`protocol/PROTOCOL_PHI_XHTTP_V0.1.md`](protocol/PROTOCOL_PHI_XHTTP_V0.1.md)

### Key Architectural Invariants:
1. **Zero-Trust Git Handoffs**: Work handoff between agents (`agy`, `codex`, `gemini`) occurs strictly through immutable Git coordinates:
   - `repo_url`: Publicly cloneable canonical repository (`https://github.com/pashamango/agent-mandates.git`).
   - `branch`: Linear ticket branch (`gitBranchName`).
   - `commit_sha`: Exact 40-character commit SHA.
   - `tree_sha`: Exact 40-character tree SHA.
   - `manifest_sha256`: Checksum of `evidence/MANIFEST.sha256`.
2. **Workspace Isolation & Pre-Flight Gate**: The receiving node bootstraps a pristine sandbox from scratch by cloning directly from the remote Git coordinates. Reusing dirty host directories or ambient containers (the root cause of the 2026-10-22 incidents MIN-145 and MIN-160) is strictly rejected by `verify_workspace_isolation()`.
3. **Protocol Phi Envelopes**: Structured envelopes (`<!-- phi-envelope:... -->`) carry identity, task correlation, Linear state, and full 128-character Ed25519 cryptographic attestations.
4. **xHTTP Suite Integration**:
   - `goDNS`: Identity trust anchor via DNS TXT records (`_mandate.<agent>.hackt.com`).
   - `x401 (Multipass)`: Mutual proof-of-possession Ed25519 challenge-response handshake.
   - `x402 (Budget Guard)`: Hardware resource controls (90-minute wall-clock cap, weekly 50-task cap).
   - `x420 (Patience Protocol)`: Dynamic backpressure under slot contention (WIP=1).
5. **Two-Tier Governance (AGENTS.md Section 5)**:
   - Sub-issues with QA PASS are automatically promoted to `Done` by the QA agent.
   - Project Gates (`[RELEASE]`) escalate to Principal Pavel Cherkashin (`pavel@mindrock.capital`) for 1-tap approval in "My Issues".

---

## 2. Reference Implementation

* **Engine**: [`src/protocol_phi.py`](src/protocol_phi.py)
* **SSI / Ed25519 Toolkit**: [`src/ssi.py`](src/ssi.py)
* **Test Suite**: [`tests/test_protocol_phi.py`](tests/test_protocol_phi.py)
* **Constitution**: [`AGENTS.md`](AGENTS.md)

---

## 3. Independent Reproduction & Verification (Clean Clone)

To independently verify this specification and test suite in an isolated environment without relying on any host state:

```bash
# 1. Clone repository from public GitHub origin
git clone -b minutta/min-188-spec-protocol-phi-xhttp-standart-vzaimodeistviya-ahentov https://github.com/pashamango/agent-mandates.git clean-dir
cd clean-dir

# 2. Pre-flight isolation verification
test "$(git remote get-url origin)" = "https://github.com/pashamango/agent-mandates.git"
git rev-parse HEAD

# 3. Run complete test suite (16/16 PASS)
python3 -m unittest discover -s tests -p 'test*.py'

# 4. Verify working tree is clean
test -z "$(git status --short)"
```
