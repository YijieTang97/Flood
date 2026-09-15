import math
import numpy as np
import pandas as pd
import geopandas as gpd
import networkx as nx
import rasterio
from scipy.spatial import cKDTree
from shapely.geometry import Point, LineString
from rasterio.warp import transform as warp_transform

# V19 constants
ACCESS_ROAD_SAMPLE_M=300.0
ACCESS_NODE_SNAP_M=3.0
ACCESS_WALK_BASE_KMH=5.0
ACCESS_CAR_CUTOFF_M=0.30
ACCESS_WALK_CUTOFF_M=0.60
ACCESS_MEDICAL_DEPTH_ABOVE_TOP_FLOOR_M=0.60
ACCESS_SUPPLY_DEMAND_DEPTH_M=0.30
ACCESS_EVAC_TOP_FLOOR_DEPTH_M=0.30
ACCESS_HOSPITAL_FAILURE_DEPTH_M=0.60
ACCESS_FACILITY_FAILURE_DEPTH_M=0.60
ACCESS_MAX_TIME_MIN=30.0
ACCESS_LOCAL_ROAD_K=5
ACCESS_LOCAL_ROAD_MAX_M=150.0
ACCESS_BUILDING_CLUSTER_GAP_M=30.0
FLOOR_HEIGHT=4.0

FCLASS_SPEED={
"motorway":100.0,"motorway_link":60.0,"trunk":80.0,"trunk_link":50.0,
"primary":60.0,"primary_link":45.0,"secondary":50.0,"secondary_link":40.0,
"tertiary":40.0,"tertiary_link":35.0,"unclassified":30.0,"residential":30.0,
"living_street":10.0,"service":20.0,"track":15.0,"path":0.0,"footway":0.0,
"pedestrian":0.0,"steps":0.0,"cycleway":15.0}

def car_speed(base,d):
    if base<=0 or d>=ACCESS_CAR_CUTOFF_M:return 0.0
    r=max(0,d)/ACCESS_CAR_CUTOFF_M
    return base*max(0,1-r**1.5)**2

def walk_speed(base,d):
    if base<=0 or d>=ACCESS_WALK_CUTOFF_M:return 0.0
    r=max(0,d)/ACCESS_WALK_CUTOFF_M
    return base*max(0,1-r**1.5)**1.5

def floors_from_row(row):
    for c in ["层数","floors","floor"]:
        if c in row.index and pd.notna(row[c]):
            try:return max(1,min(3,int(round(float(row[c])))))
            except:pass
    try:return max(1,min(3,int(round(float(row.get("Height",FLOOR_HEIGHT))/FLOOR_HEIGHT))))
    except:return 1

def residential(gdf):
    if "type_name" in gdf.columns:
        return gdf[gdf["type_name"].astype(str).str.strip()=="Residential Building"].copy()
    return gdf.copy()

def _speed(row):
    try:
        raw=str(row.get("maxspeed","")).lower().replace("km/h","").strip()
        if raw and raw not in {"nan","none"}:
            v=float(raw.split(";")[0]); return min(max(v,5),130)
    except:pass
    return FCLASS_SPEED.get(str(row.get("fclass","")).strip().lower(),30.0)

def _segments(geom,step=ACCESS_ROAD_SAMPLE_M):
    geoms=[geom] if geom.geom_type=="LineString" else list(geom.geoms) if geom.geom_type=="MultiLineString" else []
    for line in geoms:
        coords=list(line.coords)
        for a,b in zip(coords[:-1],coords[1:]):
            p1,p2=Point(a),Point(b);L=p1.distance(p2)
            if L<=0:continue
            n=max(1,int(math.ceil(L/step)))
            xs=np.linspace(p1.x,p2.x,n+1);ys=np.linspace(p1.y,p2.y,n+1)
            for i in range(n):
                yield LineString([(xs[i],ys[i]),(xs[i+1],ys[i+1])])

def _node_key(x,y):
    t=max(1.0,ACCESS_NODE_SNAP_M)
    return (int(round(x/t)),int(round(y/t)))

def _sample_xy(xs,ys,crs,raster_path):
    with rasterio.open(raster_path) as src:
        if str(crs)!=str(src.crs):
            xs,ys=warp_transform(crs,src.crs,list(xs),list(ys))
        vals=np.array([v[0] for v in src.sample(zip(xs,ys),masked=True)],float)
        if src.nodata is not None: vals[np.isclose(vals,src.nodata,equal_nan=False)]=np.nan
    return np.nan_to_num(vals,nan=0,posinf=0,neginf=0).clip(min=0)

