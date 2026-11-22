import os, calendar, datetime as dt
import streamlit as st, pandas as pd, plotly.express as px
from dotenv import load_dotenv
load_dotenv()
for k in ("DATABASE_URL", "ANTHROPIC_API_KEY"):          # Streamlit Cloud secrets -> env
    try: os.environ.setdefault(k, st.secrets[k])
    except Exception: pass
import engine as E

if not E.ready():
    import seed; seed.run()
st.set_page_config("CoverageIQ", "🗓️", layout="wide")
st.title("🗓️ CoverageIQ — roster engine built around coverage risk")
data = E.load(); emp, pr, asg = data["employees"], data["processes"], data["assignments"]
C = {"W": "#bbf7d0", "WO": "#e5e7eb", "LEAVE": "#fde68a", "HOL": "#bfdbfe", "OFF": "#f3f4f6"}
sb = st.sidebar; sb.header("Month")
y = sb.number_input("Year", 2026, 2030, 2026); mth = sb.selectbox("Month", range(1, 13), 11, format_func=lambda i: calendar.month_name[i])
days = E.month_days(y, mth); key = f"{y}-{mth:02d}"; lab = {d.strftime("%d %a"): d.isoformat() for d in days}
if sb.button("⚙️ Generate roster", type="primary"):
    with st.spinner("Solving…"): E.put_roster(key, E.solve(data, days)); E.log(key, "Roster generated")
if sb.button("↺ Reset sample data"):
    import seed; seed.run(); st.rerun()
sb.caption("Legend: W work · WO weekly off · LEAVE · HOL public holiday · OFF brand closed")
roster = E.get_roster(key)
t1, t2, t3, t4, t5, t6, t7 = st.tabs(["Roster", "Coverage & conflicts", "Change simulator", "Analytics", "Data (no-code config)", "Compare plans", "Approvals & audit"])

def grid(r, emps):
    p = r[r.emp.isin(emps)].pivot(index="emp", columns="date", values="status").reindex(emps)
    p.columns = [dt.date.fromisoformat(c).strftime("%d %a") for c in p.columns]; return p
def paint(df): return df.style.map(lambda v: f"background-color:{C.get(v, '')}")

with t1:
    if roster is None: st.info("Pick a month and click **Generate roster** in the sidebar.")
    else:
        scope = st.radio("View", ["Company", "Pod", "Brand", "Process", "Employee"], horizontal=True); ids = list(pr.process_id)
        if scope == "Company": emps = list(emp.emp)
        elif scope == "Pod":
            v = st.selectbox("Pod", sorted(emp.pod.unique())); emps = list(emp[emp.pod == v].emp)
            st.caption(f"Pod lead: {data['pods'].set_index('pod').lead.get(v)}")
        elif scope == "Brand":
            v = st.selectbox("Brand", sorted(pr.brand.unique())); ids = list(pr[pr.brand == v].process_id)
            emps = sorted(set(asg[asg.process_id.isin(ids)].emp))
        elif scope == "Process":
            v = st.selectbox("Process", ids); ids = [v]; emps = sorted(set(asg[asg.process_id == v].emp))
        else: emps = [st.selectbox("Employee", list(emp.emp))]
        g = grid(roster, emps)
        if st.toggle("Edit mode (changes are validated in the next tab)"):
            ed = st.data_editor(g, column_config={c: st.column_config.SelectboxColumn(options=list(C), width="small") for c in g.columns})
            if st.button("Save edits"):
                new = roster.copy()
                for e in ed.index:
                    for c in ed.columns: new.loc[(new.emp == e) & (new.date == lab[c]), "status"] = ed.loc[e, c]
                E.put_roster(key, new); E.log(key, "Manual roster change (edit or backup cover)"); st.rerun()
        else: st.dataframe(paint(g), height=min(900, 40 + 35 * len(g)))
        if scope in ("Brand", "Process"):
            st.markdown("**Daily coverage (working − required; negative = gap)**")
            st.dataframe(E.matrix(data, roster, days).loc[ids].style.background_gradient("RdYlGn", vmin=-2, vmax=2).format("{:.0f}", na_rep="–"))
        st.download_button("⬇ Export CSV", grid(roster, list(emp.emp)).to_csv(), f"roster_{key}.csv")

