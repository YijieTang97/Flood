from pathlib import Path
import geopandas as gpd
import pandas as pd
import streamlit as st
from streamlit_folium import st_folium

from data_config import *
from flood_engine import catalog, load_depth_cache
from assessment_engine import assess_buildings
from accessibility_engine import demand_records
from preposition_engine import run_preposition, GRID_M, SAFE_DEPTH_M, SITE_COUNT, MIN_SEPARATION_M
from preposition_map import make_preposition_map

st.set_page_config(page_title="Supply Pre-positioning",page_icon="📦",layout="wide")

@st.cache_data(show_spinner=False)
def loadv(p): return gpd.read_file(p) if p and Path(p).exists() else None

buildings=loadv(str(BUILDING_FILE)) if BUILDING_FILE else None
boundary=loadv(str(BOUNDARY_FILE)) if BOUNDARY_FILE else None
roads=loadv(str(EXTENDED_ROAD_FILE)) if EXTENDED_ROAD_FILE else None
rows=catalog(FLOOD_DIR)

st.title("Emergency Supply Pre-positioning Optimization")
st.caption("V19 migration · Flood-safe candidate screening + spatially distributed demand-oriented site optimization")

if buildings is None or boundary is None or not rows:
    st.error("Buildings, town boundary, or Flood_clipped hourly rasters are missing.")
    st.stop()

metric=buildings.estimate_utm_crs() or buildings.crs
with st.sidebar:
    st.header("Optimization Settings")
    st.metric("Candidate Grid",f"{GRID_M:.0f} m")
    st.metric("Flood-safe Threshold",f"≤ {SAFE_DEPTH_M:.2f} m")
    st.metric("Selected Sites",f"{SITE_COUNT}")
    st.metric("Minimum Separation",f"{MIN_SEPARATION_M:.0f} m")
    st.info("V4.3 preserves the V19 parameters and uses precomputed flood-depth / candidate caches when available. Map interaction does not trigger recalculation. The most severe supply-demand flood hour is selected automatically.")

# V4.2: optimization is explicitly triggered and stored in session_state.
# Folium zoom/pan/click causes a Streamlit rerun, but does NOT rerun optimization.
CACHE_KEY="_v43_preposition_result"
SIG_KEY="_v43_preposition_signature"

def _data_signature():
    parts=[str(len(rows)),str(len(buildings)),str(len(boundary))]
    try:
        parts.extend([f"{Path(r['path']).name}:{Path(r['path']).stat().st_mtime_ns}" for r in rows])
    except Exception:
        parts.extend([str(r.get("path","")) for r in rows])
    return "|".join(parts)

signature=_data_signature()
cached=st.session_state.get(CACHE_KEY)
cached_sig=st.session_state.get(SIG_KEY)
cache_valid=(cached is not None and cached_sig==signature)

with st.sidebar:
    if cache_valid:
        st.success("Optimization result cached")
        run_clicked=st.button("Re-run Optimization",type="primary",use_container_width=True)
        if st.button("Clear Cached Result",use_container_width=True):
            st.session_state.pop(CACHE_KEY,None)
            st.session_state.pop(SIG_KEY,None)
            st.rerun()
    else:
        run_clicked=st.button("Run Optimization",type="primary",use_container_width=True)

if run_clicked:
    progress_bar=st.progress(0,text="Preparing optimization...")
    status=st.empty()
    def _progress(done,total,label):
        frac=1.0 if total<=0 else min(1.0,max(0.0,done/total))
        progress_bar.progress(frac,text=f"{label}: {done}/{total}")
        status.caption(label)
    depth_cache=load_depth_cache(FLOOD_MATRIX_CACHE,len(buildings),len(rows))
    safe_cache=loadv(str(PREPOSITION_CANDIDATE_CACHE)) if PREPOSITION_CANDIDATE_CACHE.exists() else None
    if depth_cache is not None:
        status.caption("Using precomputed building flood-depth matrix")
    if safe_cache is not None:
        status.caption("Using precomputed flood-safe candidate sites")
    with st.spinner("Optimizing emergency supply pre-positioning sites..."):
        out=run_preposition(
            buildings,boundary,rows,assess_buildings,demand_records,metric,_progress,
            depth_matrix=depth_cache,safe_candidate_cache=safe_cache
        )
    st.session_state[CACHE_KEY]=out
    st.session_state[SIG_KEY]=signature
    progress_bar.empty()
    status.empty()
    st.rerun()