def build_graph(roads,raster_path):
    metric=roads.estimate_utm_crs() or roads.crs
    r=roads.to_crs(metric)
    nodes={};templates=[]
    for ridx,row in r.iterrows():
        base=_speed(row);one=str(row.get("oneway","")).strip().upper()
        direction="forward" if one in {"T","Y","YES","1","TRUE"} else "reverse" if one in {"-1","R","REVERSE"} else "both"
        for seg in _segments(row.geometry):
            a,b=list(seg.coords)[0],list(seg.coords)[-1]
            u,v=_node_key(*a),_node_key(*b)
            if u==v:continue
            nodes.setdefault(u,a);nodes.setdefault(v,b)
            mid=seg.interpolate(.5,normalized=True)
            templates.append((u,v,float(seg.length),mid.x,mid.y,base,direction,seg))
    if not templates:raise ValueError("extended_r.shp cannot build a road graph.")
    depths=_sample_xy([e[3] for e in templates],[e[4] for e in templates],metric,raster_path)
    G=nx.DiGraph()
    for k,(x,y) in nodes.items():
        G.add_node((k,"car"),x=x,y=y,mode="car")
        G.add_node((k,"walk"),x=x,y=y,mode="walk")
        G.add_edge((k,"car"),(k,"walk"),weight=0,time_min=0,length_m=0,mode="transfer")
    for e,d in zip(templates,depths):
        u,v,L,_,_,base,direction,geom=e
        cs=car_speed(base,float(d));ws=walk_speed(ACCESS_WALK_BASE_KMH,float(d))
        def add(mode,speed,directed):
            if speed<=.05:return
            t=L/(speed*1000/60)
            attrs=dict(weight=t,time_min=t,length_m=L,mode=mode,depth_m=float(d),geometry=geom)
            if directed=="forward":G.add_edge((u,mode),(v,mode),**attrs)
            elif directed=="reverse":G.add_edge((v,mode),(u,mode),**attrs)
            else:
                G.add_edge((u,mode),(v,mode),**attrs);G.add_edge((v,mode),(u,mode),**attrs)
        add("car",cs,direction)
        if ws>.05:
            t=L/(ws*1000/60);attrs=dict(weight=t,time_min=t,length_m=L,mode="walk",depth_m=float(d),geometry=geom)
            G.add_edge((u,"walk"),(v,"walk"),**attrs);G.add_edge((v,"walk"),(u,"walk"),**attrs)
    keys=list(nodes);tree=cKDTree(np.array([nodes[k] for k in keys],float))
    return G,metric,nodes,keys,tree

def candidates(tree,keys,x,y):
    k=min(ACCESS_LOCAL_ROAD_K,len(keys))
    ds,ps=tree.query([x,y],k=k);ds=np.atleast_1d(ds);ps=np.atleast_1d(ps)
    out=[(keys[int(p)],float(d)) for d,p in zip(ds,ps) if np.isfinite(d) and d<=ACCESS_LOCAL_ROAD_MAX_M]
    if not out:
        d,p=tree.query([x,y],k=1);out=[(keys[int(p)],float(d))]
    return out

def path_metrics(G,path):
    car_d=walk_d=car_t=walk_t=0.;transfer=False
    geoms=[]
    for a,b in zip(path[:-1],path[1:]):
        e=G[a][b];mode=e.get("mode")
        if mode=="transfer":transfer=True;continue
        L=float(e.get("length_m",0));t=float(e.get("time_min",0))
        if mode=="car":car_d+=L;car_t+=t
        else:walk_d+=L;walk_t+=t
        if e.get("geometry") is not None:geoms.append((mode,e["geometry"]))
    return car_d,walk_d,car_t,walk_t,transfer,geoms

def facility_records(gdf,metric,tree,keys,raster_path,mode):
    if gdf is None or gdf.empty:return []
    q=gdf.to_crs(metric);cs=q.geometry.centroid
    depths=_sample_xy(cs.x,cs.y,metric,raster_path)
    out=[]
    for (idx,row),c,d in zip(q.iterrows(),cs,depths):
        threshold=ACCESS_HOSPITAL_FAILURE_DEPTH_M if mode=="rescue" else ACCESS_FACILITY_FAILURE_DEPTH_M
        if d>=threshold:continue
        name=""
        for col in ["name","NAME","名称","Name","type_name","nature"]:
            if col in q.columns and pd.notna(row.get(col)):
                name=str(row.get(col)).strip()
                if name:break
        out.append(dict(fid=idx,name=name or f"{mode}_{idx}",x=c.x,y=c.y,
                        nodes=candidates(tree,keys,c.x,c.y),depth=float(d)))
    return out

