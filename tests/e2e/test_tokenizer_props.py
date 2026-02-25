import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

pytestmark = pytest.mark.property


@settings(deadline=None)
@given(st.text(min_size=1, max_size=10000, alphabet=st.characters(blacklist_categories=("Cs",))))
def test_tokenizer_never_crashes_on_valid_text(text):
    from tensor_grep.backends.cybert_backend import tokenize

    tokens = tokenize([text])
    assert tokens is not None
    assert len(tokens) > 0
