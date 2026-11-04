"""Language names and codes.

Whisper hears ~99 languages; M2M100 translates between 100. Codes are
ISO 639-1 where one exists. A few Whisper codes differ from M2M100's.
"""

from __future__ import annotations

# code: (English name, native name)
LANGUAGES: dict[str, tuple[str, str]] = {
    "af": ("Afrikaans", "Afrikaans"), "am": ("Amharic", "አማርኛ"), "ar": ("Arabic", "العربية"),
    "as": ("Assamese", "অসমীয়া"), "az": ("Azerbaijani", "Azərbaycan"), "ba": ("Bashkir", "Башҡорт"),
    "be": ("Belarusian", "Беларуская"), "bg": ("Bulgarian", "Български"), "bn": ("Bengali", "বাংলা"),
    "br": ("Breton", "Brezhoneg"), "bs": ("Bosnian", "Bosanski"), "ca": ("Catalan", "Català"),
    "cs": ("Czech", "Čeština"), "cy": ("Welsh", "Cymraeg"), "da": ("Danish", "Dansk"),
    "de": ("German", "Deutsch"), "el": ("Greek", "Ελληνικά"), "en": ("English", "English"),
    "es": ("Spanish", "Español"), "et": ("Estonian", "Eesti"), "fa": ("Persian", "فارسی"),
    "fi": ("Finnish", "Suomi"), "fr": ("French", "Français"), "fy": ("Frisian", "Frysk"),
    "ga": ("Irish", "Gaeilge"), "gd": ("Scottish Gaelic", "Gàidhlig"), "gl": ("Galician", "Galego"),
    "gu": ("Gujarati", "ગુજરાતી"), "ha": ("Hausa", "Hausa"), "he": ("Hebrew", "עברית"),
    "hi": ("Hindi", "हिन्दी"), "hr": ("Croatian", "Hrvatski"), "ht": ("Haitian Creole", "Kreyòl"),
    "hu": ("Hungarian", "Magyar"), "hy": ("Armenian", "Հայերեն"), "id": ("Indonesian", "Bahasa Indonesia"),
    "ig": ("Igbo", "Igbo"), "is": ("Icelandic", "Íslenska"), "it": ("Italian", "Italiano"),
    "ja": ("Japanese", "日本語"), "jv": ("Javanese", "Basa Jawa"), "ka": ("Georgian", "ქართული"),
    "kk": ("Kazakh", "Қазақ"), "km": ("Khmer", "ខ្មែរ"), "kn": ("Kannada", "ಕನ್ನಡ"),
    "ko": ("Korean", "한국어"), "lb": ("Luxembourgish", "Lëtzebuergesch"), "ln": ("Lingala", "Lingála"),
    "lo": ("Lao", "ລາວ"), "lt": ("Lithuanian", "Lietuvių"), "lv": ("Latvian", "Latviešu"),
    "mg": ("Malagasy", "Malagasy"), "mk": ("Macedonian", "Македонски"), "ml": ("Malayalam", "മലയാളം"),
    "mn": ("Mongolian", "Монгол"), "mr": ("Marathi", "मराठी"), "ms": ("Malay", "Bahasa Melayu"),
    "my": ("Burmese", "မြန်မာ"), "ne": ("Nepali", "नेपाली"), "nl": ("Dutch", "Nederlands"),
    "no": ("Norwegian", "Norsk"), "oc": ("Occitan", "Occitan"), "pa": ("Punjabi", "ਪੰਜਾਬੀ"),
    "pl": ("Polish", "Polski"), "ps": ("Pashto", "پښتو"), "pt": ("Portuguese", "Português"),
    "ro": ("Romanian", "Română"), "ru": ("Russian", "Русский"), "sd": ("Sindhi", "سنڌي"),
    "si": ("Sinhala", "සිංහල"), "sk": ("Slovak", "Slovenčina"), "sl": ("Slovenian", "Slovenščina"),
    "so": ("Somali", "Soomaali"), "sq": ("Albanian", "Shqip"), "sr": ("Serbian", "Српски"),
    "su": ("Sundanese", "Basa Sunda"), "sv": ("Swedish", "Svenska"), "sw": ("Swahili", "Kiswahili"),
    "ta": ("Tamil", "தமிழ்"), "th": ("Thai", "ไทย"), "tl": ("Tagalog", "Tagalog"),
    "tr": ("Turkish", "Türkçe"), "uk": ("Ukrainian", "Українська"), "ur": ("Urdu", "اردو"),
    "uz": ("Uzbek", "Oʻzbek"), "vi": ("Vietnamese", "Tiếng Việt"), "yi": ("Yiddish", "ייִדיש"),
    "yo": ("Yoruba", "Yorùbá"), "zh": ("Chinese", "中文"), "zu": ("Zulu", "isiZulu"),
}

# Whisper's code -> ours, where they differ.
FROM_WHISPER = {"jw": "jv", "haw": "en", "yue": "zh", "la": "it", "sa": "hi", "sn": "zu", "tt": "ru",
                "tk": "tr", "bo": "zh", "nn": "no", "fo": "da", "mt": "it"}

# Whisper can't be told these (it doesn't know them as input), they are targets only.
WHISPER_UNKNOWN = {"fy", "gd", "ga", "ig", "ff", "ns", "ss", "tn", "wo", "xh", "zu", "ilo", "ceb", "lg"}

RTL = {"ar", "fa", "he", "ps", "sd", "ur", "yi"}


def to_whisper(code: str) -> str:
    return {"jv": "jw"}.get(code, code)


def from_whisper(code: str) -> str:
    return FROM_WHISPER.get(code, code)


def label(code: str) -> str:
    name, native = LANGUAGES.get(code, (code, code))
    return name if name == native else f"{name} · {native}"
