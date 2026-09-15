import folium
import geopandas as gpd

def make_preposition_map(boundary, roads, assessed, demand, safe_candidates, selected_sites, metric_crs):
    bd=boundary.to_crs(4326); b=assessed.to_crs(4326)
    r=roads.to_crs(4326) if roads is not None and not roads.empty else None
    safe=safe_candidates.to_crs(4326); sel=selected_sites.to_crs(4326)
    minx,miny,maxx,maxy=bd.total_bounds
    m=folium.Map(location=[(miny+maxy)/2,(minx+maxx)/2],tiles=None,zoom_start=13,control_scale=True)

    if r is not None:
        folium.GeoJson(r.__geo_interface__,name="Road Network",
            style_function=lambda _:{"color":"#aaa","weight":.8,"opacity":.6}).add_to(m)

    # Demand buildings at worst hour.
    ids=set()
    for h in demand:
        try:ids.add(str(int(float(h["house_id"]))))
        except:ids.add(str(h["house_id"]))
    idcol="id" if "id" in b.columns else None
    b["_bid"]=b[idcol].apply(lambda x:str(int(float(x))) if str(x) not in {"nan","None"} else "") if idcol else b.index.astype(str)
    db=b[b["_bid"].isin(ids)]
    if not db.empty:
        folium.GeoJson(db[["_bid","geometry"]].__geo_interface__,name="Supply-demand Buildings",
            style_function=lambda _:{"color":"#a52a2a","weight":.35,"fillColor":"#e45b5b","fillOpacity":.78},
            tooltip=folium.GeoJsonTooltip(fields=["_bid"],aliases=["Demand Building:"])).add_to(m)

    for _,row in safe.iterrows():
        c=row.geometry
        folium.CircleMarker([c.y,c.x],radius=2,color="#777",weight=.4,fill=True,
            fill_color="#777",fill_opacity=.35,tooltip=f"Safe candidate {int(row['candidate_id'])}").add_to(m)

    for _,row in sel.iterrows():
        c=row.geometry;sid=int(row["site_id"])
        folium.Marker([c.y,c.x],
            icon=folium.DivIcon(html=f"""<div style="font-size:25px;color:#e6b800;text-shadow:-1px -1px 0 #000,1px -1px 0 #000,-1px 1px 0 #000,1px 1px 0 #000;">★</div>"""),
            tooltip=f"P{sid} · Optimized pre-positioning site").add_to(m)
        folium.Marker([c.y,c.x],icon=folium.DivIcon(
            html=f'<div style="font-size:12px;font-weight:bold;margin-left:17px;margin-top:-3px;">P{sid}</div>')).add_to(m)

    folium.GeoJson(bd.__geo_interface__,name="Town Boundary",
        style_function=lambda _:{"color":"#111","weight":2,"fillOpacity":0}).add_to(m)
    legend='<div style="position:fixed;bottom:30px;left:40px;z-index:9999;background:white;border:1px solid #bbb;border-radius:6px;padding:8px 10px;font-size:12px;"><b>Supply Pre-positioning</b><br><span style="color:#777">●</span> Flood-safe candidate<br><span style="color:#e6b800;font-size:17px">★</span> Optimized site<br><span style="color:#e45b5b">■</span> Supply-demand building</div>'
    m.get_root().html.add_child(folium.Element(legend))
    folium.LayerControl(collapsed=True).add_to(m);m.fit_bounds([[miny,minx],[maxy,maxx]])
    return m
