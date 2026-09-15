from pathlib import Path
import geopandas as gpd
import pandas as pd
import streamlit as st
from streamlit_folium import st_folium

from data_config import *
from flood_engine import catalog, depth_matrices_cached
from assessment_engine import assess_buildings
from web_map import create_map, nearest_clicked

st.set_page_config(
    page_title="Rural Flood Safety Assessment Platform",
    page_icon="🌊",
    layout="wide",
    initial_sidebar_state="expanded",
)

# ---------------------------------------------------------------------
# Dashboard styling
# ---------------------------------------------------------------------
st.markdown("""
<style>
.block-container {
    padding-top: 1.05rem;
    padding-bottom: 1.5rem;
    max-width: 1920px;
}
[data-testid="stSidebar"] {
    background: #f4f7fb;
    border-right: 1px solid #e4e7ec;
}
[data-testid="stMetric"] {
    border: 1px solid #e4e7ec;
    border-radius: 12px;
    padding: 12px 14px;
    background: #ffffff;
    box-shadow: 0 1px 2px rgba(16,24,40,.04);
}
[data-testid="stMetricLabel"] {
    font-size: .82rem;
}
[data-testid="stMetricValue"] {
    font-size: 1.55rem;
}
.platform-subtitle {
    color: #667085;
    font-size: .92rem;
    margin-top: -10px;
    margin-bottom: 14px;
}
.section-note {
    color: #667085;
    font-size: .82rem;
}
.selected-card {
    border: 1px solid #e4e7ec;
    border-radius: 12px;
    background: #ffffff;
    padding: 12px 14px;
    margin-bottom: 10px;
}
.status-pill {
    display:inline-block;
    border:1px solid #d0d5dd;
    border-radius:999px;
    padding:3px 9px;
    font-size:.78rem;
    background:#f9fafb;
    margin-right:5px;
}
hr {margin-top:.75rem; margin-bottom:.75rem;}
</style>
""", unsafe_allow_html=True)


@st.cache_data(show_spinner=False)
def load_vector(path):
    return gpd.read_file(path) if path and Path(path).exists() else None


@st.cache_data(show_spinner=False)
def run_hour(buildings_path, flood_folder, index):
    b = gpd.read_file(buildings_path)
    rows = catalog(flood_folder)
    cur, cum = depth_matrices_cached(b, rows, index, FLOOD_MATRIX_CACHE)
    return assess_buildings(b, cur, cum, index + 1)


def fnum(x, d=1):
    try:
        return f"{float(x):,.{d}f}"
    except Exception:
        return "--"


def clean_id(value):
    try:
        return str(int(float(value)))
    except Exception:
        return str(value)


def risk_level(v):
    try:
        v = float(v)
    except Exception:
        return "Unknown"
    if v < 20:
        return "Low"
    if v < 50:
        return "Moderate"
    if v < 80:
        return "High"
    return "Very High"


def damage_level(v):
    try:
        v = float(v)
    except Exception:
        return "Unknown"
    if v < 10:
        return "Minor"
    if v < 40:
        return "Moderate"
    if v < 70:
        return "Severe"
    return "Very Severe"


base = load_vector(str(BUILDING_FILE)) if BUILDING_FILE else None
roads = load_vector(str(ROAD_FILE)) if ROAD_FILE else None
boundary = load_vector(str(BOUNDARY_FILE)) if BOUNDARY_FILE else None
rows = catalog(FLOOD_DIR)

st.title("Rural Flood Safety Assessment Platform")
st.markdown(
    '<div class="platform-subtitle">'
    'Zhongzhou Town · Huaiji County · Integrated Flood Vulnerability Assessment & Decision Support'
    '</div>',
    unsafe_allow_html=True,
)

if base is None:
    st.error("Missing `zhongzhou_building_classified.shp`.")
    st.code(str(DATA_DIR))
    st.stop()

if not rows:
    st.error("No valid HEC-RAS Depth TIFF was recognized in `Flood_clipped`.")
    st.write("Expected format: `Depth (01SEP2008 01 00 00).Terrain.dem_hb.tif`")
    st.stop()

