from services.privacy_service import scrub_text, scrub_environment

def test_removes_personal_identifiers_and_keeps_technical_environment():
    text = scrub_text("My name is Ribence Kadel, email ribence@example.com, order ORD-92831. Visit https://example.com")
    assert "Ribence" not in text and "ribence@example.com" not in text
    assert "ORD-92831" not in text and "https://example.com" not in text
    env = scrub_environment({"device": "Dell laptop", "operating_system": "Windows 11", "email": "a@b.com"})
    assert env == {"device": "Dell laptop", "operating_system": "Windows 11"}
