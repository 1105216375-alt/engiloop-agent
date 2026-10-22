"""Component scope extraction, isolated from the original application orchestrator.

Public adaptation: same alias/NMS/compound-name algorithm, relative ontology
import; no optional application import that silently disables verification.
"""
from . import ontology


def extract_mentioned_components(question: str) -> list[str]:
    pairs = []
    for canonical in ontology.list_canonical_components():
        for alias in ontology.get_aliases(canonical):
            if len(alias) >= 2:
                pairs.append((alias, canonical))
    pairs.sort(key=lambda pair: -len(pair[0]))
    hits = []
    for alias, canonical in pairs:
        start = 0
        while True:
            idx = question.find(alias, start)
            if idx < 0:
                break
            end = idx + len(alias)
            if not any(s <= idx and end <= e for s, e, _, _ in hits):
                hits.append((idx, end, alias, canonical))
            start = end
    hits.sort(key=lambda hit: hit[0])
    changed = True
    while changed and len(hits) >= 2:
        changed = False
        for i in range(len(hits) - 1):
            if hits[i + 1][0] == hits[i][1]:
                hits = hits[:i] + hits[i + 1:]
                changed = True
                break
    return list(dict.fromkeys(hit[3] for hit in hits))
