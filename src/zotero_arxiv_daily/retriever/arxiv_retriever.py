from .base import BaseRetriever, register_retriever
import arxiv
from arxiv import Result as ArxivResult
from ..protocol import Paper
from ..utils import extract_markdown_from_pdf
from tempfile import TemporaryDirectory
import feedparser
from urllib.request import urlretrieve
from tqdm import tqdm
import os
from loguru import logger
@register_retriever("arxiv")
class ArxivRetriever(BaseRetriever):
    def __init__(self, config):
        super().__init__(config)
        if self.config.source.arxiv.category is None:
            raise ValueError("category must be specified for arxiv.")
    def _retrieve_raw_papers(self) -> list[ArxivResult]:
        client = arxiv.Client(num_retries=10,delay_seconds=10)
        if self.config.executor.debug:
            # A delivery test must work even when today's RSS feed is empty.
            query = ' OR '.join(f'cat:{category}' for category in self.config.source.arxiv.category)
            search = arxiv.Search(
                query=query,
                max_results=5,
                sort_by=arxiv.SortCriterion.SubmittedDate,
                sort_order=arxiv.SortOrder.Descending,
            )
            papers = list(client.results(search))
            if not papers:
                raise RuntimeError("arXiv delivery test returned no papers; email delivery was not tested.")
            return papers
        query = '+'.join(self.config.source.arxiv.category)
        # Get the latest paper from arxiv rss feed
        feed = feedparser.parse(f"https://rss.arxiv.org/atom/{query}")
        if feed.get('status', 200) != 200 or feed.get('bozo', False):
            raise RuntimeError(f"Failed to retrieve arXiv RSS feed (HTTP {feed.get('status', 'unknown')}).")
        if not feed.feed.get('title'):
            raise RuntimeError("arXiv RSS response has no feed title; refusing to treat it as an empty day.")
        if 'Feed error for query' in feed.feed.title:
            raise Exception(f"Invalid ARXIV_QUERY: {query}.")
        raw_papers = []
        all_paper_ids = [i.id.removeprefix("oai:arXiv.org:") for i in feed.entries if i.get("arxiv_announce_type","new") == 'new']
        logger.info(f"arXiv RSS updated {feed.feed.get('updated', 'unknown')}: {len(all_paper_ids)} new papers")

        # Get full information of each paper from arxiv api
        bar = tqdm(total=len(all_paper_ids))
        for i in range(0,len(all_paper_ids),20):
            search = arxiv.Search(id_list=all_paper_ids[i:i+20])
            batch = list(client.results(search))
            bar.update(len(batch))
            raw_papers.extend(batch)
        bar.close()

        return raw_papers

    def convert_to_paper(self, raw_paper:ArxivResult) -> Paper:
        title = raw_paper.title
        authors = [a.name for a in raw_paper.authors]
        abstract = raw_paper.summary
        pdf_url = raw_paper.pdf_url
        with TemporaryDirectory() as temp_dir:
            path = os.path.join(temp_dir, "paper.pdf")
            urlretrieve(pdf_url, path)
            try:
                full_text = extract_markdown_from_pdf(path)
            except Exception as e:
                logger.warning(f"Failed to extract full text of {title}: {e}")
                full_text = None
        return Paper(
            source=self.name,
            title=title,
            authors=authors,
            abstract=abstract,
            url=raw_paper.entry_id,
            pdf_url=pdf_url,
            full_text=full_text
        )
