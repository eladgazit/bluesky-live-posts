"""Streamlit dashboard. Run with: streamlit run src/dashboard.py"""
import pandas as pd
import streamlit as st

import queries
from db import get_conn

REFRESH_SECONDS = 10

st.set_page_config(page_title="Bluesky posts", layout="wide")
st.title("Bluesky live posts")


@st.fragment(run_every=REFRESH_SECONDS)
def dashboard() -> None:
    # A short-lived connection per refresh avoids holding a stale one
    conn = get_conn()
    conn.autocommit = True
    try:
        per_minute = pd.DataFrame(queries.posts_per_minute(conn), columns=["minute", "posts"])
        languages = pd.DataFrame(queries.top_languages(conn, 10), columns=["language", "posts"])
        hashtags = pd.DataFrame(queries.top_hashtags(conn, 15), columns=["hashtag", "posts"])
        latest = pd.DataFrame(
            queries.latest_posts(conn, 20), columns=["created_at", "lang", "text", "author"]
        )
        eval_latest = pd.DataFrame(queries.latest_eval_run(conn),
                                   columns=["id", "check", "status", "value"])
        eval_history = pd.DataFrame(queries.eval_score_history(conn),
                                    columns=["run_at", "passed", "total"])
    finally:
        conn.close()

    st.subheader("Posts per minute (last hour)")
    st.line_chart(per_minute, x="minute", y="posts")

    left, right = st.columns(2)
    with left:
        st.subheader("Top 10 languages")
        st.bar_chart(languages, x="language", y="posts", sort="-posts")
    with right:
        st.subheader("Top 15 hashtags")
        st.dataframe(hashtags, hide_index=True, width="stretch")

    st.subheader("20 latest posts")
    st.dataframe(latest, hide_index=True, width="stretch")
    st.subheader("Data quality")
    if eval_latest.empty:
        st.info("No eval runs saved yet. Run `make eval` (or `make run`, which runs it every minute).")
    else:
        eval_history["score"] = 100 * eval_history["passed"] / eval_history["total"]
        last = eval_history.iloc[-1]
        counts = eval_latest["status"].value_counts()
        c1, c2, c3 = st.columns(3)
        c1.metric("Score (latest run)", f"{int(last['passed'])}/{int(last['total'])}")
        c2.metric("Warnings", int(counts.get("WARN", 0)))
        c3.metric("Failures", int(counts.get("FAIL", 0)))
        left, right = st.columns(2)
        with left:
            st.dataframe(eval_latest, hide_index=True, width="stretch")
        with right:
            st.caption("Score over time (% of checks passed, last 60 runs)")
            st.line_chart(eval_history, x="run_at", y="score")
    st.caption(f"Refreshes every {REFRESH_SECONDS}s")


dashboard()
