#!/usr/bin/env python3
"""
Unit tests for MIN-137 Migration Verification.
"""

import os
import unittest
from min137.migration_verifier import (
    verify_active_governance_files,
    verify_inventory,
    verify_dns_spec,
    run_full_audit,
    CANONICAL_INVENTORY,
    CANONICAL_DNS_RECORDS,
    DEPRECATED_DOMAIN,
    PRIMARY_DOMAIN,
)


class TestMigrationVerification(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        # Determine repo root relative to this test file
        test_dir = os.path.dirname(os.path.abspath(__file__))
        cls.repo_root = os.path.abspath(os.path.join(test_dir, ".."))

    def test_governance_agents_md_clean(self):
        """Ensure AGENTS.md does not contain any vestor.in references."""
        agents_path = os.path.join(self.repo_root, "AGENTS.md")
        self.assertTrue(os.path.exists(agents_path), f"AGENTS.md must exist at {agents_path}")
        gov = verify_active_governance_files(self.repo_root)
        self.assertTrue(gov["clean"], f"AGENTS.md contains deprecated references: {gov['violations']}")

        with open(agents_path, "r", encoding="utf-8") as f:
            content = f.read()
        self.assertIn("pavel@mindrock.capital", content)
        self.assertNotIn(DEPRECATED_DOMAIN, content)

    def test_inventory_completeness(self):
        """Validate the inventory matrix for services and identity bindings."""
        inv = verify_inventory()
        self.assertTrue(inv["all_accounted"])
        self.assertGreaterEqual(inv["total_services"], 10)

        # Check critical services
        services = {item["service"]: item for item in CANONICAL_INVENTORY}
        self.assertIn("Google Workspace Primary Account", services)
        self.assertIn("Darwin Apple ID", services)
        self.assertIn("Cloudflare DNS Apex Zone", services)
        self.assertIn("Decile Hub (MIN-126)", services)

        for name, item in services.items():
            self.assertTrue(item["verified"], f"Service {name} is not verified")
            self.assertTrue(len(item["post_migration"]) > 0)
            self.assertNotIn(DEPRECATED_DOMAIN, item["post_migration"])

    def test_dns_spf_strict_hard_fail(self):
        """Ensure SPF policy uses Google Workspace include and strict -all hard fail."""
        dns = verify_dns_spec()
        self.assertTrue(dns["spf_valid"])
        spf = CANONICAL_DNS_RECORDS["spf"]
        self.assertTrue(spf.endswith("-all"), "SPF must specify hard fail (-all)")
        self.assertIn("include:_spf.google.com", spf)
        self.assertNotIn("outlook", spf)

    def test_dns_dmarc_quarantine_policy(self):
        """Ensure DMARC policy enforces quarantine and proper rua/ruf destinations."""
        dns = verify_dns_spec()
        self.assertTrue(dns["dmarc_valid"])
        dmarc = CANONICAL_DNS_RECORDS["dmarc"]
        self.assertIn("p=quarantine", dmarc)
        self.assertIn("dmarc@mindrock.capital", dmarc)
        self.assertNotIn(DEPRECATED_DOMAIN, dmarc)

    def test_report_and_runbook_existence(self):
        """Ensure migration audit report and Darwin runbook exist and cover all requirements."""
        report_path = os.path.join(self.repo_root, "min137", "report", "MIN-137-migration-report.md")
        self.assertTrue(os.path.exists(report_path))
        with open(report_path, "r", encoding="utf-8") as f:
            report_text = f.read()
        self.assertIn("Darwin Apple ID Migration Protocol", report_text)
        self.assertIn("Complete Service & Login Inventory Matrix", report_text)
        self.assertIn("Decile Hub", report_text)
        self.assertIn("Cloudflare", report_text)

    def test_full_audit_passes(self):
        """Run full programmatic audit and verify overall pass."""
        audit = run_full_audit(self.repo_root)
        self.assertTrue(audit["overall_pass"])
        self.assertTrue(audit["governance_audit"]["clean"])
        self.assertTrue(audit["inventory_audit"]["all_accounted"])
        self.assertTrue(audit["dns_spec_audit"]["all_valid"])


if __name__ == "__main__":
    unittest.main()
