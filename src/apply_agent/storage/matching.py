"""Which existing application, if any, a new message belongs to (see D4).

Evidence is tried from strongest to weakest:

1. The thread is already linked to an application.
2. Same company and same role, or a spelling the user merged into one (D54).
3. The role is unknown on one side, and the company has exactly one
   application. With two or more, guessing could merge distinct
   applications, so a new one is created instead.
"""

from sqlalchemy import select
from sqlalchemy.orm import Session

from apply_agent.domain import company_key, role_key
from apply_agent.storage.schema import (
    ApplicationAliasRow,
    ApplicationRow,
    ApplicationThreadRow,
)


def resolve_application(
    session: Session, thread_id: str, company: str, role: str | None
) -> ApplicationRow | None:
    linked = session.get(ApplicationThreadRow, thread_id)
    if linked is not None:
        return linked.application

    ckey, rkey = company_key(company), role_key(role)
    exact = session.scalar(
        select(ApplicationRow).where(
            ApplicationRow.company_key == ckey, ApplicationRow.role_key == rkey
        )
    )
    if exact is not None:
        return exact
    alias = session.get(ApplicationAliasRow, (ckey, rkey))
    if alias is not None:
        return session.get(ApplicationRow, alias.application_id)

    same_company = list(
        session.scalars(select(ApplicationRow).where(ApplicationRow.company_key == ckey).limit(2))
    )
    if len(same_company) == 1 and (rkey == "" or same_company[0].role_key == ""):
        return same_company[0]
    return None
