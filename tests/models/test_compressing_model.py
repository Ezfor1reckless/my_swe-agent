from minisweagent.models.compressing_model import CompressingModel, CompressingModelConfig


def _long_messages(n: int = 30) -> list[dict]:
    """A linear conversation long enough to exceed a small threshold."""
    messages = [{"role": "system", "content": "You are a bot."}]
    for i in range(n):
        messages.append({"role": "user", "content": f"Observation {i}: " + "x" * 200})
        messages.append({"role": "assistant", "content": f"Step {i}", "extra": {"actions": []}})
    return messages


def _summary_model(outputs: list[dict]):
    """A deterministic model that returns summaries."""
    from minisweagent.models.test_models import DeterministicModel

    return DeterministicModel(outputs=outputs)


def _compressing(outputs: list[dict], **kwargs) -> CompressingModel:
    kwargs.setdefault("model_kwargs", {})
    kwargs["model_kwargs"]["outputs"] = outputs
    return CompressingModel(
        config_class=CompressingModelConfig,
        model_name="wrapped",
        model_class="deterministic",
        **kwargs,
    )


def test_below_threshold_no_compression():
    """Below the threshold the history passes through unchanged."""
    outputs = [{"role": "assistant", "content": "reply", "extra": {"actions": []}}]
    model = _compressing(outputs, compression_threshold_tokens=10_000_000, keep_recent_messages=2)
    short = [{"role": "user", "content": "hi"}]
    result = model.query(short)
    assert result["content"] == "reply"
    assert model.n_compressions == 0


def test_above_threshold_compresses_old_messages():
    """Above the threshold, old messages are replaced by a summary message."""
    outputs = [
        {"role": "assistant", "content": "SUMMARY", "extra": {"actions": [], "cost": 0.1}},  # summary query
        {"role": "assistant", "content": "reply", "extra": {"actions": []}},  # main query
    ]
    model = _compressing(outputs, compression_threshold_tokens=10, keep_recent_messages=2)
    long_msgs = _long_messages(10)
    result = model.query(long_msgs)
    assert result["content"] == "reply"
    assert model.n_compressions == 1
    assert model.serialize()["info"]["config"]["compression_stats"]["n_compressions"] == 1


def test_kept_segment_starts_with_assistant_message():
    """The verbatim tail starts with an assistant message so tool results stay paired."""
    outputs = [
        {"role": "assistant", "content": "SUMMARY", "extra": {"actions": [], "cost": 0.1}},
        {"role": "assistant", "content": "reply", "extra": {"actions": []}},
    ]
    model = _compressing(outputs, compression_threshold_tokens=10, keep_recent_messages=1)
    # Build a history whose naive tail (last 1 message) would be a tool/user message,
    # but the split must back up to the previous assistant message.
    msgs = [
        {"role": "system", "content": "s"},
        {"role": "user", "content": "old user"},
        {"role": "assistant", "content": "old cmd", "extra": {"actions": []}},
        {"role": "user", "content": "old obs"},
        {"role": "assistant", "content": "recent cmd", "extra": {"actions": []}},
        {"role": "user", "content": "recent obs"},  # naive tail (1 msg) would start here
    ]
    result = model.query(msgs)
    assert result["content"] == "reply"
    assert model.n_compressions == 1
    # The split backed up to the assistant boundary: recent = [assistant, tool-result]
    _, recent = model._split(msgs, keep=1)
    assert recent[0]["role"] == "assistant"
    assert recent[-1]["role"] == "user"


def test_serialize_records_stats():
    """serialize() exposes compression stats."""
    outputs = [
        {"role": "assistant", "content": "SUMMARY", "extra": {"actions": [], "cost": 0.1}},
        {"role": "assistant", "content": "reply", "extra": {"actions": []}},
    ]
    model = _compressing(outputs, compression_threshold_tokens=10, keep_recent_messages=2)
    model.query(_long_messages(10))
    data = model.serialize()
    stats = data["info"]["config"]["compression_stats"]
    assert stats["n_compressions"] == 1
    assert stats["tokens_before"] > 0


def test_summary_model_can_be_cheaper():
    """The summary query uses a separate model instance from the main one."""
    from minisweagent.models.test_models import DeterministicModel

    outputs = [
        {"role": "assistant", "content": "reply", "extra": {"actions": []}},
    ]
    model = _compressing(outputs, compression_threshold_tokens=10, keep_recent_messages=2)
    # Route the summary query to a distinct model (e.g. a cheaper one).
    model._summary_model = lambda: DeterministicModel(
        outputs=[{"role": "assistant", "content": "SUMMARY", "extra": {"actions": []}}]
    )
    result = model.query(_long_messages(10))
    assert result["content"] == "reply"
    assert model.n_compressions == 1
