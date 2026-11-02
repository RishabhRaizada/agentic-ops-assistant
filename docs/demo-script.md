# Demo video script (target 9:30, hard limit 10:00)

Record at 1440×900 or larger with macOS screen recording (⇧⌘5, record the selected portion, microphone on).
Before recording:
1. Sign in as **admin** → Admin → **Reset demo data**.
2. Check that the header says the models are online.
3. Open these tabs in this order: the deck (slide 1), https://92-4-93-133.sslip.io/login.
4. Keep this script on a second screen.

| Time | Screen | What to say (paraphrase freely) |
|---|---|---|
| 0:00–0:40 | Deck slides 1–2 | "I approached this as I would a customer deployment: start from how the ops team actually works. Retail ops teams decide urgent issues from email. The facts are scattered, deadlines are hours away, and who can approve depends on the amount and region. OpsPilot reads each request, checks it against the systems of record, and routes it to the person who can decide." |
| 0:40–1:30 | Deck slides 3, 5, 6 | "Agents read and propose, code verifies and routes, people decide. Seven agents share one fact ledger and never call each other. Approvals, money and routing are decided in code, never by the model. Every action is on a hash-chained audit trail. The model chain is Gemini Flash-Lite first, larger Gemini models and Groq as fallbacks, then rules." |
| 1:30–2:00 | Login page | Pick **Ananya (analyst)**. "Each role has its own authority; the login page lists them." |
| 2:00–3:45 | Requests → Simulated inbox → **S1 Urgent Delivery Delay** → Deliver | Watch the timeline fill in. Then point out: (a) highlighted evidence in the email; (b) **Facts**: the email never names the supplier, so it was filled from the PO system; 120 units confirmed by inventory; launch in 6 days; shortfall 380; ₹56 lakh revenue at risk; (c) **Risk**: agent vs rules engine side by side; (d) **Options**: three costed options built from tool calls, with the cost working and the approver each one needs. Open the confidence popover: "confidence is computed from evidence, not taken from the model." |
| 3:45–4:30 | Choose the ₹1.42 lakh option → Submit | "Spend, critical risk and a supplier order change, so it goes to the West regional head, with an SLA." |
| 4:30–5:05 | **Edge: unauthorized**. Sign in as **Vikram (North manager)** → Approvals → All pending → Approve | A refusal toast explains why. Open the Audit trail and show the pinned `unauthorized_attempt` event. |
| 5:05–5:50 | **Edge: rejection**. Sign in as **Kavita (West head)** → Reject, reason "too expensive", budget cap 50000 | The Planner re-plans under the cap, and new options of ₹50,000 or less appear. "The rejection constraint is enforced by a validator, not just asked of the model." |
| 5:50–6:30 | **Edge: approval delay**. As analyst, submit the ₹36k option (routed to the Ops manager). As admin, go to Admin → Demo clock +1 h, then +3 h | The timeline shows the reminder, then the escalation to the regional head. The dashboard shows *Escalated*. |
| 6:30–7:05 | **Edge: duplicate + changing circumstances**. Simulated inbox → **S2**, then **S7** | S2 is merged into S1 with the requester linked. S7 changes the delay from 10 to 6 days: the facts update, the pending approval is withdrawn and the case is re-assessed (see *material change*). |
| 7:05–7:45 | **Edge: prompt injection**. Simulated inbox → **S6** | Red spans and a *malicious* flag. "It mentions the real PO, but flagged mail is never merged into or allowed to edit another case. No agent has a tool that can approve or send." |
| 7:45–8:25 | **Edge: conflict + system outage**. Deliver **S4** and pick *Use inventory system (100)*. Then Admin → **All AI models down** and deliver **S3** | The conflict is resolved and recorded as an override. With the models down, the banner appears, rules extract the facts, the case is flagged degraded and missing info triggers a clarification draft. Turn the toggle off. |
| 8:25–9:05 | Audit trail → **Verify chain**; Analytics → **Generate management summary** | "Every AI, human and system action, tamper-evident. The AI-quality panel shows override, fallback and self-correction rates." |
| 9:05–9:30 | Deck slide 10 | "Here is how I would deploy this with the customer: discover, integrate, shadow, pilot, hand over, each phase with an exit criterion and the targets I would be measured on. Thank you." |

Backup lines, if something is slow: "the agents are running live on Gemini. While they work, the timeline shows each step and its latency."
