"""Shared configuration for Anthropic model used across extraction scripts."""

# Single source of truth for the Claude model used in all extraction/OCR calls.
# Change this one value to update all scripts simultaneously.
# Same model as the API's natural-query endpoint (api/main.py, _NQ_MODEL).
MODEL = "claude-sonnet-5-5"

# CBM documents name pathogens and containment labs, which can trip the
# model's safety classifier. With this beta the API re-runs a refused request
# on a fallback model instead of returning a refusal.
_FALLBACK_BETA = "server-side-fallback-2026-07-01"


class ModelRefusal(RuntimeError):
    """The model (and its fallback) declined to answer."""


def create_message(client, **kwargs):
    """messages.create() with the pipeline model and server-side fallback.

    The model thinks by default, so callers must leave max_tokens headroom
    for thinking and read the reply with response_text()."""
    return client.beta.messages.create(
        model=MODEL,
        betas=[_FALLBACK_BETA],
        # Not yet a named parameter in the pinned SDK
        extra_body={"fallbacks": "default"},
        **kwargs,
    )


def response_text(msg) -> str:
    """Concatenated text blocks of a response; raises ModelRefusal on refusal.

    Never use msg.content[0].text: the first block may be a thinking block."""
    if msg.stop_reason == "refusal":
        raise ModelRefusal(getattr(msg.stop_details, "category", None) or "refusal")
    return "".join(b.text for b in msg.content if b.type == "text").strip()
