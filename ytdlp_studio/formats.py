"""Simple output choices mapped to the shared backend presets."""

AUDIO_CHOICES = {
    "MP3": {"320 kbps": "MP3 320 kbps", "192 kbps": "MP3 192 kbps"},
    "M4A": {"Best": "M4A best"},
    "Opus": {"Best": "Opus best"},
    "FLAC": {"Lossless container": "FLAC"},
}
VIDEO_CHOICES = {"MP4": {"1080p": "1080p MP4", "720p": "720p MP4",
                         "480p": "Small compatible MP4", "Best": "Best video + audio"}}


def choices_for(mode: str) -> dict:
    return VIDEO_CHOICES if mode == "video" else AUDIO_CHOICES


def selection_for(mode: str, preset: str) -> tuple[str, str]:
    choices = choices_for(mode)
    for format_name, qualities in choices.items():
        for quality, candidate in qualities.items():
            if preset == candidate:
                return format_name, quality
    format_name = next(iter(choices))
    return format_name, next(iter(choices[format_name]))
