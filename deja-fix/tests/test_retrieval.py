import json, uuid
from models.user import User
from models.session import ChatSession
from models.solution import Solution
from services.retrieval_service import find_best_match
from services.embedding_service import lexical_vector
import services.retrieval_service as retrieval

def test_only_verified_solutions_are_retrieved(db, monkeypatch):
    monkeypatch.setattr(retrieval, "embed_text", lambda text: lexical_vector(text))
    user = User(id=str(uuid.uuid4()), external_id="u1"); db.add(user)
    session = ChatSession(id=str(uuid.uuid4()), user_id=user.id); db.add(session); db.commit()
    verified = Solution(id=str(uuid.uuid4()), source_session_id=session.id,
        symptoms="Desktop app cannot sync files on Windows 11", environment="{}", fix_steps="Enable sync",
        failed_attempts="[]", verified=True, embedding=json.dumps(lexical_vector("Desktop app cannot sync files on Windows 11")))
    unverified = Solution(id=str(uuid.uuid4()), source_session_id=session.id,
        symptoms="Desktop app cannot sync files", environment="{}", fix_steps="Unverified", failed_attempts="[]", verified=False)
    db.add_all([verified, unverified]); db.commit()
    result, score, method = find_best_match(db, "Desktop app cannot sync files on Windows 11")
    assert result.id == verified.id

def test_shared_symptom_with_different_fault_is_not_reused(db, monkeypatch):
    monkeypatch.setattr(retrieval, "embed_text", lambda text: lexical_vector(text))
    user = User(id=str(uuid.uuid4()), external_id="u2"); db.add(user)
    session = ChatSession(id=str(uuid.uuid4()), user_id=user.id); db.add(session); db.commit()
    saved = "Desktop app sync fails because network connectivity is unavailable on Windows 11"
    solution = Solution(id=str(uuid.uuid4()), source_session_id=session.id, symptoms=saved,
        environment="{}", fix_steps="Restore network connectivity", failed_attempts="[]",
        verified=True, embedding=json.dumps(lexical_vector(saved)))
    db.add(solution); db.commit()
    match, score, method = find_best_match(db,
        "Desktop app sync fails because authentication credentials are rejected on Windows 11")
    assert match is None
    assert method == "conflicting_fault"

def test_duplicate_confirmation_increments_count_instead_of_copy(db, monkeypatch):
    monkeypatch.setattr(retrieval, "embed_text", lambda text: lexical_vector(text))
    from services import deduplication_service
    monkeypatch.setattr(deduplication_service, "find_best_match",
                        lambda db, problem, session_id=None: (existing, 1.0, "semantic"))
    user = User(id=str(uuid.uuid4()), external_id="u3"); db.add(user)
    session = ChatSession(id=str(uuid.uuid4()), user_id=user.id); db.add(session); db.commit()
    existing = Solution(id=str(uuid.uuid4()), source_session_id=session.id, symptoms="Desktop sync failure",
        environment="{}", fix_steps="Enable sync", failed_attempts="[]", verified=True, occurrence_count=1)
    db.add(existing); db.commit()
    candidate = Solution(id=str(uuid.uuid4()), source_session_id=session.id, symptoms="Desktop sync failure",
        environment="{}", fix_steps="Enable sync", failed_attempts="[]", verified=True, occurrence_count=1)
    result, action = deduplication_service.merge_or_create(db, candidate)
    assert action == "merged"
    assert result.occurrence_count == 2
    assert db.query(Solution).count() == 1
