from pathlib import Path

ROOT = Path(__file__).resolve().parent
DATA_DIR = ROOT / "data" / "中洲镇_Zhongzhou_Town"
FLOOD_DIR = DATA_DIR / "Flood_clipped"

def first_existing(*names):
    for name in names:
        p = DATA_DIR / name
        if p.exists():
            return p
    return None

BUILDING_FILE = first_existing("zhongzhou_building_classified.shp", "zhongzhou_building.shp")
ROAD_FILE = first_existing("zhongzhou_road.shp", "extended_r.shp")
BOUNDARY_FILE = first_existing("zhongzhou_border.shp", "zhongzhou_village_boundary.shp")

THEMES = {
    "Flood Exposure": ("cur_depth", "Blues"),
    "Building Vulnerability": ("damage_pct", "YlOrRd"),
    "Household Economic Loss": ("loss_total", "OrRd"),
    "Human Safety Risk": ("inj_prob", "Purples"),
    "Early Warning Loss Reduction": ("loss_red", "Greens"),
}


# V3.0 accessibility layers — V19 naming rules.
EXTENDED_ROAD_FILE = first_existing("extended_r.shp", "extended_road.shp", "zhongzhou_road.shp")
MEDICAL_FILE = first_existing("extended_m.shp", "zhongzhou_medi.shp")
SUPPLY_FILE = first_existing("zhongzhou_flood_supply_points.shp", "huaiji_zhongzhou_supply_points.shp", "supply_points.shp")
SHELTER_FILE = first_existing("zhongzhou_flood_shelter_points.shp", "huaiji_zhongzhou_shelter_points.shp", "shelter_points.shp")
