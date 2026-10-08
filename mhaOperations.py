"""Operations dashboard for the eCW "Encounters by Provider & Location" export.

Companion to mhaFinancials.py (same look and feel, same sidebar-filter pattern),
but built around appointment / encounter KPIs instead of claims and dollars:
volume, show / no-show / cancel rates, chart lock status, documentation lag,
provider and location scorecards, visit-type mix, and scheduling patterns.

Data prep (load_data):
  1. Flags rows whose Patient Name contains "test" (dummy/test patients); a sidebar
     toggle hides them (default on, unless the whole file is test data, as in the
     dummy export used to build this dashboard).
  2. Drops patient PII / demographic columns (name, DOB, gender, race, ethnicity);
     only Patient ID is kept so the worklists are actionable.
  3. Trims padded text, rounds times to the minute, and treats 00:00 in the
     Arrived / Checked Out columns as "not recorded" (the export's blank).

"Needs attention" encounters = chart still Unlocked, appointment date already
passed, and the visit actually happened or is unresolved (i.e. not Cancelled /
Rescheduled / No-Show). "Stale pending" = appointment date passed but the visit
status is still Pending (nobody checked it out or closed it).

Run with: streamlit run mhaOperations.py
"""

import glob
import os

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

# --------------------------------------------------------------------------
# Palette — same clinic brand ramp as mhaFinancials.py. States that escalate
# (aging, no-show / cancel) use the reserved status palette instead.
BRAND_LIGHT = "#86b6ef"
BRAND_BLUE = "#3987e5"
BRAND_DEEP = "#1c5cab"
BRAND_NAVY = "#0d366b"
BRAND_RAMP = [BRAND_LIGHT, BRAND_BLUE, BRAND_DEEP, BRAND_NAVY]

STATUS = {
    "good": "#0ca30c",
    "warning": "#fab219",
    "serious": "#ec835a",
    "critical": "#d03b3b",
}

INK_PRIMARY = "#0b0b0b"
INK_SECONDARY = "#52514e"
INK_MUTED = "#898781"
GRIDLINE = "#e1e0d9"
SURFACE = "#fcfcfb"

# Visit Status code -> reporting category
STATUS_CATEGORY = {
    "CHK": "Completed",
    "ARR": "In Clinic",
    "PEN_CHKIN": "In Clinic",
    "PEN": "Pending",
    "N/S": "No-Show",
    "CANC": "Cancelled",
    "R/S": "Rescheduled",
}
CATEGORY_ORDER = ["Completed", "In Clinic", "Pending", "No-Show", "Cancelled", "Rescheduled"]
CATEGORY_COLOR = {
    "Completed": BRAND_NAVY,
    "In Clinic": BRAND_DEEP,
    "Pending": BRAND_LIGHT,
    "No-Show": STATUS["critical"],
    "Cancelled": STATUS["serious"],
    "Rescheduled": STATUS["warning"],
}
# Statuses where the appointment never turned into a visit
NOT_A_VISIT = ["No-Show", "Cancelled", "Rescheduled"]

LOCK_ORDER = ["Locked", "Unlocked"]
LOCK_COLOR = {"Locked": BRAND_NAVY, "Unlocked": STATUS["warning"]}

UNLOCK_AGING_ORDER = ["0-7 days", "8-14 days", "15-30 days", "30+ days"]
UNLOCK_AGING_COLOR = {
    "0-7 days": STATUS["good"],
    "8-14 days": STATUS["warning"],
    "15-30 days": STATUS["serious"],
    "30+ days": STATUS["critical"],
}

WEEKDAY_ORDER = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]

st.set_page_config(page_title="MHA Operations Dashboard", layout="wide")

CHART_FONT = dict(family="system-ui, -apple-system, 'Segoe UI', sans-serif", color=INK_SECONDARY)

st.markdown(
    """
    <style>
    .section-header {
        font-size: 1.15rem;
        font-weight: 700;
        margin-top: 0.6rem;
        margin-bottom: 0.05rem;
    }
    .section-subtitle {
        font-size: 0.85rem;
        color: var(--text-color, #898781);
        opacity: 0.75;
        margin-bottom: 0.75rem;
    }
    .chart-title {
        font-size: 0.95rem;
        font-weight: 600;
        margin-bottom: 0.4rem;
    }
    div[data-testid="stMetric"] {
        background: rgba(127, 127, 127, 0.07);
        border: 1px solid rgba(127, 127, 127, 0.18);
        border-radius: 10px;
        padding: 12px 16px 8px 16px;
    }
    div[data-testid="stMetricLabel"] { opacity: 0.75; }
    div[data-testid="stMetricValue"] {
        font-size: 1.25rem;
        white-space: normal;
        overflow-wrap: break-word;
        line-height: 1.25;
    }
    </style>
    """,
    unsafe_allow_html=True,
)


def section_header(text, subtitle=None):
    st.markdown(f'<div class="section-header">{text}</div>', unsafe_allow_html=True)
    if subtitle:
        st.markdown(f'<div class="section-subtitle">{subtitle}</div>', unsafe_allow_html=True)


