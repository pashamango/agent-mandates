#!/usr/bin/env python3
"""
Unit test suite for xHTTP open standard drafts (MIN-148).
Verifies:
1. All 8 canonical documents exist and are non-empty.
2. Every JSON envelope, schema, and response example in all markdown files parses cleanly.
3. Acceptance Gates G1-G4 conformance:
   - G1: Envelope formats, request/response examples, ASCII flow diagrams.
   - G2: Security Considerations and Interop notes in every spec.
   - G3: Unified glossary and SemVer v0.1 versioning + changelog.
   - G4: Public repository README and announcement text.
4. Specific constraints:
   - x420 contains WAIT, PAY, DELEGATE, OUR_LADY, and FSCK_OFF (File System Check Off).
   - x401 contains DNS TXT goDNS verification.
   - x404 contains Semantic Hash (SemHash).
   - x410 contains Ed25519 revocation certificate and reason codes.
"""

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


class TestXHTTPSpecifications(unittest.TestCase):

    def test_required_files_exist(self):
        """Verify all 8 files checked by Codex QA exist and are substantial."""
        for rel_path in REQUIRED_FILES:
            full_path = os.path.join(ROOT_DIR, rel_path)
            self.assertTrue(os.path.isfile(full_path), f"Missing file: {rel_path}")
            size = os.path.getsize(full_path)
            self.assertGreater(size, 1000, f"File {rel_path} is too small ({size} bytes)")

    def test_json_blocks_in_markdown_parse_validly(self):
        """Extract and validate all JSON and HTTP-embedded JSON code blocks."""
        json_block_regex = re.compile(r"```json\s*\n(.*?)\n```", re.DOTALL)
        http_block_regex = re.compile(r"```http\s*\n(.*?)\n```", re.DOTALL)
        total_parsed = 0

        for root, _, files in os.walk(STANDARDS_DIR):
            for fname in files:
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
                        total_parsed += 1
                    except json.JSONDecodeError as e:
                        self.fail(f"Invalid JSON block #{idx+1} in {fname}: {e}\nContent:\n{block}")

                # Validate JSON payloads inside ```http response blocks
                for idx, block in enumerate(http_block_regex.findall(content)):
                    if "\n\n{" in block:
                        json_part = "{" + block.split("\n\n{", 1)[1]
                        try:
                            parsed = json.loads(json_part)
                            self.assertIsInstance(parsed, (dict, list))
                            total_parsed += 1
                        except json.JSONDecodeError as e:
                            self.fail(f"Invalid HTTP-embedded JSON in {fname}: {e}\nContent:\n{json_part}")

        # Total JSON schemas and examples across specs should be >= 10
        self.assertGreaterEqual(total_parsed, 10, f"Expected >= 10 JSON payloads, found {total_parsed}")

    def test_gate_g1_flow_diagrams_and_envelopes(self):
        """Verify Gate G1: flow diagrams and envelope formats present in all specs."""
        for spec_file in ["x401-multipass-auth.md", "x404-generative.md", "x410-revocation.md", "x420-patience.md"]:
            path = os.path.join(SPECS_DIR, spec_file)
            with open(path, "r", encoding="utf-8") as f:
                content = f.read()

            self.assertIn("Sequence Diagram", content, f"Missing Sequence Diagram in {spec_file}")
            self.assertIn("Envelope Specifications & Schemas", content, f"Missing envelope schemas in {spec_file}")
            self.assertIn("```json", content, f"Missing JSON envelope definition in {spec_file}")
            self.assertIn("HTTP/1.1", content, f"Missing HTTP wire protocol example in {spec_file}")

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
            "Generative 404",
            "Semantic Hash",
            "Cryptographic Revocation Certificate",
            "Patience Protocol",
            "FSCK_OFF",
            "File System Check Off",
            "Semantic Versioning 2.0.0",
            "Changelog",
        ]
        for term in terms:
            self.assertIn(term, content, f"Missing term/section '{term}' in GLOSSARY.md")

    def test_gate_g4_readme_and_announcement(self):
        """Verify Gate G4: Public repository README and announcement text."""
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

    def test_x401_godns_mechanics(self):
        """Verify x401 details: goDNS, Ed25519 PoP, DNS TXT, nonce verification."""
        path = os.path.join(SPECS_DIR, "x401-multipass-auth.md")
        with open(path, "r", encoding="utf-8") as f:
            content = f.read()

        self.assertIn("goDNS", content)
        self.assertIn("_mandate.", content)
        self.assertIn("Proof of Possession", content)
        self.assertIn("WWW-Authenticate: Multipass", content)
        self.assertIn("DNSSEC", content)

    def test_x404_semhash_and_oracle(self):
        """Verify x404 details: Semantic Hash, verification oracle, work order."""
        path = os.path.join(SPECS_DIR, "x404-generative.md")
        with open(path, "r", encoding="utf-8") as f:
            content = f.read()

        self.assertIn("SemHash", content)
        self.assertIn("verification_oracle", content)
        self.assertIn("synthesis_context", content)
        self.assertIn("max_token_budget", content)

    def test_x410_revocation_tombstones(self):
        """Verify x410 details: reason codes, certificate, non-repudiation."""
        path = os.path.join(SPECS_DIR, "x410-revocation.md")
        with open(path, "r", encoding="utf-8") as f:
            content = f.read()

        self.assertIn("COMPROMISED", content)
        self.assertIn("MANDATE_EXPIRED", content)
        self.assertIn("original_content_sha256", content)
        self.assertIn("Non-Repudiation", content)

    def test_x420_patience_actions(self):
        """Verify x420 details: WAIT, PAY, DELEGATE, OUR_LADY, and FSCK_OFF."""
        path = os.path.join(SPECS_DIR, "x420-patience.md")
        with open(path, "r", encoding="utf-8") as f:
            content = f.read()

        self.assertIn('"WAIT"', content)
        self.assertIn('"PAY"', content)
        self.assertIn('"DELEGATE"', content)
        self.assertIn('"OUR_LADY"', content)
        self.assertIn('"FSCK_OFF"', content)
        self.assertIn("File System Check Off", content)
        self.assertIn("queue_metrics", content)
        self.assertIn("avg_service_time_ms", content)


if __name__ == "__main__":
    unittest.main()
