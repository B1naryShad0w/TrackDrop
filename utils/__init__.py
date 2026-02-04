"""Shared utilities for TrackDrop.

This package consolidates all utility functions:
- http: HTTP request utilities with retry logic
- text: String normalization and text processing
- core: Tagging, file operations, and other core utilities
"""

import sys

# HTTP utilities
try:
    from utils.http import make_request_with_retries, async_make_request_with_retries
except ImportError as e:
    print(f"[utils] Failed to import from utils.http: {e}", file=sys.stderr)
    raise

# Text utilities
try:
    from utils.text import normalize_string, clean_title, sanitize_for_matching, sanitize_filename
except ImportError as e:
    print(f"[utils] Failed to import from utils.text: {e}", file=sys.stderr)
    raise

# Core utilities (from original utils.py)
try:
    from utils.core import (
        get_user_history_path,
        initialize_streamrip_db,
        remove_empty_folders,
        update_status_file,
        Tagger,
    )
except ImportError as e:
    print(f"[utils] Failed to import from utils.core: {e}", file=sys.stderr)
    raise

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
