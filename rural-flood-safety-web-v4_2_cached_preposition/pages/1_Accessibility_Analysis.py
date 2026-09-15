from pathlib import Path
import geopandas as gpd
import pandas as pd
import numpy as np
import streamlit as st
from streamlit_folium import st_folium

from data_config import *
from flood_engine import catalog, depth_matrices
from assessment_engine import assess_buildings
from accessibility_engine import calculate_accessibility
from accessibility_map import create_accessibility_map, add_selected_route

st.set_page_config(page_title="Accessibility Analysis",page_icon="🚑",layout="wide")

@st.cache_data(show_spinner=False)
def loadv(p):
    return gpd.read_file(p) if p and Path(p).exists() else None

@st.cache_data(show_spinner=False)
def assessed_at(building_path,flood_folder,idx):
    b=gpd.read_file(building_path);rs=catalog(flood_folder)
    cur,cum=depth_matrices(b,rs,idx)
    return assess_buildings(b,cur,cum,idx+1)

base=loadv(str(BUILDING_FILE)) if BUILDING_FILE else None
roads=loadv(str(EXTENDED_ROAD_FILE)) if EXTENDED_ROAD_FILE else None
boundary=loadv(str(BOUNDARY_FILE)) if BOUNDARY_FILE else None
medical=loadv(str(MEDICAL_FILE)) if MEDICAL_FILE else None
supply=loadv(str(SUPPLY_FILE)) if SUPPLY_FILE else None
shelter=loadv(str(SHELTER_FILE)) if SHELTER_FILE else None
rows=catalog(FLOOD_DIR)

st.title("Dynamic Multimodal Accessibility Analysis")
st.caption("V19 migration · Vehicle + walking relay under dynamic flooded-road conditions")

missing=[]
if base is None:missing.append("zhongzhou_building_classified.shp")
if roads is None:missing.append("extended_r.shp")
if medical is None:missing.append("extended_m.shp")
if supply is None:missing.append("zhongzhou_flood_supply_points.shp")
if shelter is None:missing.append("zhongzhou_flood_shelter_points.shp")
if missing:
    st.error("Accessibility data missing: "+", ".join(missing))
    st.stop()

labels={
"Dynamic Emergency Supply Accessibility":"supply",
"Dynamic Emergency Medical Accessibility":"rescue",
"Dynamic Emergency Evacuation Accessibility":"evacuation",
}
with st.sidebar:
    st.header("Accessibility Control")
    mode_label=st.radio("Analysis Type",list(labels))
    mode=labels[mode_label]
    idx=st.slider("Flood Time (h)",0,len(rows)-1,min(69,len(rows)-1),1)
    st.caption(rows[idx]["label"])
    st.info("Reachability threshold: 30 min\n\nCar cutoff: 0.30 m\n\nWalking cutoff: 0.60 m")
    if st.button("Clear Selection / Reset View",use_container_width=True):
        st.session_state["_selected_access_id"]=None
        st.rerun()

assessed=assessed_at(str(BUILDING_FILE),str(FLOOD_DIR),idx)
fac={"supply":supply,"rescue":medical,"evacuation":shelter}[mode]

with st.spinner("Building V19 multimodal road graph and calculating accessibility..."):
    results,summary,metric=calculate_accessibility(base,assessed,roads,fac,rows[idx]["path"],mode)

a,b,c,d,e=st.columns(5)
a.metric("Demand Buildings",f"{summary['total']:,}")
b.metric("Reachable",f"{summary['reachable']:,}")
c.metric("Unreachable",f"{summary['unreachable']:,}")
d.metric("Reachability Rate",f"{summary['rate']:.1f}%")
e.metric("Car-to-Walk Relay",f"{summary['transfer_count']:,}")
st.caption(
    f"Road-direct reachable: {summary.get('road_direct_count',0):,}  ·  "
    f"Cluster-inherited: {summary.get('cluster_inherited_count',0):,}  ·  "
    f"Same-cluster facility: {summary.get('same_cluster_direct_count',0):,}"
)

left,right=st.columns([3.1,1.25],gap="large")
# Reset selected building when scenario changes.
scenario_key=f"{mode}_{idx}"
if st.session_state.get("_access_scenario") != scenario_key:
    st.session_state["_access_scenario"]=scenario_key
    st.session_state["_selected_access_id"]=None

with left:
    st.subheader(mode_label)
    selected_id=st.session_state.get("_selected_access_id")
    fmap,mapb=create_accessibility_map(
        assessed,roads,boundary,fac,results,metric,mode,selected_id=selected_id
    )
    click_result=st_folium(
        fmap,height=720,use_container_width=True,
        returned_objects=["last_object_clicked"],key=f"access_{scenario_key}_{selected_id}"
    )

