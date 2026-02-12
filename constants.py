"""
Centralized constants for AniGrab.
"""

# Video file extensions (primary media files)
VIDEO_EXTENSIONS = frozenset({
    '.mkv',   # Matroska - most common for anime
    '.mp4',   # MPEG-4 Part 14
    '.avi',   # Audio Video Interleave
    '.m4v',   # Apple MP4 variant
    '.webm',  # WebM (VP8/VP9)
    '.ts',    # MPEG Transport Stream
    '.flv',   # Flash Video
    '.mov',   # QuickTime
})

# Subtitle file extensions
SUBTITLE_EXTENSIONS = frozenset({
    '.ass',   # Advanced SubStation Alpha
    '.srt',   # SubRip
    '.sub',   # MicroDVD
    '.ssa',   # SubStation Alpha
    '.vtt',   # WebVTT (for web-sourced content)
})

# All media extensions (video + subtitles)
MEDIA_EXTENSIONS = VIDEO_EXTENSIONS | SUBTITLE_EXTENSIONS

# Regex pattern for video extensions (for use in re.sub calls)
VIDEO_EXT_PATTERN = r'\.(mkv|mp4|avi|m4v|webm|ts|flv|mov)$'

# Regex pattern for all media extensions
MEDIA_EXT_PATTERN = r'\.(mkv|mp4|avi|m4v|webm|ts|flv|mov|ass|srt|sub|ssa|vtt)$'
