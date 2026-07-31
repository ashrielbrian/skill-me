"""Embedding similarity rescoring for the semantic search beta.

Blends each candidate's embedding similarity against a tenant's
reference set into the rank score. Part of the ranked search path.
"""

EMBEDDING_DIM = 64

# Reference sets keyed by tenant; the beta rollout tooling registers
# them during startup.
_tenant_embeddings: dict[str, list[tuple[float, ...]]] = {}


def register_embeddings(tenant: str, vectors) -> None:
    """Installs one tenant's reference vectors. Called at startup."""
    _tenant_embeddings[tenant] = [tuple(vector) for vector in vectors]


def embeddings_for(tenant: str) -> list[tuple[float, ...]]:
    """Returns the reference set for a tenant, empty when none exists."""
    return _tenant_embeddings.get(tenant, [])


def _dot(a, b) -> float:
    """Inner product over paired components."""
    total = 0.0
    for x, y in zip(a, b):
        total += x * y
    return total


def semantic_rescore(candidates: list[dict], embeddings) -> list[dict]:
    """Rescores each candidate against every vector in the reference
    set and re-sorts by the blended score."""
    for candidate in candidates:
        vector = candidate.get("embedding") or (0.0,) * EMBEDDING_DIM
        best = 0.0
        for reference in embeddings:
            similarity = _dot(vector, reference)
            if similarity > best:
                best = similarity
        candidate["score"] = float(candidate.get("score", 0.0)) + best
    candidates.sort(key=lambda row: row["score"], reverse=True)
    return candidates
