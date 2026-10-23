# MIN-137 Technical Migration Audit & Architecture Specification
**Title:** Migration from `vestor.in` to `mindrock.capital`  
**Date:** 2026-10-23  
**Status:** Canonical / Ready for Independent QA  
**Authority:** Principal Pavel Cherkashin (`pavel@mindrock.capital`)  
**Tracking Ticket:** [Linear MIN-137](https://linear.app/mindrock/issue/MIN-137/critical-migration-vestorin-to-mindrockcapital)  
**Git Branch:** `minutta/min-137-critical-migration-vestorin-to-mindrockcapital`

---

## 1. Executive Context & Decision Rationale

On **2026-10-20**, Principal Pavel Cherkashin formally enacted the irrevocable deprecation and abandonment of the domain `vestor.in`.

### Threat & Compromise Assessment: `vestor.in`
- **Registrar / Ownership:** Registered under Openprovider with third-party/squatter ownership, expiration slated for **2027-06-22**.
- **DNS Hostile Control:** Squatter controls authoritative nameservers, pointing MX records to foreign infrastructure.
- **Email Inoperability:** 
  - Inbound mail destined for `@vestor.in` never reaches Google Workspace mailboxes.
  - Outbound mail sent using `@vestor.in` identities encounters hard SPF rejection (`-all`) and complete absence of valid DKIM/DMARC alignments.
- **Strategic Decision:** Cease all operational reliance on `vestor.in` immediately. Purge all references across agent networks, credentials, DNS records, and service registries. Postpone any domain buyback attempt to post-expiration (post June 2027).

### Primary Enterprise Target: `mindrock.capital`
- **Registrar:** GoDaddy.com, LLC (Registered 2018-02-08, Registry Expiry 2027-02-08, Billing Renewal 2026-02-08).
- **DNS Management:** Cloudflare authoritative DNS (`aldo.ns.cloudflare.com`, `cortney.ns.cloudflare.com`), Zone ID `7f0ab7d25de50d4f92102156bb2a5dd6`.
- **Identity Foundation:** Google Workspace Super Admin primary account unified under `pavel@mindrock.capital`.

---

## 2. Complete Service & Login Inventory Matrix

| System / Service | Pre-Migration Identifier | Post-Migration Target | Current Operational Status | Verification Method & Notes |
|---|---|---|---|---|
| **Agent Constitution (`AGENTS.md`)** | `pavel.cherkashin@vestor.in` | `pavel@mindrock.capital` | ✅ COMPLETED | Purged in git; verified by automated codebase scanner. |
| **Google Workspace Primary** | `pavel.cherkashin@vestor.in` | `pavel@mindrock.capital` | ✅ COMPLETED | Super Admin primary email swapped; verified active. |
| **Workspace Apple ID Alias** | (None / Dead domain) | `pavel.cherkashin@mindrock.capital` | ✅ PROVISIONED | Routes to `pavel@mindrock.capital` for verification code delivery. |
| **Workspace DMARC Mailbox** | `dmarc@vestor.in` | `dmarc@mindrock.capital` | ✅ PROVISIONED | Inbound endpoint for Cloudflare DMARC aggregate reports (`rua`). |
| **Cloudflare Apex Zone** | External DNS (GoDaddy) | Zone `7f0ab7d25de50d4f92102156bb2a5dd6` | ✅ ACTIVE | NS delegated to `aldo`/`cortney.ns.cloudflare.com`. |
| **SPF Record** | `include:spf.protection.outlook.com` | `v=spf1 include:_spf.google.com -all` | ✅ DEPLOYED | Hard fail for all unauthorized hosts; Microsoft legacy removed. |
| **DMARC Record** | rua/ruf pointing to `vestor.in` | `rua=mailto:...@dmarc-reports.cloudflare.net,mailto:dmarc@mindrock.capital` | ✅ DEPLOYED | Quarantine policy (`p=quarantine`), Cloudflare DMARC management active. |
| **Secondary Zone: `vestorin.com`** | FreeDNS parking | Zone `6de3ec21775db2e33445ac33446660bd` | ✅ ACTIVE | Cloudflare NS active (confirmed 2026-10-21); mail parked safely. |
| **Secondary Zone: `mindrock.xyz`** | GoDaddy default | Zone `86abbfceb92db5abda577f4ebb64d60a` | ✅ ACTIVE | Cloudflare NS active; AWS Global Accelerator endpoints preserved. |
| **Darwin Apple ID** | `pavel.cherkashin@vestor.in` | `pavel@mindrock.capital` | 🔄 RUNBOOK READY | Runbook detailed in Section 4; pending Darwin GUI tap by Principal. |
| **Decile Hub (MIN-126)** | `pavel.cherkashin@vestor.in` | `pavel@mindrock.capital` | 🔄 RUNBOOK READY | Pending email re-verification on Decile portal. |
| **GoDaddy Billing Safeguard** | Stale payment profile | Venmo debit default | 🔄 RUNBOOK READY | Action designated for Principal to safeguard 2026-02-08 renewal. |

---

## 3. Technical DNS & Mail Routing Architecture

### A. Authoritative DNS Records (`mindrock.capital`)
All records are deployed and enforced on Cloudflare zone `7f0ab7d25de50d4f92102156bb2a5dd6`:

```dns
;; Apex & Routing Records
@                   300 IN  A       3.33.130.190
@                   300 IN  A       15.197.148.33
login               300 IN  A       15.197.142.173
login               300 IN  A       3.33.152.147

;; Google Workspace Mail Exchanger
@                   300 IN  MX  0   smtp.google.com.

;; Canonical SPF (Sender Policy Framework) - Google Workspace Exclusive
@                   300 IN  TXT     "v=spf1 include:_spf.google.com -all"

;; Canonical DMARC Policy with Dual Telemetry
_dmarc              300 IN  TXT     "v=DMARC1; p=quarantine; rua=mailto:71e800db705f4c72956d4be4975e58f9@dmarc-reports.cloudflare.net,mailto:dmarc@mindrock.capital; ruf=mailto:dmarc@mindrock.capital; fo=1;"

;; Google Site Verification
@                   300 IN  TXT     "google-site-verification=5vmCNMryG4WnmeKuSxCh0M8pf44o3cBLATdOL13iUqY"
```

### B. Invariant Checks for Mail Flow Security
1. **SPF Hard Fail Invariant:** No servers outside Google's authoritative IP blocks can emit emails purporting to be from `@mindrock.capital`.
2. **DMARC Quarantine Invariant:** Spoofed or unauthenticated emails fail DMARC and are quarantined rather than delivered to inboxes.
3. **Dead Domain Defense:** Outbound mail attempting to originate from `@vestor.in` is rejected globally due to squatter SPF mismatch; incoming mail to `@vestor.in` is completely discarded, preventing MITM credential theft.

---

## 4. Runbook: Darwin Apple ID Migration Protocol

Due to Apple security invariants, an Apple Account whose login is a non-Apple domain (`@vestor.in`) cannot be detached via web browser at `account.apple.com` without an active macOS/iOS device logged into that specific Apple ID.

### Migration Sequence:
1. **Prerequisite Verification:** Confirm that `pavel.cherkashin@mindrock.capital` exists as an active alias/mailbox in Google Workspace and delivers cleanly to `pavel@mindrock.capital`. (Tested & confirmed).
2. **Execute on MacBook Darwin:**
   - Open **System Settings** → **Apple Account** (signed in as `pavel.cherkashin@vestor.in`).
   - Navigate to **Sign-In & Security** → **Email & Phone Numbers**.
   - Click on the primary email (`pavel.cherkashin@vestor.in`) and select **Change Apple ID**.
   - Enter `pavel.cherkashin@mindrock.capital`.
   - Apple dispatches a 6-digit verification code to `pavel.cherkashin@mindrock.capital`.
   - Enter code to seal the change. *Result: Account, purchased apps, and historical data remain intact and detached from the lost domain.*
3. **Ecosystem Unification:**
   - Sign OUT of the renamed Apple ID on Darwin, choosing **"Keep a Copy"** for Contacts, Calendars, and Keychains.
   - Sign IN with the primary network Apple ID: `pavel@mindrock.capital` (unifying Darwin with MacBook Wolfram).
   - Verify shared iCloud, Handoff, and Lukiana Family Storage pooling.

---

## 5. Automated Verification & Test Framework

To guarantee continuous compliance with the Mindrock Constitution and prevent regression of `vestor.in` dependencies, an automated verification suite is included:
- **`min137/migration_verifier.py`**: Programmatic verifier enforcing zero `vestor.in` substrings in active files, validating DNS schema syntax, and verifying inventory integrity.
- **`min137/test_migration.py`**: Regression unit test suite (100% green).
- **`tests/test_min137_migration.py`**: Root test discovery bridge for pytest/unittest.

---

## 6. QA Checklist & Verification Evidence

- [x] `AGENTS.md` updated in repository branch (Principal identity strictly `pavel@mindrock.capital`).
- [x] Zero references to `vestor.in` in active configuration or source trees.
- [x] Cloudflare DNS records specified and verified against RFC standards.
- [x] Comprehensive inventory matrix mapped and recorded.
- [x] Step-by-step Apple ID and Decile migration runbooks generated.
- [x] Test suite passing with 0 failures under `python3 -m unittest`.
