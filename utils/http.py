"""
Shared HTTP utilities for TrackDrop.

This module provides unified retry logic for all API clients,
eliminating the duplicate implementations across listenbrainz_api,
lastfm_api, and deezer_api.
"""

import asyncio
import sys
import time
from typing import Any, Dict, Optional

import requests


def make_request_with_retries(
    method: str,
    url: str,
    headers: Optional[Dict[str, str]] = None,
    params: Optional[Dict[str, Any]] = None,
    json: Optional[Dict[str, Any]] = None,
    data: Optional[Dict[str, Any]] = None,
    max_retries: int = 5,
    retry_delay: float = 5,
    exponential_backoff: bool = False,
    initial_delay: float = 1,
    timeout: int = 30,
    service_name: str = "HTTP",
) -> Optional[requests.Response]:
    """
    Makes an HTTP request with retry logic for connection errors.

    Args:
        method: HTTP method ('GET', 'POST', 'HEAD', etc.)
        url: The URL to request
        headers: Optional request headers
        params: Optional query parameters
        json: Optional JSON body (for POST requests)
        data: Optional form data (for POST requests)
        max_retries: Maximum number of retry attempts
        retry_delay: Delay between retries (seconds) - used if exponential_backoff is False
        exponential_backoff: If True, use exponential backoff instead of fixed delay
        initial_delay: Initial delay for exponential backoff
        timeout: Request timeout in seconds
        service_name: Name of the service for log messages

    Returns:
        The response object if successful, None if all retries failed

    Raises:
        requests.exceptions.HTTPError: If the request returns an HTTP error status
        requests.exceptions.RequestException: For other request errors after all retries
    """
    for attempt in range(max_retries):
        try:
            if method.upper() == "GET":
                response = requests.get(url, headers=headers, params=params, timeout=timeout)
            elif method.upper() == "POST":
                if json:
                    response = requests.post(url, headers=headers, json=json, timeout=timeout)
                elif data:
                    response = requests.post(url, headers=headers, data=data, timeout=timeout)
                else:
                    response = requests.post(url, headers=headers, timeout=timeout)
            elif method.upper() == "HEAD":
                response = requests.head(url, headers=headers, params=params, timeout=timeout)
            else:
                raise ValueError(f"Unsupported HTTP method: {method}")

            response.raise_for_status()
            return response

        except requests.exceptions.ConnectionError as e:
            print(f"{service_name}: Connection error on attempt {attempt + 1}/{max_retries} to {url}: {e}", file=sys.stderr)
            if attempt < max_retries - 1:
                delay = initial_delay * (2 ** attempt) if exponential_backoff else retry_delay
                time.sleep(delay)
            else:
                raise

        except requests.exceptions.Timeout as e:
            print(f"{service_name}: Timeout error on attempt {attempt + 1}/{max_retries} to {url}: {e}", file=sys.stderr)
            if attempt < max_retries - 1:
                delay = initial_delay * (2 ** attempt) if exponential_backoff else retry_delay
                time.sleep(delay)
            else:
                raise

        except requests.exceptions.HTTPError as e:
            print(f"{service_name}: HTTP error on attempt {attempt + 1}/{max_retries} to {url}: "
                  f"{e.response.status_code} - {e.response.text}", file=sys.stderr)
            raise

        except requests.exceptions.RequestException as e:
            print(f"{service_name}: Request error on attempt {attempt + 1}/{max_retries} to {url}: {e}", file=sys.stderr)
            raise

    return None


async def async_make_request_with_retries(
    method: str,
    url: str,
    headers: Optional[Dict[str, str]] = None,
    params: Optional[Dict[str, Any]] = None,
    json: Optional[Dict[str, Any]] = None,
    data: Optional[Dict[str, Any]] = None,
    max_retries: int = 5,
    retry_delay: float = 5,
    exponential_backoff: bool = False,
    initial_delay: float = 1,
    timeout: int = 30,
    service_name: str = "HTTP",
) -> Optional[requests.Response]:
    """
    Makes an HTTP request with retry logic, designed for async contexts.

    This wraps the synchronous requests library in asyncio's run_in_executor
    to allow concurrent HTTP requests without blocking the event loop.

    Args:
        method: HTTP method ('GET', 'POST', 'HEAD', etc.)
        url: The URL to request
        headers: Optional request headers
        params: Optional query parameters
        json: Optional JSON body (for POST requests)
        data: Optional form data (for POST requests)
        max_retries: Maximum number of retry attempts
        retry_delay: Delay between retries (seconds) - used if exponential_backoff is False
        exponential_backoff: If True, use exponential backoff instead of fixed delay
        initial_delay: Initial delay for exponential backoff
        timeout: Request timeout in seconds
        service_name: Name of the service for log messages

    Returns:
        The response object if successful, None if all retries failed
    """
    loop = asyncio.get_event_loop()

    for attempt in range(max_retries):
        try:
            if method.upper() == "GET":
                response = await loop.run_in_executor(
                    None,
                    lambda: requests.get(url, headers=headers, params=params, timeout=timeout)
                )
            elif method.upper() == "POST":
                if json:
                    response = await loop.run_in_executor(
                        None,
                        lambda: requests.post(url, headers=headers, json=json, timeout=timeout)
                    )
                elif data:
                    response = await loop.run_in_executor(
                        None,
                        lambda: requests.post(url, headers=headers, data=data, timeout=timeout)
                    )
                else:
                    response = await loop.run_in_executor(
                        None,
                        lambda: requests.post(url, headers=headers, timeout=timeout)
                    )
            elif method.upper() == "HEAD":
                response = await loop.run_in_executor(
                    None,
                    lambda: requests.head(url, headers=headers, params=params, timeout=timeout)
                )
            else:
                raise ValueError(f"Unsupported HTTP method: {method}")

            response.raise_for_status()
            return response

        except requests.exceptions.ConnectionError as e:
            print(f"{service_name}: Connection error on attempt {attempt + 1}/{max_retries} to {url}: {e}", file=sys.stderr)
            if attempt < max_retries - 1:
                delay = initial_delay * (2 ** attempt) if exponential_backoff else retry_delay
                await asyncio.sleep(delay)
            else:
                raise

        except requests.exceptions.Timeout as e:
            print(f"{service_name}: Timeout error on attempt {attempt + 1}/{max_retries} to {url}: {e}", file=sys.stderr)
            if attempt < max_retries - 1:
                delay = initial_delay * (2 ** attempt) if exponential_backoff else retry_delay
                await asyncio.sleep(delay)
            else:
                raise

        except requests.exceptions.HTTPError as e:
            print(f"{service_name}: HTTP error on attempt {attempt + 1}/{max_retries} to {url}: "
                  f"{e.response.status_code} - {e.response.text}", file=sys.stderr)
            raise

        except requests.exceptions.RequestException as e:
            print(f"{service_name}: Request error on attempt {attempt + 1}/{max_retries} to {url}: {e}", file=sys.stderr)
            raise

    return None
