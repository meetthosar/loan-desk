# Discovery notes

Mock session with Udaan's head of customer operations. **Synthetic**: Udaan is fictional, and every figure below is an assumption written for this project.

## Today
- About 200,000 active borrowers across 7 branches in Maharashtra and Madhya Pradesh; 3,000 support queries a day on WhatsApp and email.
- 22 support agents, Monday to Saturday, 9:00 to 18:00. Nothing is answered outside those hours.
- Median first reply 4 hours; WhatsApp messages sent after 18:00 wait until the next morning.

## What borrowers ask (share of queries, assumed)
| Topic | Share |
| --- | --- |
| EMI due date or amount | 35% |
| Failed or pending payment | 20% |
| General policy questions | 15% |
| Foreclosure amount | 10% |
| Complaints | 10% |
| Contact changes | 5% |
| Other | 5% |

## Pain points
- Agents spend most of their time on lookups that the core banking system answers in seconds.
- Foreclosure amounts are calculated by hand in a spreadsheet; mistakes lead to complaints.
- Complaints about field officers arrive mixed in with routine queries, so serious ones wait.

## Constraints
- **RBI:** no prepayment penalty on microfinance loans; penal charges only on the overdue amount; fair practices code for recovery; grievance escalation path must be stated.
- **DPDP Act 2023:** collect and keep only what is needed; borrowers' Aadhaar must never sit in chat logs.
- Borrowers write in English, Hindi and Hinglish, often in short voice-note-style messages.
- Many borrowers share a phone with family members, so the phone number alone is weak proof of identity for changes.

## What success looks like (see PRD for targets)
Routine lookups answered instantly at any hour; complaints reach a human the same day with a summary; no wrong amounts.
