import base64
from io import BytesIO

import folium
import geopandas as gpd
import matplotlib
import matplotlib.pyplot as plt
import numpy as np
import rasterio
from branca.colormap import LinearColormap
from folium.raster_layers import ImageOverlay
from rasterio.warp import calculate_default_transform, reproject, Resampling


def to_wgs(g):
    if g is None or g.empty:
        return g
    if g.crs is None:
        raise ValueError("Vector CRS missing.")
    return g.to_crs(4326)


def scale_for(values, cmap_name):
    a = np.asarray(values, float)
    a = a[np.isfinite(a)]
    vmin = 0.0
    vmax = float(np.percentile(a, 98)) if a.size else 1.0
    if vmax <= 0:
        vmax = 1.0
    cmap = matplotlib.colormaps[cmap_name]
    cols = [matplotlib.colors.to_hex(cmap(x)) for x in np.linspace(.08, .95, 8)]
    return LinearColormap(cols, vmin=vmin, vmax=vmax)


def flood_raster_overlay(raster_path, max_size=1400):
    """
    Convert the current HEC-RAS depth TIFF to a transparent PNG overlay
    in EPSG:4326. Dry cells / NoData are fully transparent.
    """
    if raster_path is None:
        return None, None, None

    raster_path = str(raster_path)

    with rasterio.open(raster_path) as src:
        if src.crs is None:
            raise ValueError(f"Flood raster has no CRS: {raster_path}")

        # Downsample before reprojection to keep the browser responsive.
        factor = min(1.0, max_size / max(src.width, src.height))
        approx_w = max(1, int(src.width * factor))
        approx_h = max(1, int(src.height * factor))

        transform, width, height = calculate_default_transform(
            src.crs, "EPSG:4326",
            src.width, src.height, *src.bounds,
            dst_width=approx_w,
            dst_height=approx_h
        )

        dst = np.full((height, width), np.nan, dtype=np.float32)

        reproject(
            source=rasterio.band(src, 1),
            destination=dst,
            src_transform=src.transform,
            src_crs=src.crs,
            src_nodata=src.nodata,
            dst_transform=transform,
            dst_crs="EPSG:4326",
            dst_nodata=np.nan,
            resampling=Resampling.bilinear,
        )

    valid = np.isfinite(dst) & (dst > 0.01)
    if not valid.any():
        return None, None, None

    positive = dst[valid]
    vmax = float(np.percentile(positive, 98))
    vmax = max(vmax, 0.1)

    # Use a fixed zero origin so light-to-dark blue corresponds to depth.
    norm = np.clip(dst / vmax, 0.0, 1.0)
    cmap = matplotlib.colormaps["Blues"]

    rgba = cmap(0.18 + 0.78 * norm)
    rgba[..., 3] = np.where(valid, 0.68, 0.0)

    # Convert RGBA array to an in-memory transparent PNG.
    buf = BytesIO()
    plt.imsave(buf, rgba, format="png")
    encoded = base64.b64encode(buf.getvalue()).decode("ascii")

    west = transform.c
    north = transform.f
    east = west + transform.a * width
    south = north + transform.e * height

    bounds = [[south, west], [north, east]]
    return f"data:image/png;base64,{encoded}", bounds, vmax


