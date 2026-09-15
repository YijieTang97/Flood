from pathlib import Path
import numpy as np
import geopandas as gpd

from data_config import BUILDING_FILE, BOUNDARY_FILE, FLOOD_DIR, CACHE_DIR, FLOOD_MATRIX_CACHE, PREPOSITION_CANDIDATE_CACHE
from flood_engine import catalog, sample_depth
from preposition_engine import generate_candidate_grid, filter_never_flooded, GRID_M, SAFE_DEPTH_M

def main():
    CACHE_DIR.mkdir(parents=True,exist_ok=True)
    buildings=gpd.read_file(BUILDING_FILE)
    boundary=gpd.read_file(BOUNDARY_FILE)
    rows=catalog(FLOOD_DIR)
    if not rows: raise RuntimeError("No hourly TIFFs found in Flood_clipped.")

    print(f"[1/2] Building flood-depth matrix: {len(buildings):,} buildings × {len(rows)} hours")
    mat=np.zeros((len(rows),len(buildings)),dtype=np.float32)
    for i,r in enumerate(rows):
        mat[i]=sample_depth(buildings,r["path"]).astype(np.float32)
        print(f"  Flood depth {i+1}/{len(rows)}",end="\r")
    np.savez_compressed(FLOOD_MATRIX_CACHE,depth=mat)
    print(f"\n  Saved: {FLOOD_MATRIX_CACHE} ({FLOOD_MATRIX_CACHE.stat().st_size/1024/1024:.2f} MB)")

    metric=buildings.estimate_utm_crs() or buildings.crs
    candidates=generate_candidate_grid(boundary,metric,GRID_M)
    print(f"[2/2] Screening {len(candidates):,} candidates across {len(rows)} hours")
    safe=filter_never_flooded(
        candidates,rows,SAFE_DEPTH_M,
        lambda a,b,c: print(f"  Candidate safety {a}/{b}",end="\r")
    )
    safe.to_file(PREPOSITION_CANDIDATE_CACHE,driver="GPKG")
    print(f"\n  Saved: {PREPOSITION_CANDIDATE_CACHE}")
    print(f"  Flood-safe candidates: {len(safe):,}/{len(candidates):,}")
    print("\nDONE. Commit the web_cache folder to GitHub with the app.")

if __name__=="__main__":
    main()
