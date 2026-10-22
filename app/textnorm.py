"""Persian text normalisation + small number-word parser used by search and the NLU fallback."""
import re

_AR2FA = str.maketrans({"ي": "ی", "ك": "ک", "ۀ": "ه", "ة": "ه", "ؤ": "و", "أ": "ا", "إ": "ا", "ٱ": "ا"})
_DIGITS = str.maketrans("۰۱۲۳۴۵۶۷۸۹٠١٢٣٤٥٦٧٨٩", "01234567890123456789")
_DIAC = re.compile(r"[ً-ٰٟـ]")
_JUNK = re.compile(r"[‌‍‎‏‪-‮()\[\]{}«»\"'`~!@#$%^&*_=+|\\<>?؟،؛;:,./\-–—]+")
_SP = re.compile(r"\s+")


def normalize(s: str) -> str:
    if not s:
        return ""
    s = s.translate(_AR2FA).translate(_DIGITS)
    s = _DIAC.sub("", s)
    s = _JUNK.sub(" ", s)
    return _SP.sub(" ", s).strip().lower()


def to_persian_digits(n) -> str:
    return str(n).translate(str.maketrans("0123456789", "۰۱۲۳۴۵۶۷۸۹"))


_WORDS = {
    "صفر": 0, "یک": 1, "یه": 1, "دو": 2, "سه": 3, "چهار": 4, "پنج": 5, "شش": 6, "شیش": 6, "هفت": 7,
    "هشت": 8, "نه": 9, "ده": 10, "یازده": 11, "دوازده": 12, "پانزده": 15, "بیست": 20, "سی": 30,
    "چهل": 40, "پنجاه": 50, "شصت": 60, "هفتاد": 70, "هشتاد": 80, "نود": 90, "صد": 100, "نیم": 0.5,
}
_UNITS = {"میلیارد": 1_000_000_000, "میلیون": 1_000_000, "هزار": 1_000, "تومن": 1, "تومان": 1}

_NUM = r"(\d+(?:\.\d+)?|" + "|".join(sorted(_WORDS, key=len, reverse=True)) + r")"
_AMOUNT_RE = re.compile(_NUM + r"\s*(میلیارد|میلیون|هزار)?\s*(?:و\s*" + _NUM + r"\s*(هزار)?)?\s*(?:تومان|تومن|ت)?")


def parse_amount(text: str):
    """Parse the first Persian money phrase ('۳ میلیون', 'دو و نیم میلیون', '۵۰۰ هزار') -> toman int or None."""
    t = normalize(text)
    m = re.search(_NUM + r"(?:\s*و\s*نیم)?\s*(میلیارد|میلیون|هزار)", t)
    if m:
        base = m.group(1)
        v = float(base) if re.match(r"\d", base) else float(_WORDS[base])
        if "و نیم" in m.group(0):
            v += 0.5
        return int(v * _UNITS[m.group(2)])
    m = re.search(r"(\d{4,})\s*(?:تومان|تومن|ت)?", t)
    if m:
        return int(m.group(1))
    return None


# words that users type joined/split differently from catalogue titles
_SYN = [
    (re.compile(r"\bمدار\s+بسته\b"), "مداربسته"),
    (re.compile(r"\bهد\s+فون\b"), "هدفون"),
    (re.compile(r"\bپاور\s+بانک\b"), "پاوربانک"),
    (re.compile(r"\bتی\s+شرت\b"), "تیشرت"),
    (re.compile(r"\bهندز\s*فری\b"), "هندزفری"),
    (re.compile(r"\bلپ\s+تاپ\b"), "لپتاپ"),
    (re.compile(r"\bلب\s+تاب\b"), "لپتاپ"),
    (re.compile(r"\bموبایل\b"), "گوشی"),
    (re.compile(r"\bتلفن همراه\b"), "گوشی"),
    (re.compile(r"\bمچ\s+بند\b"), "مچبند"),
]


def normalize_fts(s: str) -> str:
    t = normalize(s)
    for rx, rep in _SYN:
        t = rx.sub(rep, t)
    return t
