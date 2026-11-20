import logging
import math
import re
from difflib import get_close_matches
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
    "deposit": {"payment", "payments", "order", "transfer", "utr", "paid", "screenshot"},
    "payment": {"deposit", "payments", "order", "transfer", "utr", "paid", "screenshot"},
    "withdrawal": {"withdraw", "payout", "receiving", "upi", "pending", "limit"},
    "withdraw": {"withdrawal", "payout", "receiving", "upi", "pending"},
    "reward": {"rewards", "commission", "quota", "referral", "eligible"},
    "rewards": {"reward", "commission", "quota", "referral", "eligible"},
    "account": {"password", "otp", "security", "locked", "access", "support"},
    "login": {"password", "otp", "security", "locked", "access"},
    "locked": {"lock", "blocked", "security", "wallet", "support"},
    "missing": {"pending", "status", "reward", "commission", "support"},
    "stuck": {"pending", "status", "withdrawal", "payment", "support"},
    "payout": {"withdrawal", "withdraw", "receiving", "pending", "limit"},
    "credited": {"deposit", "payment", "receiving", "status"},
    "received": {"receiving", "withdrawal", "payment", "status"},
    "wallets": {"wallet", "phonepe", "paytm", "mobikwik", "freecharge"},
    "upi": {"wallet", "locked", "blocked", "withdrawal"},
    "delayed": {"pending", "withdrawal", "payment", "status"},
    "payments": {"payment", "deposit", "order"},
    "withdrawals": {"withdrawal", "payout", "receiving"},
    "pending": {"status", "withdrawal", "payment", "order"},
    "settlement": {"withdrawal", "payment", "status"},
    "settled": {"withdrawal", "payment", "status"},
    "cashout": {"withdrawal", "payout", "receiving"},
    "cash": {"withdrawal", "payout", "receiving"},
    "refund": {"payment", "deposit", "status", "support"},
    "deducted": {"payment", "deposit", "status", "support"},
    "transfer": {"payment", "deposit", "order", "utr"},
    "business": {"wallet", "phonepe", "paytm", "mobikwik", "freecharge"},
    "limit": {"receiving", "withdrawal", "upi", "wallet"},
    "referral": {"refer", "invite", "team", "bonus", "reward", "rewards", "code", "link", "banner", "orders"},
    "refer": {"referral", "invite", "team", "bonus", "reward", "link"},
    "invite": {"referral", "refer", "team", "link", "friend", "share"},
    "bonus": {"referral", "reward", "commission", "claim", "banner", "assets", "detail"},
    "team": {"referral", "invite", "link", "member", "orders"},
    "banner": {"bonus", "referral", "detail", "assets"},
    "support": {"admin", "contact", "help", "group", "dm", "service", "available", "minutes", "uid"},
    "admin": {"support", "contact", "help", "group", "dm"},
    "general": {"about", "support", "admin", "commission", "orders", "earn"},
    "bills": {"payment", "proof", "bills", "order", "detail"},
    "proof": {"payment", "bills", "utr", "screenshot", "order"},
    "relink": {"wallet", "locked", "withdrawal", "rebind"},
    "points": {"withdrawal", "maximum", "limit"},
    "cancel": {"cancellation", "withdrawal", "support"},
    "cancellation": {"cancel", "withdrawal", "support"},
}