# ---------------------------------------------------------------------
# Sidebar
# ---------------------------------------------------------------------
with st.sidebar:
    st.header("Scenario Control")

    max_index = len(rows) - 1
    default_index = min(69, max_index)
    idx = st.slider(
        "Flood Duration (h)",
        min_value=0,
        max_value=max_index,
        value=default_index,
        step=1,
    )
    st.caption(rows[idx]["label"])

    st.divider()
    st.header("Assessment Module")
    theme = st.radio(
        "Theme",
        list(THEMES),
        label_visibility="collapsed",
    )

    st.divider()
    st.header("Map Layers")
    show_roads = st.checkbox("Road Network", value=True)
    show_boundary = st.checkbox("Town Boundary", value=True)
    show_flood = st.checkbox("Flood Depth Raster", value=True)

    st.divider()
    st.header("Decision-support Modules")
    st.success("Accessibility Analysis → available in the page navigation")
    st.success("Supply Pre-positioning → available in the page navigation")

# ---------------------------------------------------------------------
# Dynamic assessment
# ---------------------------------------------------------------------
with st.spinner(f"Calculating Hour {idx + 1} assessment results..."):
    assessed = run_hour(str(BUILDING_FILE), str(FLOOD_DIR), idx)

field, cmap = THEMES[theme]

flooded_mask = assessed["cur_depth"] > 0.01
flooded = int(flooded_mask.sum())
flooded_pct = 100.0 * flooded / len(assessed) if len(assessed) else 0

highrisk = int((assessed["inj_prob"] >= 50).sum())
severe_damage = int((assessed["damage_pct"] >= 50).sum())
total_loss = float(assessed["loss_total"].sum())
warning_benefit = float(assessed["loss_red"].sum())
warning_pct = 100.0 * warning_benefit / total_loss if total_loss > 0 else 0
max_current_depth = float(assessed["cur_depth"].max()) if len(assessed) else 0

# ---------------------------------------------------------------------
# Top KPI dashboard
# ---------------------------------------------------------------------
k1, k2, k3, k4, k5 = st.columns(5)
k1.metric("Buildings", f"{len(assessed):,}")
k2.metric("Currently Inundated", f"{flooded:,}", f"{flooded_pct:.1f}%")
k3.metric("High Safety Risk", f"{highrisk:,}")
k4.metric("Economic Loss", f"¥{total_loss:,.0f}")
k5.metric("Warning Benefit", f"¥{warning_benefit:,.0f}", f"{warning_pct:.1f}%")

st.markdown(
    f'<span class="status-pill">Hour {idx + 1}</span>'
    f'<span class="status-pill">Max current depth {max_current_depth:.2f} m</span>'
    f'<span class="status-pill">Severe-damage buildings {severe_damage:,}</span>',
    unsafe_allow_html=True,
)

# ---------------------------------------------------------------------
# Main GIS + information panel
# ---------------------------------------------------------------------
map_col, info_col = st.columns([3.15, 1.25], gap="large")

with map_col:
    title_left, title_right = st.columns([3, 1])
    with title_left:
        st.subheader(theme)
    with title_right:
        st.markdown(
            f'<div class="section-note" style="text-align:right;padding-top:12px">'
            f'{rows[idx]["date"]} {rows[idx]["hour_clock"]:02d}:{rows[idx]["minute"]:02d}'
            f'</div>',
            unsafe_allow_html=True,
        )

    fmap, map_b = create_map(
        assessed,
        roads if show_roads else None,
        boundary if show_boundary else None,
        field,
        theme,
        cmap,
        rows[idx]["path"] if show_flood else None,
    )

    result = st_folium(
        fmap,
        height=690,
        use_container_width=True,
        returned_objects=["last_object_clicked"],
        key=f"map_{idx}_{theme}_{show_roads}_{show_boundary}_{show_flood}",
    )

