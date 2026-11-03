# Insight: xHTTP drafts need a shared envelope contract

The four xHTTP drafts become interoperable only when the suite treats the
envelope as a protocol boundary, not as documentation decoration. A shared
`proto`, `version`, `request_id`, `issued_at`, and `verification` vocabulary
lets edge implementations route, reject, and audit messages consistently;
the protocol-specific fields then express the x401, x404, x410, or x420
mechanism without inventing a new framing model each time.

The practical consequence is that public-ready standards need two proofs at
once: examples must parse as JSON/HTTP, and the prose must state what happens
when cryptographic, timing, DNS, queue, or capability assumptions fail. Honest
interop limits are part of the mechanism, not an appendix.
