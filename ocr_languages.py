# SPDX-License-Identifier: AGPL-3.0-only
"""Document languages are independent of the interface language."""
LANGUAGES = {
    'deu': 'Deutsch', 'eng': 'English', 'fra': 'Français', 'spa': 'Español',
    'nld': 'Nederlands', 'ita': 'Italiano', 'por': 'Português', 'pol': 'Polski',
    'ces': 'Čeština', 'dan': 'Dansk', 'swe': 'Svenska', 'nor': 'Norsk bokmål',
    'fin': 'Suomi', 'tur': 'Türkçe', 'ukr': 'Українська', 'ell': 'Ελληνικά',
    'jpn': '日本語', 'kor': '한국어', 'ara': 'العربية', 'heb': 'עברית',
}
DEFAULT = 'deu+eng'

def choices(translate, selected=DEFAULT):
    result = {translate('Deutsch und Englisch'): DEFAULT} | {
        f'{name} ({code})': code for code, name in LANGUAGES.items()}
    if valid_language(selected) == selected and selected not in result.values():
        result[' + '.join(LANGUAGES[code] for code in selected.split('+'))] = selected
    return result

def valid_language(value):
    if not isinstance(value, str):
        return DEFAULT
    parts = value.split('+')
    return value if 1 <= len(parts) <= len(LANGUAGES) and len(set(parts)) == len(parts) and all(code in LANGUAGES for code in parts) else DEFAULT

def require_data(language, directory):
    if language != valid_language(language):
        raise ValueError('Unsupported OCR language')
    missing = [code for code in language.split('+') if not (directory / f'{code}.traineddata').is_file()]
    if missing:
        raise ValueError('OCR: ' + ', '.join(missing) + ' (.traineddata)')
