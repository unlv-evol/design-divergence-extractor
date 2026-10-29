"""dde-prep: data preparation for the Beyond Refactorings study.

Builds the case manifest that ``dde`` reads, keeps RePatch outcomes in a restricted file,
runs Checkpoint 1, and supports the coding pilot. Unlike ``dde``, this package may read
outcomes. It depends on ``dde``; ``dde`` never imports it (tested).
"""

__version__ = "0.2.0"
