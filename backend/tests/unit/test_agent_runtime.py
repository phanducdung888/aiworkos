

class TestAProposedLinkSaysWhy:
    """ADR-0073 §6. A link without a stated reason asks somebody to approve a classification whose
    basis they cannot see, which is the shape of decision people stop reading carefully."""

    def test_a_link_from_the_same_conversation_says_so(self) -> None:
        import uuid

        from app.agent.runtime import _ChosenLink, _rationale

        text = _rationale(
            _ChosenLink(id=uuid.uuid4(), project_id=None, reason="same_thread")
        )
        assert "cùng cuộc hội thoại" in text
        assert "BR-AI-17" in text, "the existing rationale was replaced rather than extended"

    def test_no_link_says_nothing_extra(self) -> None:
        from app.agent.runtime import _rationale

        assert "công việc sẵn có" not in _rationale(None)
