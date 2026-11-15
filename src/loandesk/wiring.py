"""Builds the agent and its parts from settings. The one place that knows how they connect."""

from __future__ import annotations

from contextlib import ExitStack
from dataclasses import dataclass

from loandesk.agent.graph import LoanDeskAgent
from loandesk.config import Settings
from loandesk.db.session import Database, init_db
from loandesk.embeddings import get_embedder
from loandesk.integrations.otp import OtpService
from loandesk.integrations.zendesk import get_ticket_system
from loandesk.knowledge.retriever import Retriever
from loandesk.llm import LLM, Ledger, OpenRouterLLM
from loandesk.tools.executor import ToolExecutor
from loandesk.tracing import Tracer
from loandesk.vectorstores import get_store


@dataclass
class App:
    settings: Settings
    db: Database
    ledger: Ledger
    tracer: Tracer
    agent: LoanDeskAgent
    stack: ExitStack

    def close(self) -> None:
        self.stack.close()


def make_ledger(settings: Settings) -> Ledger:
    return Ledger(settings.state_dir / "spend.json", settings.budget_usd)


def make_checkpointer(settings: Settings, stack: ExitStack):
    if settings.checkpointer == "postgres":
        from langgraph.checkpoint.postgres import PostgresSaver

        saver = stack.enter_context(
            PostgresSaver.from_conn_string(settings.database_url.replace("+psycopg", ""))
        )
        saver.setup()
        return saver
    from langgraph.checkpoint.memory import InMemorySaver

    return InMemorySaver()


def build_app(settings: Settings, *, llm: LLM | None = None, store_name: str | None = None,
              ticket_system=None, embedder=None) -> App:
    stack = ExitStack()
    init_db(settings.database_url)
    db = Database(settings.database_url)
    tracer = Tracer(settings.state_dir / "traces")
    ledger = make_ledger(settings)
    llm = llm or OpenRouterLLM(settings, ledger, tracer)
    store = get_store(store_name or settings.vector_store, settings)
    stack.callback(store.close)
    embedder = embedder or get_embedder(settings.embedding_model)
    retriever = Retriever(store, embedder, k=settings.retrieval_k)
    tickets = ticket_system or get_ticket_system(db, settings.zendesk_subdomain,
                                                 settings.zendesk_email,
                                                 settings.zendesk_api_token)
    executor = ToolExecutor(db, tickets, OtpService(db, settings.otp_mode), tracer,
                            settings.phone_hash_salt)
    agent = LoanDeskAgent(settings=settings, llm=llm, db=db, retriever=retriever,
                          executor=executor, checkpointer=make_checkpointer(settings, stack))
    return App(settings=settings, db=db, ledger=ledger, tracer=tracer, agent=agent, stack=stack)
