"""Multi-posting terminal input: accept whatever shape a paste arrives in."""

from run import split_urls


def test_splits_on_newlines():
    assert split_urls("https://a.com/1\nhttps://b.com/2") == [
        "https://a.com/1", "https://b.com/2"]


def test_splits_on_semicolons():
    assert split_urls("https://a.com/1;https://b.com/2") == [
        "https://a.com/1", "https://b.com/2"]


def test_splits_on_spaces_and_mixed_separators():
    assert split_urls("https://a.com/1 ; https://b.com/2\n https://c.com/3") == [
        "https://a.com/1", "https://b.com/2", "https://c.com/3"]


def test_drops_non_urls():
    assert split_urls("hello https://a.com/1 world") == ["https://a.com/1"]
    assert split_urls("just some text") == []


def test_deduplicates_preserving_order():
    assert split_urls("https://b.com/2;https://a.com/1;https://b.com/2") == [
        "https://b.com/2", "https://a.com/1"]


def test_strips_trailing_commas():
    assert split_urls("https://a.com/1, https://b.com/2") == [
        "https://a.com/1", "https://b.com/2"]


def test_empty_input_returns_empty_list():
    assert split_urls("") == []
    assert split_urls("   \n  ") == []
