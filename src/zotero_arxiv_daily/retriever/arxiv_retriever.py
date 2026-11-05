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
from datetime import datetime, timedelta, timezone
from loguru import logger
@register_retriever("arxiv")
class ArxivRetriever(BaseRetriever):
    def __init__(self, config):
        super().__init__(config)
        if self.config.source.arxiv.category is None:
            raise ValueError("category must be specified for arxiv.")
        if getattr(self.retriever_config, 'max_age_days', 7) <= 0:
            raise ValueError("arxiv.max_age_days must be positive.")
    def _recent_api_papers(self, client, start, end, limit=None):
        categories = ' OR '.join(f'cat:{category}' for category in self.config.source.arxiv.category)
        # arXiv ranges are inclusive and have minute precision.
        last_minute = end - timedelta(microseconds=1)
        query = f'({categories}) AND submittedDate:[{start:%Y%m%d%H%M} TO {last_minute:%Y%m%d%H%M}]'
        logger.info(f"Searching arXiv by actual submission date: {start.isoformat()} to {end.isoformat()}")
        search = arxiv.Search(
            query=query, max_results=limit,
            sort_by=arxiv.SortCriterion.SubmittedDate,
            sort_order=arxiv.SortOrder.Descending,
        )
        papers = list(client.results(search))
        recent = [p for p in papers if start <= p.published < end]
        if papers and not recent:
            raise RuntimeError("arXiv date search returned only out-of-range papers; refusing to send stale recommendations.")
        return recent

    def _retrieve_raw_papers(self) -> list[ArxivResult]:
        client = arxiv.Client(num_retries=10,delay_seconds=10)
        now = datetime.now(timezone.utc)
        cutoff = now - timedelta(days=getattr(self.retriever_config, 'max_age_days', 7))
        if self.config.executor.debug:
            papers = self._recent_api_papers(client, cutoff, now, limit=5)
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

        recent = [p for p in raw_papers if cutoff <= p.published <= now]
        stale_count = len(raw_papers) - len(recent)
        if stale_count:
            logger.warning(f"Excluded {stale_count} RSS papers outside the actual submission-date window")
        if raw_papers and not recent:
            # A feed can call old submissions 'new'. Fall back to yesterday's
            # submissions, rather than sending the stale batch or passing silently.
            today = now.replace(hour=0, minute=0, second=0, microsecond=0)
            return self._recent_api_papers(client, today - timedelta(days=1), today)
        return recent

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
            full_text=full_text,
            submitted_date=raw_paper.published,
        )
