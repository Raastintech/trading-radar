"""
SSL certificate helpers for macOS Python environments.
"""

import os
import ssl

import certifi


def create_ssl_context() -> ssl.SSLContext:
    """Create an SSL context using certifi's CA bundle."""
    return ssl.create_default_context(cafile=certifi.where())


def get_websocket_ssl_context() -> ssl.SSLContext:
    """Return SSL context for websocket clients."""
    return create_ssl_context()


def configure_ssl_defaults() -> ssl.SSLContext:
    """
    Configure process-level SSL defaults so libraries using the default HTTPS
    context pick up certifi certificates automatically.
    """
    cafile = certifi.where()
    os.environ.setdefault("SSL_CERT_FILE", cafile)
    os.environ.setdefault("REQUESTS_CA_BUNDLE", cafile)
    context = create_ssl_context()
    ssl._create_default_https_context = lambda: create_ssl_context()
    return context
