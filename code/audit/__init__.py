"""Deletion-audit agent for the Ghost Data Still Exists challenge.

The package is deliberately named ``audit`` rather than anything that collides
with the standard library: ``python code/main.py`` places ``code/`` on
``sys.path``, so a package called ``io``, ``types``, ``json`` or ``logging``
here would shadow the stdlib module of the same name for the whole process.
"""

__all__ = ["loaders", "models"]
