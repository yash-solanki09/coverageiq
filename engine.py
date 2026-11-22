"""CoverageIQ engine: demand/supply model, CP-SAT solver, validation, backups."""
import os, calendar, datetime as dt
import pandas as pd
from sqlalchemy import create_engine, text
from ortools.sat.python import cp_model

DB = "roster.db"
TABLES = ["pods", "employees", "processes", "assignments", "leaves", "holidays", "overrides"]
_eng = None
def eng():
    """SQLite locally; set DATABASE_URL to a Supabase/Postgres URL for a persistent cloud DB."""
    global _eng
    if _eng is None:
        url = os.getenv("DATABASE_URL") or f"sqlite:///{DB}"
        for p in ("postgres://", "postgresql://"):          # force the psycopg2 driver
            if url.startswith(p): url = "postgresql+psycopg2://" + url[len(p):]; break
        _eng = create_engine(url, pool_pre_ping=True)
    return _eng
def ready():
    try: return len(load()["employees"]) > 0
    except Exception: return False
def load(): return {t: pd.read_sql(f"select * from {t}", eng()) for t in TABLES}
def save(t, df): df.to_sql(t, eng(), if_exists="replace", index=False)
def get_roster(key):
    try: df = pd.read_sql(text("select emp,date,status from roster where month=:m"), eng(), params={"m": key})
    except Exception: return None
    return df if len(df) else None
def put_roster(key, df):
    with eng().begin() as c:
        c.execute(text("create table if not exists roster(month text,emp text,date text,status text)"))
        c.execute(text("delete from roster where month=:m"), {"m": key})
    df[["emp", "date", "status"]].assign(month=key).to_sql("roster", eng(), if_exists="append", index=False)
def log(month, msg):
    pd.DataFrame([(dt.datetime.now().isoformat(timespec="seconds"), month, msg)], columns=["ts", "month", "event"]).to_sql("audit", eng(), if_exists="append", index=False)
    if msg.startswith("Roster PUBLISHED") or published(month): notify(f"[{month}] {msg}")   # published rosters push every change

def month_days(y, m): return [dt.date(y, m, d) for d in range(1, calendar.monthrange(y, m)[1] + 1)]
def hols(data): return set(pd.to_datetime(data["holidays"].date).dt.date)
def operates(rule, d, hol):
    """DEMAND side: does the process need coverage on date d? (brand operating calendar)"""
    if rule == "ALL": return True                      # 365 days
    if rule == "NO_HOL": return d not in hol           # every day except public holidays
    return d.weekday() != 6 and d not in hol           # NO_SUN_HOL

