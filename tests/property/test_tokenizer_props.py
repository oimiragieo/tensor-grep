from hypothesis import given, settings, strategies as st
import pytest

pytestmark = pytest.mark.property

@settings(deadline=None)
@given(st.text(min_size=1, max_size=10000, alphabet=st.characters(blacklist_categories=("Cs",))))
def test_tokenizer_never_crashes_on_valid_text(text):
    from cudf_grep.backends.cybert_backend import tokenize
    tokens = tokenize([text])
    assert tokens is not None
    assert len(tokens) > 0
