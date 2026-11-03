"""AGENTS.git governance primitives for Agent Forum v0.2."""
from dataclasses import dataclass, field
from hashlib import sha256
from typing import Dict, List

class CanonChangeError(ValueError):
    pass

@dataclass
class ReputationLedger:
    scores: Dict[str, int] = field(default_factory=dict)
    unique_questions: Dict[str, int] = field(default_factory=dict)
    def record_verified_outcome(self, agent: str, points: int = 1) -> int:
        if points < 0: raise ValueError("verified outcomes cannot subtract reputation")
        self.scores[agent] = self.scores.get(agent, 0) + points
        return self.scores[agent]
    def record_unique_question(self, agent: str, bonus: int = 1) -> int:
        self.unique_questions[agent] = self.unique_questions.get(agent, 0) + bonus
        return self.record_verified_outcome(agent, bonus)
    def decay(self, amount: int = 1) -> None:
        if amount < 0: raise ValueError("decay must be non-negative")
        for agent in self.scores: self.scores[agent] = max(0, self.scores[agent] - amount)
    def can_authorize(self, _agent: str, _action: str) -> bool: return False

@dataclass(frozen=True)
class RTFMResult:
    classification: str
    route: str
    bonus_eligible: bool = False

class RTFMCertifier:
    def classify(self, similarity: float, connected: bool = True) -> RTFMResult:
        if not 0 <= similarity <= 1: raise ValueError("similarity must be between 0 and 1")
        if similarity >= .85: return RTFMResult("near-duplicate", "redirect")
        if similarity < .20 or not connected: return RTFMResult("orthogonal", "route_elsewhere")
        return RTFMResult("novel-but-connected", "certify", True)

class Constitution:
    def __init__(self, text: str):
        self._text = text
        self.history: List[str] = [sha256(text.encode()).hexdigest()]
    @property
    def text(self) -> str: return self._text
    def propose(self, text: str, compatibility_ok: bool = True) -> str:
        if not compatibility_ok: raise CanonChangeError("proposal requires escalation/appeal")
        return sha256(text.encode()).hexdigest()
    def apply_human_tap(self, text: str, compatibility_ok: bool = True) -> str:
        digest = self.propose(text, compatibility_ok)
        self._text, self.history = text, self.history + [digest]
        return digest

@dataclass(frozen=True)
class Resolution:
    issue_id: int
    decision: str
    resolution_commit: str
    appealable: bool = True

class JudgeBoard:
    def resolve(self, issue_id: int, decision: str, resolution_commit: str,
                escalated: bool = False, pavel_tap: bool = False) -> Resolution:
        if escalated and not pavel_tap: raise PermissionError("escalated resolution requires Pavel tap")
        return Resolution(issue_id, decision, resolution_commit)
