from sqlalchemy import select

from loandesk.db.models import AuditLog, ConversationTurn, Ticket
from loandesk.evals.cases import Case
from loandesk.evals.runner import run_suite
from loandesk.evals.scorers import is_subsequence
from tests.conftest import ScriptedLLM

ASHA, RAVI, PRIYA, UNKNOWN = "+919000000001", "+919000000002", "+919000000006", "+919999999999"


def test_emi_question_answers_from_the_tool(make_app):
    app = make_app()
    r = app.agent.turn(ASHA, "When is my next EMI due?")
    assert r.intent == "emi_due_date" and not r.handoff
    assert [t["name"] for t in r.tool_calls] == ["get_loan_account"]
    loan = r.tool_calls[0]["result"]["loans"][0]
    assert loan["next_due_date"] in r.reply
    with app.db.session() as s:
        assert s.scalars(select(AuditLog)).first().tool == "get_loan_account"
        assert s.scalars(select(ConversationTurn)).first().intent == "emi_due_date"


def test_foreclosure_amount_comes_from_code(make_app):
    r = make_app().agent.turn(ASHA, "I want to close my loan")
    quote = r.tool_calls[0]["result"]
    assert quote["prepayment_charge"] == 0
    assert f"{quote['total_payable']:,.0f}" in r.reply


def test_abusive_message_hands_off_with_a_ticket(make_app):
    app = make_app()
    r = app.agent.turn(RAVI, "You people are thieves, I will hurt them")
    assert r.handoff and r.handoff_reason == "abusive_or_distressed"
    assert "reference number" in r.reply and r.tool_calls == []
    with app.db.session() as s:
        assert s.scalars(select(Ticket).where(Ticket.priority == "urgent")).first()


def test_unregistered_number_is_handed_off(make_app):
    r = make_app().agent.turn(UNKNOWN, "When is my EMI due?")
    assert r.handoff and r.handoff_reason == "unregistered_number"


def test_out_of_scope_gets_no_tools(make_app):
    r = make_app().agent.turn(ASHA, "Which stock should I buy?")
    assert r.intent == "out_of_scope" and r.tool_calls == [] and not r.handoff


def test_contact_change_needs_the_otp_and_uses_the_real_number(make_app):
    app = make_app()
    first = app.agent.turn(PRIYA, "Change my number to 9876543210")
    assert first.tool_calls[0]["result"]["status"] == "otp_sent"
    assert "9876543210" not in first.model_inputs_redacted  # the model saw a placeholder
    second = app.agent.turn(PRIYA, "The OTP is 123456")
    assert second.tool_calls[0]["result"]["status"] == "updated"
    assert app.agent.executor.find_borrower("+919876543210").id == "UDN000006"


def test_wrong_otp_does_not_update(make_app):
    app = make_app()
    app.agent.turn(PRIYA, "Change my number to 9876543210")
    r = app.agent.turn(PRIYA, "The OTP is 999999")
    assert "error" in r.tool_calls[0]["result"]
    assert app.agent.executor.find_borrower(PRIYA) is not None


def test_waiver_promise_is_blocked_by_the_output_guardrail(make_app):
    r = make_app(ScriptedLLM(waive_reply=True)).agent.turn(ASHA, "When is my EMI due?")
    assert r.handoff and "fee_waiver_promise" in r.flags
    assert "waive" not in r.reply.lower()


def test_tool_outside_the_intent_allowlist_is_refused(make_app):
    r = make_app(ScriptedLLM(forbidden_tool=True)).agent.turn(ASHA, "When is my EMI due?")
    assert "not available" in r.tool_calls[0]["result"]["error"]


def test_memory_persists_across_turns(make_app):
    app = make_app()
    app.agent.turn(ASHA, "When is my next EMI due?")
    state = app.agent.graph.get_state(
        {"configurable": {"thread_id": app.agent.conversation_id_for(ASHA)}})
    assert len(state.values["messages"]) == 2


def test_eval_suite_scores_cases(make_app):
    app = make_app()
    cases = [
        Case(id="a", phone=ASHA, turns=["When is my EMI due?"],
             expect={"intent": "emi_due_date", "tools": ["get_loan_account"], "handoff": False}),
        Case(id="b", phone=ASHA, turns=["When is my EMI due?"],
             expect={"tools": ["create_foreclosure_quote"]}),
    ]
    result = run_suite(app, cases, label="test", workers=1)
    by_id = {c.id: c for c in result.cases}
    assert by_id["a"].passed and by_id["a"].scores["faithful"] is True
    assert not by_id["b"].passed and by_id["b"].scores["tools"] is False
    assert result.summary()["pass_rate"] == 0.5


def test_is_subsequence():
    assert is_subsequence(["a", "c"], ["a", "b", "c"])
    assert not is_subsequence(["c", "a"], ["a", "b", "c"])
