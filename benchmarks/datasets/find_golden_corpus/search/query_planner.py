"""Orders candidate documents for a lookup."""


def rank_candidate_documents(candidates, weights):
    return sorted(candidates, key=lambda c: weights.get(c, 0), reverse=True)