# Lightweight multilingual query expansion keeps PDF retrieval useful without
# sending user messages to a separate translation service. The answer model
# still receives the original wording and replies in the user's language.
MULTILINGUAL_TERMS = {
    # Hindi and common Hinglish
    "निकासी": {"withdrawal", "withdraw"}, "निकालना": {"withdrawal", "withdraw"},
    "निकालें": {"withdrawal", "withdraw"}, "पैसे": {"payment", "withdrawal"},
    "जमा": {"deposit", "payment"}, "भुगतान": {"payment", "deposit"},
    "अटका": {"pending", "stuck", "withdrawal", "payment"},
    "अटकी": {"pending", "stuck", "withdrawal", "payment"},
    "कट": {"deducted", "payment"}, "गया": {"deducted", "status"},
    "हुआ": {"status", "pending"}, "क्रेडिट": {"credited", "payment", "deposit"},
    "वॉलेट": {"wallet", "phonepe", "paytm", "mobikwik", "freecharge"},
    "वॉलेट्स": {"wallet", "phonepe", "paytm", "mobikwik", "freecharge"},
    "कमीशन": {"commission", "rate"}, "इनाम": {"reward", "rewards"},
    "रेफरल": {"referral", "reward"}, "पासवर्ड": {"password", "account"},
    "लॉक": {"locked", "blocked", "upi", "wallet"},
    "बोनस": {"bonus", "referral", "reward"}, "आमंत्रण": {"invite", "referral", "team"},
    "दोस्त": {"friend", "invite", "referral"}, "मित्र": {"friend", "invite", "referral"},
    "टीम": {"team", "referral", "invite"}, "सपोर्ट": {"support", "admin", "contact", "help"},
    "मदद": {"support", "help", "admin", "contact"}, "एडमिन": {"admin", "support", "contact"},
    # Telugu
    "ఉపసంహరణ": {"withdrawal", "withdraw"}, "డబ్బు": {"payment", "withdrawal"},
    "చెల్లింపు": {"payment", "deposit"}, "జమ": {"deposit", "payment"},
    "ఆగిపోయింది": {"pending", "stuck", "withdrawal", "payment"},
    "వాలెట్": {"wallet", "phonepe", "paytm", "mobikwik", "freecharge"},
    "కమీషన్": {"commission", "rate"}, "రివార్డ్": {"reward", "rewards"},
    "రిఫరల్": {"referral", "invite", "reward", "bonus"},
    "బోనస్": {"bonus", "referral", "reward"},
    "ఆహ్వానించడం": {"invite", "referral", "team"},
    "ఆహ్వానించండి": {"invite", "referral", "team"},
    "స్నేహితుడు": {"friend", "invite", "referral"},
    "జట్టు": {"team", "referral", "invite"},
    "రాలేదు": {"missing", "pending", "bonus", "reward"},
    "రావట్లేదు": {"missing", "pending", "bonus", "reward"},
    "సహాయం": {"support", "admin", "contact", "help"},
    "మద్దతు": {"support", "help", "admin", "contact"},
    "అడ్మిన్": {"admin", "support", "contact"},
    "అవ్వట్లేదు": {"withdrawal", "withdraw", "pending"},
    "అయింది": {"status", "locked", "completed"},
    "కానీ": {"status", "pending"},
    "చేయాలి": {"how", "resolve", "relink"},
    "బ్యాలెన్స్": {"balance", "payment", "receiving"},
    # Tamil
    "திரும்பப்": {"withdrawal", "withdraw"}, "பணம்": {"payment", "withdrawal"},
    "வைப்பு": {"deposit", "payment"}, "கட்டணம்": {"payment", "deposit"},
    "நிலுவை": {"pending", "stuck", "withdrawal", "payment"},
    "வாலட்": {"wallet", "phonepe", "paytm", "mobikwik", "freecharge"},
    "கமிஷன்": {"commission", "rate"}, "வெகுமதி": {"reward", "rewards"},
    "பரிந்துரை": {"referral", "invite", "bonus"}, "போனஸ்": {"bonus", "referral", "reward"},
    "உதவி": {"support", "help", "admin"},
    # Kannada
    "ಹಿಂಪಡೆಯುವಿಕೆ": {"withdrawal", "withdraw"}, "ಹಣ": {"payment", "withdrawal"},
    "ಠೇವಣಿ": {"deposit", "payment"}, "ಪಾವತಿ": {"payment", "deposit"},
    "ಬಾಕಿ": {"pending", "stuck", "withdrawal", "payment"},
    "ವಾಲೆಟ್": {"wallet", "phonepe", "paytm", "mobikwik", "freecharge"},
    "ಕಮಿಷನ್": {"commission", "rate"}, "ಬಹುಮಾನ": {"reward", "rewards"},
    "ರೆಫರಲ್": {"referral", "invite", "bonus"}, "ಬೋನಸ್": {"bonus", "referral", "reward"},
    "ಸಹಾಯ": {"support", "help", "admin"},
    # Malayalam
    "പിൻവലിക്കൽ": {"withdrawal", "withdraw"}, "പണം": {"payment", "withdrawal"},
    "നിക്ഷേപം": {"deposit", "payment"}, "പേയ്മെന്റ്": {"payment", "deposit"},
    "കുടുങ്ങി": {"pending", "stuck", "withdrawal", "payment"},
    "വാലറ്റ്": {"wallet", "phonepe", "paytm", "mobikwik", "freecharge"},
    "കമ്മീഷൻ": {"commission", "rate"}, "റിവാർഡ്": {"reward", "rewards"},
    "റഫറൽ": {"referral", "invite", "bonus"}, "ബോണസ്": {"bonus", "referral", "reward"},
    "സഹായം": {"support", "help", "admin"},
    # Bengali, Marathi, Gujarati
    "উত্তোলন": {"withdrawal", "withdraw"}, "টাকা": {"payment", "withdrawal"},
    "জমা": {"deposit", "payment"}, "পেমেন্ট": {"payment", "deposit"},
    "আটকে": {"pending", "stuck", "withdrawal", "payment"},
    "ওয়ালেট": {"wallet", "phonepe", "paytm", "mobikwik", "freecharge"},
    "কমিশন": {"commission", "rate"}, "পুরস্কার": {"reward", "rewards"},
    "पैसे": {"payment", "withdrawal"}, "काढणे": {"withdrawal", "withdraw"},
    "ठेव": {"deposit", "payment"}, "अडकले": {"pending", "stuck", "withdrawal", "payment"},
    "बक्षीस": {"reward", "rewards"}, "વૉલેટ": {"wallet", "phonepe", "paytm", "mobikwik", "freecharge"},
    "પૈસા": {"payment", "withdrawal"}, "ઉપાડ": {"withdrawal", "withdraw"},
    "ઉપાડવા": {"withdrawal", "withdraw"}, "ચુકવણી": {"payment", "deposit"},
    "જમા": {"deposit", "payment"}, "અટક્યું": {"pending", "stuck", "withdrawal", "payment"},
    "કમિશન": {"commission", "rate"}, "ઇનામ": {"reward", "rewards"},
    # Common Latin transliteration
    "kaise": {"how", "withdrawal", "payment"}, "nikale": {"withdrawal", "withdraw"},
    "nikalna": {"withdrawal", "withdraw"}, "paisa": {"payment", "withdrawal"},
    "atka": {"pending", "stuck", "withdrawal", "payment"},
    "ela": {"how", "withdrawal", "payment"}, "cheyali": {"how", "withdrawal", "payment"},
    "dabbu": {"payment", "withdrawal"}, "aagipoyindi": {"pending", "stuck"},
    "eppudu": {"when", "pending", "withdrawal"}, "withdraw": {"withdrawal", "payout"},
    "nikas": {"withdrawal", "withdraw"}, "payout": {"withdrawal", "receiving"},
    "referal": {"referral", "invite", "bonus", "reward"},
    "refrel": {"referral", "invite", "bonus", "reward"},
    "refferal": {"referral", "invite", "bonus", "reward"},
    "amout": {"amount", "reward", "bonus", "payment"},
    "kab": {"when", "pending", "reward", "bonus"},
    "milega": {"credited", "receive", "reward", "bonus"},
    "kaha": {"where", "support", "admin", "contact"},
    "kahan": {"where", "support", "admin", "contact"},
    "frnd": {"friend", "invite", "referral", "team"},
    "frnds": {"friend", "invite", "referral", "team"},
    "dost": {"friend", "invite", "referral"},
    "mitra": {"friend", "invite", "referral"},
    "raledu": {"missing", "pending", "bonus", "reward"},
    "ravatledu": {"missing", "pending", "bonus", "reward"},
    "madad": {"support", "help", "admin", "contact"},
    "sahayam": {"support", "help", "admin", "contact"},
    "avvatledu": {"withdrawal", "withdraw", "pending"},
    "ayindi": {"status", "locked", "withdrawal"},
    "aindi": {"status", "completed", "payment"},
    "kani": {"status", "pending", "missing"},
    "gaya": {"deducted", "payment", "status"},
    "kat": {"deducted", "payment"},
    "hua": {"status", "pending"},
    "bro": {"support", "withdrawal", "help"},
    "relink": {"wallet", "locked", "resolve"},
    "balance": {"payment", "receiving", "withdrawal"},
}


@dataclass(frozen=True)
class GuidePage:
    page_number: Optional[int]
    text: str
    image_path: Optional[Path] = None


def _tokens(text: str) -> List[str]:
    return [
        word for word in re.findall(
            r"[a-z0-9]+|[\u0900-\u097f]+|[\u0980-\u09ff]+|"
            r"[\u0a80-\u0aff]+|[\u0b80-\u0bff]+|"
            r"[\u0c00-\u0c7f]+|[\u0c80-\u0cff]+|[\u0d00-\u0d7f]+",
            text.lower(),
        )
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
        self._vocabulary = set(self._document_frequency)

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
            expanded_terms.update(MULTILINGUAL_TERMS.get(term, set()))
        # Correct simple spelling mistakes against the guide vocabulary.
        for term in terms:
            if len(term) >= 5 and term not in self._vocabulary:
                expanded_terms.update(
                    get_close_matches(term, self._vocabulary, n=2, cutoff=0.78)
                )
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
