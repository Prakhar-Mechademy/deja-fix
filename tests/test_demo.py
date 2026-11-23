import asyncio

import app.services.extractor as extractor
from app.db.models import LLMUsage
from app.schemas.solutions import ExtractedSolution, ExtractedSolutionSet
import scripts.three_session_demo as demo


def test_three_session_demo_end_to_end(monkeypatch):
    monkeypatch.setattr(demo.settings, "GEMINI_API_KEY", "integration-test-key")

    async def fake_escalation(db, session_id, history):
        db.add(LLMUsage(
            session_id=session_id,
            model="gemini-3.5-flash",
            purpose="escalation",
            input_tokens=160,
            output_tokens=40,
            total_tokens=200,
        ))
        db.commit()
        return {
            "text": "Clear the application cache, restart PhotoSync, and retry the upload.",
            "input_tokens": 160,
            "output_tokens": 40,
            "total_tokens": 200,
        }

    async def fake_extract(messages):
        return (
            ExtractedSolutionSet(solutions=[ExtractedSolution(
                problem="Photo uploads fail with error ERR-504",
                symptoms=["Photo upload remains pending"],
                device="Windows laptop",
                operating_system="Windows 11",
                application_name="PhotoSync",
                app_version="4.2.1",
                error_codes=["ERR-504"],
                fix_steps=["Clear the application cache", "Restart PhotoSync", "Retry the upload"],
                failed_attempts=["Retrying the upload without clearing the cache"],
            )]),
            {"input_tokens": 140, "output_tokens": 35, "total_tokens": 175},
        )

    monkeypatch.setattr(demo, "escalate_to_gemini", fake_escalation)
    monkeypatch.setattr(extractor, "_extract_with_gemini", fake_extract)

    result = asyncio.run(demo.run_demo(live_gemini=True))

    assert result["initial_count"] == 0
    assert result["final_verified_count"] == 1
    assert result["session_b_usage"] == 0
    assert result["session_a_usage"] == 375
    assert result["retrieval_score"] >= 0.55


def test_demo_database_is_repeatable(monkeypatch):
    monkeypatch.setattr(demo.settings, "GEMINI_API_KEY", "integration-test-key")

    async def fake_escalation(db, session_id, history):
        db.add(LLMUsage(
            session_id=session_id,
            model="gemini-3.5-flash",
            purpose="escalation",
            input_tokens=100,
            output_tokens=25,
            total_tokens=125,
        ))
        db.commit()
        return {"text": "Clear cache and restart PhotoSync", "input_tokens": 100, "output_tokens": 25, "total_tokens": 125}

    async def fake_extract(messages):
        return (
            ExtractedSolutionSet(solutions=[ExtractedSolution(
                problem="Photo uploads fail",
                symptoms=["Photo upload remains pending"],
                device="Windows laptop",
                operating_system="Windows 11",
                application_name="PhotoSync",
                app_version="4.2.1",
                error_codes=["ERR-504"],
                fix_steps=["Clear cache", "Restart PhotoSync"],
                failed_attempts=["Retry upload without clearing cache"],
            )]),
            {"input_tokens": 80, "output_tokens": 20, "total_tokens": 100},
        )

    monkeypatch.setattr(demo, "escalate_to_gemini", fake_escalation)
    monkeypatch.setattr(extractor, "_extract_with_gemini", fake_extract)

    first = asyncio.run(demo.run_demo(live_gemini=True))
    second = asyncio.run(demo.run_demo(live_gemini=True))

    assert first["initial_count"] == 0
    assert second["initial_count"] == 0
    assert first["final_verified_count"] == 1
    assert second["final_verified_count"] == 1