with t2:
    if roster is None: st.info("Generate a roster first.")
    else:
        iss = E.validate(data, roster, days, info=True); a, b, c = st.columns(3)
        a.metric("Critical", int((iss.severity == "CRITICAL").sum())); b.metric("Major", int((iss.severity == "MAJOR").sum()))
        c.metric("Brands with gaps", iss[iss.brand != ""].brand.nunique())
        if (iss.severity != "INFO").all(): st.success("No blocking conflicts. Roster can go for approval.")
        if len(iss): st.dataframe(iss, hide_index=True)
        st.plotly_chart(px.imshow(E.matrix(data, roster, days), color_continuous_scale="RdYlGn", zmin=-2, zmax=2, aspect="auto",
                                  title="Coverage heatmap (0 = exactly met, red = gap)"), width="stretch")
        gaps = iss[iss.type == "Coverage gap"].reset_index(drop=True)
        if len(gaps):
            st.subheader("Resolve a gap")
            i = st.selectbox("Gap", gaps.index, format_func=lambda i: f"{gaps.date[i]} · {gaps.process[i]}")
            sg = E.suggest(data, roster, gaps.process[i], gaps.date[i]); st.caption(gaps.detail[i])
            if st.button("🤖 Explain with Claude"): st.info(E.explain(gaps.loc[i], sg))
            if not sg: st.warning("No one is free that day. Needs a manager decision (hire, client waiver, or reduce requirement).")
            else:
                pick = st.radio("Ranked backups (same pod first, lightest load)", sg, format_func=lambda t: f"{t[0]} · pod {t[1]} · {t[2]} days worked")
                if st.button("Assign as cover"):
                    E.save("assignments", pd.concat([asg, pd.DataFrame([(pick[0], gaps.process[i], "backup")], columns=asg.columns)]))
                    new = roster.copy(); new.loc[(new.emp == pick[0]) & (new.date == gaps.date[i]), "status"] = "W"
                    E.put_roster(key, new); E.log(key, "Manual roster change (edit or backup cover)"); st.rerun()

with t3:
    st.write("Add a leave **after** the roster exists. See the gaps it creates, then re-solve with minimal disruption.")
    if roster is None: st.info("Generate a roster first.")
    else:
        e = st.selectbox("Employee", list(emp.emp), key="sim_e"); rng = st.date_input("Leave dates", (days[0], days[0]), min_value=days[0], max_value=days[-1])
        if st.button("Analyse impact") and len(rng) == 2:
            lv = [(e, (rng[0] + dt.timedelta(k)).isoformat(), "LEAVE") for k in range((rng[1] - rng[0]).days + 1)]
            d2 = dict(data); d2["leaves"] = pd.concat([data["leaves"], pd.DataFrame(lv, columns=data["leaves"].columns)])
            naive = roster.copy(); naive.loc[(naive.emp == e) & naive.date.isin([x[1] for x in lv]), "status"] = "LEAVE"
            new = E.solve(d2, days, prev=roster)
            st.session_state.prop = dict(label=f"Leave for {e}", d2=d2, new=new, before=len(E.validate(data, roster, days)), naive=E.validate(d2, naive, days), after=E.validate(d2, new, days))
        st.markdown("**…or change a brand requirement** (calendar or minimum staff), then re-plan with minimal disruption")
        c1, c2, c3 = st.columns(3); pid = c1.selectbox("Process", list(pr.process_id), key="rq_p"); cur = pr.set_index("process_id").loc[pid]; cals = ["ALL", "NO_HOL", "NO_SUN_HOL"]
        cal = c2.selectbox("Calendar", cals, index=cals.index(cur.calendar), key="rq_c"); mn = c3.number_input("Min staff", 1, 10, int(cur.min_staff), key="rq_m")
        if st.button("Analyse requirement change"):
            p2 = pr.copy(); p2.loc[p2.process_id == pid, "calendar"] = cal; p2.loc[p2.process_id == pid, "min_staff"] = mn; d2 = dict(data); d2["processes"] = p2
            new = E.solve(d2, days, prev=roster)
            st.session_state.prop = dict(label=f"Requirement change on {pid}", d2=d2, new=new, before=len(E.validate(data, roster, days)), naive=E.validate(d2, roster, days), after=E.validate(d2, new, days))
        p = st.session_state.get("prop")
        if p:
            a, b, c = st.columns(3); a.metric("Issues before", p["before"]); b.metric("Right after leave (no re-plan)", len(p["naive"])); c.metric("After minimal re-solve", len(p["after"]))
            st.markdown("**New problems caused by this leave**"); st.dataframe(p["naive"].merge(E.validate(data, roster, days), how="left", indicator=True).query("_merge=='left_only'").drop(columns="_merge"), hide_index=True)
            st.markdown("**Roster cells that would change**"); st.dataframe(E.diff(roster, p["new"]), hide_index=True)
            if st.button("✅ Approve & publish change"):
                E.save("leaves", p["d2"]["leaves"]); E.save("processes", p["d2"]["processes"]); E.put_roster(key, p["new"]); E.log(key, f"{p['label']} approved: {len(E.diff(roster, p['new']))} cells re-planned"); st.session_state.prop = None; st.rerun()

