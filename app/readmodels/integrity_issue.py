"""One vocabulary for "problems the system found in its own knowledge".

Four detectors already exist — duplicate entity, claim conflict, unlinked literal,
unresolved predicate — and each surface had started to name them its own way
(待确认 / 需要审核 / 冲突 / 可能重复). That is how a product ends up saying the same
thing four ways and none of them precisely. Every detector now reports an
:class:`IntegrityIssue`, and every surface renders *that* instead of inventing a
phrase; the words live here, once.

Three rules, each about not overstating what was found:

* **A finding is not an error.** ``severity`` is ``info`` or ``warning``, never
  ``error``. Two similar names are usually two different things and a literal object
  is legitimate; the system reports candidates, a human decides.
* **A predicate is never spelled as an identifier.** ``predicate_label`` may be
  ``None`` for a predicate the registry does not name, and a ``None`` label produces a
  sentence that mentions no predicate at all. Falling back to ``has_ceo`` would leak
  internal vocabulary into prose the first time a label is missing (ADR-011).
* **Actions are named, not described.** ``available_actions`` carries identifiers
  (``merge``, ``not_same``, ``confirm``, ``ignore``, ``resolve_conflict``) so a caller
  maps them onto its own controls rather than parsing our prose.

Pure module: no database, no framework, no LLM.
"""
from __future__ import annotations

from dataclasses import dataclass, field

# What was found.
DUPLICATE_ENTITY = 'duplicate_entity'
CLAIM_CONFLICT = 'claim_conflict'
OBJECT_LINK = 'object_link'
KINDS = (DUPLICATE_ENTITY, CLAIM_CONFLICT, OBJECT_LINK)

# How much it should interrupt. Never 'error': nothing here is a defect yet.
INFO = 'info'
WARNING = 'warning'
SEVERITIES = (INFO, WARNING)

# How serious each finding is by default, so two callers cannot disagree.
DEFAULT_SEVERITY = {
    DUPLICATE_ENTITY: WARNING,
    CLAIM_CONFLICT: WARNING,
    # A suggestion about a free-text object interrupts nobody: the claim is intact and
    # correct as written, it is merely not connected to anything yet.
    OBJECT_LINK: INFO,
}

# The operations a surface may offer for each kind. Identifiers, not labels.
ACTIONS = {
    DUPLICATE_ENTITY: ('merge', 'not_same'),
    CLAIM_CONFLICT: ('resolve_conflict',),
    OBJECT_LINK: ('confirm', 'ignore'),
}


@dataclass(frozen=True)
class IntegrityIssue:
    """One thing the system noticed about its own knowledge, in product language."""

    kind: str
    title: str
    description: str = ''
    severity: str = WARNING
    affected_ids: tuple[str, ...] = field(default_factory=tuple)
    available_actions: tuple[str, ...] = field(default_factory=tuple)

    def to_dict(self) -> dict:
        return {
            'kind': self.kind,
            'severity': self.severity,
            'title': self.title,
            'description': self.description,
            'affected_ids': list(self.affected_ids),
            'available_actions': list(self.available_actions),
        }


def _label_or_none(predicate: str, label: str | None) -> str | None:
    """The predicate in words, or nothing. Never the canonical identifier."""
    return label or None


def duplicate_entity_issue(pair: dict) -> IntegrityIssue:
    """Two entity rows that may name one thing."""
    a, b = pair.get('entity_a') or {}, pair.get('entity_b') or {}
    left, right = str(a.get('name') or ''), str(b.get('name') or '')
    claims = (a.get('claims') or 0) + (b.get('claims') or 0)
    detail = (f'合并后这 {claims} 条知识会归到同一个主体名下。'
              '合并不会自动消除事实冲突，也不会替你判断哪条成立。') if claims else \
             '两边都还没有知识挂在上面，合并不会改变任何一条陈述。'
    return IntegrityIssue(
        kind=DUPLICATE_ENTITY,
        severity=DEFAULT_SEVERITY[DUPLICATE_ENTITY],
        title=f'「{left}」与「{right}」可能是同一个主体',
        description=detail,
        affected_ids=(str(a.get('id') or ''), str(b.get('id') or '')),
        available_actions=ACTIONS[DUPLICATE_ENTITY],
    )


def claim_conflict_issue(conflict: dict) -> IntegrityIssue:
    """Two statements that cannot both hold.

    The predicate is named only when the registry names it. Without a label the
    sentence stays true and says less, which is the right way round.
    """
    subject = str(conflict.get('subject_name') or '')
    label = _label_or_none(str(conflict.get('predicate') or ''),
                           conflict.get('predicate_label'))
    objects = [str(o) for o in (conflict.get('objects') or []) if o]
    values = ' 与 '.join(f'「{o}」' for o in objects[:2])
    title = (f'{subject} 的「{label}」有两个取值' if label
             else f'{subject} 上有两条互不相容的陈述')
    parts = [f'知识库里同时记着 {values}。']
    if conflict.get('functional'):
        parts.append('这是一个单值关系，同一时间只应有一个取值，需要你判断哪条成立。')
    else:
        parts.append('两条可能各有适用条件，需要你判断是否都保留。')
    parts.append('被取代的那条不会删除，会作为历史保留。')
    return IntegrityIssue(
        kind=CLAIM_CONFLICT,
        severity=DEFAULT_SEVERITY[CLAIM_CONFLICT],
        title=title,
        description=''.join(parts),
        affected_ids=tuple(str(i) for i in (conflict.get('claim_ids') or []) if i),
        available_actions=ACTIONS[CLAIM_CONFLICT],
    )


def object_link_issue(proposal: dict) -> IntegrityIssue:
    """A free-text object that may refer to an entity the wiki already has."""
    text = str(proposal.get('object_text') or '')
    subject = str(proposal.get('subject_label') or '')
    label = _label_or_none(str(proposal.get('predicate') or ''),
                           proposal.get('predicate_label'))
    names = [str(c.get('name')) for c in (proposal.get('candidates') or []) if c.get('name')]
    where = f'{subject} 的「{label}」' if label else f'{subject} 上'
    if not names:
        detail = '系统没有找到它指的是哪个已有主体。'
    elif len(names) == 1:
        detail = f'可能是「{names[0]}」。确认后会连接到这个主体，不新建、也不合并。'
    else:
        detail = ('多个主体都声明了这个名称：' + '、'.join(f'「{n}」' for n in names)
                  + '。需要你选一个，系统不替你做决定。')
    return IntegrityIssue(
        kind=OBJECT_LINK,
        severity=DEFAULT_SEVERITY[OBJECT_LINK],
        title=f'{where}记着「{text}」，还没有连接到主体',
        description=detail,
        affected_ids=(str(proposal.get('claim_id') or ''),),
        available_actions=ACTIONS[OBJECT_LINK],
    )
