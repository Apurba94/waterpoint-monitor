"""Waterpoint Monitor: track water supply and quality."""
from . import core
from .web import create_app

__all__ = ["core", "create_app"]
