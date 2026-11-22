"""Realistic sample data: 32 employees, 4 pods, 27 brands (22 single-resource = 81%)."""
import random, os, pandas as pd, engine as E

NAMES = "Aarav Diya Ishaan Meera Kabir Anaya Vivaan Riya Arjun Sneha Rohan Pooja Karan Nisha Dev Tanvi Manav Isha Harsh Kriti Neel Simran Om Jhanvi Raj Mansi Parth Hetal Jay Bhumi Sahil Dhruvi".split()
SINGLES = ["Aurelle", "Nimbus Tea", "Terra Threads", "Pawsome", "Glowkart", "Urban Roots", "Spice Route", "Kiddo Co", "Fitnest", "Brewlab", "Silkline",
           "Zestify", "Craftly", "Mintleaf", "Oakwood", "Petalia", "Hexa Gear", "Cozynest", "Drift Denim", "Salt & Sage", "Vanta", "Orbit Audio"]

def run():
    random.seed(7)
    from sqlalchemy import text
    with E.eng().begin() as c:
        for t in ("roster", "audit", "approvals"): c.execute(text(f"drop table if exists {t}"))
    pods = ["Alpha", "Bravo", "Charlie", "Delta"]
    emp = [(n, pods[i % 4], random.choice(["SUN", "SUN", "MON", "TUE", "WED", "THU", "FRI", "SAT"])) for i, n in enumerate(NAMES)]
    P, A = [], []                                           # (brand, process, calendar, min) ; (emp, brand|process, role)
    def add(b, pn, cal, mn, team, role="primary"):
        P.append((f"{b}|{pn}", b, pn, cal, mn)) if not any(x[0] == f"{b}|{pn}" for x in P) else None
        A.extend((e, f"{b}|{pn}", role) for e in team)
    n = NAMES
    add("Lumora", "CS", "ALL", 1, [n[0], n[1]]);          add("Lumora", "NDR", "NO_SUN_HOL", 1, [n[2]])
    add("Veloura", "CS", "NO_HOL", 1, [n[3], n[4]]);      add("Veloura", "ACC", "NO_SUN_HOL", 1, [n[5]])
    add("Kaya Organics", "CS", "ALL", 1, [n[6], n[7], n[8]]); add("Kaya Organics", "Retention", "NO_SUN_HOL", 1, [n[8]])
    add("Bloomr", "CS", "NO_HOL", 2, [n[9], n[10], n[11]]);   add("Bloomr", "NDR", "NO_SUN_HOL", 1, [n[11], n[0]])
    add("Trueform", "ACC", "NO_HOL", 1, [n[10], n[11]]);      add("Trueform", "Retention", "NO_SUN_HOL", 1, [n[11]])
    for i, b in enumerate(SINGLES):                         # one employee handles ALL processes of the brand
        e = n[12 + i] if i < 20 else n[12 + i - 20]
        procs = random.sample(["CS", "ACC", "NDR", "Retention"], random.choice([1, 1, 2, 3]))
        if i < 3 and "CS" not in procs: procs[0] = "CS"
        for j, pn in enumerate(procs):
            cal = ("ALL" if i < 2 else "NO_HOL") if (i < 3 and pn == "CS") else "NO_SUN_HOL"
            add(b, pn, cal, 1, [e])
        if i == 1: add(b, [x for x in procs if x == "CS"][0], "ALL", 1, [n[14]], "backup")   # cross-trained backup
    leaves = [("Diya", "2026-12-10"), ("Diya", "2026-12-11"), ("Meera", "2026-12-24"), ("Meera", "2026-12-26"),
              ("Sneha", "2026-12-14"), ("Nisha", "2026-12-21"), ("Nisha", "2026-12-22"), ("Kriti", "2026-12-07")]
    H = [("2026-11-08", "Diwali"), ("2026-11-10", "Gujarati New Year"), ("2026-12-25", "Christmas"), ("2027-01-01", "New Year"),
         ("2027-01-14", "Uttarayan"), ("2027-01-26", "Republic Day")]
    E.save("employees", pd.DataFrame(emp, columns=["emp", "pod", "pref_off"]))
    E.save("pods", pd.DataFrame([(p, NAMES[i]) for i, p in enumerate(pods)], columns=["pod", "lead"]))
    pdf = pd.DataFrame(P, columns=["process_id", "brand", "process", "calendar", "min_staff"]); pdf["shift_start"], pdf["shift_end"] = "10:00", "19:00"
    pdf.loc[pdf.process_id == "Veloura|ACC", ["shift_start", "shift_end"]] = ["14:00", "23:00"]   # overlaps CS: fine
    pdf.loc[pdf.process_id == "Bloomr|NDR", ["shift_start", "shift_end"]] = ["21:00", "06:00"]    # night shift: clashes with day shift for shared staff
    E.save("processes", pdf)
    E.save("assignments", pd.DataFrame(A, columns=["emp", "process_id", "role"]))
    E.save("leaves", pd.DataFrame([(e, d, "LEAVE") for e, d in leaves], columns=["emp", "date", "type"]))
    E.save("overrides", pd.DataFrame([("Kaya Organics|CS", "2026-12-31", 2), ("Lumora|CS", "2026-12-24", 2)], columns=["process_id", "date", "min_staff"]))
    E.save("holidays", pd.DataFrame(H, columns=["date", "name"]))

if __name__ == "__main__": run()