def style_fig(fig, height=340, show_legend=None, legend_position="top"):
    """Shared chart chrome. Title lives outside the figure (see chart_card)."""
    if show_legend is None:
        show_legend = len(fig.data) > 1

    if legend_position == "right":
        legend = dict(
            orientation="v", yanchor="middle", y=0.5, xanchor="left", x=1.02,
            font=dict(color=INK_SECONDARY, size=12),
        )
        margin = dict(l=8, r=160, t=12, b=8)
    else:
        legend = dict(
            orientation="h", yanchor="bottom", y=1.02, xanchor="left", x=0,
            font=dict(color=INK_SECONDARY, size=12),
        )
        margin = dict(l=8, r=8, t=40 if show_legend else 12, b=8)

    fig.update_layout(
        font=CHART_FONT,
        plot_bgcolor=SURFACE,
        paper_bgcolor=SURFACE,
        showlegend=show_legend,
        margin=margin,
        height=height,
        legend=legend,
        hoverlabel=dict(bgcolor="white", font=dict(color=INK_PRIMARY)),
    )
    fig.update_xaxes(showgrid=False, linecolor=GRIDLINE, tickfont=dict(color=INK_MUTED))
    fig.update_yaxes(showgrid=True, gridcolor=GRIDLINE, zeroline=False, tickfont=dict(color=INK_MUTED))
    return fig


def chart_card(fig, title):
    with st.container(border=True):
        st.markdown(f'<div class="chart-title">{title}</div>', unsafe_allow_html=True)
        st.plotly_chart(fig, width="stretch", config={"displayModeBar": False})


def hbar(series, color=BRAND_BLUE, title=None):
    """Horizontal count bar from a value_counts-style Series."""
    series = series.sort_values(ascending=True)
    fig = go.Figure(
        go.Bar(
            x=series.values, y=series.index, orientation="h",
            marker_color=color, text=series.values, textposition="outside",
        )
    )
    style_fig(fig, height=max(300, 30 * len(series) + 60), show_legend=False)
    if title:
        chart_card(fig, title)
    return fig


def pct(n, d):
    return (n / d * 100) if d else 0.0


def dates_only(frame):
    """Show date columns as plain dates (no 00:00:00 time) in tables and CSVs."""
    frame = frame.copy()
    for col in frame.select_dtypes(include=["datetime"]).columns:
        frame[col] = frame[col].dt.date
    return frame


# --------------------------------------------------------------------------
# Data loading
# --------------------------------------------------------------------------
DESKTOP_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def find_default_workbook():
    matches = glob.glob(os.path.join(DESKTOP_DIR, "Encounters by Provider*.xlsx"))
    if not matches:
        return None
    return max(matches, key=os.path.getmtime)


DEFAULT_PATH = find_default_workbook()

PII_COLS = ["Patient Name", "Patient DOB", "Patient Gender", "Patient Race", "Patient Ethnicity"]
TIME_COLS = [
    "Appointment Start Time", "Appointment End Time",
    "Appointment Arrived Time", "Appointment Checked Out Time",
]


def to_minutes(val):
    """Excel time-of-day -> minutes since midnight, rounded to the minute.
    Start times in the export carry float noise (e.g. 09:59:59.999 = 10:00)."""
    if pd.isna(val):
        return float("nan")
    if hasattr(val, "hour"):
        secs = val.hour * 3600 + val.minute * 60 + val.second + getattr(val, "microsecond", 0) / 1e6
        return round(secs / 60)
    parsed = pd.to_datetime(str(val), errors="coerce")
    if pd.isna(parsed):
        return float("nan")
    return parsed.hour * 60 + parsed.minute


def bucket_unlocked_age(days):
    if days <= 7:
        return "0-7 days"
    if days <= 14:
        return "8-14 days"
    if days <= 30:
        return "15-30 days"
    return "30+ days"


def split_code_label(val):
    """'OV : Office Visit' -> ('OV', 'Office Visit'). Falls back to the whole string."""
    if pd.isna(val):
        return "Unspecified", "Unspecified"
    parts = str(val).split(" : ", 1)
    code = parts[0].strip()
    label = parts[1].strip() if len(parts) > 1 else code
    return code, label


