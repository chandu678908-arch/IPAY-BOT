import logging
import math
import re
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional

log = logging.getLogger("ipay-bot.guide")

STOP_WORDS = {
    "a", "about", "after", "all", "am", "an", "and", "any", "are", "as", "at",
    "be", "been", "before", "but", "by", "can", "could", "did", "do", "does",
    "for", "from", "had", "has", "have", "he", "her", "here", "him", "his",
    "how", "i", "if", "in", "into", "is", "it", "its", "me", "my", "of", "on",
    "or", "our", "please", "should", "so", "that", "the", "their", "them",
    "there", "they", "this", "to", "up", "was", "we", "were", "what", "when",
    "where", "which", "who", "why", "will", "with", "would", "you", "your",
}

TOPIC_TERMS = {
    "deposit": {"payment", "order", "transfer", "utr", "paid", "screenshot"},
    "payment": {"deposit", "order", "transfer", "utr", "paid", "screenshot"},
    "withdrawal": {"withdraw", "payout", "receiving", "upi", "pending", "limit"},
    "withdraw": {"withdrawal", "payout", "receiving", "upi", "pending"},
    "reward": {"rewards", "commission", "quota", "referral", "eligible"},
    "rewards": {"reward", "commission", "quota", "referral", "eligible"},
    "account": {"password", "otp", "security", "locked", "access", "support"},
    "login": {"password", "otp", "security", "locked", "access"},
    "locked": {"lock", "blocked", "security", "wallet", "support"},
    "missing": {"pending", "status", "reward", "commission", "support"},
    "stuck": {"pending", "status", "withdrawal", "payment", "support"},
}


@dataclass(frozen=True)
class GuidePage:
    page_number: Optional[int]
    text: str
    image_path: Optional[Path] = None


def _tokens(text: str) -> List[str]:
    return [
        word for word in re.findall(r"[a-z0-9]+", text.lower())
        if len(word) > 1 and word not in STOP_WORDS
    ]


class Guide:
    def __init__(
        self,
        pdf_path: Path,
        knowledge_path: Path,
        image_dir: Path,
    ) -> None:
        self.pages: List[GuidePage] = []
        self.source = "knowledge base"
        if pdf_path.is_file():
            self._load_pdf(pdf_path, image_dir)
            self.source = "PDF guide"
        else:
            log.warning(
                "Guide PDF was not found at %s; using the text knowledge base without page images",
                pdf_path,
            )
            self._load_knowledge(knowledge_path)
        if not self.pages:
            raise RuntimeError(
                "No guide content is available. Add the official guide PDF or knowledge base."
            )
        self._page_tokens = [Counter(_tokens(page.text)) for page in self.pages]
        self._document_frequency = Counter(
            word for page_tokens in self._page_tokens for word in page_tokens
        )

    def _load_pdf(self, pdf_path: Path, image_dir: Path) -> None:
        try:
            import pymupdf as fitz
        except ImportError as exc:
            raise RuntimeError(
                "PyMuPDF is required to read and render the guide PDF. "
                "Install dependencies with python -m pip install -r requirements.txt."
            ) from exc

        image_dir.mkdir(parents=True, exist_ok=True)
        document = fitz.open(pdf_path)
        try:
            for index, page in enumerate(document):
                page_number = index + 1
                text = page.get_text("text").strip()
                image_path = image_dir / f"page-{page_number:03d}.jpg"
                if not image_path.is_file():
                    pixmap = page.get_pixmap(
                        matrix=fitz.Matrix(1.35, 1.35),
                        alpha=False,
                        annots=True,
                    )
                    pixmap.save(str(image_path), jpg_quality=78)
                self.pages.append(GuidePage(page_number, text, image_path))
        finally:
            document.close()
        log.info("Loaded %d pages from the official PDF guide", len(self.pages))

    def _load_knowledge(self, knowledge_path: Path) -> None:
        if not knowledge_path.is_file():
            log.error("Knowledge base file was not found at %s", knowledge_path)
            return
        content = knowledge_path.read_text(encoding="utf-8")
        sections = re.split(r"(?=^#{1,3}\s+)", content, flags=re.MULTILINE)
        self.pages.extend(
            GuidePage(None, section.strip())
            for section in sections
            if section.strip()
        )

    def retrieve(self, query: str, top_k: int = 2) -> List[GuidePage]:
        terms = set(_tokens(query))
        expanded_terms = set(terms)
        for term in terms:
            expanded_terms.update(TOPIC_TERMS.get(term, set()))
        if not expanded_terms:
            return []

        scored = []
        for index, page_tokens in enumerate(self._page_tokens):
            score = 0.0
            for term in expanded_terms:
                frequency = page_tokens.get(term, 0)
                if frequency:
                    document_frequency = self._document_frequency[term]
                    inverse_document_frequency = math.log(
                        1 + len(self.pages) / (1 + document_frequency)
                    )
                    query_weight = 1.0 if term in terms else 0.35
                    score += (
                        query_weight
                        * (1 + math.log(frequency))
                        * inverse_document_frequency
                    )
            if score:
                scored.append((score, index))
        scored.sort(key=lambda result: result[0], reverse=True)
        return [self.pages[index] for _, index in scored[:top_k]]
