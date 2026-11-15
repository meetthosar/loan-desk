# Solution blueprint

## Decision tree
1. Unregistered number asking about an account → handoff.
2. Abusive, distressed, or asks for a person → handoff (urgent if distressed).
3. Intent unknown, or confidence below 0.75 → handoff.
4. Out of scope → polite decline, no tools.
5. Otherwise → retrieve policy passages → responder with the intent's tools → output check → reply, or handoff if the check fails.

## The 6 tools
| Tool | Input (model-visible) | Does |
| --- | --- | --- |
| get_loan_account | loan_id? | Loans: EMI, next due date, outstanding, overdue, status |
| get_payment_status | loan_id?, last_n | Recent payments with failure reasons |
| create_foreclosure_quote | loan_id?, foreclosure_date? | Exact closure amount, valid 3 days |
| update_contact | field, new_value, otp? | Sends an OTP, then changes phone or email |
| create_zendesk_ticket | category, priority, subject, description | Ticket for a human team |
| handoff_to_human | reason, summary | Passes the conversation on |

No tool takes a borrower id: the harness adds it.

## Escalation rules
| Trigger | Priority |
| --- | --- |
| Distress or threats | urgent |
| Asked for a human; guardrail failure; model error | high |
| Complaint about field officer conduct | high (ticket) |
| Other complaints | normal (ticket) |

## The 0.75 threshold
A model's own confidence score is not calibrated. It is logged on every turn and used as one signal; the threshold is tuned against the eval set, and the explicit rules above carry most of the weight.
