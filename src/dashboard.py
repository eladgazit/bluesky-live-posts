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
    st.caption(f"Refreshes every {REFRESH_SECONDS}s")


dashboard()