def demand_records(buildings,assessed,metric,tree,keys,mode,allow_no_tree=False):
    h=residential(assessed).to_crs(metric);out=[]
    for idx,row in h.iterrows():
        cur=float(row.get("cur_depth",0));cum=float(row.get("cum_depth",0));fl=floors_from_row(row)
        top=max(0,cur-(fl-1)*FLOOR_HEIGHT)
        need=(cum>=ACCESS_SUPPLY_DEMAND_DEPTH_M) if mode=="supply" else (top>ACCESS_MEDICAL_DEPTH_ABOVE_TOP_FLOOR_M) if mode=="rescue" else (top>ACCESS_EVAC_TOP_FLOOR_DEPTH_M)
        if not need:continue
        c=row.geometry.centroid
        nd=[] if allow_no_tree else candidates(tree,keys,c.x,c.y)
        out.append(dict(house_id=row.get("id",idx),gdf_index=idx,x=c.x,y=c.y,
                        nodes=nd,floors=fl,cur=cur,cum=cum,top=top))
    return out

def calculate_accessibility(buildings,assessed,roads,facilities,raster_path,mode):
    G,metric,nodes,keys,tree=build_graph(roads,raster_path)
    houses=demand_records(buildings,assessed,metric,tree,keys,mode)
    facs=facility_records(facilities,metric,tree,keys,raster_path,mode)
    results={}
    walk_mpm=ACCESS_WALK_BASE_KMH*1000/60
    if not houses:return results,{"total":0,"reachable":0,"unreachable":0,"rate":0,"avg_time_min":np.nan,"transfer_count":0},metric
    if not facs:
        for h in houses:results[h["house_id"]]=dict(reachable=False,reason="all_facilities_failed")
        return results,{"total":len(houses),"reachable":0,"unreachable":len(houses),"rate":0,"avg_time_min":np.nan,"transfer_count":0},metric

    if mode in {"supply","rescue"}:
        srcmap={};sources=[]
        for f in facs:
            for n,off in f["nodes"]:
                s=(n,"car")
                if s in G and (s not in srcmap or off<srcmap[s][1]):
                    srcmap[s]=(f,off)
                    if s not in sources:sources.append(s)
        lengths,paths=nx.multi_source_dijkstra(G,sources=sources,weight="weight") if sources else ({},{})
        for h in houses:
            opts=[]
            for n,off in h["nodes"]:
                for state in [(n,"car"),(n,"walk")]:
                    if state in lengths:opts.append((lengths[state]+off/walk_mpm,state,off))
            if not opts:
                results[h["house_id"]]=dict(reachable=False,reason="network_disconnected");continue
            _,target,hoff=min(opts,key=lambda z:z[0]);path=paths[target];fac,foff=srcmap[path[0]]
            cd,wd,ct,wt,tr,geoms=path_metrics(G,path)
            total=lengths[target]+(hoff+foff)/walk_mpm
            src_key=path[0][0]; dst_key=path[-1][0]
            sx,sy=nodes[src_key]; tx,ty=nodes[dst_key]
            full_geoms=[]
            if math.hypot(fac["x"]-sx,fac["y"]-sy)>0.2:
                full_geoms.append(("walk",LineString([(fac["x"],fac["y"]),(sx,sy)])))
            full_geoms.extend(geoms)
            if math.hypot(h["x"]-tx,h["y"]-ty)>0.2:
                full_geoms.append(("walk",LineString([(tx,ty),(h["x"],h["y"])])))
            results[h["house_id"]]=dict(reachable=total<=ACCESS_MAX_TIME_MIN,network_reachable=True,
                facility_name=fac["name"],facility_id=fac["fid"],total_time_min=total,
                total_distance_m=cd+wd+hoff+foff,car_distance_m=cd,walk_distance_m=wd+hoff+foff,
                car_time_min=ct,walk_time_min=wt+(hoff+foff)/walk_mpm,transfer=tr,
                reason="" if total<=ACCESS_MAX_TIME_MIN else "over_time_threshold",path_geoms=full_geoms)
    else:
        RG=G.reverse(copy=False);srcmap={};sources=[]
        for f in facs:
            for n,off in f["nodes"]:
                for state in [(n,"car"),(n,"walk")]:
                    if state in G and (state not in srcmap or off<srcmap[state][1]):
                        srcmap[state]=(f,off)
                        if state not in sources:sources.append(state)
        lengths,paths=nx.multi_source_dijkstra(RG,sources=sources,weight="weight") if sources else ({},{})
        for h in houses:
            opts=[]
            for n,off in h["nodes"]:
                for state in [(n,"car"),(n,"walk")]:
                    if state in lengths:opts.append((lengths[state]+off/walk_mpm,state,off))
            if not opts:
                results[h["house_id"]]=dict(reachable=False,reason="network_disconnected");continue
            _,start,hoff=min(opts,key=lambda z:z[0]);rev=paths[start];path=list(reversed(rev))
            fac,foff=srcmap[rev[0]];cd,wd,ct,wt,tr,geoms=path_metrics(G,path)
            total=lengths[start]+(hoff+foff)/walk_mpm
            src_key=path[0][0]; dst_key=path[-1][0]
            sx,sy=nodes[src_key]; tx,ty=nodes[dst_key]
            full_geoms=[]
            if math.hypot(h["x"]-sx,h["y"]-sy)>0.2:
                full_geoms.append(("walk",LineString([(h["x"],h["y"]),(sx,sy)])))
            full_geoms.extend(geoms)
            if math.hypot(fac["x"]-tx,fac["y"]-ty)>0.2:
                full_geoms.append(("walk",LineString([(tx,ty),(fac["x"],fac["y"])])))
            results[h["house_id"]]=dict(reachable=total<=ACCESS_MAX_TIME_MIN,network_reachable=True,
                facility_name=fac["name"],facility_id=fac["fid"],total_time_min=total,
                total_distance_m=cd+wd+hoff+foff,car_distance_m=cd,walk_distance_m=wd+hoff+foff,
                car_time_min=ct,walk_time_min=wt+(hoff+foff)/walk_mpm,transfer=tr,
                reason="" if total<=ACCESS_MAX_TIME_MIN else "over_time_threshold",path_geoms=full_geoms)
    # V19 cluster-first correction after true road-path calculation.
    results=apply_v19_cluster_accessibility(buildings,facilities,houses,facs,results,metric)
    summary=summarize_results(results)
    return results,summary,metric


