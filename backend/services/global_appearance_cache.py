"""
Global Person Appearance Cache - Cross-camera re-identification helpers.

Provides fast lookup and storage for person embeddings across all cameras.
"""
import numpy as np
from datetime import datetime


class GlobalAppearanceCache:
    """In-memory cache for global person appearances."""

    def __init__(self):
        # person_id -> {"face_emb": np.array, "body_emb": np.array, "name": str, ...}
        self.cache = {}

    def add_person(self, person_id: str, name: str, face_emb=None, body_emb=None, confidence: float = 0.85):
        """Add or update a person in the global cache."""
        if person_id not in self.cache:
            self.cache[person_id] = {
                "name": name,
                "face_emb": face_emb,
                "body_emb": body_emb,
                "confidence": confidence,
                "first_seen": datetime.utcnow(),
                "last_seen": datetime.utcnow(),
                "sighting_count": 1,
            }
        else:
            # Update existing
            if body_emb is not None:
                self.cache[person_id]["body_emb"] = body_emb
            if face_emb is not None:
                self.cache[person_id]["face_emb"] = face_emb
            self.cache[person_id]["last_seen"] = datetime.utcnow()
            self.cache[person_id]["sighting_count"] += 1
            self.cache[person_id]["confidence"] = max(self.cache[person_id]["confidence"], confidence)

    def find_best_match(self, body_emb: np.ndarray, threshold: float = 0.62):
        """
        Find best matching person by body embedding.
        
        Returns: (person_id, similarity_score) or (None, 0.0) if no match
        """
        if body_emb is None:
            return None, 0.0

        best_person_id = None
        best_sim = 0.0

        for person_id, data in self.cache.items():
            stored_body_emb = data.get("body_emb")
            if stored_body_emb is None:
                continue

            # Cosine similarity
            sim = float(np.dot(body_emb, stored_body_emb))
            if sim > best_sim:
                best_sim = sim
                best_person_id = person_id

        if best_sim >= threshold:
            return best_person_id, best_sim

        return None, best_sim

    def find_by_face(self, face_emb: np.ndarray, threshold: float = 0.40):
        """
        Find person by face embedding (for re-validation).
        
        Returns: (person_id, similarity_score) or (None, 0.0) if no match
        """
        if face_emb is None:
            return None, 0.0

        best_person_id = None
        best_sim = 0.0

        for person_id, data in self.cache.items():
            stored_face_emb = data.get("face_emb")
            if stored_face_emb is None:
                continue

            # Cosine similarity
            sim = float(np.dot(face_emb, stored_face_emb))
            if sim > best_sim:
                best_sim = sim
                best_person_id = person_id

        if best_sim >= threshold:
            return best_person_id, best_sim

        return None, best_sim

    def get_person(self, person_id: str):
        """Get person data from cache."""
        return self.cache.get(person_id)

    def size(self):
        """Get cache size."""
        return len(self.cache)

    def clear(self):
        """Clear all cache."""
        self.cache.clear()


# Global singleton instance
_global_cache = GlobalAppearanceCache()


def add_person_global(person_id: str, name: str, face_emb=None, body_emb=None, confidence: float = 0.85):
    """Add person to global appearance cache."""
    _global_cache.add_person(person_id, name, face_emb, body_emb, confidence)


def find_global_match(body_emb: np.ndarray, threshold: float = 0.62):
    """Find best matching person in global cache by body appearance."""
    return _global_cache.find_best_match(body_emb, threshold)


def find_global_face_match(face_emb: np.ndarray, threshold: float = 0.40):
    """Find best matching person in global cache by face."""
    return _global_cache.find_by_face(face_emb, threshold)


def get_global_person(person_id: str):
    """Get person from global cache."""
    return _global_cache.get_person(person_id)


def global_cache_size():
    """Get global cache size."""
    return _global_cache.size()


def clear_global_cache():
    """Clear global cache."""
    _global_cache.clear()
