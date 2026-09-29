"""Workflow Application — use-case orchestration that spans agents.

The Research use case (create a task, run the multi-step pipeline, propose the findings
as knowledge) is orchestration, not a single repository call, so it belongs here rather
than in the route handler. Other use-case orchestration (Ingest / Extract / Ask / Review /
Correct) will migrate to this module as Phase 1 continues; the route handlers stay thin.
"""
from __future__ import annotations

from ..models import ResearchCreate
from ..repositories import ResearchRepository
from . import NotFoundError


def create_research(payload: ResearchCreate) -> dict:
    rid = ResearchRepository().create(question_id=payload.question_id,
                                      question_text=payload.question_text)
    return {'id': rid, 'status': 'open'}


async def run_research(task_id: str) -> dict:
    """Run a research task's pipeline and persist the findings.

    The pipeline is an agent workflow; this function owns the status transitions around
    it and maps a missing task to ``NotFoundError`` (HTTP 404) and any pipeline failure to
    a bare exception the adapter translates to HTTP 502.
    """
    research = ResearchRepository()
    task = research.get(task_id)
    if not task:
        raise NotFoundError('Research task not found')
    research.set_status(task_id, 'running')
    from ..workflows.agent_workflow import run_research_pipeline
    result = await run_research_pipeline(task['question_text'])
    research.set_status(task_id, 'completed', result['answer'])
    return {'id': task_id, 'status': 'completed', 'findings': result['answer'],
            'agent': result['agent'], 'packet_id': result.get('packet_id')}
