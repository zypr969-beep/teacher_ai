from pathlib import Path
import shutil
import pandas as pd
import streamlit as st

RESULTS = Path("output/results.csv")
GROUPS = Path("output/groups")
RENAMED = Path("output/named")

st.set_page_config(page_title="Teacher Face Matcher", layout="wide")
st.title("Teacher Face Matcher")
st.caption("Review AI suggestions before any confirmed groups are copied into named folders.")

if not RESULTS.exists():
    st.warning("Run build_refs.py, index_photos.py, and match_groups.py first.")
    st.stop()

df = pd.read_csv(RESULTS)
if "approved" not in df.columns:
    df["approved"] = ""
if "final_name" not in df.columns:
    df["final_name"] = ""

for index, row in df.iterrows():
    group = row["group"]
    st.divider()
    st.header(group)
    images = [p for p in (GROUPS / group).glob("*") if p.is_file()]
    cols = st.columns(min(max(len(images[:5]), 1), 5))
    for image, col in zip(images[:5], cols):
        col.image(str(image), caption=image.name, use_container_width=True)

    st.subheader("AI suggestions")
    for i in range(1, 4):
        ncol, scol = f"candidate_{i}", f"score_{i}"
        if ncol in df.columns and pd.notna(row.get(ncol)):
            st.write(f"{i}. {row[ncol]} — similarity {float(row[scol]):.4f}")

    current = row.get("final_name", "")
    final_name = st.text_input("Final name", value="" if pd.isna(current) else str(current), key=f"name_{index}")
    approved = st.checkbox("I manually confirmed this match", value=row.get("approved", "") == "YES", key=f"approve_{index}")
    df.loc[index, "final_name"] = final_name
    df.loc[index, "approved"] = "YES" if approved else ""

if st.button("Save review"):
    df.to_csv(RESULTS, index=False)
    st.success("Review saved.")

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
        target = RENAMED / safe_name
        target.mkdir(parents=True, exist_ok=True)
        source = GROUPS / row["group"]
        for file in source.iterdir():
            if file.is_file() and not (target / file.name).exists():
                shutil.copy2(file, target / file.name)
        count += 1
    st.success(f"Created/updated {count} confirmed teacher folders in output/named.")
