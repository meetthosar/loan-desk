# Rule vs LLM

For each step, does code or the model do it? The model is the only part whose output can vary, so it gets only the steps that need language.

| Step | Code or model | Why |
| --- | --- | --- |
| Identify the borrower | Code | From the channel's phone number; never from the message |
| Remove personal data | Code | Regex and a vault; must be certain |
| Sort the intent | **Model** (small) | Free text, three languages |
| Decide on a handoff | Code, using the model's signals | Thresholds and rules are auditable |
| Find policy passages | Code (embeddings + vector search) | Deterministic given the query |
| EMI date and amount | Code | Database lookup |
| Payment status | Code | Database lookup |
| Foreclosure amount | Code | A formula (`domain/loan_math.py`) |
| OTP check | Code | Security |
| Choose which tool to call, with what arguments | **Model** | Depends on the conversation |
| Write the reply | **Model** | Language, tone, the borrower's own words |
| Check the reply | Code | Waiver promises, ungrounded amounts, leaked identity numbers |
| Score evals | Code, plus a model judge for faithfulness only | The judge is checked against hand labels |
