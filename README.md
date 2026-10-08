# mhaOperations.py

Streamlit operations dashboard for MHA appointments and encounters, built from data export. Companion to `mhaFinancials.py`
(same look, same sidebar-filter pattern), but focused on appointment/encounter KPIs.

## Requirements

- Python packages: see `requirements.txt` (`streamlit`, `pandas`, `plotly`, `openpyxl`)

## Running it

```powershell
cd "C:\Users\...\Python"
streamlit run mhaOperations.py
```

## Data prep (applied on load)

1. Rows whose Patient Name contains "test" are flagged; the sidebar toggle
   **Hide test patients** drops them (default on, unless the whole file is test data).
2. Patient name, DOB, gender, race and ethnicity are dropped. Only Patient ID is kept
   so worklists stay actionable.
3. Text is trimmed, times are rounded to the minute, and `00:00` in the Arrived /
   Checked Out columns is treated as "not recorded".

## Definitions

- **Needs lock**: chart Unlocked, appointment date passed, and the visit is not a
  Cancelled / Rescheduled / No-Show.
- **Stale pending**: appointment date passed but Visit Status is still Pending.
- **No-show rate**: no-shows / appointments that were not cancelled or rescheduled.
- **Cancel / reschedule rate**: share of all appointments.

## Sections

Overview · Monthly Trends · Locked vs Unlocked Encounters (with downloadable worklist) ·
Visit Status & Follow-Up · Provider & Location (scorecard) · Visit Types ·
Scheduling Patterns & Visit Flow · Documentation · Monthly Summary · Appointment Detail.

## Sidebar filters

Appointment month, chart lock status, provider, facility, visit status, visit type.
