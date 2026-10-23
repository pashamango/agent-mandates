# Agent Mandates & xHTTP Open Standards

**Canonical Repository for Autonomous Agent Identity, Delegation Mandates, and xHTTP Edge Standards.**

## Overview

This repository hosts:
1. **Agent Mandates & Protocol Phi (Φ-0.1)**: Cryptographic identity delegations, goDNS trust anchors, and agent-to-agent task handoff standards.
2. **xHTTP Open Standard Suite (v0.1.0-draft)**: Open protocols for the autonomous agent edge web:
   * **[`x401` Multipass Authentication Challenge](file:///Users/pavelcherkashin/ChitAkasha-Wolfram/worktrees/MIN-148-XHTTP-SPECS-0001/xhttp-standards/specs/x401-multipass-auth.md)**: Proof-of-possession Ed25519 authentication anchored in DNS TXT records.
   * **[`x404` Generative 404 & Semantic Hash](file:///Users/pavelcherkashin/ChitAkasha-Wolfram/worktrees/MIN-148-XHTTP-SPECS-0001/xhttp-standards/specs/x404-generative.md)**: On-demand edge synthesis work orders with `SemHash` cache deduplication.
   * **[`x410` Revocation with Certificate](file:///Users/pavelcherkashin/ChitAkasha-Wolfram/worktrees/MIN-148-XHTTP-SPECS-0001/xhttp-standards/specs/x410-revocation.md)**: Immutable Ed25519-signed revocation tombstones and non-repudiation proofs.
   * **[`x420` Patience Protocol](file:///Users/pavelcherkashin/ChitAkasha-Wolfram/worktrees/MIN-148-XHTTP-SPECS-0001/xhttp-standards/specs/x420-patience.md)**: Structured backpressure negotiation with queue telemetry and `WAIT`, `PAY`, `DELEGATE`, `OUR_LADY`, `FSCK_OFF` actions.
   * **[`/.well-known/xhttp`](file:///Users/pavelcherkashin/ChitAkasha-Wolfram/worktrees/MIN-148-XHTTP-SPECS-0001/xhttp-standards/WELL-KNOWN.md)**: Canonical edge capability discovery endpoint.

## Specifications Index

Explore the complete specifications suite in the [`xhttp-standards/`](file:///Users/pavelcherkashin/ChitAkasha-Wolfram/worktrees/MIN-148-XHTTP-SPECS-0001/xhttp-standards/) directory:
* [xHTTP Suite Overview & Index](file:///Users/pavelcherkashin/ChitAkasha-Wolfram/worktrees/MIN-148-XHTTP-SPECS-0001/xhttp-standards/README.md)
* [Public Announcement](file:///Users/pavelcherkashin/ChitAkasha-Wolfram/worktrees/MIN-148-XHTTP-SPECS-0001/xhttp-standards/ANNOUNCEMENT.md)
* [Unified Glossary & SemVer Changelog](file:///Users/pavelcherkashin/ChitAkasha-Wolfram/worktrees/MIN-148-XHTTP-SPECS-0001/xhttp-standards/GLOSSARY.md)
* [Well-Known Discovery](file:///Users/pavelcherkashin/ChitAkasha-Wolfram/worktrees/MIN-148-XHTTP-SPECS-0001/xhttp-standards/WELL-KNOWN.md)

## Verification & Testing

```bash
# Run the formal test suite
python3 -m unittest discover -s tests -p 'test*.py'
```

## License

This standard and reference implementation are released under the [MIT License](file:///Users/pavelcherkashin/ChitAkasha-Wolfram/worktrees/MIN-148-XHTTP-SPECS-0001/LICENSE).