with t4:
    st.subheader("Single-point-of-failure risk by brand"); st.dataframe(E.risk(data), hide_index=True)
    if roster is not None:
        f = E.fairness(data, roster, days); st.plotly_chart(px.bar(f, x="emp", y="sun_hol_worked", title="Sundays and holidays worked (fairness)"), width="stretch")
        st.dataframe(f, hide_index=True)

with t5:
    st.caption("Edit, add or delete rows. No code changes needed. calendar ∈ ALL | NO_HOL | NO_SUN_HOL; role ∈ primary | backup; dates are YYYY-MM-DD.")
    for t in E.TABLES:
        with st.expander(t):
            ed = st.data_editor(data[t], num_rows="dynamic", key=f"ed_{t}")
            if st.button(f"Save {t}", key=f"sv_{t}"): E.save(t, ed.dropna(how="all")); st.rerun()

with t6:
    st.write("Same month, three optimisation strategies. Choose the trade-off that suits your clients and team.")
    if st.button("Compare plans"):
        with st.spinner("Solving 3 plans…"): st.session_state.cmp = E.compare(data, days)
    if "cmp" in st.session_state:
        tbl, plans = st.session_state.cmp; st.dataframe(tbl, hide_index=True)
        pick = st.radio("Adopt plan", list(plans), horizontal=True)
        if st.button("Adopt selected plan"): E.put_roster(key, plans[pick]); E.log(key, f"Adopted plan: {pick}"); st.rerun()

with t7:
    try: ap = pd.read_sql("select * from approvals", E.eng()); ap = ap[ap.month == key]
    except Exception: ap = pd.DataFrame(columns=["month", "pod", "status"])
    done = set(ap.pod); lead = data["pods"].set_index("pod").lead
    for pod in sorted(emp.pod.unique()):
        c1, c2 = st.columns([4, 1]); c1.write(f"**{pod}** (lead {lead.get(pod)}): " + ("✅ approved" if pod in done else "⏳ pending pod-lead approval"))
        if pod not in done and c2.button("Approve", key=f"ap_{pod}"):
            pd.DataFrame([(key, pod, "approved")], columns=["month", "pod", "status"]).to_sql("approvals", E.eng(), if_exists="append", index=False); E.log(key, f"{pod} lead approved"); st.rerun()
    crit = 0 if roster is None else int((E.validate(data, roster, days).severity == "CRITICAL").sum())
    force = st.checkbox(f"Manager override: publish with {crit} acknowledged critical gap(s) (logged)") if crit else True
    if st.button("🚀 Publish roster", disabled=roster is None or len(done) < emp.pod.nunique() or not force):
        E.log(key, f"Roster PUBLISHED ({crit} gaps acknowledged)"); st.success("Published. Change feed updated below.")
    st.subheader("Change log / notification feed")
    try: st.dataframe(pd.read_sql("select * from audit order by ts desc", E.eng()), hide_index=True)
    except Exception: st.caption("No events yet.")