out=st.session_state.get(CACHE_KEY)
if out is None or st.session_state.get(SIG_KEY)!=signature:
    st.info("Click **Run Optimization** in the left panel to start the full-event supply pre-positioning analysis.")
    st.stop()

wi=out["worst_idx"];sev=out["severity"];dem=out["demand"];cand=out["candidates"];safe=out["safe_candidates"];sel=out["selected_sites"]

a,b,c,d,e=st.columns(5)
a.metric("Most Severe Hour",f"{wi+1} h")
b.metric("Supply-demand Buildings",f"{len(dem):,}")
c.metric("Initial Candidates",f"{len(cand):,}")
d.metric("Flood-safe Candidates",f"{len(safe):,}")
e.metric("Optimized Sites",f"{len(sel):,}")

left,right=st.columns([3.1,1.25],gap="large")
with left:
    st.subheader(f"Supply Pre-positioning · Most Severe Flood Scenario (Hour {wi+1})")
    fmap=make_preposition_map(boundary,roads,out["assessed"],dem,safe,sel,metric)
    st_folium(fmap,height=720,use_container_width=True,key="preposition_map")

with right:
    st.subheader("Optimization Summary")
    st.write(f"**Most severe flood hour:** Hour {wi+1}")
    st.write(f"**Flood-affected residential buildings:** {sev['flooded_houses']:,}")
    st.write(f"**Maximum residential flood depth:** {sev['max_depth_m']:.2f} m")
    st.write(f"**Sum of residential flood depths:** {sev['sum_depth_m']:.1f} m")
    st.divider()
    st.write(f"**Candidate grid spacing:** {GRID_M:.0f} m")
    st.write(f"**Initial candidate sites:** {len(cand):,}")
    st.write(f"**Flood-safe candidate sites:** {len(safe):,}")
    st.write(f"**Safe-depth threshold:** ≤ {SAFE_DEPTH_M:.2f} m")
    st.write(f"**Selected sites:** {len(sel):,}")
    st.divider()
    st.markdown("#### Site Selection Criteria")
    st.write("1. Inside the study area")
    st.write("2. Remain flood-safe throughout the event")
    st.write("3. Stay close to buildings with supply demand")
    st.write("4. Maintain spatial dispersion among selected sites")

st.subheader("Optimized Pre-positioning Sites")
if sel is not None and not sel.empty:
    table=sel.drop(columns="geometry").copy()
    table=table.rename(columns={"site_id":"Site","candidate_id":"Candidate ID",
        "event_max_depth_m":"Event Max Depth (m)","nearest_demand_m":"Nearest Demand (m)",
        "assigned_demand_buildings":"Assigned Demand Buildings",
        "mean_assigned_distance_m":"Mean Assigned Distance (m)"})
    table["Site"]=table["Site"].apply(lambda x:f"P{int(x)}")
    st.dataframe(table,hide_index=True,use_container_width=True)
else:
    st.warning("No optimized site was generated.")

with st.expander("Method"):
    st.write("Candidates are generated on a regular 500 m grid inside the town boundary. A candidate is retained only if its maximum flood depth over all hourly rasters is ≤ 0.05 m. Five sites are then selected sequentially using the V19 greedy p-median-style objective: mean distance to the nearest selected site + 0.25 × the 90th-percentile distance, while preferring at least 500 m separation between selected sites.")

st.caption("V4.3 · Performance Edition · Precomputed GIS cache + session-cached optimization · V19 flood-safe screening and greedy p-median-style site optimization")
