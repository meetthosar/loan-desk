# Intent map

Five core intents, plus two supporting ones. The full list of 10 example messages per intent (English and Hinglish) is in [`evals/messages.yaml`](../evals/messages.yaml).

| Intent | Example | Tools allowed | Ends in |
| --- | --- | --- | --- |
| emi_due_date | "Agli EMI kab hai?" | get_loan_account | Reply with date and amount |
| failed_payment | "My auto debit failed" | get_payment_status, get_loan_account, create_zendesk_ticket | Reply; ticket if money was debited but not credited |
| foreclosure_quote | "Loan band karna hai, kitna dena padega?" | get_loan_account, create_foreclosure_quote | Reply with the quote |
| contact_update | "Mobile number badalna hai" | update_contact | OTP, then update |
| complaint | "The field officer came at 9 pm" | get_loan_account, create_zendesk_ticket | Ticket with number and escalation path |
| policy_question | "What is the late fee?" | none | Reply from policy pages (cacheable) |
| out_of_scope | "Which stock should I buy?" | none | Polite decline |

`handoff_to_human` is allowed for every intent except out_of_scope.
