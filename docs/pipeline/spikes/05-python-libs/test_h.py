from hypothesis import given, strategies as s
@given(s.lists(s.integers()))
def test_sort(l): assert sorted(sorted(l))==sorted(l)