with right:
    st.subheader("Scenario Summary")
    st.write(f"**Simulation hour:** {idx+1} h")
    st.write(f"**Demand buildings:** {summary['total']:,}")
    st.write(f"**Reachable within 30 min:** {summary['reachable']:,}")
    st.write(f"**Unreachable:** {summary['unreachable']:,}")
    if np.isfinite(summary["avg_time_min"]):
        st.write(f"**Average reachable time:** {summary['avg_time_min']:.1f} min")
    st.divider()
    st.subheader("Selected Building")
    click=click_result.get("last_object_clicked") if click_result else None
    if click:
        p=gpd.GeoSeries(gpd.points_from_xy([click["lng"]],[click["lat"]]),crs=4326).to_crs(3857).iloc[0]
        bm=mapb.to_crs(3857);dist=bm.geometry.distance(p);candidate=mapb.loc[dist.idxmin()]
        bid=candidate.get("id",candidate.get("_web_id","--"))
        try:key=str(int(float(bid)))
        except:key=str(bid)
        if st.session_state.get("_selected_access_id") != key:
            st.session_state["_selected_access_id"]=key
            st.rerun()

    key=st.session_state.get("_selected_access_id")
    selected=None
    if key is not None:
        hit=mapb[mapb["_web_id"].astype(str)==str(key)]
        if not hit.empty:selected=hit.iloc[0]

    if selected is None:
        st.info("Click a demand building to inspect accessibility and display its route.")
    else:
        lookup={}
        for k,v in results.items():
            try:kk=str(int(float(k)))
            except:kk=str(k)
            lookup[kk]=v
        info=lookup.get(str(key))
        st.markdown(f"### Building {key}")
        if info is None:
            st.write("**Status:** No current demand")
        elif not info.get("network_reachable",info.get("reachable",False)) and not info.get("reachable"):
            st.error("Unreachable")
            st.write(f"Reason: {info.get('reason','network_disconnected')}")
        else:
            if info.get("same_cluster_facility"):
                st.success("Directly reachable within the same residential-building cluster")
                st.write("**Route type:** Same cluster facility — no road path required")
            elif info.get("cluster_inherited"):
                st.success("Reachable through residential-building cluster inheritance")
                st.write(f"**Route type:** Shared road path inherited from Building {info.get('inherited_from_house_id','--')}")
            else:
                st.success("Reachable" if info.get("reachable") else "Road-connected, but > 30 min")
                st.write("**Route type:** Direct multimodal road path")
            st.write(f"**Target facility:** {info.get('facility_name','--')}")
            x,y=st.columns(2)
            x.metric("Total Time",f"{info.get('total_time_min',0):.1f} min")
            y.metric("Total Distance",f"{info.get('total_distance_m',0)/1000:.2f} km")
            x,y=st.columns(2)
            x.metric("Driving Time",f"{info.get('car_time_min',0):.1f} min")
            y.metric("Walking Time",f"{info.get('walk_time_min',0):.1f} min")
            x,y=st.columns(2)
            x.metric("Driving Distance",f"{info.get('car_distance_m',0)/1000:.2f} km")
            y.metric("Walking Distance",f"{info.get('walk_distance_m',0)/1000:.2f} km")
            st.write(f"**Car-to-walk relay:** {'Yes' if info.get('transfer') else 'No'}")

with st.expander("Accessibility Result Table"):
    table=[]
    for hid,v in results.items():
        table.append({"Building ID":hid,"Reachable":v.get("reachable",False),
            "Target Facility":v.get("facility_name",""),
            "Total Time (min)":v.get("total_time_min",np.nan),
            "Driving Time (min)":v.get("car_time_min",np.nan),
            "Walking Time (min)":v.get("walk_time_min",np.nan),
            "Driving Distance (m)":v.get("car_distance_m",np.nan),
            "Walking Distance (m)":v.get("walk_distance_m",np.nan),
            "Car-to-Walk Relay":v.get("transfer",False),
            "Cluster Inherited":v.get("cluster_inherited",False),
            "Inherited From":v.get("inherited_from_house_id",""),
            "Same-cluster Facility":v.get("same_cluster_facility",False),
            "Reason":v.get("reason","")})
    st.dataframe(pd.DataFrame(table),hide_index=True,use_container_width=True,height=420)

st.caption("V3.2 · Full first/last-mile walking connectors + selected target highlighting + route interaction")
