from criteriabench.providers.openai import _INSTRUCTIONS


def test_live_prompt_defines_and_self_checks_evidence_offsets() -> None:
    assert "zero-based Unicode code-point" in _INSTRUCTIONS
    assert "end_char is exclusive" in _INSTRUCTIONS
    assert (
        "eligibility_text[evidence.start_char:evidence.end_char] exactly equals evidence.quote"
        in _INSTRUCTIONS
    )
    assert "evidence.quote exactly equals source_text" in _INSTRUCTIONS
