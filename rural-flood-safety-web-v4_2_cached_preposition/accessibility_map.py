import folium
import geopandas as gpd
import pyproj
from shapely.ops import transform

def _wgs(g):
    return g.to_crs(4326) if g is not None and not g.empty else g

def _idstr(v):
    try:return str(int(float(v)))
    except:return str(v)

def add_selected_route(m,info,metric_crs):
    if not info:return
    segs=info.get("path_geoms") or []
    if not segs:return
    transformer=pyproj.Transformer.from_crs(metric_crs,4326,always_xy=True).transform
    for item in segs:
        if isinstance(item,(tuple,list)) and len(item)==2:
            mode,geom=item
        else:
            mode,geom="car",item
        g=transform(transformer,geom)
        coords=[(y,x) for x,y in g.coords]
        color="#1769aa" if mode=="car" else "#f28c18"
        folium.PolyLine(coords,color=color,weight=6 if mode=="car" else 8,opacity=.98,
            tooltip="Driving route" if mode=="car" else "Walking route").add_to(m)

def create_accessibility_map(assessed,roads,boundary,facilities,results,metric_crs,mode,selected_id=None):
    b=_wgs(assessed.copy());r=_wgs(roads.copy()) if roads is not None else None
    bd=_wgs(boundary.copy()) if boundary is not None else None
    f=_wgs(facilities.copy()) if facilities is not None else None
    base=bd if bd is not None else b
    minx,miny,maxx,maxy=base.total_bounds
    m=folium.Map(location=[(miny+maxy)/2,(minx+maxx)/2],tiles=None,zoom_start=13,control_scale=True)

    if r is not None:
        folium.GeoJson(r.__geo_interface__,name="Road Network",
            style_function=lambda _:{"color":"#a0a0a0","weight":1,"opacity":.65}).add_to(m)

    idcol="id" if "id" in b.columns else None
    b["_access_status"]="No demand"
    b["_web_id"]=b[idcol].map(_idstr) if idcol else b.index.astype(str)
    lookup={_idstr(k):v for k,v in results.items()}
    for i,row in b.iterrows():
        info=lookup.get(_idstr(row[idcol] if idcol else i))
        if info is not None:
            if info.get("same_cluster_facility"):status="Cluster-direct"
            elif info.get("cluster_inherited"):status="Cluster-inherited"
            elif info.get("reachable"):status="Road-reachable"
            else:status="Unreachable"
            b.at[i,"_access_status"]=status

    geo=b[["_web_id","_access_status","geometry"]]
    def sty(feat):
        s=feat["properties"]["_access_status"]
        if s=="Road-reachable":return {"color":"#17643a","weight":.35,"fillColor":"#3cab6f","fillOpacity":.85}
        if s=="Cluster-inherited":return {"color":"#1d5b78","weight":.35,"fillColor":"#4da6c8","fillOpacity":.85}
        if s=="Cluster-direct":return {"color":"#5a3a8a","weight":.35,"fillColor":"#9b7bd1","fillOpacity":.88}
        if s=="Unreachable":return {"color":"#9b2c2c","weight":.4,"fillColor":"#e45b5b","fillOpacity":.85}
        return {"color":"#aaa","weight":.2,"fillColor":"#ddd","fillOpacity":.10}
    folium.GeoJson(geo.__geo_interface__,name="Buildings",style_function=sty,
        highlight_function=lambda _:{"color":"#111","weight":2,"fillOpacity":.95},
        tooltip=folium.GeoJsonTooltip(fields=["_web_id","_access_status"],
            aliases=["Building ID:","Accessibility:"])).add_to(m)

    if f is not None:
        labels={"supply":"Supply Facility","rescue":"Medical Facility","evacuation":"Shelter"}
        for idx,row in f.iterrows():
            c=row.geometry.centroid
            name=""
            for col in ["name","NAME","名称","Name","type_name","nature"]:
                if col in f.columns and str(row.get(col,"")).strip() not in {"","nan","None"}:
                    name=str(row.get(col));break
            folium.CircleMarker([c.y,c.x],radius=4.5,color="#222",weight=1.0,fill=True,
                fill_color="#222",fill_opacity=.9,tooltip=name or labels[mode]).add_to(m)

    if selected_id is not None:
        info=lookup.get(_idstr(selected_id))
        add_selected_route(m,info,metric_crs)
        # Highlight selected target facility.
        if info is not None and f is not None and info.get("facility_id") in f.index:
            fr=f.loc[info.get("facility_id")]
            fc=fr.geometry.centroid
            folium.CircleMarker(
                [fc.y,fc.x],radius=8,color="#ffd000",weight=3,fill=True,
                fill_color="#222",fill_opacity=1,tooltip="Selected target facility"
            ).add_to(m)
        hit=b[b["_web_id"]==_idstr(selected_id)]
        if not hit.empty:
            c=hit.geometry.iloc[0].centroid
            folium.CircleMarker([c.y,c.x],radius=8,color="#ffd000",weight=3,
                fill=True,fill_color="#ffffff",fill_opacity=.2,tooltip="Selected building").add_to(m)

    if bd is not None:
        folium.GeoJson(bd.__geo_interface__,name="Boundary",
            style_function=lambda _:{"color":"#111","weight":2,"fillOpacity":0}).add_to(m)

    legend = '<div style="position:fixed;bottom:28px;left:42px;z-index:9999;background:white;border:1px solid #bbb;border-radius:6px;padding:7px 10px;font-size:12px;"><b>Selected route</b><br><span style="color:#1769aa;font-weight:bold;">━━</span> Driving<br><span style="color:#ef8a17;font-weight:bold;">━━</span> Walking</div>'
    m.get_root().html.add_child(folium.Element(legend))
    folium.LayerControl(collapsed=True).add_to(m);m.fit_bounds([[miny,minx],[maxy,maxx]])
    return m,b
