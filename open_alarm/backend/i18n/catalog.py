from __future__ import annotations

import json
from pathlib import Path

DEFAULT_LOCALE = "en"
LOCALES_DIR = Path(__file__).with_name("locales")
LANGUAGE_TAGS = {"en": "en-GB", "fi": "fi-FI"}
SUPPORTED_LOCALES = frozenset(LANGUAGE_TAGS)


def normalize_locale(value: str | None) -> str:
    """Normalize Home Assistant/browser locale values to a supported language code."""
    if not value:
        return DEFAULT_LOCALE

    normalized = value.strip().lower().replace("_", "-")
    language = normalized.split("-", maxsplit=1)[0]
    return language if language in SUPPORTED_LOCALES else DEFAULT_LOCALE


class TranslationCatalog:
    def __init__(self, locales_dir: str | Path = LOCALES_DIR) -> None:
        self._locales_dir = Path(locales_dir)
        self._cache: dict[str, dict[str, str]] = {}

    def messages(self, locale: str | None) -> dict[str, str]:
        code = normalize_locale(locale)
        if code not in self._cache:
            self._cache[code] = self._load(code)
        return dict(self._cache[code])

    def translate(self, locale: str | None, key: str, **values: object) -> str:
        code = normalize_locale(locale)
        messages = self.messages(code)
        text = messages.get(key)
        if text is None and code != DEFAULT_LOCALE:
            text = self.messages(DEFAULT_LOCALE).get(key)
        if text is None:
            text = key
        return text.format_map(_SafeFormat(values))

    def bundle(self, locale: str | None) -> dict[str, object]:
        code = normalize_locale(locale)
        return {
            "locale": code,
            "language_tag": LANGUAGE_TAGS[code],
            "messages": self.messages(code),
        }

    def keys(self, locale: str | None) -> frozenset[str]:
        return frozenset(self.messages(locale))

    def _load(self, code: str) -> dict[str, str]:
        path = self._locales_dir / f"{code}.json"
        payload = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(payload, dict) or not all(
            isinstance(key, str) and isinstance(value, str) for key, value in payload.items()
        ):
            raise ValueError(f"translation catalog {path} must contain a string-to-string object")
        return payload


class _SafeFormat(dict[str, object]):
    def __missing__(self, key: str) -> str:
        return "{" + key + "}"
