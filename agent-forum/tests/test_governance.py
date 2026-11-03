import pytest
from src.governance import CanonChangeError, Constitution, JudgeBoard, RTFMCertifier, ReputationLedger

def test_reputation_is_verified_and_never_authority():
    ledger = ReputationLedger()
    assert ledger.record_unique_question("agy@wolfram") == 1
    ledger.decay()
    assert ledger.scores["agy@wolfram"] == 0
    assert not ledger.can_authorize("agy@wolfram", "merge")

def test_rtfm_routes_three_cases():
    certifier = RTFMCertifier()
    assert certifier.classify(.9).route == "redirect"
    assert certifier.classify(.1).route == "route_elsewhere"
    result = certifier.classify(.5)
    assert result.route == "certify" and result.bonus_eligible

def test_constitution_is_append_only_until_human_tap():
    canon = Constitution("v1")
    with pytest.raises(CanonChangeError): canon.propose("v2", compatibility_ok=False)
    assert canon.text == "v1"
    canon.apply_human_tap("v2")
    assert canon.text == "v2" and len(canon.history) == 2

def test_judge_board_escalation_requires_pavel_and_records_commit():
    board = JudgeBoard()
    with pytest.raises(PermissionError): board.resolve(7, "maintainer", "abc", escalated=True)
    resolution = board.resolve(7, "accepted", "abc", escalated=True, pavel_tap=True)
    assert resolution.resolution_commit == "abc" and resolution.appealable
