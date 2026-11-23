from models.llm_log import LLMLog

def record_usage(db, session_id: str, result: dict, purpose: str):
    row = LLMLog(session_id=session_id, provider="gemini", model=result.get("model", "unknown"),
                 purpose=purpose, input_tokens=int(result.get("input_tokens", 0)),
                 output_tokens=int(result.get("output_tokens", 0)))
    db.add(row)
    db.commit()
    return row
