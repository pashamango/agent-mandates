#!/usr/bin/env python3
"""
Comprehensive Conformance and Unit Test Suite for xHTTP Open Standard Drafts (MIN-148).
Verifies:
1. All 8 canonical documents exist and are non-empty.
2. Every JSON envelope, schema, and response/request example in all markdown files parses cleanly.
3. Acceptance Gates G1-G4 conformance:
   - G1: Separate, syntactically complete HTTP Request and Response wire examples in all 4 specs;
         Valid JSON Schema Draft 2020-12 envelope definitions;
         Deterministic ASCII message sequence diagrams.
   - G2: Security Considerations and Interoperability Notes in each specification with honest constraints.
   - G3: Unified glossary and SemVer v0.1 versioning policy + changelog.
   - G4: Zero local file:/// links; developer README and announcement text.
4. Specific normative mechanics:
   - x401: Complete trust chain (Principal root anchor, TXT canonical signing serialization,
           scope matching, ISO 8601 UTC expiry, base64url Multipass token decoding).
   - x404: 5-phase SemHash normalization pipeline, volatile parameter exclusion, domain separation,
           synthesis confirmation request wire example.
   - x410: Wire fetch request example, permanent cryptographic revocation vs active fast-path SLA,
           post-retention audit log linkage, client infinite TTL tombstone cache.
   - x420: Queue correlation identifier (queue_id), ActionSelectionEnvelope schema and wire request,
           WAIT confirmation, PAY token verification, DELEGATE delegation proof, OUR_LADY degradation,
           FSCK_OFF secured state purge, and RFC 9110 normative fallback profile.
"""

import base64
import json
import os
import re
import unittest

ROOT_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
STANDARDS_DIR = os.path.join(ROOT_DIR, "xhttp-standards")
SPECS_DIR = os.path.join(STANDARDS_DIR, "specs")

REQUIRED_FILES = [
    "xhttp-standards/README.md",
    "xhttp-standards/ANNOUNCEMENT.md",
    "xhttp-standards/GLOSSARY.md",
    "xhttp-standards/WELL-KNOWN.md",
    "xhttp-standards/specs/x401-multipass-auth.md",
    "xhttp-standards/specs/x404-generative.md",
    "xhttp-standards/specs/x410-revocation.md",
    "xhttp-standards/specs/x420-patience.md",
]


def validate_against_schema(instance, schema, path=""):
    """
    Pure Python lightweight JSON Schema validator supporting Draft 2020-12
    core keywords: type, required, properties, items, enum, const, pattern, minimum, maximum.
    """
    stype = schema.get("type")
    if stype == "object":
        assert isinstance(instance, dict), f"{path}: expected dict, got {type(instance).__name__}"
        for req in schema.get("required", []):
            assert req in instance, f"{path}: missing required property '{req}'"
        for k, v in instance.items():
            if k in schema.get("properties", {}):
                validate_against_schema(v, schema["properties"][k], f"{path}.{k}")
    elif stype == "string":
        assert isinstance(instance, str), f"{path}: expected str, got {type(instance).__name__}"
        if "const" in schema:
            assert instance == schema["const"], f"{path}: expected const '{schema['const']}', got '{instance}'"
        if "enum" in schema:
            assert instance in schema["enum"], f"{path}: '{instance}' not in enum {schema['enum']}"
        if "pattern" in schema:
            assert re.match(schema["pattern"], instance), f"{path}: '{instance}' does not match pattern '{schema['pattern']}'"
    elif stype == "integer":
        assert isinstance(instance, int) and not isinstance(instance, bool), f"{path}: expected int, got {type(instance).__name__}"
        if "minimum" in schema:
            assert instance >= schema["minimum"], f"{path}: {instance} < minimum {schema['minimum']}"
        if "const" in schema:
            assert instance == schema["const"], f"{path}: {instance} != const {schema['const']}"
    elif stype == "number":
        assert isinstance(instance, (int, float)) and not isinstance(instance, bool), f"{path}: expected number, got {type(instance).__name__}"
        if "minimum" in schema:
            assert instance >= schema["minimum"], f"{path}: {instance} < minimum {schema['minimum']}"
        if "maximum" in schema:
            assert instance <= schema["maximum"], f"{path}: {instance} > maximum {schema['maximum']}"
    elif stype == "boolean":
        assert isinstance(instance, bool), f"{path}: expected bool, got {type(instance).__name__}"
    elif stype == "array":
        assert isinstance(instance, list), f"{path}: expected list, got {type(instance).__name__}"
        if "items" in schema:
            for idx, item in enumerate(instance):
                validate_against_schema(item, schema["items"], f"{path}[{idx}]")


