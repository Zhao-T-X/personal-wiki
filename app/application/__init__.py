"""Application Layer — the business orchestration boundary above the HTTP adapter.

This package is the convergence target described in
``docs/architecture/CURRENT-ARCHITECTURE.md`` and ``ARCHITECTURE-GAP.md``. The FastAPI
routes in ``app/main.py`` are an HTTP adapter only: validate, call an application
function, return. They must never read or write knowledge state through the
repositories directly, and must never run multi-step business orchestration.

Owning the orchestration here — rather than in the route handlers — is what lets
``main.py`` shrink to a router and gives every business capability a single owner:

* ``knowledge`` — read & write knowledge-shaped state (entities, claims, relations,
  ideas, questions, events).
* ``search``   — every retrieval, the single source the API/QA/Research read from.
* ``operation``— the write authority for knowledge. Today some paths (status PATCH,
  claim-relation resolution, entity merge) still mutate repositories *inside this
  layer*; Phase 3 moves them onto the Operation framework as first-class operations
  (ARCHIVE / RESTORE / ACCEPT / MERGE). Either way the API never writes knowledge itself.
* ``workflow`` — use-case orchestration that spans agents (research for now).

These modules may use the domain, repositories and workflows, but not FastAPI or
``sqlite3`` directly. Errors that the HTTP layer must translate are raised as the
exceptions below; the adapter maps them to status codes.
"""
from __future__ import annotations


class ApplicationError(Exception):
    """Base class for errors an HTTP adapter should translate to a status code."""


class ValidationError(ApplicationError):
    """Input violates a business rule. Maps to HTTP 422."""


class BadRequestError(ApplicationError):
    """The request itself is malformed / not applicable. Maps to HTTP 400."""


class NotFoundError(ApplicationError):
    """The targeted object does not exist. Maps to HTTP 404."""


class ConflictError(ApplicationError):
    """A unique constraint was violated (name / content already exists). Maps to HTTP 409."""
