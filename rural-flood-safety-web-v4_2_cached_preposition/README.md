# Rural Flood Safety Assessment Platform — V4.2

V4.2 keeps the V3.2 assessment/accessibility modules and adds the V19 Emergency Supply Pre-positioning Optimization.

## Pages
1. Integrated flood vulnerability assessment
2. Accessibility Analysis
3. Supply Pre-positioning

## V19 pre-positioning logic preserved
- Automatically identify the most severe supply flood hour.
- Generate candidate points on a 500 m regular grid inside the study-area boundary.
- Keep only candidates whose maximum depth over the entire flood event is <= 0.05 m.
- Select 5 optimized sites.
- Prefer >= 500 m spatial separation.
- Greedy p-median-style objective:
  mean nearest-demand distance + 0.25 × 90th-percentile nearest-demand distance.
- Demand buildings use the existing supply-demand definition from the accessibility model.
- The optimization does not invent straight-line delivery routes.

## Run
```bash
python -m pip install -r requirements.txt
streamlit run streamlit_app.py
```

Keep your existing `data/中洲镇_Zhongzhou_Town/` folder unchanged.


## V4.2 performance fix
V4.0 repeatedly reread earlier hourly rasters while searching for the worst hour. V4.2 builds the building-by-hour depth matrix in one pass, so each building flood raster is read once. A progress bar is shown for both flood scanning and candidate safety screening.

## V4.2 interaction/cache fix
- The optimization no longer runs automatically when the page opens.
- Click `Run Optimization` once to calculate the full flood event.
- The complete optimization result is stored in `st.session_state`.
- Folium zoom, pan, layer interaction, and other Streamlit reruns reuse the cached result.
- Use `Re-run Optimization` only when you intentionally want to recalculate.
- `Clear Cached Result` discards the current session result.
- If the flood raster set or core input sizes change, the page invalidates the cached result automatically.
- V4.1's one-pass hourly-depth optimization and all V19 pre-positioning parameters are retained.
