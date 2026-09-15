import re
from pathlib import Path
import numpy as np
import rasterio
from rasterio.warp import transform as warp_transform

PATTERN=re.compile(
    r"Depth\s*\(\s*(\d{2}[A-Za-z]{3}\d{4})\s+(\d{2})\s+(\d{2})\s+(\d{2})\s*\)",
    re.I
)

def catalog(folder):
    files=list(Path(folder).glob("*.tif"))+list(Path(folder).glob("*.tiff"))
    rows=[]
    for p in files:
        m=PATTERN.search(p.name)
        if m:
            date,h,mi,se=m.groups()
            rows.append({"path":p,"date":date.upper(),"hour_clock":int(h),
                         "minute":int(mi),"second":int(se)})
    rows.sort(key=lambda x:(x["date"],x["hour_clock"],x["minute"],x["second"],x["path"].name))
    # The web slider is simulation sequence hour, while the timestamp is retained as label.
    for i,r in enumerate(rows, start=1):
        r["sim_hour"]=i
        r["label"]=f'Hour {i} — {r["date"]} {r["hour_clock"]:02d}:{r["minute"]:02d}'
    return rows

def representative_xy(gdf):
    if gdf.crs is None: raise ValueError("Building layer has no CRS.")
    pts=gdf.geometry.representative_point()
    return np.asarray(pts.x),np.asarray(pts.y)

def sample_depth(gdf, raster_path):
    x,y=representative_xy(gdf)
    with rasterio.open(raster_path) as src:
        if src.crs is None: raise ValueError(f"Raster has no CRS: {raster_path}")
        if str(gdf.crs)!=str(src.crs):
            xx,yy=warp_transform(gdf.crs,src.crs,x.tolist(),y.tolist())
        else:
            xx,yy=x,y
        arr=np.array([v[0] for v in src.sample(zip(xx,yy),masked=True)],dtype=float)
        if src.nodata is not None:
            arr[np.isclose(arr,src.nodata,equal_nan=False)]=np.nan
    return np.nan_to_num(arr,nan=0.0,posinf=0.0,neginf=0.0).clip(min=0)

def depth_matrices(gdf, rows, upto_index):
    series=[]
    for r in rows[:upto_index+1]:
        series.append(sample_depth(gdf,r["path"]))
    if not series:
        z=np.zeros(len(gdf),float); return z,z
    mat=np.vstack(series)
    return mat[-1],np.maximum.accumulate(mat,axis=0)[-1]
