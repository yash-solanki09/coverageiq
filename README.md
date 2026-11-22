# CoverageIQ: a roster planner built around coverage

Operations managers at a multi-brand support company plan monthly rosters for 30+ people across brands, processes and pods. This app generates the roster, checks it, and tells the manager exactly where coverage breaks and what could fix it.

I built it for the Datastraw Smart Roster Management assignment, with AI assistance (Claude) for writing code. The design choices and trade-offs below are what I can explain.

---

## The idea in one paragraph

I split the problem in two. **Demand** is what each brand process needs on each date (its operating calendar and minimum staff). **Supply** is who can actually work that date (leave, holidays, weekly offs, which processes they handle). A **coverage gap** is demand minus supply. Everything else, generation, validation, backups and change handling, is about finding and closing gaps. This keeps a brand's operating calendar separate from an employee's own schedule, which the brief asks for.

## What it does

| Area | Features |
|---|---|
| Data | Employees, pods and leads, brands, processes, assignments, leaves, holidays, per-day minimum overrides. All editable in the browser (Data tab), no code changes. |
| Rules | Three calendars per process: `ALL` (365 days), `NO_HOL` (every day except public holidays), `NO_SUN_HOL`. Minimum staff per process, plus per-day overrides. Shift start/end per process. |
| Generation | Month picker, then one click generates the whole team's roster with a constraint solver. |
| Views | Company, pod, brand, process and employee views. Coloured grid, plus a coverage table for brands and processes. |
| Validation | Coverage gaps, 7 days without a weekly off, work on approved leave, missing comp-off, incompatible shifts, preference not honoured. Works on generated and hand-edited rosters. |
| Fixing gaps | Ranked backup suggestions (same pod first, lightest workload), one-click assign, and a Claude explanation button. |
| Changes | Leave simulator and requirement-change simulator. Both re-solve with minimal changes and show a before/after diff before you approve. |
| Planning | Three strategies compared side by side: Balanced, Fairness-first, Wellbeing-first. |
| Analytics | Single-point-of-failure risk per brand, coverage heatmap, fairness chart (Sundays and holidays worked). |
| Workflow | Pod-lead approvals, publish gate, change log, Slack/email alerts for changes after publishing. CSV export. |

## Project layout

```
app.py           Streamlit interface (7 tabs)
engine.py        Database layer, demand model, solver, validator, backups, comparison, notifications
seed.py          Sample data generator
requirements.txt Dependencies
.env.example     Environment variables (copy to .env)
```

## Run it locally

```bash
python3 -m venv venv
source venv/bin/activate            # Windows: venv\Scripts\activate
python -m pip install -r requirements.txt
python -m streamlit run app.py
```

Open http://localhost:8501. Sample data creates itself on first launch. Pick **December 2026** and click **Generate roster**.

### Optional settings (`.env`)

| Variable | Purpose |
|---|---|
| `DATABASE_URL` | Supabase/PostgreSQL URL. Empty = local SQLite file. |
| `ANTHROPIC_API_KEY` | Enables Claude gap explanations. Without it you get a rule-based explanation. |
| `CLAUDE_MODEL` | Model used for explanations. |
| `SLACK_WEBHOOK_URL`, `SMTP_HOST`, `SMTP_USER`, `SMTP_PASS`, `NOTIFY_EMAIL_TO` | Alerts when a published roster changes. |

## Deploy (Supabase + Streamlit Community Cloud)

1. Create a free Supabase project and copy its Postgres connection string.
2. Push this folder to a public GitHub repo (`.env` is gitignored, never commit keys).
3. On share.streamlit.io, create an app from the repo, main file `app.py`.
4. In the app's **Secrets**, add `DATABASE_URL = "..."` and optionally `ANTHROPIC_API_KEY = "..."`.
5. Open the app once. Tables and sample data are created automatically.

SQLite on Streamlit Cloud is wiped on restart, which is why I use Supabase there.

## Demo walkthrough

1. Generate December 2026. Open the **Roster** tab and switch between company, pod, brand, process and employee views.
2. Open **Coverage & conflicts**. The red cells are real gaps, mostly one-person brands that run 365 days (for example Aurelle). One person cannot cover every day and also take a weekly off.
3. Pick a gap, read the explanation, and assign a suggested backup.
4. Open **Change simulator**. Add a leave for someone in a three-person brand (Vivaan, Kaya Organics) and see that the team absorbs it. Then add one for a one-person brand and watch a new gap appear.
5. Change a requirement (for example raise a process minimum to 2), re-plan, review the diff, approve.
6. **Compare plans**, then **Approvals & audit** to approve pods and publish.

---

# Tell us how you think

### 1. How did you model employees, brands, processes, pods and their relationships?

Seven core tables:

- `pods(pod, lead)`: each pod has a lead.
- `employees(emp, pod, pref_off)`: every employee belongs to one pod and has a preferred weekly-off day.
- `processes(process_id, brand, process, calendar, min_staff, shift_start, shift_end)`: the unit of demand. A brand's rules live here.
- `assignments(emp, process_id, role)`: many-to-many link between employees and processes. This is how one person can handle several processes or several brands, and how several people can share one process. `role` is `primary` or `backup`.
- `leaves(emp, date, type)`, `holidays(date, name)`, `overrides(process_id, date, min_staff)`.

The output goes in `roster(month, emp, date, status)` with statuses `W`, `WO`, `LEAVE`, `HOL`, `OFF`.

Brand is not its own table. It is a column on `processes`. That was a shortcut: it keeps the model small, but brand-level settings (like a client contact) would need their own table later.

### 2. How does the system generate a roster and check the constraints?

Generation uses OR-Tools CP-SAT. Each employee-day gets a yes/no "works" variable.

