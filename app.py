from __future__ import annotations

from pathlib import Path
import shutil

import pandas as pd
import streamlit as st

RESULTS = Path("output/results_v2.csv")
GROUPS = Path("output/groups")
RENAMED = Path("output/named")

st.set_page_config(page_title="Teacher Face AI", layout="wide")
st.title("Teacher Face AI — Review Queue")
st.caption("AI ranks candidates; a human must confirm before any files are organized.")

if not RESULTS.exists():
    st.warning("Run pipeline_v2.py first, or run index_photos.py + build_reference_bank.py + match_groups_v2.py.")
    st.stop()

df = pd.read_csv(RESULTS)
for column, default in (("approved", ""), ("final_name", "")):
    if column not in df.columns:
        df[column] = default

st.sidebar.metric("Groups", len(df))
st.sidebar.metric("Confirmed", int((df["approved"] == "YES").sum()))

for index, row in df.iterrows():
    group = str(row["group"])
    st.divider()
    st.header(group)

    status = str(row.get("status", "REVIEW"))
    best = str(row.get("best_candidate", ""))
    best_dept = str(row.get("best_department", "")) or "unknown department"
    score = row.get("best_score", "")
    margin = row.get("margin", "")
    st.write(
        f"**Model state:** {status}  |  **Best candidate:** {best or 'none'} ({best_dept})  "
        f"|  **Score:** {score}  |  **Margin:** {margin}"
    )

    images = [p for p in (GROUPS / group).glob("*") if p.is_file()]
    cols = st.columns(min(max(len(images[:6]), 1), 6))
    for image, col in zip(images[:6], cols):
        col.image(str(image), caption=image.name, use_container_width=True)

    st.subheader("Ranked candidates")
    candidate_rows = []
    for i in range(1, 6):
        ncol, scol, dcol = f"candidate_{i}", f"score_{i}", f"candidate_{i}_department_slug"
        if ncol in df.columns and pd.notna(row.get(ncol)):
            candidate_rows.append({
                "Rank": i,
                "Teacher": row[ncol],
                "Score": row[scol],
                "Department": row.get(dcol, ""),
            })
    if candidate_rows:
        st.dataframe(pd.DataFrame(candidate_rows), hide_index=True, use_container_width=True)
    else:
        st.info("No candidate available from the current reference bank.")

    current = row.get("final_name", "")
    final_name = st.text_input("Final teacher name", value="" if pd.isna(current) else str(current), key=f"name_{index}")
    approved = st.checkbox(
        "I manually confirmed this match",
        value=row.get("approved", "") == "YES",
        key=f"approve_{index}",
    )
    df.loc[index, "final_name"] = final_name
    df.loc[index, "approved"] = "YES" if approved else ""

if st.button("Save review"):
    df.to_csv(RESULTS, index=False)
    st.success("Review saved.")


def department_slug_for(row: pd.Series, final_name: str) -> str:
    """Find which department the confirmed name belongs to, by matching it
    against the ranked candidates for this row (or falling back to the
    model's best-match department). Returns 'unassigned' if the confirmed
    name doesn't match any known candidate for this row (e.g. it was typed
    in manually rather than picked from the ranked list)."""
    for i in range(1, 6):
        ncol, dcol = f"candidate_{i}", f"candidate_{i}_department_slug"
        if ncol in row.index and str(row.get(ncol, "")).strip() == final_name:
            slug = str(row.get(dcol, "")).strip()
            if slug:
                return slug
    best_name = str(row.get("best_candidate", "")).strip()
    if best_name == final_name:
        slug = str(row.get("best_department_slug", "")).strip()
        if slug:
            return slug
    return "unassigned"


st.divider()
if st.button("Apply confirmed names"):
    RENAMED.mkdir(parents=True, exist_ok=True)
    count = 0
    for _, row in df.iterrows():
        if row.get("approved") != "YES":
            continue
        final_name = str(row.get("final_name", "")).strip()
        if not final_name:
            continue
        safe_name = "".join(c for c in final_name if c.isalnum() or c in " -_.").strip()
        if not safe_name:
            continue

        dept_slug = department_slug_for(row, final_name)
        target = RENAMED / dept_slug / safe_name
        target.mkdir(parents=True, exist_ok=True)

        source = GROUPS / str(row["group"])
        if not source.exists():
            continue
        for file in source.iterdir():
            if file.is_file() and not (target / file.name).exists():
                shutil.copy2(file, target / file.name)
        count += 1
    st.success(f"Created/updated {count} confirmed teacher folders under output/named/<department>/.")
