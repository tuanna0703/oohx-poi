"""Normalize + dedupe pipeline (Phase 3 + 4).

Phase 3: ``NormalizePipeline`` reads a ``raw_pois`` row, runs the per-source
extractor + the language-aware normalizers, generates a name embedding, and
writes a ``processed_pois`` row.
"""
