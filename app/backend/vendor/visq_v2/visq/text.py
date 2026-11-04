"""Essay text helpers: normalising, ids, sentences, locating an extracted element in the essay, copy checks."""
import hashlib
import re

from . import config

STOP = set("the a an of and to in is that it for as with are this by or from at which be was were on not but "
           "have has had can will would should could their they them its his her we you i".split())


class EssayError(ValueError):
    pass


def normalise(text: str) -> str:
    t = text.replace("\r\n", "\n").replace("\r", "\n")
    t = re.sub(r"[ \t]+", " ", t)
    return re.sub(r"\n{3,}", "\n\n", t).strip()


def words(text: str) -> list[str]:
    return re.findall(r"[A-Za-z0-9']+", text.lower())


def check_length(text: str) -> int:
    n = len(text.split())
    if n < config.MIN_ESSAY_WORDS:
        raise EssayError(f"essay has {n} words; Visq needs at least {config.MIN_ESSAY_WORDS}")
    if n > config.MAX_ESSAY_WORDS:
        raise EssayError(f"essay has {n} words; the limit is {config.MAX_ESSAY_WORDS}")
    return n


def essay_id(text: str) -> str:
    return hashlib.sha256(normalise(text).encode()).hexdigest()[:12]


def sentences(text: str) -> list[str]:
    flat = re.sub(r"\s+", " ", text.strip())
    return [s.strip() for s in re.split(r"(?<=[.!?])\s+(?=[A-Z\"'(\[])", flat) if s.strip()]


def locate(essay: str, element: str, window: int = 1, max_words: int = 120) -> str:
    """The essay sentence that best matches `element` (word overlap), with `window` sentences either side."""
    sents = sentences(essay)
    target = {w for w in words(element) if w not in STOP}
    if not sents or not target:
        return ""
    best, best_score = 0, 0.0
    for i, s in enumerate(sents):
        sw = {w for w in words(s) if w not in STOP}
        if not sw:
            continue
        score = len(target & sw) / len(target | sw)
        if score > best_score:
            best, best_score = i, score
    if best_score == 0:
        return ""
    chunk = " ".join(sents[max(0, best - window): best + window + 1])
    return " ".join(chunk.split()[:max_words])


def longest_common_run(a: list[str], b: list[str]) -> int:
    best = 0
    prev = [0] * (len(b) + 1)
    for x in a:
        cur = [0] * (len(b) + 1)
        for j, y in enumerate(b, start=1):
            if x == y:
                cur[j] = prev[j - 1] + 1
                best = max(best, cur[j])
        prev = cur
    return best