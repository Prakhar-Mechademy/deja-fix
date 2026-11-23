from services.confirmation_service import confirmation_state, is_confirmed

def test_explicit_positive():
    assert is_confirmed("That worked, thanks!")
    assert confirmation_state("problem solved") == "positive"

def test_negative_overrides_positive_wording():
    assert confirmation_state("It worked before, but now it did not work") == "negative"

def test_ambiguous_is_not_confirmation():
    assert confirmation_state("Okay, thanks") == "ambiguous"
    assert not is_confirmed("I'll try that")
