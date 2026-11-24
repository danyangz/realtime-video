"""Text helpers: sentence chunking for streaming TTS, transcript cleanup, echo detection."""

from __future__ import annotations

import re

import zhconv

# Sentence-ending punctuation: always a good place to hand text to TTS.
HARD_BREAKS = set("。！？!?；;…\n")
# Clause punctuation: used to cut earlier, so the first audio arrives sooner.
SOFT_BREAKS = set("，,、：:")
# Characters that belong to the end of the previous chunk (closing quotes/brackets).
CLOSERS = set("”’」』）)】》\"'")


class SentenceChunker:
    """Accumulates streamed LLM text and yields chunks that are good TTS units.

    The first chunk is cut at the first clause break after `first_min` chars so the
    avatar starts talking quickly; later chunks prefer whole sentences.
    """

    def __init__(self, first_min: int = 6, soft_min: int = 16, max_len: int = 60) -> None:
        self.first_min = first_min
        self.soft_min = soft_min
        self.max_len = max_len
        self.buf = ""
        self.emitted = 0

    def feed(self, text: str) -> list[str]:
        self.buf += text
        out: list[str] = []
        while (cut := self._find_cut()) is not None:
            chunk, self.buf = self.buf[:cut], self.buf[cut:]
            chunk = chunk.strip()
            if chunk:
                out.append(chunk)
                self.emitted += 1
        return out

    def flush(self) -> list[str]:
        chunk, self.buf = self.buf.strip(), ""
        if chunk:
            self.emitted += 1
            return [chunk]
        return []

    def _find_cut(self) -> int | None:
        b = self.buf
        soft_min = self.first_min if self.emitted == 0 else self.soft_min
        n = len(b)
        for i, ch in enumerate(b):
            is_break = False
            if ch in HARD_BREAKS:
                is_break = True
            elif ch == "." and i + 1 < n and b[i + 1].isspace() and not (i > 0 and b[i - 1].isdigit()):
                is_break = True
            elif ch in SOFT_BREAKS and i + 1 >= soft_min:
                # Don't split numbers like 1,000 or 10:30.
                between_digits = 0 < i < n - 1 and b[i - 1].isdigit() and b[i + 1].isdigit()
                is_break = not between_digits
            if is_break:
                j = i + 1
                while j < n and (b[j] in CLOSERS or b[j] in HARD_BREAKS):
                    j += 1
                if j == n:
                    # The next token might be a closing quote or more punctuation ("？”", "……");
                    # wait for it so it stays with this chunk. flush() handles the very end.
                    return None
                return j
            if i + 1 >= self.max_len:
                # Very long run without punctuation: cut at the last space if there is one.
                space = b.rfind(" ", 0, i + 1)
                return space + 1 if space > self.max_len // 2 else i + 1
        return None


class ThinkFilter:
    """Removes <think>...</think> spans from a token stream (tags may be split across chunks)."""

    OPEN, CLOSE = "<think>", "</think>"

    def __init__(self) -> None:
        self.buf = ""
        self.inside = False

    def feed(self, s: str) -> str:
        self.buf += s
        out: list[str] = []
        while True:
            tag = self.CLOSE if self.inside else self.OPEN
            idx = self.buf.find(tag)
            if idx >= 0:
                if not self.inside:
                    out.append(self.buf[:idx])
                self.buf = self.buf[idx + len(tag):]
                self.inside = not self.inside
                continue
            keep = 0
            for k in range(min(len(tag) - 1, len(self.buf)), 0, -1):
                if tag.startswith(self.buf[-k:]):
                    keep = k
                    break
            emit = self.buf[: len(self.buf) - keep]
            if not self.inside:
                out.append(emit)
            self.buf = self.buf[len(self.buf) - keep:]
            return "".join(out)

    def flush(self) -> str:
        rest = "" if self.inside else self.buf
        self.buf = ""
        return rest


_URL_RE = re.compile(r"https?://\S+")
_EMOJI_RE = re.compile(
    "[\U0001F000-\U0001FAFF\U00002600-\U000027BF\U0001F900-\U0001F9FF️‍⭐⭕←-⇿]"
)
_MD_LINE_RE = re.compile(r"^\s*(?:#{1,6}\s*|>\s*|[-*+]\s+|\d+[.)]\s+)", re.MULTILINE)
_MD_INLINE_RE = re.compile(r"(\*\*|__|`+|~~)")
_SPEAKABLE_RE = re.compile(r"[㐀-鿿豈-﫿A-Za-z0-9]")


def speakable_len(text: str) -> int:
    """Number of characters that actually produce speech (CJK, letters, digits)."""
    return len(_SPEAKABLE_RE.findall(text))


def clean_for_tts(text: str) -> str:
    """Strip things that shouldn't be read aloud. Returns "" if nothing speakable is left."""
    t = _URL_RE.sub("", text)
    t = _MD_LINE_RE.sub("", t)
    t = _MD_INLINE_RE.sub("", t)
    t = _EMOJI_RE.sub("", t)
    t = t.replace("*", "").replace("#", "")
    t = re.sub(r"\s+", " ", t).strip()
    return t if speakable_len(t) > 0 else ""


# Phrases Whisper is known to hallucinate on silence/noise for Chinese audio
# (it was trained on lots of subtitled video).
_HALLUCINATIONS = (
    "字幕由", "字幕提供", "字幕志愿者", "中文字幕", "请不吝点赞", "点赞订阅", "点赞 订阅", "订阅转发",
    "订阅 转发", "打赏支持", "明镜与点点", "谢谢观看", "感谢观看", "感谢收看", "下期再见",
    "amara.org", "优优独播剧场", "yoyo television",
)


def normalize_transcript(text: str) -> str:
    """Simplified Chinese, trimmed, with common Whisper hallucinations removed."""
    t = zhconv.convert(text or "", "zh-cn").strip()
    if not t or speakable_len(t) == 0:
        return ""
    low = t.lower()
    if len(t) < 40 and any(h in low for h in _HALLUCINATIONS):
        return ""
    return t


def _bigrams(s: str) -> set[str]:
    return {s[i : i + 2] for i in range(len(s) - 1)}


def looks_like_echo(heard: str, said: str, threshold: float = 0.6) -> bool:
    """True if `heard` (from the mic) is probably the avatar's own voice leaking back.

    Compares character bigrams of the transcript against what the avatar recently said.
    """
    h = "".join(_SPEAKABLE_RE.findall(zhconv.convert(heard, "zh-cn").lower()))
    s = "".join(_SPEAKABLE_RE.findall(zhconv.convert(said, "zh-cn").lower()))
    if not h or not s:
        return False
    if len(h) == 1:
        return h in s
    hb = _bigrams(h)
    overlap = len(hb & _bigrams(s)) / len(hb)
    return overlap >= threshold
