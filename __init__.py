"""Hermes Internal Bridge (HIB) plugin entrypoint.

The Hermes plugin host imports this module and calls :func:`register` with the
plugin context. Standalone plugin directories are not installed as packages, so
this entrypoint bootstraps the project root onto ``sys.path`` first (the same
pattern the reference plugin uses for its standalone entrypoints).
"""

from __future__ import annotations

import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)


def register(ctx) -> None:
    """Register the plugin with the Hermes plugin host."""
    from hermes_internal_bridge.plugin import register_plugin

    register_plugin(ctx)