@st.cache_data
def load_data(path_or_buffer):
    raw = pd.read_excel(path_or_buffer, sheet_name=0, header=0)

    raw = raw.copy()
    raw["Is Test Patient"] = raw["Patient Name"].astype(str).str.contains("test", case=False, na=False)
    df = raw.drop(columns=[c for c in PII_COLS if c in raw.columns]).copy()

    # Only strip real strings: the time columns are object dtype but hold datetime.time
    for col in df.select_dtypes(include=["object", "string"]).columns:
        df[col] = df[col].map(lambda v: v.strip() if isinstance(v, str) else v)

    df["Appointment Date"] = pd.to_datetime(df["Appointment Date"], errors="coerce")
    df["Note Done Date"] = pd.to_datetime(df["Note Done Date"], errors="coerce")
    df = df.dropna(subset=["Appointment Date"])

    for col in ("Appointment Provider Name", "Resource Provider Name"):
        df[col] = (
            df[col].fillna("Unassigned").astype(str).str.strip().str.rstrip(",").str.strip()
            .replace("", "Unassigned")
        )
    df["Provider"] = df["Appointment Provider Name"]
    df["Resource Provider"] = df["Resource Provider Name"]
    df["Facility"] = df["Appointment Facility Name"].fillna("Unspecified")

    df["Service Month"] = df["Appointment Date"].dt.to_period("M")
    df["Weekday"] = df["Appointment Date"].dt.day_name()

    codes = df["Visit Type"].apply(split_code_label)
    df["Visit Type Code"] = [c for c, _ in codes]
    df["Visit Type Label"] = [l for _, l in codes]

    scodes = df["Visit Status"].apply(split_code_label)
    df["Status Code"] = [c for c, _ in scodes]
    df["Status Label"] = [l for _, l in scodes]
    df["Status Category"] = df["Status Code"].map(STATUS_CATEGORY).fillna("Pending")

    df["Chart Lock Status"] = df["Chart Lock Status"].fillna("Unlocked")
    df["Is Locked"] = df["Chart Lock Status"].str.lower().eq("locked")

    for col in TIME_COLS:
        df[col + " Min"] = df[col].apply(to_minutes)
    # 00:00 in arrived / checked-out means "not recorded"
    for col in ("Appointment Arrived Time Min", "Appointment Checked Out Time Min"):
        df.loc[df[col] == 0, col] = float("nan")

    start = df["Appointment Start Time Min"]
    end = df["Appointment End Time Min"]
    arrived = df["Appointment Arrived Time Min"]
    out = df["Appointment Checked Out Time Min"]
    df["Scheduled Minutes"] = (end - start).where((end - start) > 0)
    df["Wait Minutes"] = (start - arrived).where(arrived.notna())  # negative = arrived after start
    df["Visit Minutes"] = (out - arrived).where((out - arrived) > 0)
    df["Start Hour"] = (start // 60)

    today = pd.Timestamp.now().normalize()
    df["Is Past"] = df["Appointment Date"] < today
    df["Days Since Appt"] = (today - df["Appointment Date"]).dt.days
    df["Is Visit"] = ~df["Status Category"].isin(NOT_A_VISIT)
    df["Needs Lock"] = (~df["Is Locked"]) & df["Is Past"] & df["Is Visit"]
    df["Stale Pending"] = df["Is Past"] & df["Status Category"].eq("Pending")
    df["Unlocked Age Bucket"] = df["Days Since Appt"].apply(bucket_unlocked_age)

    df["Has Note"] = df["Note Done Date"].notna()
    df["Doc Lag Days"] = (df["Note Done Date"] - df["Appointment Date"]).dt.days.where(df["Has Note"])
    df["Provider Mismatch"] = df["Provider"] != df["Resource Provider"]

    return df


st.sidebar.title("Data source")
uploaded = st.sidebar.file_uploader("Upload an Encounters by Provider & Location .xlsx (optional)", type="xlsx")

if uploaded is not None:
    raw_df = load_data(uploaded)
elif DEFAULT_PATH:
    raw_df = load_data(DEFAULT_PATH)
else:
    st.error(
        "Couldn't find an 'Encounters by Provider*.xlsx' file on the Desktop. "
        "Upload the file using the sidebar."
    )
    st.stop()

# --------------------------------------------------------------------------
# Sidebar filters
# --------------------------------------------------------------------------
st.sidebar.title("Filters")

all_test = bool(raw_df["Is Test Patient"].all())
hide_test = st.sidebar.checkbox(
    "Hide test patients", value=not all_test,
    help="Hides rows whose patient name contains 'test'. Off by default when the whole file is test data.",
)
if hide_test:
    raw_df = raw_df[~raw_df["Is Test Patient"]]
if raw_df.empty:
    st.warning("No appointments left after hiding test patients.")
    st.stop()

month_order = sorted(raw_df["Service Month"].dropna().unique())
month_labels = [m.strftime("%b %Y") for m in month_order]
label_to_period = dict(zip(month_labels, month_order))

selected_month_labels = st.sidebar.multiselect(
    "Appointment month", options=month_labels, default=month_labels,
)
selected_months = [label_to_period[l] for l in selected_month_labels]

lock_selected = st.sidebar.pills(
    "Chart lock status", options=LOCK_ORDER, default=LOCK_ORDER, selection_mode="multi"
)

provider_options = sorted(raw_df["Provider"].dropna().unique())
selected_providers = st.sidebar.multiselect("Provider", options=provider_options, default=provider_options)

facility_options = sorted(raw_df["Facility"].dropna().unique())
selected_facilities = st.sidebar.pills(
    "Facility", options=facility_options, default=facility_options, selection_mode="multi"
)

category_options = [c for c in CATEGORY_ORDER if c in set(raw_df["Status Category"])]
selected_categories = st.sidebar.pills(
    "Visit status", options=category_options, default=category_options, selection_mode="multi"
)

visit_type_options = sorted(raw_df["Visit Type"].dropna().unique())
selected_visit_types = st.sidebar.multiselect("Visit type", options=visit_type_options, default=visit_type_options)

df = raw_df[raw_df["Service Month"].isin(selected_months)]
df = df[df["Chart Lock Status"].isin(lock_selected)]
df = df[df["Provider"].isin(selected_providers)]
df = df[df["Facility"].isin(selected_facilities)]
df = df[df["Status Category"].isin(selected_categories)]
df = df[df["Visit Type"].isin(selected_visit_types)]

# --------------------------------------------------------------------------
# Header
# --------------------------------------------------------------------------
st.title("MHA Operations Dashboard")
if len(df):
    date_min = df["Appointment Date"].min().strftime("%b %d, %Y")
    date_max = df["Appointment Date"].max().strftime("%b %d, %Y")
    as_of = pd.Timestamp.now().strftime("%b %d, %Y")
    st.caption(
        f"Appointment dates {date_min} – {date_max} · {len(df)} appointments shown "
        f"· lock and pending checks as of {as_of}"
    )
else:
    st.warning("No appointments match the current filters.")
    st.stop()

total_appts = len(df)
cat_counts = df["Status Category"].value_counts()
completed = int(cat_counts.get("Completed", 0))
no_shows = int(cat_counts.get("No-Show", 0))
cancelled = int(cat_counts.get("Cancelled", 0))
rescheduled = int(cat_counts.get("Rescheduled", 0))
pending = int(cat_counts.get("Pending", 0))
in_clinic = int(cat_counts.get("In Clinic", 0))

# Rates: cancelled / rescheduled slots were never expected to show, so the
# no-show rate is measured against appointments that were still on the books.
kept_base = total_appts - cancelled - rescheduled
locked_n = int(df["Is Locked"].sum())
unlocked_n = total_appts - locked_n
needs_lock_n = int(df["Needs Lock"].sum())
stale_pending_n = int(df["Stale Pending"].sum())

# --------------------------------------------------------------------------
# Overview
# --------------------------------------------------------------------------
section_header("Overview", "Appointment volume and outcomes for the filtered view")
k = st.columns(5)
k[0].metric("Appointments", f"{total_appts:,}")
k[1].metric("Completed (Checked Out)", f"{completed:,}")
k[2].metric("No-Show Rate", f"{pct(no_shows, kept_base):.1f}%", f"{no_shows} no-show(s)", delta_color="off")
k[3].metric("Cancellation Rate", f"{pct(cancelled, total_appts):.1f}%", f"{cancelled} cancelled", delta_color="off")
k[4].metric("Rescheduled", f"{rescheduled:,}")

k2 = st.columns(5)
k2[0].metric("Charts Locked", f"{locked_n:,}", f"{pct(locked_n, total_appts):.0f}% of appts", delta_color="off")
k2[1].metric("Charts Unlocked", f"{unlocked_n:,}", f"{pct(unlocked_n, total_appts):.0f}% of appts", delta_color="off")
k2[2].metric("Needs Lock (past, unlocked)", f"{needs_lock_n:,}")
k2[3].metric("Stale Pending (past date)", f"{stale_pending_n:,}")
k2[4].metric("Notes Completed", f"{int(df['Has Note'].sum()):,}", f"{pct(df['Has Note'].sum(), total_appts):.0f}% of appts", delta_color="off")

st.write("")

# --------------------------------------------------------------------------
# Monthly trends
# --------------------------------------------------------------------------
def month_index(frame):
    frame = frame.reindex(selected_months).fillna(0)
    frame.index = [m.strftime("%b %Y") for m in frame.index]
    return frame


section_header("Monthly Trends", "Appointment volume, outcomes, and rates by appointment month")

monthly_status = month_index(df.groupby(["Service Month", "Status Category"]).size().unstack(fill_value=0))
monthly_total = monthly_status.sum(axis=1)

c1, c2 = st.columns(2)
with c1:
    fig = go.Figure(
        go.Bar(
            x=monthly_total.index, y=monthly_total.values, marker_color=BRAND_BLUE,
            text=monthly_total.astype(int), textposition="outside",
        )
    )
    style_fig(fig, show_legend=False)
    chart_card(fig, "Appointments by Month")

with c2:
    fig = go.Figure()
    for cat in CATEGORY_ORDER:
        if cat in monthly_status.columns:
            fig.add_bar(name=cat, x=monthly_status.index, y=monthly_status[cat], marker_color=CATEGORY_COLOR[cat])
    fig.update_layout(barmode="stack")
    style_fig(fig)
    chart_card(fig, "Visit Status Mix by Month")

rate_df = pd.DataFrame(index=monthly_status.index)
for cat in ("No-Show", "Cancelled", "Rescheduled"):
    rate_df[cat] = monthly_status.get(cat, 0)
rate_df["Total"] = monthly_total
kept = (rate_df["Total"] - rate_df["Cancelled"] - rate_df["Rescheduled"]).replace(0, float("nan"))
rate_df["No-Show Rate"] = (rate_df["No-Show"] / kept * 100).fillna(0)
rate_df["Cancel Rate"] = (rate_df["Cancelled"] / rate_df["Total"].replace(0, float("nan")) * 100).fillna(0)
rate_df["Resched Rate"] = (rate_df["Rescheduled"] / rate_df["Total"].replace(0, float("nan")) * 100).fillna(0)

c3, c4 = st.columns(2)
with c3:
    fig = go.Figure()
    fig.add_scatter(name="No-Show Rate", x=rate_df.index, y=rate_df["No-Show Rate"], mode="lines+markers",
                    line=dict(color=STATUS["critical"], width=5), marker=dict(size=10))
    fig.add_scatter(name="Cancel Rate", x=rate_df.index, y=rate_df["Cancel Rate"], mode="lines+markers",
                    line=dict(color=STATUS["serious"], width=5), marker=dict(size=10))
    fig.add_scatter(name="Reschedule Rate", x=rate_df.index, y=rate_df["Resched Rate"], mode="lines+markers",
                    line=dict(color=STATUS["warning"], width=5), marker=dict(size=10))
    fig.update_yaxes(ticksuffix="%", rangemode="tozero")
    style_fig(fig)
    chart_card(fig, "No-Show / Cancel / Reschedule Rate by Month")

with c4:
    monthly_lock = month_index(df.groupby(["Service Month", "Chart Lock Status"]).size().unstack(fill_value=0))
    fig = go.Figure()
    for lock in LOCK_ORDER:
        if lock in monthly_lock.columns:
            fig.add_bar(name=lock, x=monthly_lock.index, y=monthly_lock[lock], marker_color=LOCK_COLOR[lock])
    fig.update_layout(barmode="stack")
    style_fig(fig)
    chart_card(fig, "Locked vs Unlocked Charts by Month")

st.write("")

# --------------------------------------------------------------------------
# Chart lock status
# --------------------------------------------------------------------------
section_header(
    "Locked vs Unlocked Encounters",
    "Unlocked charts for visits that already happened are open documentation work. "
    "Cancelled, rescheduled and no-show appointments are excluded from 'needs lock'.",
)

lk = st.columns(4)
lk[0].metric("Locked", f"{locked_n:,}")
lk[1].metric("Unlocked", f"{unlocked_n:,}")
lk[2].metric("Lock Rate (past visits)", f"{pct(df[df['Is Past'] & df['Is Visit']]['Is Locked'].sum(), (df['Is Past'] & df['Is Visit']).sum()):.0f}%")
oldest = int(df.loc[df["Needs Lock"], "Days Since Appt"].max()) if needs_lock_n else 0
lk[3].metric("Oldest Unlocked Encounter", f"{oldest:,} days" if needs_lock_n else "None")

c5, c6 = st.columns(2)
with c5:
    by_prov_lock = df.groupby(["Provider", "Chart Lock Status"]).size().unstack(fill_value=0)
    by_prov_lock = by_prov_lock.assign(_t=by_prov_lock.sum(axis=1)).sort_values("_t").drop(columns="_t")
    fig = go.Figure()
    for lock in LOCK_ORDER:
        if lock in by_prov_lock.columns:
            fig.add_bar(name=lock, y=by_prov_lock.index, x=by_prov_lock[lock], orientation="h",
                        marker_color=LOCK_COLOR[lock])
    fig.update_layout(barmode="stack")
    style_fig(fig, height=max(300, 40 * len(by_prov_lock) + 80))
    chart_card(fig, "Lock Status by Provider")

with c6:
    by_status_lock = df.groupby(["Status Category", "Chart Lock Status"]).size().unstack(fill_value=0)
    by_status_lock = by_status_lock.reindex([c for c in CATEGORY_ORDER if c in by_status_lock.index])
    fig = go.Figure()
    for lock in LOCK_ORDER:
        if lock in by_status_lock.columns:
            fig.add_bar(name=lock, x=by_status_lock.index, y=by_status_lock[lock], marker_color=LOCK_COLOR[lock])
    fig.update_layout(barmode="stack")
    style_fig(fig)
    chart_card(fig, "Lock Status by Visit Status")

needs = df[df["Needs Lock"]]
c7, c8 = st.columns(2)
with c7:
    aging = needs.groupby("Unlocked Age Bucket").size().reindex(UNLOCK_AGING_ORDER).fillna(0)
    fig = go.Figure(
        go.Bar(
            x=aging.index, y=aging.values,
            marker_color=[UNLOCK_AGING_COLOR[b] for b in aging.index],
            text=aging.values.astype(int), textposition="outside",
        )
    )
    style_fig(fig, show_legend=False)
    chart_card(fig, "Unlocked Past Encounters by Age (days since appointment)")

with c8:
    needs_prov = needs.groupby("Provider").size()
    if len(needs_prov):
        chart_card(hbar(needs_prov, STATUS["warning"]), "Unlocked Past Encounters by Provider")
    else:
        with st.container(border=True):
            st.markdown('<div class="chart-title">Unlocked Past Encounters by Provider</div>', unsafe_allow_html=True)
            st.success("No unlocked past encounters in this view.")

with st.expander(f"Unlocked encounter worklist ({needs_lock_n})", expanded=False):
    worklist_cols = [
        "Appointment Date", "Patient ID", "Provider", "Facility", "Visit Type Code",
        "Status Label", "Days Since Appt", "Note Done Date",
    ]
    worklist = dates_only(needs.sort_values("Days Since Appt", ascending=False)[worklist_cols])
    st.dataframe(worklist, width="stretch", hide_index=True)
    st.download_button(
        "Download unlocked worklist as CSV", worklist.to_csv(index=False).encode("utf-8"),
        "unlocked_encounters.csv", "text/csv",
    )

st.write("")

# --------------------------------------------------------------------------
# Visit status detail
# --------------------------------------------------------------------------
section_header(
    "Visit Status & Follow-Up",
    "Where appointments stand today. Pending appointments whose date has passed were never checked out or closed.",
)

vs = st.columns(4)
vs[0].metric("Pending (all)", f"{pending:,}")
vs[1].metric("Stale Pending (date passed)", f"{stale_pending_n:,}")
vs[2].metric("In Clinic (Checked-In)", f"{in_clinic:,}")
vs[3].metric("Show Rate", f"{pct(completed + in_clinic, kept_base):.0f}%")

c9, c10 = st.columns(2)
with c9:
    status_counts = df["Status Label"].value_counts()
    chart_card(hbar(status_counts), "Appointments by Visit Status (detail)")

with c10:
    stale = df[df["Stale Pending"]].groupby("Provider").size()
    if len(stale):
        chart_card(hbar(stale, STATUS["serious"]), "Stale Pending Appointments by Provider")
    else:
        with st.container(border=True):
            st.markdown('<div class="chart-title">Stale Pending Appointments by Provider</div>', unsafe_allow_html=True)
            st.success("No stale pending appointments in this view.")

st.write("")

# --------------------------------------------------------------------------
# Provider & location
# --------------------------------------------------------------------------
section_header("Provider & Location", "Volume, outcomes, and documentation status by provider and facility")

c11, c12 = st.columns(2)
with c11:
    chart_card(hbar(df["Provider"].value_counts()), "Appointments by Provider")
with c12:
    chart_card(hbar(df["Facility"].value_counts()), "Appointments by Facility")

prov_month = month_index(df.groupby(["Service Month", "Provider"]).size().unstack(fill_value=0))
fig = go.Figure()
provider_totals = prov_month.sum().sort_values(ascending=False)
for i, prov in enumerate(provider_totals.index):
    color = BRAND_RAMP[i] if i < len(BRAND_RAMP) else INK_MUTED
    fig.add_bar(name=prov, x=prov_month.index, y=prov_month[prov], marker_color=color)
fig.update_layout(barmode="group")
style_fig(fig, height=320)
chart_card(fig, "Appointments by Provider and Month")

scorecard = (
    df.groupby("Provider")
    .agg(
        Appointments=("Provider", "size"),
        Completed=("Status Category", lambda s: (s == "Completed").sum()),
        **{"No-Shows": ("Status Category", lambda s: (s == "No-Show").sum())},
        Cancelled=("Status Category", lambda s: (s == "Cancelled").sum()),
        Rescheduled=("Status Category", lambda s: (s == "Rescheduled").sum()),
        Locked=("Is Locked", "sum"),
        **{"Needs Lock": ("Needs Lock", "sum")},
        **{"Stale Pending": ("Stale Pending", "sum")},
        **{"Avg Appt Min": ("Scheduled Minutes", "mean")},
    )
    .sort_values("Appointments", ascending=False)
)
scorecard["Unlocked"] = scorecard["Appointments"] - scorecard["Locked"]
scorecard["Lock %"] = scorecard["Locked"] / scorecard["Appointments"] * 100
kept_p = (scorecard["Appointments"] - scorecard["Cancelled"] - scorecard["Rescheduled"]).replace(0, float("nan"))
scorecard["No-Show %"] = (scorecard["No-Shows"] / kept_p * 100).fillna(0)
scorecard = scorecard[[
    "Appointments", "Completed", "No-Shows", "No-Show %", "Cancelled", "Rescheduled",
    "Locked", "Unlocked", "Lock %", "Needs Lock", "Stale Pending", "Avg Appt Min",
]]
scorecard_tot = scorecard.sum(numeric_only=True)
scorecard_tot["No-Show %"] = pct(no_shows, kept_base)
scorecard_tot["Lock %"] = pct(locked_n, total_appts)
scorecard_tot["Avg Appt Min"] = df["Scheduled Minutes"].mean()
scorecard = pd.concat([scorecard, scorecard_tot.rename("Total").to_frame().T])
scorecard.index.name = "Provider"
scorecard = scorecard.reset_index()
score_int = ["Appointments", "Completed", "No-Shows", "Cancelled", "Rescheduled", "Locked", "Unlocked", "Needs Lock", "Stale Pending"]
scorecard[score_int] = scorecard[score_int].astype(int)

st.markdown('<div class="chart-title">Provider Scorecard</div>', unsafe_allow_html=True)
st.dataframe(
    scorecard, width="stretch", hide_index=True,
    column_config={
        "No-Show %": st.column_config.NumberColumn(format="%.1f%%"),
        "Lock %": st.column_config.NumberColumn(format="%.0f%%"),
        "Avg Appt Min": st.column_config.NumberColumn(format="%.0f"),
    },
)
st.download_button(
    "Download provider scorecard as CSV", scorecard.to_csv(index=False).encode("utf-8"),
    "provider_scorecard.csv", "text/csv",
)

pf = pd.crosstab(df["Provider"], df["Facility"])
pf["Total"] = pf.sum(axis=1)
st.markdown('<div class="chart-title">Provider by Facility</div>', unsafe_allow_html=True)
st.dataframe(pf.reset_index(), width="stretch", hide_index=True)

mismatch_n = int(df["Provider Mismatch"].sum())
if mismatch_n:
    st.caption(
        f"{mismatch_n} appointment(s) were booked under one provider but a different resource provider "
        "(e.g. covering / shared resource) — see the detail table below."
    )

st.write("")

# --------------------------------------------------------------------------
# Visit types
# --------------------------------------------------------------------------
section_header("Visit Types", "What kinds of visits are being scheduled")

top_vt = df["Visit Type Label"].value_counts()
vt = st.columns(3)
vt[0].metric("Distinct Visit Types", f"{df['Visit Type Code'].nunique():,}")
vt[1].metric("Top Visit Type", top_vt.index[0])
vt[2].metric("Top Visit Type Share", f"{pct(top_vt.iloc[0], total_appts):.0f}% ({int(top_vt.iloc[0]):,})")

c13, c14 = st.columns(2)
with c13:
    chart_card(hbar(df["Visit Type Label"].value_counts()), "Appointments by Visit Type")

with c14:
    vt_month = month_index(df.groupby(["Service Month", "Visit Type Code"]).size().unstack(fill_value=0))
    vt_order = vt_month.sum().sort_values(ascending=False).index.tolist()
    fig = go.Figure()
    for i, code in enumerate(vt_order):
        color = BRAND_RAMP[i] if i < len(BRAND_RAMP) else INK_MUTED
        fig.add_bar(name=code, x=vt_month.index, y=vt_month[code], marker_color=color)
    fig.update_layout(barmode="stack")
    style_fig(fig)
    chart_card(fig, "Visit Type Mix by Month (top 4 colored, rest gray)")

vt_status = pd.crosstab(df["Visit Type Label"], df["Status Category"]).reindex(
    columns=[c for c in CATEGORY_ORDER if c in set(df["Status Category"])], fill_value=0
)
vt_status["Total"] = vt_status.sum(axis=1)
vt_status = vt_status.sort_values("Total", ascending=False).reset_index()
st.markdown('<div class="chart-title">Visit Type by Outcome</div>', unsafe_allow_html=True)
st.dataframe(vt_status, width="stretch", hide_index=True)

st.write("")

# --------------------------------------------------------------------------
# Scheduling patterns & visit flow
# --------------------------------------------------------------------------
section_header(
    "Scheduling Patterns & Visit Flow",
    "When appointments are booked, how long they are, and (where recorded) waits and visit length",
)

flow = df[df["Wait Minutes"].notna()]
visit_len = df["Visit Minutes"].dropna()
sp = st.columns(4)
sp[0].metric("Avg Scheduled Length", f"{df['Scheduled Minutes'].mean():.0f} min" if df["Scheduled Minutes"].notna().any() else "N/A")
sp[1].metric("Arrival Times Recorded", f"{len(flow):,}", f"{pct(len(flow), total_appts):.0f}% of appts", delta_color="off")
sp[2].metric("Avg Arrival vs Start", f"{flow['Wait Minutes'].mean():.0f} min early" if len(flow) and flow["Wait Minutes"].mean() >= 0 else (f"{-flow['Wait Minutes'].mean():.0f} min late" if len(flow) else "N/A"))
sp[3].metric("Avg Check-In to Check-Out", f"{visit_len.mean():.0f} min" if len(visit_len) else "N/A")

c15, c16 = st.columns(2)
with c15:
    wd = df.groupby("Weekday").size().reindex(WEEKDAY_ORDER).dropna()
    fig = go.Figure(
        go.Bar(x=wd.index, y=wd.values, marker_color=BRAND_BLUE, text=wd.values.astype(int), textposition="outside")
    )
    style_fig(fig, show_legend=False)
    chart_card(fig, "Appointments by Day of Week")

with c16:
    hr = df.dropna(subset=["Start Hour"]).groupby("Start Hour").size()
    if len(hr):
        hr = hr.reindex(range(int(hr.index.min()), int(hr.index.max()) + 1), fill_value=0)
    fig = go.Figure(
        go.Bar(
            x=[f"{int(h) % 12 or 12} {'AM' if h < 12 else 'PM'}" for h in hr.index], y=hr.values,
            marker_color=BRAND_DEEP, text=hr.values.astype(int), textposition="outside",
        )
    )
    style_fig(fig, show_legend=False)
    chart_card(fig, "Appointments by Scheduled Start Hour")

wd_month = df.groupby(["Weekday", "Status Category"]).size().unstack(fill_value=0).reindex(WEEKDAY_ORDER).dropna(how="all")
fig = go.Figure()
for cat in CATEGORY_ORDER:
    if cat in wd_month.columns:
        fig.add_bar(name=cat, x=wd_month.index, y=wd_month[cat], marker_color=CATEGORY_COLOR[cat])
fig.update_layout(barmode="stack")
style_fig(fig, height=300)
chart_card(fig, "Visit Status by Day of Week")

st.write("")

# --------------------------------------------------------------------------
# Documentation
# --------------------------------------------------------------------------
section_header("Documentation", "Note completion and lag between the appointment and the note being marked done")

lagged = df["Doc Lag Days"].dropna()
dc = st.columns(3)
dc[0].metric("Notes Done", f"{int(df['Has Note'].sum()):,}")
dc[1].metric("Notes Not Done (past visits)", f"{int((~df['Has Note'] & df['Is Past'] & df['Is Visit']).sum()):,}")
dc[2].metric("Avg Days to Note Done", f"{lagged.mean():.1f} days" if len(lagged) else "N/A")

note_month = month_index(
    df.assign(Note=df["Has Note"].map({True: "Note Done", False: "No Note"}))
    .groupby(["Service Month", "Note"]).size().unstack(fill_value=0)
)
fig = go.Figure()
for label, color in (("Note Done", BRAND_NAVY), ("No Note", BRAND_LIGHT)):
    if label in note_month.columns:
        fig.add_bar(name=label, x=note_month.index, y=note_month[label], marker_color=color)
fig.update_layout(barmode="stack")
style_fig(fig, height=300)
chart_card(fig, "Note Completion by Appointment Month")

st.write("")

# --------------------------------------------------------------------------
# Monthly summary (exportable rollup)
# --------------------------------------------------------------------------
section_header("Monthly Summary", "A grouped, exportable rollup of the key operational counts by appointment month")

summary = pd.DataFrame(index=monthly_status.index)
summary["Appointments"] = monthly_total
for cat in CATEGORY_ORDER:
    summary[cat] = monthly_status.get(cat, 0)
lock_m = month_index(df.groupby(["Service Month", "Chart Lock Status"]).size().unstack(fill_value=0))
summary["Locked"] = lock_m.get("Locked", 0)
summary["Unlocked"] = lock_m.get("Unlocked", 0)
summary["Needs Lock"] = month_index(df.groupby("Service Month")["Needs Lock"].sum().to_frame())["Needs Lock"]
summary["Notes Done"] = month_index(df.groupby("Service Month")["Has Note"].sum().to_frame())["Has Note"]
summary["No-Show %"] = rate_df["No-Show Rate"]
summary["Cancel %"] = rate_df["Cancel Rate"]
summary.index.name = "Month"
summary = summary.reset_index()

sum_int = [c for c in summary.columns if c not in ("Month", "No-Show %", "Cancel %")]
summary[sum_int] = summary[sum_int].astype(int)
total_row = summary[sum_int].sum()
total_row["Month"] = "Total"
total_row["No-Show %"] = pct(no_shows, kept_base)
total_row["Cancel %"] = pct(cancelled, total_appts)
summary = pd.concat([summary, total_row.to_frame().T], ignore_index=True)
summary[sum_int] = summary[sum_int].astype(int)

st.dataframe(
    summary, width="stretch", hide_index=True,
    column_config={
        "No-Show %": st.column_config.NumberColumn(format="%.1f%%"),
        "Cancel %": st.column_config.NumberColumn(format="%.1f%%"),
    },
)
st.download_button(
    "Download monthly summary as CSV", summary.to_csv(index=False).encode("utf-8"),
    "operations_monthly_summary.csv", "text/csv",
)

st.write("")

# --------------------------------------------------------------------------
# Raw data table
# --------------------------------------------------------------------------
section_header("Appointment Detail")
display_cols = [
    "Appointment Date", "Patient ID", "Provider", "Resource Provider", "Facility", "Visit Type Code",
    "Status Label", "Chart Lock Status", "Note Done Date", "Scheduled Minutes", "Days Since Appt",
]
detail = dates_only(df[display_cols].sort_values("Appointment Date"))
st.dataframe(detail, width="stretch", hide_index=True)
st.download_button(
    "Download filtered data as CSV", detail.to_csv(index=False).encode("utf-8"),
    "filtered_appointments.csv", "text/csv",
)
