from core.text import (
    SentenceChunker,
    ThinkFilter,
    clean_for_tts,
    looks_like_echo,
    normalize_transcript,
    speakable_len,
)


def chunk_all(deltas, **kw):
    c = SentenceChunker(**kw)
    out = []
    for d in deltas:
        out += c.feed(d)
    out += c.flush()
    return out


def test_chunker_first_chunk_cuts_early_at_comma():
    # A comma within the first 6 chars is too early to cut; the sentence end is used instead.
    out = chunk_all(list("你好呀，今天过得怎么样？我刚刚在看书。"))
    assert out == ["你好呀，今天过得怎么样？", "我刚刚在看书。"]
    # A comma after 6+ chars cuts the first chunk early so audio starts sooner.
    out = chunk_all(list("嗯嗯，我觉得这个想法很不错，可以试试看。"))
    assert out[0] == "嗯嗯，我觉得这个想法很不错，"
    assert "".join(out) == "嗯嗯，我觉得这个想法很不错，可以试试看。"


def test_chunker_keeps_text_intact_and_absorbs_closers():
    text = "他说：“真的吗？”我说是的！好吧……那就这样。"
    out = chunk_all(list(text))
    assert "".join(out) == text.replace(" ", "")
    assert any(piece.endswith("？”") for piece in out)


def test_chunker_does_not_split_numbers():
    out = chunk_all(["价格是1,000元，时间是10:30，记得来哦。"], first_min=3)
    joined = "|".join(out)
    assert "1,000" in joined and "10:30" in joined


def test_chunker_english_period():
    out = chunk_all(["Hello there. How are you? Fine."])
    assert out == ["Hello there.", "How are you?", "Fine."]


def test_chunker_forces_cut_on_long_runs():
    out = chunk_all(["啊" * 150], max_len=60)
    assert all(len(p) <= 60 for p in out)
    assert "".join(out) == "啊" * 150


def test_think_filter_split_tags():
    f = ThinkFilter()
    parts = ["<thi", "nk>内部推理", "</th", "ink>你好", "，世界"]
    assert "".join(f.feed(p) for p in parts) + f.flush() == "你好，世界"


def test_think_filter_passthrough():
    f = ThinkFilter()
    assert f.feed("a < b 并且 <tag>") + f.flush() == "a < b 并且 <tag>"


def test_clean_for_tts():
    assert clean_for_tts("**好的**！😀 看这里 https://x.com/a") == "好的！ 看这里"
    assert clean_for_tts("- 第一点\n- 第二点") == "第一点 第二点"
    assert clean_for_tts("……") == ""
    assert clean_for_tts("😀") == ""


def test_normalize_transcript():
    assert normalize_transcript("這是一個測試") == "这是一个测试"
    assert normalize_transcript("  ") == ""
    assert normalize_transcript("。") == ""
    assert normalize_transcript("字幕由Amara.org社区提供") == ""
    assert normalize_transcript("谢谢观看") == ""
    assert normalize_transcript("今天天气不错") == "今天天气不错"


def test_speakable_len():
    assert speakable_len("你好，world！") == 7


def test_echo_detection():
    said = "今天天气很好，我们可以去公园散步，顺便买杯咖啡。"
    assert looks_like_echo("我们可以去公园散步", said)
    assert looks_like_echo("我們可以去公園", said)  # traditional chars still match
    assert not looks_like_echo("等一下我有个问题", said)
    assert not looks_like_echo("你好", "")
