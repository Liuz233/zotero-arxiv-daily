from types import SimpleNamespace
from unittest.mock import Mock

import arxiv
import feedparser
import pytest

from zotero_arxiv_daily.retriever.arxiv_retriever import ArxivRetriever


def make_retriever(debug=False):
    return ArxivRetriever(SimpleNamespace(
        source=SimpleNamespace(arxiv=SimpleNamespace(category=['cs.AI', 'cs.CL'])),
        executor=SimpleNamespace(debug=debug),
    ))


def test_debug_uses_recent_search_without_rss(monkeypatch):
    client = Mock()
    papers = [object() for _ in range(5)]
    client.results.return_value = iter(papers)
    monkeypatch.setattr(arxiv, 'Client', Mock(return_value=client))
    parse = Mock(side_effect=AssertionError('Debug delivery must not depend on RSS'))
    monkeypatch.setattr(feedparser, 'parse', parse)

    assert make_retriever(debug=True)._retrieve_raw_papers() == papers
    search = client.results.call_args.args[0]
    assert search.query == 'cat:cs.AI OR cat:cs.CL'
    assert search.max_results == 5
    assert search.sort_by == arxiv.SortCriterion.SubmittedDate
    assert search.sort_order == arxiv.SortOrder.Descending
    parse.assert_not_called()


def test_empty_debug_search_fails_instead_of_passing_delivery_test(monkeypatch):
    client = Mock()
    client.results.return_value = iter([])
    monkeypatch.setattr(arxiv, 'Client', Mock(return_value=client))
    with pytest.raises(RuntimeError, match='email delivery was not tested'):
        make_retriever(debug=True)._retrieve_raw_papers()


@pytest.mark.parametrize('response', [
    {'status': 503, 'feed': {'title': 'Unavailable'}, 'entries': []},
    {'status': 200, 'bozo': True, 'feed': {'title': 'Broken XML'}, 'entries': []},
    {'status': 200, 'feed': {}, 'entries': []},
])
def test_broken_feed_is_not_an_empty_day(monkeypatch, response):
    monkeypatch.setattr(feedparser, 'parse', Mock(return_value=feedparser.FeedParserDict(response)))
    with pytest.raises(RuntimeError):
        make_retriever()._retrieve_raw_papers()


def test_valid_empty_feed_is_allowed(monkeypatch):
    response = feedparser.FeedParserDict(
        status=200, bozo=False,
        feed=feedparser.FeedParserDict(title='arXiv updates', updated='2026-11-01'),
        entries=[],
    )
    monkeypatch.setattr(feedparser, 'parse', Mock(return_value=response))
    assert make_retriever()._retrieve_raw_papers() == []


def test_daily_feed_keeps_new_announcements_only(monkeypatch):
    response = feedparser.FeedParserDict(
        status=200, bozo=False,
        feed=feedparser.FeedParserDict(title='arXiv updates'),
        entries=[
            feedparser.FeedParserDict(id='oai:arXiv.org:2611.00001', arxiv_announce_type='new'),
            feedparser.FeedParserDict(id='oai:arXiv.org:2611.00002', arxiv_announce_type='replace'),
        ],
    )
    monkeypatch.setattr(feedparser, 'parse', Mock(return_value=response))
    client = Mock()
    paper = object()
    client.results.return_value = iter([paper])
    monkeypatch.setattr(arxiv, 'Client', Mock(return_value=client))

    assert make_retriever()._retrieve_raw_papers() == [paper]
    assert client.results.call_args.args[0].id_list == ['2611.00001']
