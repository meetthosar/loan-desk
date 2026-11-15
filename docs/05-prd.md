# PRD: LoanDesk pilot

**Goal:** answer routine borrower questions instantly at any hour, and get complaints to a human the same day, without ever giving a wrong amount.

## Acceptance criteria per intent
| Intent | Accepted when |
| --- | --- |
| EMI due date | Date and amount match the core banking record; a borrower with two loans is asked which one |
| Failed payment | Failure reason stated; how to pay now given; ticket opened when money was debited but not credited |
| Foreclosure | Amount equals `create_foreclosure_quote`; validity date and "no prepayment charge" stated |
| Contact update | Never updated without the right OTP; three wrong codes cancel it |
| Complaint | Ticket number given within the same reply; field officer complaints are high priority |
| All | No waiver promises; no identity numbers in replies; replies in the borrower's language |

## KPI targets (all assumptions, to be checked against the simulation)
| KPI | Today (assumed) | Pilot target |
| --- | --- | --- |
| Containment rate (no human needed) | 0% | 60% |
| Escalation rate | 100% | ≤ 40% |
| Average handle time | 4 h first reply | < 10 s agent time |
| Cost per conversation | ~₹35 agent time | < $0.03 model cost |
| p95 latency | n/a | < 8 s |
| Wrong amounts | unknown | 0 in the eval set |

## Out of scope for the pilot
Loan origination, collections outreach, rescheduling, voice, a real WhatsApp Business account.
