#!/usr/bin/env python3
"""
Cleanup script to remove obsolete environment variables from .env and docker-compose files.
These variables were used by the old tag-based playlist mode which has been removed.

Usage:
    python cleanup_obsolete_env.py [--dry-run]

Options:
    --dry-run    Show what would be removed without making changes
"""

import re
import sys
import os

# Obsolete variables that should be removed
OBSOLETE_VARS = {
    'TRACKDROP_TARGET_COMMENT',
    'TRACKDROP_LASTFM_TARGET_COMMENT',
    'TRACKDROP_LLM_TARGET_COMMENT',
    'TRACKDROP_ALBUM_RECOMMENDATION_COMMENT',
    'TRACKDROP_PLAYLIST_MODE',
    'TRACKDROP_PLAYLIST_HISTORY_FILE',
    # Also check without prefix
    'TARGET_COMMENT',
    'LASTFM_TARGET_COMMENT',
    'LLM_TARGET_COMMENT',
    'ALBUM_RECOMMENDATION_COMMENT',
    'PLAYLIST_MODE',
    'PLAYLIST_HISTORY_FILE',
}


def cleanup_env_file(filepath, dry_run=False):
    """Remove obsolete variables from a .env file."""
    if not os.path.exists(filepath):
        print(f"File not found: {filepath}")
        return 0

    with open(filepath, 'r') as f:
        lines = f.readlines()

    new_lines = []
    removed = []

    for line in lines:
        stripped = line.strip()
        # Skip empty lines and comments
        if not stripped or stripped.startswith('#'):
            new_lines.append(line)
            continue

        # Check if this line sets an obsolete variable
        match = re.match(r'^([A-Z_]+)\s*=', stripped)
        if match:
            var_name = match.group(1)
            if var_name in OBSOLETE_VARS:
                removed.append(stripped)
                continue

        new_lines.append(line)

    if removed:
        print(f"\n{filepath}:")
        print(f"  Found {len(removed)} obsolete variable(s):")
        for var in removed:
            print(f"    - {var}")

        if not dry_run:
            with open(filepath, 'w') as f:
                f.writelines(new_lines)
            print(f"  Removed from file.")
        else:
            print(f"  (dry-run: no changes made)")
    else:
        print(f"\n{filepath}: No obsolete variables found.")

    return len(removed)


def cleanup_docker_compose(filepath, dry_run=False):
    """Remove obsolete environment variables from docker-compose.yml."""
    if not os.path.exists(filepath):
        print(f"File not found: {filepath}")
        return 0

    with open(filepath, 'r') as f:
        content = f.read()

    original_content = content
    removed = []

    # Pattern to match environment variable lines in docker-compose
    # Handles both "- VAR=value" and "VAR: value" formats
    for var in OBSOLETE_VARS:
        # Match "- VAR=..." or "- VAR: ..." format (with optional quotes)
        patterns = [
            rf'^\s*-\s*{var}=[^\n]*\n',
            rf'^\s*-\s*{var}:\s*[^\n]*\n',
            rf'^\s*{var}:\s*[^\n]*\n',
            rf'^\s*-\s*"{var}=[^"]*"\n',
            rf"^\s*-\s*'{var}=[^']*'\n",
        ]
        for pattern in patterns:
            matches = re.findall(pattern, content, re.MULTILINE)
            for match in matches:
                if match.strip() not in [r.strip() for r in removed]:
                    removed.append(match)
            content = re.sub(pattern, '', content, flags=re.MULTILINE)

    if removed:
        print(f"\n{filepath}:")
        print(f"  Found {len(removed)} obsolete variable(s):")
        for var in removed:
            print(f"    - {var.strip()}")

        if not dry_run:
            with open(filepath, 'w') as f:
                f.write(content)
            print(f"  Removed from file.")
        else:
            print(f"  (dry-run: no changes made)")
    else:
        print(f"\n{filepath}: No obsolete variables found.")

    return len(removed)


def main():
    dry_run = '--dry-run' in sys.argv

    print("=" * 60)
    print("TrackDrop - Obsolete Environment Variable Cleanup")
    print("=" * 60)

    if dry_run:
        print("\n[DRY RUN MODE - No changes will be made]\n")

    print("Obsolete variables being checked:")
    for var in sorted(OBSOLETE_VARS):
        print(f"  - {var}")

    total_removed = 0

    # Check common locations
    locations = [
        '.env',
        'docker/.env',
        'docker/docker-compose.yml',
        'docker-compose.yml',
    ]

    for loc in locations:
        if os.path.exists(loc):
            if loc.endswith('.yml') or loc.endswith('.yaml'):
                total_removed += cleanup_docker_compose(loc, dry_run)
            else:
                total_removed += cleanup_env_file(loc, dry_run)

    print("\n" + "=" * 60)
    if total_removed > 0:
        if dry_run:
            print(f"Total: {total_removed} obsolete variable(s) would be removed.")
            print("Run without --dry-run to apply changes.")
        else:
            print(f"Total: {total_removed} obsolete variable(s) removed.")
    else:
        print("No obsolete variables found in any files.")
    print("=" * 60)


if __name__ == '__main__':
    main()