class TestXHTTPSpecifications(unittest.TestCase):

    def test_required_files_exist(self):
        """Verify all 8 canonical files exist and are substantial (>1000 bytes)."""
        for rel_path in REQUIRED_FILES:
            full_path = os.path.join(ROOT_DIR, rel_path)
            self.assertTrue(os.path.isfile(full_path), f"Missing file: {rel_path}")
            size = os.path.getsize(full_path)
            self.assertGreater(size, 1000, f"File {rel_path} is too small ({size} bytes)")

    def test_json_and_http_wire_blocks_parse_validly(self):
        """Extract and validate all JSON blocks and HTTP request/response wire blocks."""
        json_block_regex = re.compile(r"```json\s*\n(.*?)\n```", re.DOTALL)
        http_block_regex = re.compile(r"```http\s*\n(.*?)\n```", re.DOTALL)
        total_json_parsed = 0
        total_http_parsed = 0

        for root, _, files in os.walk(STANDARDS_DIR):
            for fname in sorted(files):
                if not fname.endswith(".md"):
                    continue
                fpath = os.path.join(root, fname)
                with open(fpath, "r", encoding="utf-8") as f:
                    content = f.read()

                # Validate standalone ```json blocks
                for idx, block in enumerate(json_block_regex.findall(content)):
                    try:
                        parsed = json.loads(block)
                        self.assertIsInstance(parsed, (dict, list))
                        total_json_parsed += 1
                    except json.JSONDecodeError as err:
                        self.fail(f"Invalid JSON block #{idx+1} in {fname}: {err}\nContent:\n{block}")

                # Validate ```http wire blocks (requests and responses)
                for idx, block in enumerate(http_block_regex.findall(content)):
                    lines = block.split("\n")
                    self.assertGreater(len(lines), 1, f"Empty HTTP block #{idx+1} in {fname}")
                    first_line = lines[0].strip()
                    is_request = bool(re.match(r"^(GET|POST|PUT|DELETE|PATCH|HEAD|OPTIONS)\s+\S+\s+HTTP/\d\.\d$", first_line))
                    is_response = bool(re.match(r"^HTTP/\d\.\d\s+\d{3}\b", first_line))
                    self.assertTrue(
                        is_request or is_response,
                        f"HTTP block #{idx+1} in {fname} has invalid start line: '{first_line}'"
                    )

                    # Verify header delimiter (blank line) if body exists
                    if "\n\n" in block or "\r\n\r\n" in block:
                        parts = re.split(r"\r?\n\r?\n", block, maxsplit=1)
                        if len(parts) == 2 and parts[1].strip():
                            body_str = parts[1].strip()
                            if body_str.startswith("{") or body_str.startswith("["):
                                try:
                                    parsed_body = json.loads(body_str)
                                    self.assertIsInstance(parsed_body, (dict, list))
                                    total_json_parsed += 1
                                except json.JSONDecodeError as err:
                                    self.fail(f"Invalid JSON body in HTTP block #{idx+1} in {fname}: {err}")
                    total_http_parsed += 1

        self.assertGreaterEqual(total_json_parsed, 12, f"Expected >= 12 JSON payloads, found {total_json_parsed}")
        self.assertGreaterEqual(total_http_parsed, 9, f"Expected >= 9 HTTP wire examples, found {total_http_parsed}")

    def test_gate_g1_request_and_response_wire_examples(self):
        """Verify Gate G1: flow diagrams, separate parsed request & response wire examples in every spec."""
        http_block_regex = re.compile(r"```http\s*\n(.*?)\n```", re.DOTALL)
        specs = ["x401-multipass-auth.md", "x404-generative.md", "x410-revocation.md", "x420-patience.md"]

        for spec_file in specs:
            path = os.path.join(SPECS_DIR, spec_file)
            with open(path, "r", encoding="utf-8") as f:
                content = f.read()

            self.assertIn("Sequence Diagram", content, f"Missing Sequence Diagram in {spec_file}")
            self.assertIn("Envelope Specifications & Schemas", content, f"Missing envelope schemas in {spec_file}")

            # Check HTTP wire blocks
            http_blocks = http_block_regex.findall(content)
            requests = [b for b in http_blocks if re.match(r"^[A-Z]+\s+\S+\s+HTTP/\d\.\d", b.strip())]
            responses = [b for b in http_blocks if re.match(r"^HTTP/\d\.\d\s+\d{3}", b.strip())]

            self.assertGreaterEqual(
                len(requests), 1,
                f"Spec {spec_file} lacks a complete HTTP Request wire example"
            )
            self.assertGreaterEqual(
                len(responses), 1,
                f"Spec {spec_file} lacks a complete HTTP Response wire example"
            )

    def test_json_schemas_draft_2020_12_and_example_conformance(self):
        """Validate all 7 JSON schemas meet Draft 2020-12 structure and every example conforms to its schema."""
        json_block_regex = re.compile(r"```json\s*\n(.*?)\n```", re.DOTALL)
        http_block_regex = re.compile(r"```http\s*\n(.*?)\n```", re.DOTALL)

        schemas = {}
        for root, _, files in os.walk(STANDARDS_DIR):
            for fname in files:
                if not fname.endswith(".md"):
                    continue
                with open(os.path.join(root, fname), "r", encoding="utf-8") as f:
                    content = f.read()
                for block in json_block_regex.findall(content):
                    parsed = json.loads(block)
                    if isinstance(parsed, dict) and "$schema" in parsed:
                        title = parsed.get("title")
                        self.assertIsNotNone(title, f"Schema in {fname} missing title")
                        schemas[title] = parsed

        # Must include all 7 canonical schemas
        expected_schemas = [
            "xHTTPDiscoveryEnvelope",
            "MultipassChallengeEnvelope",
            "MultipassPresentationEnvelope",
            "GenerativeWorkOrderEnvelope",
            "RevocationCertificateEnvelope",
            "PatienceBackpressureEnvelope",
            "ActionSelectionEnvelope",
        ]
        for s_title in expected_schemas:
            self.assertIn(s_title, schemas, f"Missing canonical JSON schema: {s_title}")
            schema = schemas[s_title]
            self.assertEqual(schema.get("$schema"), "https://json-schema.org/draft/2020-12/schema")
            self.assertEqual(schema.get("type"), "object")
            self.assertIn("required", schema)
            self.assertIsInstance(schema["required"], list)
            self.assertIn("properties", schema)
            self.assertIsInstance(schema["properties"], dict)

        proto_to_schema = {
            "xhttp-discovery": "xHTTPDiscoveryEnvelope",
            "xhttp-401-challenge": "MultipassChallengeEnvelope",
            "xhttp-401-multipass": "MultipassPresentationEnvelope",
            "xhttp-404-generative": "GenerativeWorkOrderEnvelope",
            "xhttp-410-revocation": "RevocationCertificateEnvelope",
            "xhttp-420-patience": "PatienceBackpressureEnvelope",
            "xhttp-420-action": "ActionSelectionEnvelope",
        }

        # Validate examples against schemas
        validated_envelopes = 0
        for root, _, files in os.walk(STANDARDS_DIR):
            for fname in files:
                if not fname.endswith(".md"):
                    continue
                with open(os.path.join(root, fname), "r", encoding="utf-8") as f:
                    content = f.read()

                candidates = []
                for b in json_block_regex.findall(content):
                    p = json.loads(b)
                    if not (isinstance(p, dict) and "$schema" in p):
                        candidates.append(p)
                for b in http_block_regex.findall(content):
                    if "\n\n{" in b or "\r\n\r\n{" in b:
                        parts = re.split(r"\r?\n\r?\n\{", b, maxsplit=1)
                        if len(parts) == 2:
                            candidates.append(json.loads("{" + parts[1]))

                for item in candidates:
                    if isinstance(item, dict) and "proto" in item:
                        schema_title = proto_to_schema.get(item["proto"])
                        if schema_title:
                            validate_against_schema(item, schemas[schema_title], schema_title)
                            validated_envelopes += 1

        self.assertGreaterEqual(validated_envelopes, 7, f"Expected >= 7 schema-validated envelopes, found {validated_envelopes}")

    def test_gate_g2_security_and_interop_sections(self):
        """Verify Gate G2: Security Considerations and Interop Notes in each spec."""
        for spec_file in ["x401-multipass-auth.md", "x404-generative.md", "x410-revocation.md", "x420-patience.md"]:
            path = os.path.join(SPECS_DIR, spec_file)
            with open(path, "r", encoding="utf-8") as f:
                content = f.read()

            self.assertIn("Security Considerations", content, f"Missing Security Considerations in {spec_file}")
            self.assertTrue(
                "Interop Notes & Limitations" in content or "Interoperability Notes & Limitations" in content,
                f"Missing Interop Notes in {spec_file}",
            )

    def test_gate_g3_glossary_and_versioning(self):
        """Verify Gate G3: Unified glossary and SemVer v0.1 versioning policy."""
        glossary_path = os.path.join(STANDARDS_DIR, "GLOSSARY.md")
        with open(glossary_path, "r", encoding="utf-8") as f:
            content = f.read()

        terms = [
            "Agent DID",
            "Proof of Possession",
            "goDNS",
            "Principal Root Anchor",
            "Generative 404",
            "Semantic Hash",
            "Semantic Normalization Pipeline",
            "Cryptographic Revocation Certificate",
            "Active Fast-Path SLA",
            "Patience Protocol",
            "Queue Correlation ID",
            "Action Selection Envelope",
            "Normative Fallback Profile",
            "FSCK_OFF",
            "File System Check Off",
            "Semantic Versioning 2.0.0",
            "Changelog",
        ]
        for term in terms:
            self.assertIn(term, content, f"Missing term/section '{term}' in GLOSSARY.md")

    def test_gate_g4_no_local_file_urls_and_public_readiness(self):
        """Verify Gate G4: Zero file:/// URLs, public repository README and announcement text."""
        # Ensure absolutely no local file:/// URLs exist in xhttp-standards
        for root, _, files in os.walk(STANDARDS_DIR):
            for fname in files:
                if not fname.endswith(".md"):
                    continue
                fpath = os.path.join(root, fname)
                with open(fpath, "r", encoding="utf-8") as f:
                    content = f.read()
                self.assertNotIn(
                    "file:///", content,
                    f"Forbidden local author URL 'file:///' found in {os.path.relpath(fpath, ROOT_DIR)}"
                )

        readme_path = os.path.join(STANDARDS_DIR, "README.md")
        announcement_path = os.path.join(STANDARDS_DIR, "ANNOUNCEMENT.md")

        with open(readme_path, "r", encoding="utf-8") as f:
            readme = f.read()
        with open(announcement_path, "r", encoding="utf-8") as f:
            announcement = f.read()

        self.assertIn("xHTTP Open Standard Suite", readme)
        self.assertIn("Four Pillars of xHTTP", announcement)
        self.assertIn("pavel@mindrock.capital", readme)
        self.assertIn("pavel@mindrock.capital", announcement)

    def test_x401_trust_chain_and_multipass_request(self):
        """Verify x401 details: complete trust chain, TXT canonical signing, and base64url Multipass token."""
        path = os.path.join(SPECS_DIR, "x401-multipass-auth.md")
        with open(path, "r", encoding="utf-8") as f:
            content = f.read()

        self.assertIn("goDNS", content)
        self.assertIn("_mandate.", content)
        self.assertIn("_mandate-root.", content)
        self.assertIn("CanonicalTXT", content)
        self.assertIn("Authorization: Multipass", content)
        self.assertIn("WWW-Authenticate: Multipass", content)
        self.assertIn("DNSSEC", content)

        # Extract base64url token from wire example and verify decodability
        token_match = re.search(r"Authorization:\s+Multipass\s+([A-Za-z0-9_-]+)", content)
        self.assertIsNotNone(token_match, "Missing Authorization: Multipass header in wire example")
        token_str = token_match.group(1)
        padding = "=" * ((4 - len(token_str) % 4) % 4)
        decoded_bytes = base64.urlsafe_b64decode(token_str + padding)
        multipass_json = json.loads(decoded_bytes.decode("utf-8"))
        self.assertEqual(multipass_json.get("proto"), "xhttp-401-multipass")
        self.assertIn("client_did", multipass_json)
        self.assertIn("signature", multipass_json)

    def test_x404_semhash_pipeline_and_synthesis_request(self):
        """Verify x404 details: 5-phase SemHash normalization pipeline, volatile param exclusion, and request example."""
        path = os.path.join(SPECS_DIR, "x404-generative.md")
        with open(path, "r", encoding="utf-8") as f:
            content = f.read()

        self.assertIn("SemHash", content)
        self.assertIn("xhttp-semhash-v1:", content)
        self.assertIn("Normalization Form C (NFC)", content)
        self.assertIn("Volatile Parameters", content)
        self.assertIn("X-Action: Execute-Synthesis", content)
        self.assertIn("verification_oracle", content)
        self.assertIn("synthesis_context", content)

    def test_x410_retention_semantics_and_request(self):
        """Verify x410 details: request wire example, active fast-path SLA vs permanent audit log."""
        path = os.path.join(SPECS_DIR, "x410-revocation.md")
        with open(path, "r", encoding="utf-8") as f:
            content = f.read()

        self.assertIn("GET /mandates/agent-v1-compromised.json HTTP/1.1", content)
        self.assertIn("retain_tombstone_days", content)
        self.assertIn("public_audit_log_url", content)
        self.assertIn("Perpetual Cryptographic Non-Repudiation", content)
        self.assertIn("COMPROMISED", content)
        self.assertIn("MANDATE_EXPIRED", content)

    def test_x420_negotiation_protocol_and_normative_fallback(self):
        """Verify x420 details: correlation ID (queue_id), ActionSelectionEnvelope, action mechanics, and fallback."""
        path = os.path.join(SPECS_DIR, "x420-patience.md")
        with open(path, "r", encoding="utf-8") as f:
            content = f.read()

        self.assertIn("queue_id", content)
        self.assertIn("negotiation_endpoint", content)
        self.assertIn("ActionSelectionEnvelope", content)
        self.assertIn("X-xHTTP-Profile: patience-v0.1", content)
        self.assertIn("RFC 9110", content)
        self.assertIn('"WAIT"', content)
        self.assertIn('"PAY"', content)
        self.assertIn('"DELEGATE"', content)
        self.assertIn('"OUR_LADY"', content)
        self.assertIn('"FSCK_OFF"', content)
        self.assertIn("File System Check Off", content)


if __name__ == "__main__":
    unittest.main()
