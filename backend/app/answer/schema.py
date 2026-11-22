"""The answer JSON schema from spec section 3, sent in Ollama's `format` parameter."""

from __future__ import annotations

REFUSAL_PHRASE = "This information is not available in the provided documents."

ANSWER_SCHEMA: dict = {
    "type": "object",
    "properties": {
        "found_in_document": {"type": "boolean"},
        "sentences": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "text": {"type": "string"},
                    "ids": {"type": "array", "items": {"type": "string"}},
                },
                "required": ["text", "ids"],
            },
        },
        "missing": {"type": "string"},
    },
    "required": ["found_in_document", "sentences", "missing"],
}
