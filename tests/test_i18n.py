from open_alarm.backend.i18n.catalog import (
    DEFAULT_LOCALE,
    SUPPORTED_LOCALES,
    TranslationCatalog,
    normalize_locale,
)


def test_english_and_finnish_are_required_locales() -> None:
    assert SUPPORTED_LOCALES == frozenset({"en", "fi"})
    assert DEFAULT_LOCALE == "en"


def test_locale_normalization_accepts_home_assistant_style_language_tags() -> None:
    assert normalize_locale("fi") == "fi"
    assert normalize_locale("fi-FI") == "fi"
    assert normalize_locale("fi_FI") == "fi"
    assert normalize_locale("en-GB") == "en"
    assert normalize_locale("sv-SE") == "en"
    assert normalize_locale(None) == "en"


def test_finnish_and_english_catalogs_have_identical_keys() -> None:
    catalog = TranslationCatalog()
    assert catalog.keys("fi") == catalog.keys("en")


def test_finnish_alarm_terms_are_translated() -> None:
    catalog = TranslationCatalog()
    assert catalog.translate("fi", "alarm.action.acknowledge") == "Kuittaa"
    assert catalog.translate("fi", "quality.UNAVAILABLE") == "Ei saatavilla"
    assert catalog.translate("fi", "alarm.lifecycle.ACTIVE_UNACK") == "Aktiivinen, kuittaamaton"


def test_unknown_locale_falls_back_to_english() -> None:
    catalog = TranslationCatalog()
    assert catalog.translate("sv", "alarm.action.acknowledge") == "Acknowledge"


def test_translation_formatting_preserves_missing_placeholders_safely() -> None:
    catalog = TranslationCatalog()
    assert catalog.translate("fi", "notification.current_value", value="23,5 °C") == (
        "Nykyinen arvo: 23,5 °C"
    )
    assert catalog.translate("fi", "notification.current_value") == "Nykyinen arvo: {value}"