def solve(data, days, prev=None, secs=15, w=None):
    """SUPPLY side: CP-SAT. Hard: leave, >=1 off per 7 days. Coverage is soft (slack) so
    infeasible demand is reported, never hidden. Soft: preferred off day, fairness, stability."""
    emp = data["employees"].set_index("emp"); pr = data["processes"].set_index("process_id")
    asg = data["assignments"]; hol = hols(data)
    wt = {**PROFILES["Balanced"], **(w or {})}; ov = {(r.process_id, r.date): int(r.min_staff) for r in data["overrides"].itertuples()}
    leave = {(r.emp, r.date) for r in data["leaves"].itertuples()}
    mine = {e: list(asg[asg.emp == e].process_id) for e in emp.index}
    op = {(p, d): operates(pr.loc[p, "calendar"], d, hol) for p in pr.index for d in days}
    free = lambda e, d: (e, d.isoformat()) not in leave and any(op[p, d] for p in mine[e])
    m = cp_model.CpModel(); W = {}; obj = []
    for e in emp.index:
        for d in days:
            W[e, d] = m.NewBoolVar(f"w_{e}_{d}")
            if not free(e, d): m.Add(W[e, d] == 0)
    L = wt["maxrun"] + 1                                     # weekly-off rule (hard): max consecutive working days
    for e in emp.index:
        for s in range(len(days) - L + 1): m.Add(sum(W[e, days[s + k]] for k in range(L)) <= L - 1)
    for p in pr.index:                                      # coverage (soft, heavily weighted)
        team = list(asg[asg.process_id == p].emp)
        for d in days:
            if op[p, d]:
                need = ov.get((p, d.isoformat()), int(pr.loc[p, "min_staff"]))   # per-day minimum overrides
                s = m.NewIntVar(0, need, ""); m.Add(sum(W[e, d] for e in team) + s >= need); obj.append(wt["cov"] * s)
    pv = {(r.emp, r.date): r.status for r in prev.itertuples()} if prev is not None else {}
    for e in emp.index:
        sh = []
        for d in days:
            if not free(e, d): continue
            pref = d.strftime("%a").upper() == emp.loc[e, "pref_off"]
            obj.append(wt["pref"] * W[e, d] + 2 * (1 - W[e, d]) if pref else 2 * (1 - W[e, d]))   # preference
            if d.weekday() == 6 or d in hol: sh.append(W[e, d])
            if (e, d.isoformat()) in pv: obj.append(wt["stab"] * (1 - W[e, d]) if pv[e, d.isoformat()] == "W" else wt["stab"] * W[e, d])  # stability
        if sh:                                              # fairness: spread Sunday/holiday work
            mx = m.NewIntVar(0, len(days), ""); m.Add(sum(sh) <= mx); obj.append(wt["fair"] * mx)
    for e in emp.index:                                     # comp-off: holiday work earns an extra off within 14 days (hard)
        for hi, d in enumerate(days):
            wnd = days[hi + 1: hi + 15]
            if d in hol and free(e, d) and len(wnd) >= 7: m.Add(sum(W[e, x] for x in wnd) + (len(wnd) // 7 + 1) * W[e, d] <= len(wnd))
    m.Minimize(sum(obj)); sv = cp_model.CpSolver(); sv.parameters.max_time_in_seconds = secs; sv.parameters.num_workers = 4
    sv.Solve(m); rows = []
    for e in emp.index:
        for d in days:
            iso = d.isoformat()
            if (e, iso) in leave: st = "LEAVE"
            elif not any(op[p, d] for p in mine[e]): st = "HOL" if d in hol else "OFF"
            else: st = "W" if sv.Value(W[e, d]) else "WO"
            rows.append((e, iso, st))
    return pd.DataFrame(rows, columns=["emp", "date", "status"])

def cov(data, roster, days):
    hol = hols(data); asg = data["assignments"]; stt = {(r.emp, r.date): r.status for r in roster.itertuples()}
    need, got = {}, {}; ov = {(r.process_id, r.date): int(r.min_staff) for r in data["overrides"].itertuples()}
    for r in data["processes"].itertuples():
        team = list(asg[asg.process_id == r.process_id].emp)
        for d in days:
            if operates(r.calendar, d, hol):
                k = (r.process_id, d.isoformat()); need[k] = ov.get(k, r.min_staff)
                got[k] = sum(stt.get((e, k[1])) == "W" for e in team)
    return need, got, stt

def matrix(data, roster, days):
    need, got, _ = cov(data, roster, days)
    ids = list(data["processes"].process_id)
    mat = pd.DataFrame(index=ids, columns=[d.strftime("%d %a") for d in days], dtype=float)
    for (p, iso), n in need.items(): mat.loc[p, dt.date.fromisoformat(iso).strftime("%d %a")] = got[p, iso] - n
    return mat

def validate_all(data, roster, days):
    """Works on ANY roster (generated or hand-edited). Returns explained issues."""
    need, got, stt = cov(data, roster, days); asg = data["assignments"]
    pr = data["processes"].set_index("process_id"); leave = {(r.emp, r.date) for r in data["leaves"].itertuples()}
    out = []; hol = hols(data); pref = data["employees"].set_index("emp").pref_off
    for (p, iso), n in need.items():
        if got[p, iso] < n:
            team = list(asg[asg.process_id == p].emp)
            why = ", ".join(f"{e}={stt.get((e, iso))}" for e in team)
            out.append(("CRITICAL", "Coverage gap", pr.loc[p, "brand"], p, iso, f"needs {n}, has {got[p, iso]} ({why})"))
    for e in data["employees"].emp:
        sts = [stt.get((e, d.isoformat())) for d in days]
        n = sum(x == "W" and d.strftime("%a").upper() == pref[e] for d, x in zip(days, sts))
        if n: out.append(("INFO", "Preference not honoured", "", "", days[0].isoformat(), f"{e} works {n} day(s) on preferred off day ({pref[e]}); coverage rules take priority"))
        for hi, d in enumerate(days):
            wnd = sts[hi + 1: hi + 15]
            if sts[hi] == "W" and d in hol and len(wnd) >= 7 and sum(x != "W" for x in wnd) < len(wnd) // 7 + 1:
                out.append(("MAJOR", "Comp-off pending", "", "", d.isoformat(), f"{e} worked a holiday and needs an extra off within 14 days"))
        for s in range(len(days) - 6):
            if all(x == "W" for x in sts[s:s + 7]): out.append(("MAJOR", "7 days without weekly off", "", "", days[s].isoformat(), f"{e} works 7 straight days")); break
        for d, x in zip(days, sts):
            if x == "W" and (e, d.isoformat()) in leave: out.append(("CRITICAL", "Worked on approved leave", "", "", d.isoformat(), e))
    out += shift_conflicts(data)
    return pd.DataFrame(out, columns=["severity", "type", "brand", "process", "date", "detail"])

def suggest(data, roster, process_id, iso, k=3):
    """Backup candidates: free that day (WO/OFF/HOL), not already on the process. Same pod first, lightest load."""
    emp = data["employees"].set_index("emp"); asg = data["assignments"]
    team = list(asg[asg.process_id == process_id].emp); pod = emp.loc[team[0], "pod"] if team else None
    day = roster[roster.date == iso].set_index("emp").status; load_ = roster[roster.status == "W"].groupby("emp").size()
    c = [(e, emp.loc[e, "pod"], int(load_.get(e, 0))) for e in emp.index if e not in team and day.get(e) in ("WO", "OFF", "HOL")]
    return sorted(c, key=lambda t: (t[1] != pod, t[2]))[:k]

def diff(a, b):
    m = a.merge(b, on=["emp", "date"], suffixes=("_before", "_after")); return m[m.status_before != m.status_after]

def fairness(data, roster, days):
    hol = hols(data); sh = {d.isoformat() for d in days if d.weekday() == 6 or d in hol}
    g = roster.assign(w=roster.status == "W", s=(roster.status == "W") & roster.date.isin(sh))
    return g.groupby("emp").agg(worked=("w", "sum"), sun_hol_worked=("s", "sum"),
                                weekly_offs=("status", lambda x: (x == "WO").sum()), leave=("status", lambda x: (x == "LEAVE").sum())).reset_index()

def risk(data):
    asg = data["assignments"]; rows = []
    for b, g in data["processes"].groupby("brand"):
        lvl = "LOW"; team = set()
        for r in g.itertuples():
            n = (asg.process_id == r.process_id).sum(); team |= set(asg[asg.process_id == r.process_id].emp)
            if r.calendar in ("ALL", "NO_HOL") and n - r.min_staff <= 0: lvl = "HIGH"
            elif n - r.min_staff <= 0 and lvl != "HIGH": lvl = "MEDIUM"
        rows.append((b, len(team), ", ".join(g.process), ", ".join(sorted(set(g.calendar))), lvl))
    return pd.DataFrame(rows, columns=["brand", "employees", "processes", "calendars", "spof_risk"]).sort_values("spof_risk")

def validate(data, roster, days, info=False):
    v = validate_all(data, roster, days); return v if info else v[v.severity != "INFO"]

PROFILES = {"Balanced": dict(cov=1000, pref=3, fair=10, stab=4, maxrun=6), "Fairness-first": dict(cov=1000, pref=3, fair=60, stab=4, maxrun=6),
            "Wellbeing-first (max 5 days in a row)": dict(cov=1000, pref=6, fair=20, stab=4, maxrun=5)}
def compare(data, days):
    """Solve the same month under different strategies so the manager can choose the trade-off."""
    rows, plans = [], {}
    for n, w in PROFILES.items():
        r = solve(data, days, w=w, secs=10); v = validate_all(data, r, days); f = fairness(data, r, days); plans[n] = r
        rows.append((n, int((v.type == "Coverage gap").sum()), int((v.type == "Preference not honoured").sum()), int(f.sun_hol_worked.max()), round(float(f.sun_hol_worked.std()), 2)))
    return pd.DataFrame(rows, columns=["plan", "coverage_gaps", "employees_off_preference", "max_sun_hol_one_person", "sun_hol_spread"]), plans

def explain(row, sg):
    """Plain-language gap explanation. Uses Claude if ANTHROPIC_API_KEY is set, else a rule-based fallback."""
    base = f"{row.process} needs cover on {row.date}, but {row.detail}. " + (f"Best backup: {sg[0][0]} (pod {sg[0][1]}, {sg[0][2]} days worked)." if sg else "Nobody is free: escalate to the manager.")
    if not os.getenv("ANTHROPIC_API_KEY"): return base + " (Set ANTHROPIC_API_KEY for AI explanations.)"
    try:
        import anthropic
        r = anthropic.Anthropic().messages.create(model=os.getenv("CLAUDE_MODEL", "claude-sonnet-5-5"), max_tokens=300, messages=[{"role": "user", "content":
            f"You are a roster-ops assistant. In 3 short sentences explain this coverage gap to an operations manager and recommend one action.\nGap: {row.to_dict()}\nBackup candidates (employee, pod, days worked): {sg}"}])
        return r.content[0].text
    except Exception as ex: return base + f" (AI unavailable: {ex})"

def _mins(t): h, m = map(int, str(t).split(":")); return h * 60 + m
def shift_conflicts(data):
    """Config check: one person cannot cover two processes whose shift windows never overlap."""
    pr = data["processes"].set_index("process_id"); out = []
    if "shift_start" not in pr: return out
    iv = {}
    for p, r in pr.iterrows(): a, b = _mins(r.shift_start), _mins(r.shift_end); iv[p] = (a, b if b > a else b + 1440)
    for e, g in data["assignments"].groupby("emp"):
        ps = list(g.process_id)
        for i in range(len(ps)):
            for j in range(i + 1, len(ps)):
                (a1, b1), (a2, b2) = iv[ps[i]], iv[ps[j]]
                if max(a1, a2) >= min(b1, b2):
                    f = lambda p: f"{p} ({pr.loc[p, 'shift_start']}-{pr.loc[p, 'shift_end']})"
                    out.append(("MAJOR", "Incompatible shifts", pr.loc[ps[i], "brand"], ps[i], "", f"{e} is assigned {f(ps[i])} and {f(ps[j])}, which never overlap"))
    return out

def published(month):
    try: return len(pd.read_sql(text("select 1 from audit where month=:m and event like :p"), eng(), params={"m": month, "p": "Roster PUBLISHED%"})) > 0
    except Exception: return False
def notify(msg):
    """Slack (SLACK_WEBHOOK_URL) and/or email (SMTP_HOST, SMTP_USER, SMTP_PASS, NOTIFY_EMAIL_TO). No-op if unset."""
    import json, urllib.request, smtplib
    from email.message import EmailMessage
    try:
        if os.getenv("SLACK_WEBHOOK_URL"):
            urllib.request.urlopen(urllib.request.Request(os.environ["SLACK_WEBHOOK_URL"], json.dumps({"text": f"CoverageIQ: {msg}"}).encode(), {"Content-Type": "application/json"}), timeout=5)
        if os.getenv("SMTP_HOST") and os.getenv("NOTIFY_EMAIL_TO"):
            m = EmailMessage(); m["Subject"] = "CoverageIQ roster update"; m["From"] = os.environ["SMTP_USER"]; m["To"] = os.environ["NOTIFY_EMAIL_TO"]; m.set_content(msg)
            with smtplib.SMTP_SSL(os.environ["SMTP_HOST"], 465, timeout=8) as sv: sv.login(os.environ["SMTP_USER"], os.environ["SMTP_PASS"]); sv.send_message(m)
    except Exception: pass
