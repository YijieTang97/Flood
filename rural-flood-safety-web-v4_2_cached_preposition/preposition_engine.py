import numpy as np
import geopandas as gpd
import rasterio
from rasterio.warp import transform as warp_transform
from shapely.geometry import Point
from shapely.ops import unary_union
from flood_engine import sample_depth

GRID_M=500.0
SAFE_DEPTH_M=0.05
SITE_COUNT=5
MIN_SEPARATION_M=500.0

def build_depth_matrix(buildings, rows, progress=None):
    """Read every hourly flood raster exactly once for all buildings."""
    n=len(rows)
    mat=np.zeros((n,len(buildings)),dtype=np.float32)
    for i,row in enumerate(rows):
        mat[i]=sample_depth(buildings,row["path"]).astype(np.float32)
        if progress: progress(i+1,n,"Reading hourly flood depth")
    return mat

def find_worst_hour_from_matrix(mat):
    """Same V19 severity key: sum depth, flooded count, max depth."""
    if mat.size==0: return 0,{"sum_depth_m":0.0,"flooded_houses":0,"max_depth_m":0.0}
    sums=mat.sum(axis=1)
    flooded=(mat>0.01).sum(axis=1)
    maxd=mat.max(axis=1)
    # Python tuple comparison semantics preserved.
    best=max(range(mat.shape[0]),key=lambda i:(float(sums[i]),int(flooded[i]),float(maxd[i])))
    return best,{"sum_depth_m":float(sums[best]),"flooded_houses":int(flooded[best]),"max_depth_m":float(maxd[best])}

def _sample_points(points_gdf,raster_path):
    if points_gdf is None or points_gdf.empty:return np.array([],dtype=float)
    xs=points_gdf.geometry.x.to_numpy(float);ys=points_gdf.geometry.y.to_numpy(float)
    with rasterio.open(raster_path) as src:
        if src.crs is None:raise ValueError(f"Flood raster has no CRS: {raster_path}")
        if str(points_gdf.crs)!=str(src.crs):
            xs,ys=warp_transform(points_gdf.crs,src.crs,xs.tolist(),ys.tolist())
        vals=np.array([v[0] for v in src.sample(zip(xs,ys),masked=True)],float)
        if src.nodata is not None: vals[np.isclose(vals,src.nodata,equal_nan=False)]=np.nan
    return np.nan_to_num(vals,nan=0,posinf=0,neginf=0).clip(min=0)

def generate_candidate_grid(boundary,metric_crs,step=GRID_M):
    bm=boundary.to_crs(metric_crs)
    geom=unary_union([g for g in bm.geometry if g is not None and not g.is_empty])
    if geom is None or geom.is_empty:raise ValueError("Study-area boundary is empty.")
    minx,miny,maxx,maxy=geom.bounds
    def mk(sp):
        return [Point(float(x),float(y)) for x in np.arange(minx+sp/2,maxx,sp)
                for y in np.arange(miny+sp/2,maxy,sp) if geom.covers(Point(float(x),float(y)))]
    pts=mk(float(step))
    if not pts:pts=mk(max(100.0,float(step)/2))
    return gpd.GeoDataFrame({"candidate_id":np.arange(1,len(pts)+1,dtype=int)},geometry=pts,crs=metric_crs)

def filter_never_flooded(candidates,rows,safe_depth=SAFE_DEPTH_M,progress=None):
    maxd=np.zeros(len(candidates),float)
    n=len(rows)
    for i,row in enumerate(rows):
        maxd=np.maximum(maxd,_sample_points(candidates,row["path"]))
        if progress:progress(i+1,n,"Screening flood-safe candidate sites")
    out=candidates.copy()
    out["event_max_depth_m"]=maxd
    out["never_flooded"]=maxd<=float(safe_depth)
    return out[out["never_flooded"]].copy()

