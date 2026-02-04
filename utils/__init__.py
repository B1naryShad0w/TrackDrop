"""Shared utilities for TrackDrop.

This package consolidates all utility functions:
- http: HTTP request utilities with retry logic
- text: String normalization and text processing
- core: Tagging, file operations, and other core utilities
"""

# HTTP utilities
from utils.http import make_request_with_retries, async_make_request_with_retries

# Text utilities
from utils.text import normalize_string, clean_title, sanitize_for_matching, sanitize_filename

# Core utilities (from original utils.py)
from utils.core import (
    get_user_history_path,
    initialize_streamrip_db,
    remove_empty_folders,
    update_status_file,
    Tagger,
)

__all__ = [
    # HTTP
    'make_request_with_retries',
    'async_make_request_with_retries',
    # Text
    'normalize_string',
    'clean_title',
    'sanitize_for_matching',
    'sanitize_filename',
    # Core
    'get_user_history_path',
    'initialize_streamrip_db',
    'remove_empty_folders',
    'update_status_file',
    'Tagger',
]