def create_map(buildings, roads, boundary, field, theme, cmap_name, flood_path=None):
    b = to_wgs(buildings.copy())
    r = to_wgs(roads.copy()) if roads is not None and not roads.empty else None
    bd = to_wgs(boundary.copy()) if boundary is not None and not boundary.empty else None

    base = bd if bd is not None else b
    minx, miny, maxx, maxy = base.total_bounds

    # Clean gray background by default. OSM remains optional and OFF.
    m = folium.Map(
        location=[(miny + maxy) / 2, (minx + maxx) / 2],
        zoom_start=13,
        tiles=None,
        control_scale=True,
        prefer_canvas=True,
    )

    folium.TileLayer(
        tiles="https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png",
        attr="© OpenStreetMap contributors",
        name="OpenStreetMap",
        overlay=False,
        control=True,
        show=False,
    ).add_to(m)

    # 1) Flood raster: below roads/buildings, above blank background.
    try:
        image, raster_bounds, raster_vmax = flood_raster_overlay(flood_path)
        if image is not None:
            ImageOverlay(
                image=image,
                bounds=raster_bounds,
                opacity=1.0,
                name="Flood Depth Raster",
                interactive=False,
                cross_origin=False,
                zindex=2,
            ).add_to(m)
    except Exception as exc:
        # Keep the vector map usable; Streamlit still calculates depth separately.
        print(f"[Flood overlay warning] {exc}")

    # 2) Roads
    if r is not None:
        folium.GeoJson(
            r.__geo_interface__,
            name="Roads",
            style_function=lambda _: {
                "color": "#666666",
                "weight": 1.0,
                "opacity": .85,
            },
        ).add_to(m)

    # 3) Buildings
    vals = b[field].astype(float).fillna(0)
    sc = scale_for(vals, cmap_name)
    sc.caption = theme

    b["_web_value"] = vals
    if "id" in b.columns:
        b["_web_id"] = b["id"].apply(
            lambda x: str(int(float(x))) if x is not None and str(x) != "nan" else "--"
        )
    elif "ID" in b.columns:
        b["_web_id"] = b["ID"].apply(
            lambda x: str(int(float(x))) if x is not None and str(x) != "nan" else "--"
        )
    else:
        b["_web_id"] = b.index.astype(str)

    keep = [
        "_web_id", "_web_value", "id", "Height", "buildingAr", "type_name",
        "cur_depth", "cum_depth", "damage_pct", "loss_total", "inj_prob",
        "warn_loss", "loss_red", "red_pct", "floors_calc", "area_calc"
    ]
    keep = [c for c in keep if c in b.columns]
    geo = b[keep + ["geometry"]]

    def sty(f):
        try:
            v = float(f["properties"].get("_web_value", 0) or 0)
        except Exception:
            v = 0.0

        # Flood Exposure: dry buildings remain almost transparent so the
        # underlying inundation raster is clearly visible.
        if theme == "Flood Exposure" and v <= 0.01:
            return {
                "color": "#777777",
                "weight": 0.25,
                "fillColor": "#FFFFFF",
                "fillOpacity": 0.05,
            }

        return {
            "color": "#555555",
            "weight": 0.30,
            "fillColor": sc(v),
            "fillOpacity": 0.78,
        }

    folium.GeoJson(
        geo.__geo_interface__,
        name="Buildings",
        style_function=sty,
        highlight_function=lambda _: {
            "color": "#111111",
            "weight": 2,
            "fillOpacity": .95,
        },
        tooltip=folium.GeoJsonTooltip(
            fields=["_web_id", "_web_value"],
            aliases=["Building ID:", theme + ":"],
        ),
    ).add_to(m)

    # 4) Boundary on top
    if bd is not None:
        folium.GeoJson(
            bd.__geo_interface__,
            name="Boundary",
            style_function=lambda _: {
                "color": "#111111",
                "weight": 2.0,
                "fillOpacity": 0,
            },
        ).add_to(m)

    sc.add_to(m)
    folium.LayerControl(collapsed=True).add_to(m)
    m.fit_bounds([[miny, minx], [maxy, maxx]])
    return m, b


def nearest_clicked(b, click):
    if b is None or not click:
        return None
    lat, lng = click.get("lat"), click.get("lng")
    if lat is None or lng is None:
        return None

    metric = b.to_crs(3857)
    p = gpd.GeoSeries(
        gpd.points_from_xy([lng], [lat]), crs=4326
    ).to_crs(3857).iloc[0]

    d = metric.geometry.distance(p)
    idx = d.idxmin()
    if float(d.loc[idx]) > 80:
        return None
    return b.loc[idx]