**Hard rules (never broken):**
- No work on approved leave.
- No work on a day when none of the employee's processes operate.
- At most 6 working days in any 7-day window, so every week has an off.
- After working a public holiday, an extra off within the next 14 days (comp-off).

**Coverage is soft with a huge penalty.** Each process-day has a slack variable costing 1000 per missing person. I did this deliberately: if coverage were hard, an impossible month would just return "no solution". With slack, the solver returns the best possible roster and the gaps become visible, which matches the brief's "do not silently generate an invalid roster".

**Preferences, in order of weight:** coverage, then fairness (minimise the most Sunday/holiday shifts any one person gets), then stability versus the previous roster, then preferred off days. A small cost on idle operating days stops the solver from handing out random extra offs.

**Checking** is a separate function, `validate()`, that does not trust the solver. It rebuilds coverage from the roster cells, so it also catches problems in hand-edited rosters. It reports coverage gaps, 7 days without an off, work on leave, missing comp-offs, incompatible shifts and preferences not honoured.

### 3. Single-resource brands versus multi-employee brands

I did not write separate code for the two cases. The model handles both.

- A person who works on a day covers **all** the processes assigned to them that day. For a one-person brand that is simply everything the brand needs.
- For a process with several people, their working days add up against the minimum. Two people on one process, minimum 1, means they can cover each other's offs. Three people on a minimum of 2 is tighter.
- The **single-point-of-failure score** (Analytics tab) shows the difference clearly. A brand is HIGH risk when a seven-day process has no spare person beyond its minimum, and MEDIUM when it has no spare but runs fewer days.

The sample data follows the brief: 22 of 27 brands (81%) have one dedicated employee, and 5 have two or three. Some people handle two brands.

### 4. What happens when a sole employee is unavailable on a required day?

The cell shows up as a **critical coverage gap**. The message names the process, the date, the requirement and each assigned person's status that day (for example `needs 1, has 0 (Karan=WO)`).

Then the app helps resolve it:
- It lists backup candidates who are free that day (off, or their own brand is closed), not already on the process, same pod first, fewest days worked.
- The manager can assign one as cover. That adds them as a `backup` on the process and puts them to work that day.
- Claude can explain the gap in plain language and suggest an action (rule-based fallback without an API key).
- If nobody is free, the app says so and the decision goes to the manager.

The solver does not auto-assign cover from other brands, because pulling someone onto a different client is a management decision.

### 5. How does it handle conflicting requirements or insufficient staffing?

Requirements have an explicit priority: **mandatory rules beat client coverage, which beats preferences.** Leave and the weekly off are never overridden. Coverage gaps are reported and weighed above fairness and preferences. A preferred day off is the first thing to give way, and the app lists who lost theirs and why.

A one-person brand that must run 365 days cannot satisfy both coverage and a weekly off. That is a genuine conflict, so the app flags it every week instead of hiding it. The gap list, the heatmap and the publish gate (critical gaps block publishing unless the manager explicitly overrides, and the override is logged) are all part of this.

### 6. What is automated, and what is left to humans?

**Automated:** building the demand calendar, generating the roster, finding conflicts, ranking backups, comparing plans, re-planning after a change with minimal disruption, and sending alerts.

**Human:** approving pod rosters, assigning backups, accepting a re-plan, publishing with known gaps, editing configuration, and deciding whether to hire or renegotiate with a client. Rosters affect people's lives and client contracts, so I wanted the tool to recommend and the manager to decide. The one exception is the solver's own output, which is a draft until someone approves it.

### 7. If the company doubled in size?

The current solver handles 32 people and a month in under a second, so doubling is fine on speed. What would actually need to change:

- **Split the solve by pod or by connected group** of employees who share processes, and run the groups in parallel. People in different groups never affect each other's coverage.
- **Real database practices:** a proper `brands` table, indexes on dates and employees, and stored rosters per version rather than per month.
- **Login and roles** (manager, pod lead, viewer). Right now anyone with the link can approve.
- **Background jobs** for solving, so the page does not wait.
- **Multi-month planning** so weekly-off and comp-off rules work across month boundaries.

### 8. One more week: what first?

1. **Automated tests** for the validator and the solver rules. They are the part people trust, so they should be proven.
2. **A real shift model.** Right now shifts are a static compatibility check. The solver still plans one shift per person per day. A proper model would assign people to shifts and handle overnight work and rest gaps.
3. **A comp-off ledger** that tracks earned and taken balances instead of only enforcing an extra off.
4. **Roles and login**, then email delivery tested end to end.

---

## Assumptions

- One shift per person per day; the whole roster is built per calendar month.
- The weekly-off rule is checked inside the month, not across month boundaries.
- A public holiday does not automatically close a brand. Only the calendar rule does.
- Comp-offs are enforced as "an extra off in the next 14 days". Approved comp-offs can be entered as leaves.
- Holiday dates in the sample data are illustrative, not an official list.
- A backup on a process counts toward its coverage from then on.
- Sample names, brands and pods are fictional.

## Known limitations (being upfront)

- December 2026 sample data shows 14 coverage gaps. They are real and come from one-person brands that must run 7 days a week.
- The Wellbeing-first plan (max 5 days in a row) creates many more gaps, because the team is too thin to afford it. This is the trade-off the plan is meant to expose.
- Shift checks are static. Overnight rest rules are not modelled.
- No login or permissions. Approval is a button anyone can press.
- Approvals do not reset when the roster is edited afterwards.
- I tested the database layer on SQLite and a local PostgreSQL server. I have not tested Slack or email delivery or the Claude explanation with live keys.
- Claude explanations only describe a gap. They never change the roster.
