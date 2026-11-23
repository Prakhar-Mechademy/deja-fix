import numpy as np

from app.schemas.solutions import ExtractedSolution
from app.services.generalisation import generalise_extracted_solution, validate_canonical_solution
import app.services.generalisation as generalisation
from app.utils.pii import contains_pii, redact_pii


def test_generalises_customer_wording(monkeypatch):
    monkeypatch.setattr(generalisation, "embed_text", lambda text: np.array([1.0, 0.0], dtype=np.float32))

    extracted = ExtractedSolution(
        problem="My app won't upload my photos and my name is Alice",
        fix_steps=["Please clear the application cache"],
    )
    canonical = generalise_extracted_solution(extracted, confirmed=True)

    assert "alice" not in canonical["problem_description"].lower()
    assert "my name" not in canonical["problem_description"].lower()
    assert "Upload" in canonical["canonical_title"] or "upload" in canonical["problem_description"]


def test_pii_is_removed_but_technical_context_is_preserved(monkeypatch):
    monkeypatch.setattr(generalisation, "embed_text", lambda text: np.array([1.0, 0.0], dtype=np.float32))

    extracted = ExtractedSolution(
        problem="Customer on iPhone 15 reports error ERR-504",
        device="Apple iPhone 15",
        operating_system="iOS 18",
        application_name="PhotoSync",
        app_version="4.2.1",
        error_codes=["ERR-504"],
        fix_steps=["Restart PhotoSync"],
    )

    canonical = generalise_extracted_solution(extracted, confirmed=True)

    assert canonical["device"] == "Apple iPhone 15"
    assert canonical["operating_system"] == "iOS 18"
    assert canonical["application_name"] == "PhotoSync"
    assert canonical["app_version"] == "4.2.1"
    assert "ERR-504" in canonical["error_codes"]

    sanitised = " ".join([
        canonical["problem_description"],
        canonical["device"] or "",
        canonical["operating_system"] or "",
    ])

    assert "alice" not in sanitised.lower()


def test_robust_pii_sanitisation_patterns():
    unsafe = "Alice alice@example.com 555-123-4567 account_id ABCD1234 @alice 10.0.0.1 password=hunter2 https://example.com/user/alice api_key secret123"
    safe = redact_pii(unsafe)

    assert "alice@example.com" not in safe
    assert "555-123-4567" not in safe
    assert "10.0.0.1" not in safe
    assert "hunter2" not in safe
    assert "secret123" not in safe
    assert contains_pii("alice@example.com") is True


def test_steps_are_normalised_without_added_instructions(monkeypatch):
    monkeypatch.setattr(generalisation, "embed_text", lambda text: np.array([1.0, 0.0], dtype=np.float32))

    extracted = ExtractedSolution(
        problem="Printer remains offline",
        fix_steps=["Please reconnect the USB cable", "Restart the print spooler"],
    )
    canonical = generalise_extracted_solution(extracted, confirmed=True)

    assert canonical["fix_steps"] == ["Reconnect the USB cable", "Restart the print spooler"]


def test_quality_validation_rejects_missing_steps_or_confirmation(monkeypatch):
    monkeypatch.setattr(generalisation, "embed_text", lambda text: np.array([1.0, 0.0], dtype=np.float32))

    canonical = generalise_extracted_solution(
        ExtractedSolution(problem="Printer remains offline", fix_steps=[]),
        confirmed=False,
    )

    valid, reasons = validate_canonical_solution(canonical)
    assert valid is False
    assert "missing actionable troubleshooting steps" in reasons
    assert "missing explicit customer confirmation" in reasons


def test_sanitisation_occurs_before_persistable_output(monkeypatch):
    monkeypatch.setattr(generalisation, "embed_text", lambda text: np.array([1.0, 0.0], dtype=np.float32))

    canonical = generalise_extracted_solution(
        ExtractedSolution(
            problem="My account abc@example.com reports ERR-100",
            fix_steps=["Reset password for account 123456"],
        ),
        confirmed=True,
    )

    serialised = str(canonical)
    assert "abc@example.com" not in serialised
