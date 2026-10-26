"""Evaluation harness.

``code/evaluation/main.py`` scores predictions against
``dataset/sample_output.csv`` and the team's own additional cases. It is the only
place allowed to read ``sample_output.csv``; the audit pipeline never does.
"""

__all__: list[str] = []