def _idstr(v):
    try:
        f=float(v)
        if np.isfinite(f) and f.is_integer(): return str(int(f))
    except: pass
    return str(v)

def _building_id_field(g):
    for c in ["房屋ID","id","ID","building_id","TARGET_FID"]:
        if c in g.columns:return c
    return None

def build_residential_clusters(buildings, facilities, metric_crs):
    """
    V19 continuous residential-building clusters:
    polygon boundary distance <= 30 m => adjacent; connected components => cluster.
    A facility belongs to the nearest eligible building cluster only when <=30 m.
    """
    bg=buildings.to_crs(metric_crs).copy()
    bidf=_building_id_field(bg)
    if bidf is None:
        bg["_cluster_bid"]=bg.index.astype(str);bidf="_cluster_bid"
    bg["_cluster_bid_str"]=bg[bidf].map(_idstr)

    if "type_name" in bg.columns:
        typ=bg["type_name"].astype(str).str.strip()
        resident=typ.isin(["居民住宅","Residential Building"]) | typ.str.contains("Residential",case=False,na=False)
    elif "属性" in bg.columns:
        resident=bg["属性"].astype(str).str.zfill(2).eq("00")
    else:
        resident=pd.Series(True,index=bg.index)

    eg=bg.loc[resident].copy()
    if eg.empty:return {},{},{}
    graph=nx.Graph()
    for bid in eg["_cluster_bid_str"]:graph.add_node(str(bid))
    gap=float(ACCESS_BUILDING_CLUSTER_GAP_M)
    try:sidx=eg.sindex
    except:sidx=None
    idxs=list(eg.index)
    posmap={idx:i for i,idx in enumerate(idxs)}
    for pos_i,idx_i in enumerate(idxs):
        gi=eg.loc[idx_i].geometry
        if gi is None or gi.is_empty:continue
        minx,miny,maxx,maxy=gi.bounds
        cand=list(sidx.intersection((minx-gap,miny-gap,maxx+gap,maxy+gap))) if sidx is not None else range(len(eg))
        for pos_j in cand:
            if pos_j<=pos_i:continue
            idx_j=eg.index[pos_j];gj=eg.loc[idx_j].geometry
            if gj is not None and not gj.is_empty and float(gi.distance(gj))<=gap:
                graph.add_edge(str(eg.loc[idx_i,"_cluster_bid_str"]),str(eg.loc[idx_j,"_cluster_bid_str"]))
    members={};bmap={}
    for cid,comp in enumerate(nx.connected_components(graph)):
        members[cid]=list(comp)
        for bid in comp:bmap[str(bid)]=cid

    fmap={}
    if facilities is not None and not facilities.empty:
        fq=facilities.to_crs(metric_crs)
        for fid,row in fq.iterrows():
            p=row.geometry.centroid
            ds=eg.geometry.distance(p)
            if len(ds):
                ni=ds.idxmin()
                if float(ds.loc[ni])<=gap:
                    fmap[fid]=bmap.get(str(eg.loc[ni,"_cluster_bid_str"]))
                else:fmap[fid]=None
            else:fmap[fid]=None
    return bmap,fmap,members

