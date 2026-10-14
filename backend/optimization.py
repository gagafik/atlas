"""Per-search priorities and independently measured objective components."""
from pydantic import BaseModel, ConfigDict, Field


class Optimization(BaseModel):
    model_config = ConfigDict(extra='forbid')
    early: int = Field(default=2, ge=0, le=1000)
    stability: int = Field(default=3, ge=0, le=1000)
    preferences: int = Field(default=1, ge=0, le=1000)


def measure(state, original, target_ids, penalty, weights):
    old = {(p.workloadId,p.day,p.period,p.roomId) for p in original.placements}
    placements = [p for p in state.placements if p.workloadId in target_ids]
    early = sum(p.period for p in placements)
    changed = sum((p.workloadId,p.day,p.period,p.roomId) not in old for p in placements)
    return dict(early=early, stability=changed, preferences=penalty, placed=len(placements),
                total=early*weights.early+changed*weights.stability+penalty*weights.preferences)
