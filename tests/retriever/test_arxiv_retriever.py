from zotero_arxiv_daily.retriever.arxiv_retriever import ArxivRetriever
import feedparser
import pickle
from datetime import datetime, timezone

def test_arxiv_retriever(config, monkeypatch):
    # This integration fixture represents the August 20, 2025 release.
    class FixtureDateTime(datetime):
        @classmethod
        def now(cls, tz=None):
            return datetime(2025, 8, 21, tzinfo=timezone.utc)
    monkeypatch.setattr('zotero_arxiv_daily.retriever.arxiv_retriever.datetime', FixtureDateTime)

    parsed_result = feedparser.parse("tests/retriever/arxiv_rss_example.xml")
    raw_parser = feedparser.parse
    def mock_feedparser_parse(url):
        if url == f"https://rss.arxiv.org/atom/{'+'.join(config.source.arxiv.category)}":
            return parsed_result
        return raw_parser(url)
    monkeypatch.setattr(feedparser, "parse", mock_feedparser_parse)
    
    retriever = ArxivRetriever(config)
    papers = retriever.retrieve_papers()
    parsed_results = [i for i in parsed_result.entries if i.get("arxiv_announce_type","new") == 'new']
    assert len(papers) == len(parsed_results)
    paper_titles = [i.title for i in papers]
    parsed_titles = [i.title for i in parsed_results]
    assert set(paper_titles) == set(parsed_titles)