def apply_v19_cluster_accessibility(buildings, facilities_gdf, houses, facs, results, metric_crs):
    """
    V19 strategy:
    A) valid facility in same cluster => whole demand cluster directly reachable, no road path.
    B) otherwise, if >=1 demand building has a true <=30-min road path, all demand buildings
       in that cluster inherit the nearest truly road-reachable building's road path.
       No house-to-house straight line is added.
    """
    bmap,fmap,members=build_residential_clusters(buildings,facilities_gdf,metric_crs)
    for h in houses:h["cluster_id"]=bmap.get(_idstr(h["house_id"]))
    for f in facs:f["cluster_id"]=fmap.get(f["fid"])

    cluster_facs={}
    for f in facs:
        if f.get("cluster_id") is not None:cluster_facs.setdefault(f["cluster_id"],[]).append(f)
    cluster_houses={}
    for h in houses:
        if h.get("cluster_id") is not None:cluster_houses.setdefault(h["cluster_id"],[]).append(h)

    for cid,hs in cluster_houses.items():
        same=cluster_facs.get(cid,[])
        if same:
            for h in hs:
                fac=min(same,key=lambda f:math.hypot(h["x"]-f["x"],h["y"]-f["y"]))
                old=results.get(h["house_id"],{})
                results[h["house_id"]]={**old,"reachable":True,"network_reachable":True,
                    "within_time_threshold":True,"same_cluster_facility":True,
                    "cluster_inherited":False,"road_direct_reachable":False,"cluster_id":cid,
                    "facility_name":fac["name"],"facility_id":fac["fid"],
                    "total_time_min":0.0,"total_distance_m":0.0,
                    "car_distance_m":0.0,"walk_distance_m":0.0,
                    "car_time_min":0.0,"walk_time_min":0.0,"transfer":False,
                    "reason":"","path_geoms":[]}
            continue

        seeds=[]
        for h in hs:
            info=results.get(h["house_id"],{})
            if info.get("reachable") and info.get("path_geoms") and not info.get("cluster_inherited") and not info.get("same_cluster_facility"):
                seeds.append(h)
                info["cluster_id"]=cid;info["road_direct_reachable"]=True
        if not seeds:continue

        for h in hs:
            hid=h["house_id"];info=results.get(hid,{})
            if info.get("reachable") and info.get("path_geoms") and not info.get("cluster_inherited"):
                continue
            seed=min(seeds,key=lambda q:math.hypot(h["x"]-q["x"],h["y"]-q["y"]))
            inherited=dict(results[seed["house_id"]])
            inherited.update({"reachable":True,"network_reachable":True,"within_time_threshold":True,
                "cluster_inherited":True,"same_cluster_facility":False,"cluster_id":cid,
                "inherited_from_house_id":seed["house_id"],"road_direct_reachable":False,"reason":""})
            results[hid]=inherited
    return results

def summarize_results(results):
    vals=list(results.values());reach=[v for v in vals if v.get("reachable")]
    finite=[float(v.get("total_time_min",np.nan)) for v in reach if np.isfinite(v.get("total_time_min",np.nan))]
    return dict(total=len(vals),reachable=len(reach),unreachable=len(vals)-len(reach),
        rate=100*len(reach)/len(vals) if vals else 0,
        avg_time_min=float(np.mean(finite)) if finite else np.nan,
        transfer_count=sum(bool(v.get("transfer")) for v in reach),
        same_cluster_direct_count=sum(bool(v.get("same_cluster_facility")) for v in reach),
        cluster_inherited_count=sum(bool(v.get("cluster_inherited")) for v in reach),
        road_direct_count=sum(bool(v.get("road_direct_reachable")) for v in reach))
