"""Offline evaluation harness for the extraction path (task §16/§17).

Pure, deterministic and offline: nothing here imports FastAPI, AgentScope,
sqlite3 or the LLM client, so the numbers are reproducible in CI with no API
key and no database. The harness only reads already-produced model drafts and
runs them through the real Knowledge Compilation Pipeline.
"""
from .extraction_eval import (CaseResult, CompiledDraft, ExtractionCase,
                             compile_draft, evaluate_case, evaluate_dataset,
                             load_cases)
from .metrics import f1, precision, recall

__all__ = [
    'f1', 'precision', 'recall',
    'ExtractionCase', 'CompiledDraft', 'CaseResult',
    'load_cases', 'compile_draft', 'evaluate_case', 'evaluate_dataset',
]
