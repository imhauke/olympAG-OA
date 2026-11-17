# Private courtesy-key handover — author instructions

Reviewer guides: [English](../README.md) · [Deutsch](../README.de.md).
The seven-day key is a handover arrangement, **not an application expiry feature**.
Writing this guide has not created a key or configured provider-side expiry or revocation.

1. Use a separate review credential/project, rather than your normal development key. Check
   its model access, quota and pricing in Google AI Studio.
2. Record the actual handover time and exact expiry **seven days later**, with timezone.
   Set provider-side expiry if that credential supports it; otherwise arrange revocation at
   that time and revoke it in Google. A date in `.env` or a README does not disable a key.
   This project does not implement a revocation scheduler.
3. Send the key privately, or create a fresh private `.env` containing only the review key and
   demo settings, alongside the review copy. Do not distribute your development `.env`, which
   may contain other keys. Assemble a review ZIP from the project files; do not include `out/`,
   private documents or your development environment.
   Never put it in Git, a public README, sample reports or CI logs. `.env` is already ignored.
4. Set `DOCX_AGENT_PROVIDER=google_genai`, `DOCX_AGENT_MODEL=gemini-3.5-flash-lite`,
   `DOCX_AGENT_MAX_TURNS=5` and `DOCX_AGENT_MAX_RETRIES=0` for the first trial.
5. At expiry, revoke the credential and confirm that access has ended. Offline examples still
   work; reviewers can use their own key for further live edits.

Private message template — fill in the dates and send outside the repository:

```text
Here is your courtesy key for the docx-agent review:
Key: [send the actual review key privately]
Handover time: [YYYY-MM-DD HH:MM, timezone]
Access ends: [YYYY-MM-DD HH:MM, timezone; seven days after handover]
Model: gemini-3.5-flash-lite

Follow README.md step 5, then try the synthetic example in step 6.
Access shares the review project's quota and may be rate-limited.
Please do not forward the key or use real patient records.
After expiry, offline examples still work; use your own key for further live edits.
```

Official references: [key management](https://ai.google.dev/gemini-api/docs/api-key),
[model pricing and availability](https://ai.google.dev/gemini-api/docs/pricing).