def select_sites(safe_candidates,demand_houses,n_sites=SITE_COUNT,min_sep=MIN_SEPARATION_M):
    if safe_candidates is None or safe_candidates.empty:return safe_candidates
    if not demand_houses:return safe_candidates.iloc[0:0].copy()
    n_sites=max(1,min(int(n_sites),len(safe_candidates)))
    demand_xy=np.asarray([[float(h["x"]),float(h["y"])] for h in demand_houses],float)
    cand_xy=np.column_stack([safe_candidates.geometry.x.to_numpy(float),safe_candidates.geometry.y.to_numpy(float)])
    dx=cand_xy[:,None,0]-demand_xy[None,:,0];dy=cand_xy[:,None,1]-demand_xy[None,:,1]
    dm=np.sqrt(dx*dx+dy*dy)
    selected=[];current=np.full(len(demand_houses),np.inf,float)
    for _ in range(n_sites):
        bi=None;bs=np.inf
        for ci in range(len(safe_candidates)):
            if ci in selected:continue
            if selected:
                dsel=np.sqrt(np.sum((cand_xy[selected]-cand_xy[ci])**2,axis=1))
                if np.any(dsel<float(min_sep)):continue
            nb=np.minimum(current,dm[ci]);score=float(np.mean(nb))+0.25*float(np.percentile(nb,90))
            if score<bs:bs=score;bi=ci
        if bi is None:
            for ci in range(len(safe_candidates)):
                if ci in selected:continue
                nb=np.minimum(current,dm[ci]);score=float(np.mean(nb))+0.25*float(np.percentile(nb,90))
                if score<bs:bs=score;bi=ci
        if bi is None:break
        selected.append(bi);current=np.minimum(current,dm[bi])
    result=safe_candidates.iloc[selected].copy().reset_index(drop=True)
    result["site_id"]=np.arange(1,len(result)+1,dtype=int)
    if len(result):
        chosen=np.column_stack([result.geometry.x.to_numpy(float),result.geometry.y.to_numpy(float)])
        D=np.sqrt((chosen[:,None,0]-demand_xy[None,:,0])**2+(chosen[:,None,1]-demand_xy[None,:,1])**2)
        result["nearest_demand_m"]=D.min(axis=1)
        assign=D.argmin(axis=0)
        result["assigned_demand_buildings"]=[int((assign==i).sum()) for i in range(len(result))]
        result["mean_assigned_distance_m"]=[float(D[i,assign==i].mean()) if np.any(assign==i) else np.nan for i in range(len(result))]
    return result

def run_preposition(buildings,boundary,rows,assess_func,demand_records_func,metric_crs,progress=None,depth_matrix=None,safe_candidate_cache=None):
    # V4.1: O(H) raster passes, replacing V4.0's repeated O(H^2) rescans.
    mat=np.asarray(depth_matrix,dtype=np.float32) if depth_matrix is not None else build_depth_matrix(buildings,rows,progress)
    worst_idx,severity=find_worst_hour_from_matrix(mat)
    cur=mat[worst_idx]
    cum=np.max(mat[:worst_idx+1],axis=0)
    assessed=assess_func(buildings.copy(),cur,cum,worst_idx+1)
    houses=demand_records_func(buildings,assessed,metric_crs,None,None,"supply",allow_no_tree=True)
    demand=[h for h in houses if h.get("cum",0)>=0.30]
    candidates=generate_candidate_grid(boundary,metric_crs)
    if safe_candidate_cache is not None and not safe_candidate_cache.empty:
        safe=safe_candidate_cache.to_crs(metric_crs).copy()
        # Retain V19 safety criterion; cache contains only event-safe candidates.
        if "never_flooded" in safe.columns:
            safe=safe[safe["never_flooded"].astype(bool)].copy()
    else:
        safe=filter_never_flooded(candidates,rows,SAFE_DEPTH_M,progress)
    selected=select_sites(safe,demand)
    if progress:progress(1,1,"Optimization complete")
    return dict(worst_idx=worst_idx,severity=severity,assessed=assessed,demand=demand,
                candidates=candidates,safe_candidates=safe,selected_sites=selected)
