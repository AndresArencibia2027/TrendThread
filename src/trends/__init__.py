"""Gemini-powered trend intelligence engine for TrendThread.

This package replaces the legacy multi-source scraping workflow (X/Twitter,
GDELT news, BigQuery Google Trends, KnowYourMeme, SerpApi) with a single
server-side Gemini / Vertex AI trend engine.

The legacy code is NOT deleted; it is disabled behind feature flags. See
``src/trends/config.py``.
"""