with info_col:
    st.subheader("Scenario Summary")
    s1, s2 = st.columns(2)
    s1.metric("Simulation Hour", f"{idx + 1} h")
    s2.metric("Flooded", f"{flooded:,}")

    st.write(f"**HEC-RAS time:** {rows[idx]['date']} {rows[idx]['hour_clock']:02d}:{rows[idx]['minute']:02d}")
    st.write(f"**Assessment theme:** {theme}")

    with st.expander("Current raster", expanded=False):
        st.code(rows[idx]["path"].name)

    st.divider()
    st.subheader("Selected Building")

    click = result.get("last_object_clicked") if result else None
    row = nearest_clicked(map_b, click) if click else None

    if row is None:
        st.info("Click a building polygon to inspect its assessment results.")
    else:
        bid = clean_id(row.get("id", row.get("_web_id", "--")))
        st.markdown(f"### Building {bid}")

        try:
            current_depth = float(row["cur_depth"])
            cumulative_depth = float(row["cum_depth"])
            damage = float(row["damage_pct"])
            risk = float(row["inj_prob"])
            loss = float(row["loss_total"])
            warn_loss = float(row["warn_loss"])
            benefit = float(row["loss_red"])
            red_pct = float(row["red_pct"])
        except Exception:
            current_depth = cumulative_depth = damage = risk = loss = warn_loss = benefit = red_pct = 0.0

        st.markdown(
            f'<span class="status-pill">Risk: {risk_level(risk)}</span>'
            f'<span class="status-pill">Damage: {damage_level(damage)}</span>',
            unsafe_allow_html=True,
        )

        a, b = st.columns(2)
        a.metric("Current Depth", f"{current_depth:.2f} m")
        b.metric("Maximum Depth", f"{cumulative_depth:.2f} m")

        a, b = st.columns(2)
        a.metric("Building Damage", f"{damage:.1f}%")
        b.metric("Human Safety Risk", f"{risk:.1f}%")

        a, b = st.columns(2)
        a.metric("Economic Loss", f"¥{loss:,.0f}")
        b.metric("Warning Benefit", f"¥{benefit:,.0f}")

        st.progress(min(max(red_pct / 100.0, 0.0), 1.0), text=f"Loss reduction: {red_pct:.1f}%")

        st.markdown("#### Building Profile")
        p1, p2 = st.columns(2)
        p1.write(f"**Floors:** {int(row['floors_calc'])}")
        p2.write(f"**Area:** {fnum(row['area_calc'], 1)} m²")

        if "Height" in row.index:
            p1.write(f"**Height:** {fnum(row['Height'], 2)} m")
        if "type_name" in row.index:
            p2.write(f"**Type:** {row['type_name']}")

        st.markdown("#### Warning Scenario")
        w1, w2 = st.columns(2)
        w1.metric("Without Warning", f"¥{loss:,.0f}")
        w2.metric("With Warning", f"¥{warn_loss:,.0f}")

# ---------------------------------------------------------------------
# Assessment overview
# ---------------------------------------------------------------------
st.divider()
st.subheader("Assessment Overview")

o1, o2, o3, o4 = st.columns(4)
o1.metric("Flooded Buildings", f"{flooded:,}")
o2.metric("Damage ≥ 50%", f"{severe_damage:,}")
o3.metric("Safety Risk ≥ 50%", f"{highrisk:,}")
o4.metric("Loss Reduction", f"{warning_pct:.1f}%")

with st.expander("Building Result Table"):
    cols = [
        c for c in [
            "id", "Height", "buildingAr", "type_name", "floors_calc",
            "cur_depth", "cum_depth", "damage_pct", "loss_total",
            "inj_prob", "warn_loss", "loss_red", "red_pct"
        ]
        if c in assessed.columns
    ]
    preview = assessed[cols].copy()
    st.dataframe(preview, hide_index=True, use_container_width=True, height=360)

with st.expander("Data & Layer Status"):
    status = pd.DataFrame(
        {
            "Dataset": [
                "Buildings",
                "Road Network",
                "Town Boundary",
                "Flood Depth Rasters",
            ],
            "Status": [
                BUILDING_FILE.name if BUILDING_FILE else "Missing",
                ROAD_FILE.name if ROAD_FILE else "Missing",
                BOUNDARY_FILE.name if BOUNDARY_FILE else "Missing",
                f"{len(rows)} recognized hourly TIFFs",
            ],
        }
    )
    st.dataframe(status, hide_index=True, use_container_width=True)

st.caption(
    "V4.3 · Performance Edition · Precomputed flood-depth cache · Integrated flood vulnerability assessment + dynamic multimodal accessibility · "
    "Dynamic HEC-RAS depth · Building-level vulnerability, economic loss, "
    "human safety risk and early-warning benefit."
)
