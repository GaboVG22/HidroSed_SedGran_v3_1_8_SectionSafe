
from __future__ import annotations

import json
import time
from pathlib import Path

import pandas as pd
import streamlit as st
from shapely.geometry import LineString

from modules.kmz_utils import read_kml, parse_first_point, parse_lines, line_to_shapely_wgs84
from modules.opentopo_engine import bbox_from_margin, bbox_area_km2, build_url, download_dem
from modules.opentopo_tiled_download import download_dem_normal_or_tiled, recommended_tiling
from modules.dem_processing import generate_contours
from modules.tiled_contours import generate_tiled_contours_from_dem, split_bbox_km2_strategy
from modules.topography_support import read_kmz_kml_bytes, parse_topographic_contours, improve_section_points_with_topo
from modules.section_qaqc import select_and_fill_sections, section_report_summary
from modules.visual_3d_hydraulic import create_3d_profile_figure, figure_to_html_bytes
from modules.watershed_morphometry import delineate_basin, metrics_dataframe
from modules.axis_sections import generate_preliminary_axis, export_axis_kmz, generate_cross_sections, sections_excel_bytes
from modules.hydrology_methods import DEFAULT_T, rational_method, dga_ac_series, combine_design_flows, time_concentration_kirpich
from modules.sediment_scour import hydraulic_and_sediment
from modules.hydraulic_hecras_like import hecras_like_steady_profile, sediment_from_hecras_profile
from modules.cartographic_output import make_cartographic_sheet
from modules.roughness_engine import ROUGHNESS_TABLE, COWAN_FACTORS, suggested_roughness, compose_roughness_manual, cowan_n, table_n, roughness_confidence
from modules.synthetic_trapezoid_sections import generate_trapezoid_reach_sections, trapezoid_capacity_table
from modules.granulometry_kmz import read_kmz_or_kml_to_text, parse_granulometry_points, normalize_granulometry_table, validate_granulometry, assign_granulometry_to_sections
from modules.hydrologic_transfer_dual import transfer_flow_area_altitude_distance, rank_hydrometric_stations
from modules.supreme_dashboard import CSS, kpi_html, global_confidence_report
from modules.basin_contours_export import build_basin_contours_kmz
from modules.hydrology_advanced import build_hydrology, adopt_flows as adopt_flows_advanced, PERIODS as HYDRO_PERIODS
from modules.sediment_dynamic import classify_sediment_zones, summarize_zones
from modules.granulometry_engine import (
    DEFAULT_PROFILES_MM, default_profiles_dataframe, profile_to_characteristics,
    extract_granulometry_from_excel, characteristic_table, method_diameter_table,
    profile_curve_dataframe, confidence_label
)
from modules.sections_v13_core import (
    read_kml_or_kmz as v13_read_kml_or_kmz, extract_lines_from_kml as v13_extract_lines_from_kml,
    make_transformers as v13_make_transformers, utm_epsg_from_datum as v13_utm_epsg_from_datum,
    get_lines_dataframe as v13_get_lines_dataframe, project_geom as v13_project_geom,
    generate_chainages as v13_generate_chainages, build_sections as v13_build_sections,
    sections_to_dataframe as v13_sections_to_dataframe, sample_profiles as v13_sample_profiles,
    sample_longitudinal_axis_profile as v13_sample_longitudinal_axis_profile,
    estimated_longitudinal_from_sections as v13_estimated_longitudinal_from_sections,
    evaluate_section_quality as v13_evaluate_section_quality,
    evaluate_modelable_sections as v13_evaluate_modelable_sections,
    build_longitudinal_modelacion as v13_build_longitudinal_modelacion,
    filter_sections_for_modelacion as v13_filter_sections_for_modelacion,
    filter_selected_profile_points as v13_filter_selected_profile_points,
    make_kmz_modelacion as v13_make_kmz_modelacion,
    make_zip_download as v13_make_zip_download,
)

st.set_page_config(page_title="HidroSed v3.0 Dios Supremo · Hotfix Cuenca Anti-Snap · Topo Opcional", page_icon="🌊", layout="wide")

st.markdown(CSS, unsafe_allow_html=True)

OUT = Path("outputs")
OUT.mkdir(exist_ok=True)

if "project_id" not in st.session_state:
    st.session_state["project_id"] = str(int(time.time()))
PROJECT = OUT / st.session_state["project_id"]
PROJECT.mkdir(parents=True, exist_ok=True)


def has(key: str) -> bool:
    v = st.session_state.get(key)
    if v is None:
        return False
    if hasattr(v, "empty"):
        return not v.empty
    if isinstance(v, (str, bytes, list, tuple, dict)):
        return len(v) > 0
    return True


def badge(key, label):
    if has(key):
        st.sidebar.success(f"✓ {label}")
    else:
        st.sidebar.warning(f"○ {label}")


def save_bytes(name: str, data: bytes) -> Path:
    path = PROJECT / name
    path.write_bytes(data)
    return path


def periods_from_text(txt: str):
    vals = set(DEFAULT_T)
    if txt.strip():
        for t in txt.replace(";", ",").split(","):
            try:
                vals.add(float(t.strip()))
            except Exception:
                pass
    return sorted(vals)


st.sidebar.title("HidroSed SedGran v3.1.8")
st.sidebar.caption("Centro de control hidráulico-hidrológico · QA · 3D · trazabilidad")
for k, label in [
    ("control_point", "1 Punto control"),
    ("axis_line", "1 Eje cauce"),
    ("topo_support_df", "1 Curvas apoyo topo"),
    ("dem_path", "2 DEM"),
    ("basin_metrics", "3 Cuenca/morfometría"),
    ("contours_kmz", "4 Curvas"),
    ("sections_df", "4 Secciones"),
    ("hydrology_done", "5 Hidrología"),
    ("q_design", "6 Caudales"),
    ("hydraulic_profile_df", "8 Perfil tipo HEC-RAS"),
    ("sediment_df", "8 Socavación/sedimentos"),
    ("profile_3d_html", "8 Perfil 3D hidráulico"),
    ("cartographic_png", "9 Lámina cartográfica"),
]:
    badge(k, label)

st.markdown(
    """
<div class='hs-hero'>
  <h1>🌊 HidroSed SedGran v3.1.8 · Secciones v13 · Hidrología · Sedimentos</h1>
  <p>Plataforma hidráulica-hidrológica avanzada para cuencas y cauces: DEM OpenTopography, delimitación, curvas, secciones reales o trapezoidales, hidrología normativa, hidráulica 1D tipo HEC‑RAS mejorada, rugosidad avanzada, granulometría georreferenciada, sedimentos, socavación, QA, incertidumbre y visualización 3D.</p>
  <span class='hs-pill'>HEC-RAS 1D enhanced</span><span class='hs-pill'>Hidrología DGA/MC</span><span class='hs-pill'>Rugosidad Cowan/Strickler</span><span class='hs-pill'>Sección trapezoidal fallback</span><span class='hs-pill'>Granulometría tipo/Excel/KMZ</span>
</div>
""",
    unsafe_allow_html=True,
)

st.info(
    "Secuencia oficial: 1 Entrada → 2 DEM → 3 Cuenca/Morfometría → 4 Curvas/Eje → "
    "5 Secciones → 6 Hidrología → 7 Caudales → 8 Hidráulica/Sedimentos → 9 Exportación → 10 Modo Supremo QA/Rugosidad. "
    "Modo recomendado: cuencas hasta 10.000 km² con DEM COP30 y controles QA."
)

tabs = st.tabs([
    "1 · Entrada",
    "2 · DEM OpenTopo",
    "3 · Cuenca y morfometría",
    "4 · Curvas y eje",
    "5 · Secciones",
    "6 · Hidrología",
    "7 · Caudales",
    "8 · Socavación y sedimentos",
    "9 · Cartografía y exportar",
    "10 · Supremo QA/Rugosidad/Trapezoidal",
])

with tabs[0]:
    st.header("1 · Entrada geométrica")
    c1, c2 = st.columns(2)
    with c1:
        point_file = st.file_uploader("KMZ/KML con punto de control", type=["kmz", "kml"], key="point_file")
        if point_file and st.button("Leer punto de control"):
            try:
                kml = read_kml(point_file)
                cp = parse_first_point(kml)
                st.session_state["control_point"] = {"lat": cp.lat, "lon": cp.lon, "name": cp.name}
                st.success(f"Punto leído: {cp.name} · lat {cp.lat:.8f}, lon {cp.lon:.8f}")
            except Exception as exc:
                st.error(str(exc))
    with c2:
        axis_file = st.file_uploader("KMZ/KML eje de cauce opcional", type=["kmz", "kml"], key="axis_file")
        if axis_file and st.button("Leer eje de cauce"):
            try:
                kml = read_kml(axis_file)
                lines = parse_lines(kml)
                if not lines:
                    raise ValueError("No se encontró LineString válido para eje de cauce.")
                line = line_to_shapely_wgs84(lines[0])
                st.session_state["axis_line"] = list(line.coords)
                st.success(f"Eje leído: {lines[0].name} · puntos {len(st.session_state['axis_line'])}")
            except Exception as exc:
                st.error(str(exc))

    st.divider()
    st.subheader("Curvas de nivel de apoyo topográfico opcionales")
    st.caption("Este archivo es 100% opcional. Si no se carga, si falla la lectura o si no contiene cotas válidas, la app continúa usando solo el DEM.")
    topo_file = st.file_uploader(
        "KMZ/KML con curvas de nivel topográficas de apoyo",
        type=["kmz", "kml"],
        key="topo_support_file",
        help="Archivo opcional. Mejora cotas de secciones si las curvas contienen cota en nombre, ExtendedData o coordenada Z.",
    )

    if not topo_file and "topo_support_df" not in st.session_state:
        st.info("Sin curvas de apoyo topográfico: el proceso continuará normalmente con el DEM.")

    if topo_file and st.button("Leer curvas topográficas de apoyo"):
        try:
            topo_kml = read_kmz_kml_bytes(topo_file)
            topo_df = parse_topographic_contours(topo_kml)

            if topo_df is None or topo_df.empty:
                st.session_state.pop("topo_support_df", None)
                st.warning("El archivo fue leído, pero no se detectaron curvas útiles. Se continuará solo con DEM.")
            elif "z_m" not in topo_df.columns or topo_df["z_m"].notna().sum() == 0:
                st.session_state.pop("topo_support_df", None)
                st.warning("El archivo no contiene cotas reconocibles. Se continuará solo con DEM.")
            else:
                st.session_state["topo_support_df"] = topo_df
                st.success(f"Curvas de apoyo leídas: {topo_df['contour_id'].nunique()} curvas · {len(topo_df)} vértices · {topo_df['z_m'].notna().sum()} cotas válidas.")
        except Exception as exc:
            st.session_state.pop("topo_support_df", None)
            st.warning(f"No fue posible usar las curvas topográficas de apoyo. El proceso continuará solo con DEM. Detalle: {exc}")

    if has("topo_support_df"):
        topo_ok = st.session_state["topo_support_df"]
        st.caption("Muestra de curvas topográficas de apoyo cargadas")
        st.dataframe(topo_ok.head(100), use_container_width=True)
        if st.button("Quitar curvas de apoyo y continuar solo con DEM"):
            st.session_state.pop("topo_support_df", None)
            st.success("Curvas de apoyo removidas. La app continuará solo con DEM.")

    if has("control_point"):
        st.subheader("Punto de control activo")
        st.json(st.session_state["control_point"])
    if has("axis_line"):
        st.subheader("Eje de cauce activo")
        st.write(f"Puntos del eje: {len(st.session_state['axis_line'])}")

with tabs[1]:
    st.header("2 · DEM OpenTopography / DEM manual con BBox controlado")

    if not has("control_point"):
        st.warning("Primero ingresa el KMZ/KML con punto de control.")
    else:
        cp = st.session_state["control_point"]

        st.markdown(
            "<div class='hs-info'><b>Mejora v3.1.4:</b> este módulo usa la lógica de la app demcop30_streamlit: "
            "el Área bbox es la ventana rectangular del DEM, no la superficie real de la cuenca. "
            "Seleccione un preajuste según el tamaño esperado para evitar descargas excesivas.</div>",
            unsafe_allow_html=True,
        )

        c1, c2, c3 = st.columns(3)

        with c1:
            api_key = st.text_input("API Key OpenTopography", type="password", key="api_key_manual")
            dem_type = st.selectbox("DEM", ["COP30", "NASADEM", "SRTMGL1", "SRTMGL3"], index=0)
            dem_manual_file = st.file_uploader(
                "DEM GeoTIFF manual opcional",
                type=["tif", "tiff"],
                help="Si ya descargaste el DEM con demcop30_streamlit u otra app estable, cárgalo aquí y omite OpenTopography."
            )
            if dem_manual_file and st.button("Usar DEM manual GeoTIFF"):
                try:
                    dem_bytes = dem_manual_file.getvalue()
                    dem_path = save_bytes("dem_manual_geotiff.tif", dem_bytes)
                    st.session_state["dem_path"] = str(dem_path)
                    st.session_state["dem_bytes"] = dem_bytes
                    st.session_state["dem_source"] = "DEM manual GeoTIFF"
                    st.success(f"DEM manual activo: {len(dem_bytes)/(1024*1024):.2f} MB")
                except Exception as exc:
                    st.error(f"No se pudo cargar DEM manual: {exc}")

        with c2:
            bbox_profile = st.selectbox(
                "Tamaño esperado de la cuenca",
                [
                    "Quebrada pequeña ≤ 50 km²",
                    "Cuenca pequeña 50–500 km²",
                    "Cuenca mediana 500–2.000 km²",
                    "Cuenca grande > 2.000 km²",
                    "Manual"
                ],
                index=0,
                help="Este preajuste controla la ventana DEM. No limita el cálculo hidráulico posterior."
            )

            profile_defaults = {
                "Quebrada pequeña ≤ 50 km²": {"margin_km": 7.5, "margin_deg": 0.06, "bbox_max": 500.0, "expected": 20.0, "basin_max": 80.0, "snap": 250},
                "Cuenca pequeña 50–500 km²": {"margin_km": 15.0, "margin_deg": 0.12, "bbox_max": 2500.0, "expected": 150.0, "basin_max": 750.0, "snap": 500},
                "Cuenca mediana 500–2.000 km²": {"margin_km": 30.0, "margin_deg": 0.25, "bbox_max": 10000.0, "expected": 1000.0, "basin_max": 3000.0, "snap": 1000},
                "Cuenca grande > 2.000 km²": {"margin_km": 60.0, "margin_deg": 0.50, "bbox_max": 40000.0, "expected": 5000.0, "basin_max": 15000.0, "snap": 1500},
                "Manual": {"margin_km": 10.0, "margin_deg": 0.08, "bbox_max": 1000.0, "expected": 50.0, "basin_max": 200.0, "snap": 250},
            }
            prof = profile_defaults[bbox_profile]
            margin_unit = st.radio("Unidad margen", ["km", "grados"], horizontal=True)
            default_margin = prof["margin_km"] if margin_unit == "km" else prof["margin_deg"]
            margin = st.number_input(
                "Margen desde punto",
                min_value=0.001,
                value=float(default_margin),
                step=1.0 if margin_unit == "km" else 0.01,
                format="%.3f" if margin_unit == "grados" else "%.1f",
                help="El margen se aplica hacia norte, sur, este y oeste. Aumente solo si la cuenca toca el borde del DEM."
            )

            st.session_state["bbox_profile"] = bbox_profile
            st.session_state["expected_basin_default"] = float(prof["expected"])
            st.session_state["max_basin_default"] = float(prof["basin_max"])
            st.session_state["snap_default_m"] = int(prof["snap"])

        with c3:
            area_limit = st.number_input(
                "Límite técnico bbox [km²]",
                min_value=1.0,
                value=float(prof["bbox_max"]),
                step=100.0 if prof["bbox_max"] <= 2500 else 1000.0,
                help="Control de seguridad para evitar descargas demasiado grandes. El bbox no es el área de cuenca."
            )
            expected_for_warning = st.number_input(
                "Área real esperada referencial [km²]",
                min_value=0.0,
                value=float(prof["expected"]),
                step=10.0 if prof["expected"] >= 100 else 5.0,
                help="Solo se usa para advertir si el bbox es desproporcionado."
            )
            st.session_state["expected_basin_default"] = float(expected_for_warning)

        bbox = bbox_from_margin(cp["lat"], cp["lon"], margin, margin_unit)
        area = bbox_area_km2(bbox)
        st.session_state["bbox_area_km2"] = float(area)

        k1, k2, k3 = st.columns(3)
        k1.metric("Área bbox aprox.", f"{area:,.1f} km²")
        k2.metric("Margen", f"{margin:g} {margin_unit}")
        k3.metric("Preajuste", bbox_profile)
        st.caption("El Área bbox aprox. corresponde a la ventana rectangular de descarga del DEM. No corresponde al área real de la cuenca.")

        if expected_for_warning and expected_for_warning > 0:
            ratio_bbox = area / expected_for_warning
            if ratio_bbox > 100:
                st.error(
                    f"El bbox es {ratio_bbox:,.0f} veces mayor que el área referencial. "
                    "Reduzca margen o use un preajuste menor. Un bbox excesivo hace más lenta la app y puede inducir ajustes erróneos."
                )
            elif ratio_bbox > 25:
                st.warning(
                    f"El bbox es {ratio_bbox:,.0f} veces mayor que el área referencial. "
                    "Puede funcionar, pero probablemente es más grande de lo necesario."
                )

        rec = recommended_tiling(area)
        st.caption(f"Recomendación descarga DEM: {rec['mode']} · {rec['rows']} x {rec['cols']} teselas")

        if area > area_limit:
            st.error("El bbox supera el límite técnico definido. Reduce margen, cambia preajuste o aumenta el límite bajo tu responsabilidad.")
        elif area < max(10.0, expected_for_warning*1.2 if expected_for_warning else 10.0):
            st.warning("El bbox podría ser demasiado pequeño para contener toda la cuenca. Si la cuenca toca el borde del DEM, aumente el margen gradualmente.")
        else:
            st.success("Bounding box válido para construir la solicitud.")

        st.subheader("Bounding box calculado")
        bbox_cols = st.columns(5)
        bbox_cols[0].metric("south", f"{bbox['south']:.6f}")
        bbox_cols[1].metric("north", f"{bbox['north']:.6f}")
        bbox_cols[2].metric("west", f"{bbox['west']:.6f}")
        bbox_cols[3].metric("east", f"{bbox['east']:.6f}")
        bbox_cols[4].metric("Área aprox.", f"{area:,.0f} km²")

        st.code(build_url(dem_type, bbox, "API_KEY_OCULTA"), language="text")

        st.subheader("Modo de descarga DEM")
        d1, d2, d3 = st.columns(3)
        with d1:
            download_mode = st.selectbox("Descarga DEM", ["Auto", "Normal", "Por partes"], index=0)
        with d2:
            tile_rows_dem = st.selectbox("Filas DEM", [1, 2, 3, 4, 5, 6, 8], index=[1,2,3,4,5,6,8].index(rec["rows"]) if rec["rows"] in [1,2,3,4,5,6,8] else 1)
        with d3:
            tile_cols_dem = st.selectbox("Columnas DEM", [1, 2, 3, 4, 5, 6, 8], index=[1,2,3,4,5,6,8].index(rec["cols"]) if rec["cols"] in [1,2,3,4,5,6,8] else 1)

        if area <= area_limit:
            if st.button("Descargar DEM GeoTIFF", type="primary"):
                try:
                    progress = st.progress(0.0)
                    status = st.empty()

                    def cb(msg, frac):
                        status.info(msg)
                        progress.progress(min(max(float(frac), 0.0), 1.0))

                    result = download_dem_normal_or_tiled(
                        dem_type,
                        bbox,
                        api_key,
                        mode=download_mode,
                        rows=int(tile_rows_dem),
                        cols=int(tile_cols_dem),
                        progress_callback=cb,
                    )
                    dem_bytes = result.dem_bytes
                    dem_path = save_bytes(f"dem_{dem_type}_unificado.tif", dem_bytes)
                    st.session_state["dem_path"] = str(dem_path)
                    st.session_state["dem_bytes"] = dem_bytes
                    st.session_state["dem_bbox"] = bbox
                    st.session_state["dem_source"] = "OpenTopography"
                    st.session_state["dem_download_meta"] = result.metadata
                    progress.progress(1.0)
                    status.success("DEM listo para delimitación, curvas y secciones.")
                    st.success(f"DEM descargado/unificado: {len(dem_bytes)/(1024*1024):.2f} MB")
                except Exception as exc:
                    st.error(str(exc))

        if has("dem_download_meta"):
            st.subheader("Metadata descarga DEM")
            st.json(st.session_state["dem_download_meta"])

        if has("dem_bytes"):
            st.download_button("Descargar DEM", st.session_state["dem_bytes"], file_name="dem_hidrosed_unificado.tif", mime="image/tiff")


with tabs[2]:
    st.header("3 · Delimitar cuenca y calcular parámetros morfológicos")
    if not has("dem_path") or not has("control_point"):
        st.warning("Necesitas DEM descargado y punto de control.")
    else:
        cp = st.session_state["control_point"]
        st.markdown(
            "<div class='hs-info'><b>Corrección v3.1.1:</b> el ajuste del punto al cauce ahora evita saltar a ríos principales cercanos. "
            "Para quebradas pequeñas, use radio 100 a 500 m y active control de área.</div>",
            unsafe_allow_html=True,
        )
        c1, c2, c3, c4 = st.columns(4)
        default_expected_area = float(st.session_state.get("expected_basin_default", 20.0))
        default_basin_limit = float(st.session_state.get("max_basin_default", max(default_expected_area*4, 80.0)))
        default_snap = int(st.session_state.get("snap_default_m", 250))
        snap_options = [50, 100, 250, 500, 1000, 1500, 2500, 5000]
        default_snap_index = snap_options.index(default_snap) if default_snap in snap_options else 2

        with c1:
            selection_mode = st.selectbox(
                "Modo ajuste punto",
                ["area_controlled", "closest", "max_acc"],
                index=0,
                format_func=lambda x: {
                    "area_controlled": "Controlado por área (recomendado)",
                    "closest": "Celda cercana",
                    "max_acc": "Máxima acumulación (antiguo)"
                }[x],
            )
            snap_radius = st.selectbox("Radio ajuste punto al cauce [m]", snap_options, index=default_snap_index)
        with c2:
            expected_area = st.number_input("Área esperada aprox. [km²]", min_value=0.0, value=default_expected_area, step=max(5.0, default_expected_area/20.0))
            basin_area_limit = st.number_input("Área máxima permitida [km²]", min_value=1.0, value=default_basin_limit, step=max(10.0, default_basin_limit/20.0))
        with c3:
            basin_max_cells = st.selectbox("Máx. celdas delimitación", [500_000, 1_000_000, 1_500_000, 2_500_000, 4_000_000, 6_000_000], index=3, format_func=lambda x: f"{x:,}".replace(",", "."))
        with c4:
            simplify_basin = st.selectbox("Simplificación polígono [m]", [0, 20, 30, 50, 80, 120, 200], index=3)

        st.info("No uses el modo antiguo de máxima acumulación salvo diagnóstico. En cuencas cercanas a cauces principales puede saltar al río mayor y devolver áreas sobredimensionadas.")

        if st.button("Delimitar cuenca desde DEM + punto de control", type="primary"):
            try:
                result = delineate_basin(
                    st.session_state["dem_path"],
                    outlet_lon=float(cp["lon"]),
                    outlet_lat=float(cp["lat"]),
                    snap_radius_m=float(snap_radius),
                    max_cells=int(basin_max_cells),
                    simplify_m=float(simplify_basin),
                    expected_area_km2=float(expected_area) if expected_area > 0 else None,
                    max_area_km2=float(basin_area_limit) if basin_area_limit > 0 else None,
                    selection_mode=str(selection_mode),
                )
                if result.metrics.get("area_km2", 0) > basin_area_limit:
                    st.warning(
                        f"Alerta QA: el área delimitada ({result.metrics.get('area_km2', 0):.2f} km²) "
                        f"supera el máximo permitido ({basin_area_limit:.2f} km²). Revise punto/radio/DEM."
                    )
                st.session_state["basin_kmz"] = result.kmz_bytes
                st.session_state["basin_kml"] = result.kml_bytes
                st.session_state["basin_preview"] = result.preview_png
                st.session_state["basin_metrics"] = result.metrics
                st.session_state["basin_metrics_df"] = metrics_dataframe(result.metrics)
                save_bytes("cuenca_delimitada.kmz", result.kmz_bytes)
                save_bytes("cuenca_delimitada.kml", result.kml_bytes)
                if result.preview_png:
                    save_bytes("preview_cuenca.png", result.preview_png)
                st.success("Cuenca delimitada y morfometría calculada.")
            except Exception as exc:
                st.error(str(exc))

        if has("basin_metrics"):
            m = st.session_state["basin_metrics"]
            c1, c2, c3, c4 = st.columns(4)
            c1.metric("Área", f"{m['area_km2']:.3f} km²")
            c2.metric("Perímetro", f"{m['perimetro_km']:.3f} km")
            c3.metric("Kc", f"{m['coef_compacidad_kc']:.3f}")
            c4.metric("Factor forma", f"{m['factor_forma']:.3f}")
            if float(m.get("area_km2", 0)) > 1000:
                st.warning("La cuenca delimitada supera 10.000 km². La app puede mostrar resultados, pero este modo fue configurado para cuencas ≤ 10.000 km²; revise DEM, punto de salida y tiempos de procesamiento.")
            if m.get("advertencias"):
                st.warning("Advertencias QA:")
                for a in m["advertencias"]:
                    st.write(f"- {a}")
            else:
                st.success("QA cuenca: sin advertencias automáticas. Revisar igualmente en la vista previa/KMZ.")
            st.dataframe(st.session_state["basin_metrics_df"], use_container_width=True)
            if isinstance(m.get("candidatos_salida_top"), list) and m.get("candidatos_salida_top"):
                with st.expander("QA ajuste del punto: candidatos evaluados", expanded=False):
                    st.dataframe(pd.DataFrame(m["candidatos_salida_top"]), use_container_width=True)
            if has("basin_preview"):
                st.image(st.session_state["basin_preview"], caption="Cuenca delimitada y acumulación de flujo", use_container_width=True)
            d1, d2 = st.columns(2)
            d1.download_button("Descargar cuenca KMZ", st.session_state["basin_kmz"], file_name="cuenca_delimitada.kmz", mime="application/vnd.google-earth.kmz")
            d2.download_button("Descargar cuenca KML", st.session_state["basin_kml"], file_name="cuenca_delimitada.kml", mime="application/vnd.google-earth.kml+xml")


with tabs[3]:
    st.header("4 · Curvas de nivel, modo por teselas y eje de cauce")
    if not has("dem_path"):
        st.warning("Primero descarga el DEM.")
    else:
        c1, c2, c3 = st.columns(3)
        with c1:
            interval = st.selectbox("Distancia entre curvas [m]", [1, 2, 5, 10, 20, 25, 50, 100, 200], index=0)
            st.caption("Mínimo: 1 m. Para cuencas cercanas a 10.000 km², 1 m puede generar KMZ muy pesado si el relieve es alto.")
        with c2:
            contour_mode = st.selectbox("Modo curvas", ["Automático", "Normal", "Por teselas y unificado"], index=0)
        with c3:
            max_levels = st.selectbox("Máx. niveles cota", [1000, 3000, 5000, 10000, 20000, 30000], index=4)

        bbox_area_ref = float(st.session_state.get("bbox_area_km2", 0) or 0)
        strategy = split_bbox_km2_strategy(bbox_area_ref)
        st.caption(f"Estrategia sugerida: {strategy['tile_rows']} x {strategy['tile_cols']} teselas · {strategy['nota']}")

        c4, c5, c6 = st.columns(3)
        with c4:
            max_cells = st.selectbox("Máx. celdas curvas normal", [1_000_000, 2_500_000, 4_000_000, 6_000_000, 10_000_000, 20_000_000], index=3, format_func=lambda x: f"{x:,}".replace(",", "."))
        with c5:
            tile_rows = st.selectbox("Filas teselas", [2, 3, 4, 5, 6, 8, 10], index=[2,3,4,5,6,8,10].index(strategy["tile_rows"]) if strategy["tile_rows"] in [2,3,4,5,6,8,10] else 3)
        with c6:
            tile_cols = st.selectbox("Columnas teselas", [2, 3, 4, 5, 6, 8, 10], index=[2,3,4,5,6,8,10].index(strategy["tile_cols"]) if strategy["tile_cols"] in [2,3,4,5,6,8,10] else 3)

        use_tiled = contour_mode == "Por teselas y unificado" or (contour_mode == "Automático" and bbox_area_ref >= 10000)

        if use_tiled:
            st.info("Modo por teselas activo: el DEM se procesa por partes y las curvas se unifican en un solo KMZ/KML.")
        else:
            st.info("Modo normal activo: el DEM se procesa como una sola unidad.")

        if st.button("Generar curvas KMZ/KML", type="primary"):
            try:
                if use_tiled:
                    out = generate_tiled_contours_from_dem(
                        st.session_state["dem_path"],
                        interval_m=float(interval),
                        tile_rows=int(tile_rows),
                        tile_cols=int(tile_cols),
                        max_levels=int(max_levels),
                        index_interval_m=max(float(interval) * 10.0, 10.0),
                    )
                else:
                    out = generate_contours(
                        st.session_state["dem_path"],
                        interval_m=float(interval),
                        max_cells=int(max_cells),
                        max_levels=int(max_levels),
                    )
                st.session_state["contours_kmz"] = out.kmz_bytes
                st.session_state["contours_kml"] = out.kml_bytes
                st.session_state["contours_preview"] = out.preview_png
                st.session_state["contours_meta"] = out.metadata
                save_bytes("curvas_nivel_unificadas.kmz", out.kmz_bytes)
                save_bytes("curvas_nivel_unificadas.kml", out.kml_bytes)
                if out.preview_png:
                    save_bytes("preview_curvas.png", out.preview_png)
                st.success("Curvas generadas correctamente.")
            except Exception as exc:
                st.error(str(exc))

        if has("contours_meta"):
            st.json(st.session_state["contours_meta"])
        if has("contours_preview"):
            st.image(st.session_state["contours_preview"], caption="Vista previa curvas/DEM", use_container_width=True)
        if has("contours_kmz"):
            c1, c2 = st.columns(2)
            c1.download_button("Descargar curvas KMZ unificadas", st.session_state["contours_kmz"], file_name="curvas_nivel_unificadas.kmz", mime="application/vnd.google-earth.kmz")
            c2.download_button("Descargar curvas KML unificadas", st.session_state["contours_kml"], file_name="curvas_nivel_unificadas.kml", mime="application/vnd.google-earth.kml+xml")

        if has("basin_kml") and has("contours_kml"):
            st.divider()
            st.subheader("Cuenca + curvas de nivel recortadas")
            st.caption("Salida equivalente al visualizador de cuenca correcta: polígono de cuenca + curvas dentro de la cuenca en un solo KMZ/KML.")
            clip_basin_curves = st.checkbox("Recortar curvas al polígono de cuenca", value=True)
            if st.button("Generar KMZ cuenca + curvas de nivel", type="secondary"):
                try:
                    bc = build_basin_contours_kmz(
                        st.session_state["basin_kml"],
                        st.session_state["contours_kml"],
                        clip_to_basin=bool(clip_basin_curves),
                    )
                    st.session_state["basin_contours_kmz"] = bc.kmz_bytes
                    st.session_state["basin_contours_kml"] = bc.kml_bytes
                    st.session_state["basin_contours_preview"] = bc.preview_png
                    st.session_state["basin_contours_meta"] = bc.metadata
                    save_bytes("cuenca_curvas_nivel.kmz", bc.kmz_bytes)
                    save_bytes("cuenca_curvas_nivel.kml", bc.kml_bytes)
                    if bc.preview_png:
                        save_bytes("preview_cuenca_curvas.png", bc.preview_png)
                    st.success("KMZ cuenca + curvas generado correctamente.")
                except Exception as exc:
                    st.error(str(exc))

        if has("basin_contours_meta"):
            st.json(st.session_state["basin_contours_meta"])
        if has("basin_contours_preview"):
            st.image(st.session_state["basin_contours_preview"], caption="Vista previa cuenca + curvas de nivel", use_container_width=True)
        if has("basin_contours_kmz"):
            c1, c2 = st.columns(2)
            c1.download_button("Descargar KMZ cuenca + curvas de nivel", st.session_state["basin_contours_kmz"], file_name="cuenca_curvas_nivel.kmz", mime="application/vnd.google-earth.kmz")
            c2.download_button("Descargar KML cuenca + curvas de nivel", st.session_state["basin_contours_kml"], file_name="cuenca_curvas_nivel.kml", mime="application/vnd.google-earth.kml+xml")

        st.divider()
        st.subheader("Eje de cauce")
        if has("axis_line"):
            st.success("Eje de cauce cargado desde KMZ/KML.")
        else:
            st.warning("No hay eje cargado. Se puede generar un eje preliminar para continuar.")
            c1, c2 = st.columns(2)
            with c1:
                axis_len = st.number_input("Longitud eje preliminar [km]", min_value=0.1, value=5.0, step=0.5)
            with c2:
                az = st.number_input("Azimut eje preliminar [°]", min_value=0.0, max_value=360.0, value=0.0, step=5.0)
            if st.button("Generar eje preliminar"):
                from modules.axis_sections import generate_preliminary_axis
                cp = st.session_state["control_point"]
                line = generate_preliminary_axis(cp["lon"], cp["lat"], length_km=axis_len, azimuth_deg=az)
                st.session_state["axis_line"] = line
                st.success("Eje preliminar generado.")

with tabs[4]:
    st.header("5 · Secciones transversales · Motor v13 UTM19S 3D")
    st.markdown("""
Esta etapa usa como motor principal la lógica de **app_secciones_kmz_v13_fix_km_final_utm19s_3d**: eje + curvas de nivel KMZ/KML, cálculo métrico en UTM, generación de secciones, muestreo por intersección con curvas, QA de secciones modelables y exportables.
""")

    section_engine = st.radio(
        "Motor de secciones",
        ["Motor v13 KMZ/curvas/eje UTM19S 3D", "Motor DEM actual"],
        index=0,
        horizontal=True,
    )

    if section_engine.startswith("Motor v13"):
        v13_file = st.file_uploader(
            "KMZ/KML con eje del cauce y curvas de nivel",
            type=["kmz", "kml"],
            key="v13_sections_kmz",
            help="Puede ser el KMZ generado por la App A: cuenca + curvas + eje, o un KMZ con eje y curvas topográficas.",
        )
        st.subheader("Sistema métrico")
        cr1, cr2, cr3 = st.columns(3)
        with cr1:
            datum_key = st.selectbox("Datum", ["WGS84", "SIRGAS2000", "PSAD56", "SAD69"], index=0)
        with cr2:
            utm_zone = st.selectbox("Huso UTM", [17, 18, 19, 20, 21], index=2)
        with cr3:
            hemisphere = st.selectbox("Hemisferio", ["S", "N"], index=0)
        try:
            metric_epsg = v13_utm_epsg_from_datum(datum_key, int(utm_zone), hemisphere)
        except Exception as epsg_exc:
            st.warning(str(epsg_exc))
            metric_epsg = "EPSG:32719"
        st.info(f"CRS activo: {metric_epsg}")

        if v13_file:
            try:
                fwd, inv = v13_make_transformers(metric_epsg)
                kml_text = v13_read_kml_or_kmz(v13_file, v13_file.name)
                lines = v13_extract_lines_from_kml(kml_text)
                if not lines:
                    st.warning("No se encontraron líneas tipo LineString en el KMZ/KML.")
                else:
                    lines_df = v13_get_lines_dataframe(lines, fwd)
                    st.subheader("Elementos lineales detectados")
                    st.dataframe(lines_df, use_container_width=True, hide_index=True)

                    line_options = [f"{r.fid} | {r.name} | L={r.largo_m:,.1f} m" for _, r in lines_df.iterrows()]
                    axis_opt = st.selectbox("Seleccionar eje del cauce", line_options)
                    axis_fid = axis_opt.split("|")[0].strip()
                    axis_feature = next(f for f in lines if f.fid == axis_fid)
                    axis_metric = v13_project_geom(axis_feature.geometry_wgs84, fwd)

                    filter_regex = st.text_input("Filtro opcional para curvas por nombre", value="", help="Ejemplo: curva|contour|cota. Vacío: todas excepto eje.")
                    candidate_contours = [f for f in lines if f.fid != axis_fid]
                    if filter_regex.strip():
                        try:
                            rx = re.compile(filter_regex, re.IGNORECASE)
                            candidate_contours = [f for f in candidate_contours if rx.search(f.name)]
                        except re.error:
                            st.warning("Filtro regex inválido; se usan todas las líneas excepto eje.")

                    contour_rows = []
                    contours_metric = []
                    for f in candidate_contours:
                        z = f.z_candidate
                        contour_rows.append({"fid": f.fid, "name": f.name, "z_m": z, "largo_m": round(v13_project_geom(f.geometry_wgs84, fwd).length, 2)})
                    contour_df = pd.DataFrame(contour_rows)
                    st.subheader("Curvas candidatas")
                    contour_df = st.data_editor(contour_df, use_container_width=True, hide_index=True, num_rows="fixed")
                    valid_curves = contour_df[pd.to_numeric(contour_df["z_m"], errors="coerce").notna()].copy()
                    for _, rr in valid_curves.iterrows():
                        f = next(feat for feat in candidate_contours if feat.fid == rr["fid"])
                        contours_metric.append((f.fid, float(rr["z_m"]), v13_project_geom(f.geometry_wgs84, fwd)))

                    st.subheader("Parámetros de secciones")
                    p1, p2, p3, p4 = st.columns(4)
                    with p1:
                        km_start = st.number_input("Km inicial", value=0.0, step=0.1)
                        km_end = st.number_input("Km final", value=float(axis_metric.length/1000.0), step=0.1)
                    with p2:
                        standard_spacing = st.number_input("Espaciamiento base [m]", min_value=1.0, value=100.0, step=10.0)
                        width_m = st.number_input("Ancho sección [m]", min_value=5.0, value=80.0, step=10.0)
                    with p3:
                        dense_start = st.number_input("Densificar desde km", value=0.0, step=0.1)
                        dense_end = st.number_input("Densificar hasta km", value=0.0, step=0.1)
                    with p4:
                        dense_count = st.number_input("N° secciones densificadas", min_value=0, value=0, step=1)
                        min_points_each_bank = st.number_input("Mín. puntos por ribera", min_value=1, value=2, step=1)

                    if st.button("Generar secciones v13 + QA", type="primary"):
                        if not contours_metric:
                            st.error("No hay curvas con cota válida para generar perfiles.")
                        else:
                            dense_s = float(dense_start) if dense_count > 0 else None
                            dense_e = float(dense_end) if dense_count > 0 else None
                            chainages = v13_generate_chainages(axis_metric.length, float(km_start), float(km_end), float(standard_spacing), dense_s, dense_e, int(dense_count), include_ends=True)
                            sections = v13_build_sections(axis_metric, chainages, float(width_m))
                            sections_table = v13_sections_to_dataframe(sections, inv)
                            profile_points, profile_summary = v13_sample_profiles(sections, contours_metric, inv)
                            longitudinal_axis = v13_sample_longitudinal_axis_profile(axis_metric, contours_metric, inv)
                            longitudinal_est = v13_estimated_longitudinal_from_sections(profile_summary)
                            section_quality = v13_evaluate_section_quality(sections, profile_points, profile_summary)
                            modelable = v13_evaluate_modelable_sections(sections, profile_points, profile_summary, section_quality=section_quality, min_points_each_bank=int(min_points_each_bank), min_total_points=4, require_axis_elevation=True)
                            longitudinal_model = v13_build_longitudinal_modelacion(profile_summary, modelable, longitudinal_axis)
                            selected_sections = v13_filter_sections_for_modelacion(sections, modelable)
                            selected_points = v13_filter_selected_profile_points(profile_points, modelable)

                            # Conversión al formato interno HidroSed para hidráulica conectada.
                            if selected_sections:
                                sec_base = v13_sections_to_dataframe(selected_sections, inv)
                            else:
                                sec_base = sections_table.copy()
                            summary_base = profile_summary.copy()
                            sec_internal = sec_base.merge(summary_base[["section_id", "cota_min_m", "cota_max_m", "cota_eje_estimada_m"]], on="section_id", how="left") if not summary_base.empty else sec_base.copy()
                            sec_internal["section_id_original"] = sec_internal["section_id"].astype(str)
                            id_map = {sid: i+1 for i, sid in enumerate(sec_internal["section_id_original"].tolist())}
                            sec_internal["section_id"] = sec_internal["section_id_original"].map(id_map).astype(int)
                            sec_internal["pk_m"] = pd.to_numeric(sec_internal["chainage_m"], errors="coerce")
                            sec_internal["cota_fondo_m"] = pd.to_numeric(sec_internal.get("cota_min_m"), errors="coerce")
                            sec_internal["cota_borde_izq_m"] = pd.to_numeric(sec_internal.get("cota_max_m"), errors="coerce")
                            sec_internal["cota_borde_der_m"] = pd.to_numeric(sec_internal.get("cota_max_m"), errors="coerce")
                            sec_internal["lon_eje"] = sec_internal.get("eje_lon")
                            sec_internal["lat_eje"] = sec_internal.get("eje_lat")

                            pts_source = selected_points if not selected_points.empty else profile_points
                            pts_internal = pts_source.copy()
                            pts_internal["section_id_original"] = pts_internal["section_id"].astype(str)
                            pts_internal = pts_internal[pts_internal["section_id_original"].isin(id_map.keys())].copy()
                            pts_internal["section_id"] = pts_internal["section_id_original"].map(id_map).astype(int)
                            pts_internal["pk_m"] = pd.to_numeric(pts_internal["chainage_m"], errors="coerce")
                            pts_internal["z_m"] = pd.to_numeric(pts_internal["elevacion_m"], errors="coerce")
                            pts_internal["offset_m"] = pd.to_numeric(pts_internal["offset_m"], errors="coerce")
                            # Asegura al menos 3 puntos por sección para hidráulica; si hay menos, queda QA visible.

                            st.session_state["sections_df"] = sec_internal
                            st.session_state["section_points_df"] = pts_internal
                            st.session_state["sections_mode"] = "v13_kmz_utm19s_3d"
                            st.session_state["sections_v13_raw_df"] = sections_table
                            st.session_state["sections_v13_profile_summary"] = profile_summary
                            st.session_state["sections_v13_quality_df"] = section_quality
                            st.session_state["sections_v13_modelable_df"] = modelable
                            st.session_state["sections_v13_longitudinal_modelacion"] = longitudinal_model
                            st.session_state["axis_metric_length_m"] = float(axis_metric.length)

                            try:
                                kmz_model = v13_make_kmz_modelacion(selected_sections if selected_sections else sections, selected_points if not selected_points.empty else profile_points, longitudinal_model, inv)
                                st.session_state["sections_v13_modelacion_kmz"] = kmz_model
                            except Exception:
                                pass
                            try:
                                zip_bytes = v13_make_zip_download(sections, profile_points, profile_summary, longitudinal_axis, longitudinal_est, axis_metric, contours_metric, inv, metric_epsg=metric_epsg, section_quality=section_quality, modelable_sections=modelable, selected_profile_points=selected_points, longitudinal_modelacion=longitudinal_model)
                                st.session_state["sections_v13_zip"] = zip_bytes
                            except Exception:
                                pass
                            st.success(f"Motor v13: secciones generadas {len(sec_internal)} · puntos útiles {len(pts_internal)} · modelables {int(modelable.get('seleccionada_modelacion', pd.Series(dtype=bool)).sum()) if not modelable.empty else 0}")

                    if has("sections_df") and st.session_state.get("sections_mode") == "v13_kmz_utm19s_3d":
                        st.subheader("Secciones v13 convertidas para HidroSed")
                        st.dataframe(st.session_state["sections_df"], use_container_width=True)
                        st.subheader("Puntos de perfil v13")
                        st.dataframe(st.session_state["section_points_df"].head(500), use_container_width=True)
                        if has("sections_v13_modelable_df"):
                            st.subheader("QA de secciones modelables")
                            st.dataframe(st.session_state["sections_v13_modelable_df"], use_container_width=True)
                        if has("sections_v13_modelacion_kmz"):
                            st.download_button("Descargar KMZ modelación v13", st.session_state["sections_v13_modelacion_kmz"], file_name="secciones_modelacion_v13.kmz", mime="application/vnd.google-earth.kmz")
                        if has("sections_v13_zip"):
                            st.download_button("Descargar ZIP completo v13", st.session_state["sections_v13_zip"], file_name="salida_secciones_v13_hidrosed.zip", mime="application/zip")
            except Exception as exc:
                st.error(f"Error en motor v13 de secciones: {exc}")
        else:
            st.info("Carga un KMZ/KML con eje y curvas para usar el motor v13.")

    else:
        st.info("Motor DEM actual disponible como respaldo. Para esta versión se recomienda el motor v13 KMZ/curvas/eje.")
        if not has("axis_line") or not has("dem_path"):
            st.warning("Necesitas DEM y eje de cauce.")
        else:
            c1, c2, c3 = st.columns(3)
            with c1:
                spacing = st.number_input("Espaciamiento secciones [m]", min_value=5.0, value=100.0, step=10.0)
            with c2:
                width = st.number_input("Ancho sección [m]", min_value=5.0, value=80.0, step=10.0)
            with c3:
                pts_side = st.number_input("Puntos por lado", min_value=2, value=10, step=1)
            if st.button("Generar secciones desde eje + DEM", type="primary"):
                try:
                    line = LineString(st.session_state["axis_line"])
                    sec_raw, pts_raw = generate_cross_sections(line, st.session_state["dem_path"], spacing_m=float(spacing), width_m=float(width), points_each_side=int(pts_side))
                    st.session_state["sections_df"] = sec_raw
                    st.session_state["section_points_df"] = pts_raw
                    st.session_state["sections_mode"] = "dem_actual"
                    st.success(f"Secciones DEM generadas: {len(sec_raw)}")
                except Exception as exc:
                    st.error(str(exc))


with tabs[5]:
    st.header("6 · Hidrología reforzada · metodología y caudales")
    basin_m = st.session_state.get("basin_metrics", {})
    area_default = float(basin_m.get("area_km2", st.session_state.get("expected_basin_default", 10.0)) or 10.0)
    length_default = float(basin_m.get("bbox_largo_km", 5.0) or 5.0)
    dz_default = float(basin_m.get("desnivel_m", 0.0) or 0.0)

    st.markdown("""
Este módulo aplica el núcleo HidroSed de hidrología: morfometría, selección metodológica, tiempos de concentración, IDF sintética desde P24, DGA‑AC/regional, racional modificado y transferencia hidrológica si existe estación de referencia.
""")

    c1, c2, c3, c4 = st.columns(4)
    with c1:
        area_km2 = st.number_input("Área cuenca [km²]", min_value=0.001, value=area_default, step=max(0.5, area_default/100))
        C = st.number_input("Coeficiente escorrentía C", min_value=0.01, max_value=1.0, value=0.45, step=0.05)
    with c2:
        length_km = st.number_input("Longitud cauce [km]", min_value=0.001, value=length_default, step=0.5)
        slope = st.number_input("Pendiente media [m/m]", min_value=0.00001, value=0.01, step=0.001, format="%.5f")
    with c3:
        p24_10 = st.number_input("P24,10 [mm]", min_value=0.0, value=80.7, step=1.0)
        alpha = st.number_input("Factor alfa DGA-AC", min_value=0.1, value=2.14, step=0.01)
    with c4:
        basin_regime = st.selectbox("Régimen", ["pluvial", "nivo-pluvial", "mixto / árido"], index=0)
        periods_txt = st.text_input("Periodos T", value="2,5,10,25,50,100,200")

    periods = periods_from_text(periods_txt)

    st.subheader("Transferencia hidrológica opcional")
    t1, t2, t3, t4, t5 = st.columns(5)
    with t1:
        use_transfer = st.checkbox("Usar estación de referencia", value=False)
    with t2:
        station_area = st.number_input("Área estación [km²]", min_value=0.0, value=max(area_km2, 1.0), step=10.0)
    with t3:
        station_q100 = st.number_input("Q100 estación [m³/s]", min_value=0.0, value=0.0, step=10.0)
    with t4:
        b_exp = st.number_input("Exponente b", min_value=0.30, max_value=1.20, value=0.75, step=0.05)
    with t5:
        f_alt = st.number_input("Factor altitud", min_value=0.10, max_value=3.00, value=1.00, step=0.05)
        f_dist = st.number_input("Factor similitud", min_value=0.10, max_value=3.00, value=1.00, step=0.05)

    if st.button("Calcular hidrología reforzada", type="primary"):
        try:
            tc_df, hydro_all, rec_df, uncertainty_df = build_hydrology(
                area_km2=float(area_km2),
                length_km=float(length_km),
                slope=float(slope),
                C=float(C),
                p24_10=float(p24_10),
                alpha=float(alpha),
                periods=periods,
                include_transfer=bool(use_transfer and station_area > 0 and station_q100 > 0),
                station_area=float(station_area),
                station_q100=float(station_q100),
                b_exp=float(b_exp),
                f_alt=float(f_alt),
                f_dist=float(f_dist),
                dz_m=dz_default,
                basin_regime=basin_regime,
            )
            st.session_state["tc_methods_df"] = tc_df
            st.session_state["hydrology_all_methods"] = hydro_all
            st.session_state["hydrology_methods_recommendation"] = rec_df
            st.session_state["hydrology_uncertainty_df"] = uncertainty_df
            st.session_state["hydrology_inputs"] = {
                "area_km2": area_km2, "C": C, "length_km": length_km, "slope": slope,
                "p24_10": p24_10, "alpha": alpha, "regimen": basin_regime,
                "transferencia": bool(use_transfer), "station_area": station_area, "station_q100": station_q100,
            }
            st.session_state["hydrology_done"] = True
            st.success("Hidrología reforzada calculada.")
        except Exception as exc:
            st.error(str(exc))

    if has("tc_methods_df"):
        k1, k2, k3 = st.columns(3)
        tc_med = pd.to_numeric(st.session_state["tc_methods_df"].get("tc_adoptado_h"), errors="coerce").dropna()
        k1.metric("Tc adoptado", f"{float(tc_med.iloc[0]):.2f} h" if len(tc_med) else "N/D")
        k2.metric("Métodos hidrológicos", len(st.session_state.get("hydrology_all_methods", [])))
        k3.metric("Periodos", len(periods))
        st.subheader("Tiempos de concentración")
        st.dataframe(st.session_state["tc_methods_df"], use_container_width=True)
        st.subheader("Recomendación metodológica")
        st.dataframe(st.session_state["hydrology_methods_recommendation"], use_container_width=True)
        st.subheader("Caudales por método")
        st.dataframe(st.session_state["hydrology_all_methods"], use_container_width=True)
        st.subheader("Incertidumbre entre métodos")
        st.dataframe(st.session_state["hydrology_uncertainty_df"], use_container_width=True)
        try:
            import plotly.express as px
            fig = px.line(st.session_state["hydrology_all_methods"], x="T_anios", y="Q_m3s", color="metodo", markers=True, title="Comparación de caudales por metodología")
            st.plotly_chart(fig, use_container_width=True)
        except Exception:
            pass


with tabs[6]:
    st.header("7 · Cálculo y adopción de caudales")
    if not has("hydrology_done"):
        st.warning("Primero calcula hidrología reforzada.")
    else:
        mode = st.selectbox("Criterio de adopción", ["envolvente_maxima", "mediana_metodos", "promedio_metodos"], index=0)
        st.caption("Para diseño conservador se recomienda envolvente máxima; para diagnóstico se puede comparar mediana/promedio.")
        if st.button("Adoptar caudales", type="primary"):
            q = adopt_flows_advanced(st.session_state.get("hydrology_all_methods"), mode=mode)
            st.session_state["q_design"] = q
            st.session_state["q_adoption_mode"] = mode
            st.success("Caudales adoptados.")
        if has("q_design"):
            st.dataframe(st.session_state["q_design"], use_container_width=True)
            try:
                import plotly.express as px
                fig = px.bar(st.session_state["q_design"], x="T_anios", y="Q_m3s", title=f"Caudales adoptados · {st.session_state.get('q_adoption_mode','')}")
                st.plotly_chart(fig, use_container_width=True)
            except Exception:
                pass
            st.download_button("Descargar caudales adoptados CSV", st.session_state["q_design"].to_csv(index=False).encode("utf-8"), file_name="caudales_adoptados_hidrosed.csv", mime="text/csv")


with tabs[7]:
    st.header("8 · Hidráulica 1D tipo HEC-RAS, socavación y transporte")
    st.markdown(
        """
Este módulo usa las secciones transversales generadas desde el DEM y las resuelve como **sistema conectado**.

La lógica es tipo HEC‑RAS 1D permanente simplificado:

```text
Secciones ordenadas por PK
↓
Condición de borde aguas abajo
↓
Balance de energía entre secciones
↓
Pérdidas por fricción
↓
Pérdidas locales por contracción/expansión
↓
Perfil de cota de agua por periodo de retorno
↓
Shields / MPM / socavación preliminar
```
"""
    )

    if not has("sections_df") or not has("section_points_df") or not has("q_design"):
        st.warning("Necesitas secciones transversales completas y caudales adoptados.")
    else:
        st.subheader("Parámetros de modelación hidráulica conectada")
        c1, c2, c3, c4 = st.columns(4)
        with c1:
            S = st.number_input(
                "Pendiente energía/fricción inicial",
                min_value=0.00001,
                value=float(st.session_state.get("hydrology_inputs", {}).get("slope", 0.01)),
                step=0.001,
                format="%.5f",
            )
        with c2:
            n_default_sup = float(st.session_state.get("n_manning_adoptado", 0.035) or 0.035)
            n = st.number_input("Manning n", min_value=0.010, value=n_default_sup, step=0.005, format="%.3f")
        with c3:
            contr = st.number_input("Coef. contracción", min_value=0.0, max_value=1.0, value=0.10, step=0.05)
        with c4:
            expan = st.number_input("Coef. expansión", min_value=0.0, max_value=1.0, value=0.30, step=0.05)

        with st.expander("Granulometría para sedimentos y socavación", expanded=True):
            st.caption("Seleccione una granulometría tipo o cargue Excel/CSV real. La app calcula D16, D30, D35, D50, D60, D65, D84, D90 y Dm para las metodologías internas.")
            g1, g2, g3 = st.columns([1.1, 1.1, 1.0])
            with g1:
                gran_mode = st.radio(
                    "Fuente granulométrica",
                    ["Perfil tipo por defecto", "Excel/CSV granulometría real"],
                    horizontal=False,
                    key="gran_mode_sedgran_v316",
                )
                profile_name = st.selectbox(
                    "Perfil tipo",
                    list(DEFAULT_PROFILES_MM.keys()),
                    index=3,
                    disabled=gran_mode != "Perfil tipo por defecto",
                    key="gran_profile_name_v316",
                )
            with g2:
                gran_excel = st.file_uploader(
                    "Cargar Excel/CSV granulometría",
                    type=["xlsx", "xls", "csv"],
                    disabled=gran_mode != "Excel/CSV granulometría real",
                    help="Puede contener diámetros D16/D50/D84/etc. o curva por tamiz con abertura_mm y porcentaje_pasa.",
                    key="gran_excel_v316",
                )
                use_default_if_fail = st.checkbox("Usar perfil tipo si Excel falla", value=True, key="gran_fallback_v316")
            with g3:
                st.dataframe(default_profiles_dataframe()[["perfil", "material", "D50_mm", "D84_mm", "D90_mm"]], use_container_width=True, hide_index=True)

            gran_metrics = None
            gran_samples = pd.DataFrame()
            gran_diag = []

            if gran_mode == "Excel/CSV granulometría real" and gran_excel is not None:
                try:
                    gran_result = extract_granulometry_from_excel(gran_excel)
                    if gran_result["ok"]:
                        gran_metrics = gran_result["characteristics"]
                        gran_samples = gran_result["samples"]
                        gran_diag = gran_result["diagnostics"]
                        st.success("Granulometría real leída desde Excel/CSV.")
                    else:
                        gran_diag = gran_result.get("diagnostics", [])
                        if use_default_if_fail:
                            gran_metrics = profile_to_characteristics(profile_name)
                            st.warning("No se detectó granulometría válida en Excel/CSV. Se usará perfil tipo.")
                        else:
                            st.error("No se detectó granulometría válida en Excel/CSV.")
                except Exception as exc:
                    gran_diag = [str(exc)]
                    if use_default_if_fail:
                        gran_metrics = profile_to_characteristics(profile_name)
                        st.warning(f"Error leyendo Excel/CSV. Se usará perfil tipo. Detalle: {exc}")
                    else:
                        st.error(str(exc))
            else:
                gran_metrics = profile_to_characteristics(profile_name)

            st.session_state["granulometry_metrics"] = gran_metrics
            st.session_state["granulometry_samples_df"] = gran_samples
            st.session_state["granulometry_method_table_df"] = method_diameter_table(gran_metrics)
            st.session_state["granulometry_characteristic_df"] = characteristic_table(gran_metrics)
            st.session_state["granulometry_curve_df"] = profile_curve_dataframe(gran_metrics)

            d50_default = float(gran_metrics.get("D50_m", 0.045) or 0.045)
            d90_default = float(gran_metrics.get("D90_m", 0.20) or 0.20)

            gg1, gg2, gg3, gg4 = st.columns(4)
            gg1.metric("Perfil / fuente", str(gran_metrics.get("perfil", "granulometría")))
            gg2.metric("D50", f"{gran_metrics.get('D50_mm', float('nan')):.2f} mm")
            gg3.metric("D84", f"{gran_metrics.get('D84_mm', float('nan')):.2f} mm")
            gg4.metric("Confianza", confidence_label(gran_metrics))

            gran_tab1, gran_tab2, gran_tab3, gran_tab4 = st.tabs([
                "Diámetros",
                "Metodologías",
                "Curva granulométrica",
                "Muestras Excel/CSV",
            ])

            with gran_tab1:
                st.dataframe(
                    st.session_state["granulometry_characteristic_df"],
                    use_container_width=True,
                    hide_index=True,
                )

            with gran_tab2:
                st.dataframe(
                    st.session_state["granulometry_method_table_df"],
                    use_container_width=True,
                    hide_index=True,
                )

            with gran_tab3:
                if gran_diag:
                    st.info(" | ".join(gran_diag))
                try:
                    import plotly.express as px
                    curve_df = st.session_state["granulometry_curve_df"]
                    if not curve_df.empty:
                        fig_gr = px.line(
                            curve_df,
                            x="diametro_mm",
                            y="porcentaje_pasa",
                            markers=True,
                            title="Curva granulométrica adoptada",
                            labels={"diametro_mm": "Diámetro [mm]", "porcentaje_pasa": "% que pasa"},
                        )
                        fig_gr.update_xaxes(type="log")
                        st.plotly_chart(fig_gr, use_container_width=True)
                    else:
                        st.info("No hay curva granulométrica disponible.")
                except Exception as exc:
                    st.warning(f"No se pudo graficar curva granulométrica: {exc}")

            with gran_tab4:
                if not gran_samples.empty:
                    st.dataframe(gran_samples, use_container_width=True)
                else:
                    st.info("No se cargaron muestras Excel/CSV. Se usa el perfil tipo seleccionado.")

        c5, c6, c7, c8 = st.columns(4)
        with c5:
            boundary = st.selectbox("Condición aguas abajo", ["tirante_normal", "cota_conocida"], index=0)
        with c6:
            ds_wse = st.number_input("Cota agua aguas abajo [m]", value=0.0, step=0.5, help="Solo se usa si seleccionas cota_conocida.")
        with c7:
            d50 = st.number_input("D50 adoptado [m]", min_value=0.00001, value=d50_default, step=max(d50_default/10, 0.0001), format="%.5f")
        with c8:
            d90 = st.number_input("D90 adoptado [m]", min_value=0.00001, value=d90_default, step=max(d90_default/10, 0.0001), format="%.5f")

        if st.button("Calcular perfil hidráulico conectado tipo HEC-RAS", type="primary"):
            try:
                profile = hecras_like_steady_profile(
                    st.session_state["sections_df"],
                    st.session_state["section_points_df"],
                    st.session_state["q_design"],
                    n_manning=float(n),
                    downstream_mode=boundary,
                    downstream_wse=float(ds_wse) if boundary == "cota_conocida" else None,
                    slope_energy=float(S),
                    contraction_coeff=float(contr),
                    expansion_coeff=float(expan),
                    alpha=1.0,
                )
                sed = sediment_from_hecras_profile(profile, d50_m=float(d50), d90_m=float(d90), slope_energy=float(S))
                zones = classify_sediment_zones(sed)
                zone_summary = summarize_zones(zones)
                st.session_state["hydraulic_profile_df"] = profile
                st.session_state["hydraulic_df"] = profile
                st.session_state["sediment_df"] = zones if not zones.empty else sed
                st.session_state["sediment_zone_summary_df"] = zone_summary
                st.session_state["hecras_like_inputs"] = {
                    "modelo": "1D permanente tipo HEC-RAS simplificado",
                    "n_manning": float(n),
                    "pendiente_energia": float(S),
                    "coef_contraccion": float(contr),
                    "coef_expansion": float(expan),
                    "condicion_aguas_abajo": boundary,
                    "cota_aguas_abajo": float(ds_wse) if boundary == "cota_conocida" else None,
                    "D50_m": float(d50),
                    "D84_m": float(st.session_state.get("granulometry_metrics", {}).get("D84_m", float("nan"))),
                    "D90_m": float(d90),
                    "granulometria_fuente": st.session_state.get("granulometry_metrics", {}).get("fuente", "sin_dato"),
                    "granulometria_perfil": st.session_state.get("granulometry_metrics", {}).get("perfil", "sin_dato"),
                    "granulometria_confianza": st.session_state.get("granulometry_metrics", {}).get("confianza_granulometria", None),
                }
                n_fallback = int(profile.get("geometria_fallback", pd.Series(dtype=bool)).fillna(False).sum()) if not profile.empty else 0
                if n_fallback > 0:
                    st.warning(
                        f"Perfil hidráulico calculado con {n_fallback} registros usando sección sintética fallback. "
                        "El cálculo continúa, pero esas secciones deben revisarse topográficamente."
                    )
                else:
                    st.success("Perfil hidráulico conectado calculado con secciones reales.")
            except Exception as exc:
                st.error(str(exc))

        if has("hydraulic_profile_df"):
            st.subheader("Perfil hidráulico conectado")
            st.dataframe(st.session_state["hydraulic_profile_df"], use_container_width=True)
            if "geometria_status" in st.session_state["hydraulic_profile_df"].columns:
                qa_geom = st.session_state["hydraulic_profile_df"].groupby("geometria_status").size().reset_index(name="registros")
                st.caption("QA geometría de secciones usada en el cálculo")
                st.dataframe(qa_geom, use_container_width=True, hide_index=True)

            try:
                import plotly.express as px
                prof = st.session_state["hydraulic_profile_df"]
                fig = px.line(
                    prof,
                    x="pk_m",
                    y="cota_agua_m",
                    color="T_anios",
                    markers=True,
                    title="Perfil de cota de agua por periodo de retorno",
                    labels={"pk_m": "PK [m]", "cota_agua_m": "Cota agua [m]"},
                )
                st.plotly_chart(fig, use_container_width=True)
            except Exception:
                pass

            st.download_button(
                "Descargar perfil hidráulico CSV",
                st.session_state["hydraulic_profile_df"].to_csv(index=False).encode("utf-8"),
                file_name="perfil_hidraulico_tipo_hecras.csv",
                mime="text/csv",
            )

        if has("sediment_df"):
            st.subheader("Transporte, socavación, erosión y depositación")
            sed_view = st.session_state["sediment_df"]
            st.dataframe(sed_view, use_container_width=True)
            if has("sediment_zone_summary_df"):
                st.subheader("Resumen de zonas críticas")
                st.dataframe(st.session_state["sediment_zone_summary_df"], use_container_width=True)
            try:
                import plotly.express as px
                if {"pk_m", "socavacion_general_m", "T_anios", "zona_hidrosed"}.issubset(sed_view.columns):
                    fig_scour = px.scatter(
                        sed_view,
                        x="pk_m",
                        y="socavacion_general_m",
                        color="zona_hidrosed",
                        size="indice_riesgo_sedimento" if "indice_riesgo_sedimento" in sed_view.columns else None,
                        facet_col="T_anios" if sed_view["T_anios"].nunique() <= 4 else None,
                        title="Zonas de socavación, transporte y depositación por PK",
                    )
                    st.plotly_chart(fig_scour, use_container_width=True)
                if {"pk_m", "Qs_total_m3_s", "T_anios", "tendencia_sedimentaria"}.issubset(sed_view.columns):
                    fig_qs = px.line(
                        sed_view,
                        x="pk_m",
                        y="Qs_total_m3_s",
                        color="T_anios",
                        line_group="tendencia_sedimentaria",
                        title="Transporte de sedimentos longitudinal",
                    )
                    st.plotly_chart(fig_qs, use_container_width=True)
            except Exception:
                pass
            st.download_button(
                "Descargar socavación/sedimentos CSV",
                st.session_state["sediment_df"].to_csv(index=False).encode("utf-8"),
                file_name="socavacion_sedimentos.csv",
                mime="text/csv",
            )

        st.divider()
        st.subheader("Perfil longitudinal 3D con secciones y fenómenos hidráulicos")
        if has("sections_df") and has("section_points_df"):
            v1, v2, v3, v4 = st.columns(4)
            with v1:
                vex = st.slider("Exageración vertical", min_value=0.5, max_value=10.0, value=1.5, step=0.5)
            with v2:
                show_water = st.checkbox("Mostrar lámina de agua", value=True)
            with v3:
                show_scour = st.checkbox("Mostrar socavación", value=True)
            with v4:
                show_depo = st.checkbox("Mostrar depositación", value=True)

            if st.button("Generar perfil longitudinal 3D", type="primary"):
                try:
                    fig3d = create_3d_profile_figure(
                        st.session_state["sections_df"],
                        st.session_state["section_points_df"],
                        hydraulic_df=st.session_state.get("hydraulic_profile_df"),
                        sediment_df=st.session_state.get("sediment_df"),
                        vertical_exaggeration=float(vex),
                        show_water=bool(show_water),
                        show_scour=bool(show_scour),
                        show_deposition=bool(show_depo),
                    )
                    st.session_state["profile_3d_fig"] = fig3d
                    html3d = figure_to_html_bytes(fig3d)
                    st.session_state["profile_3d_html"] = html3d
                    save_bytes("perfil_longitudinal_3d_hidrosed.html", html3d)
                    st.success("Perfil 3D generado.")
                except Exception as exc:
                    st.error(str(exc))

            if has("profile_3d_fig"):
                st.plotly_chart(st.session_state["profile_3d_fig"], use_container_width=True)
            if has("profile_3d_html"):
                st.download_button(
                    "Descargar perfil 3D HTML",
                    st.session_state["profile_3d_html"],
                    file_name="perfil_longitudinal_3d_hidrosed.html",
                    mime="text/html",
                )
        else:
            st.info("Genera primero las secciones transversales.")


        st.warning(
            "Nota técnica: este motor aplica flujo permanente 1D con balance de energía, "
            "pero no reemplaza una modelación HEC‑RAS oficial calibrada. Para diseño final se deben revisar "
            "condiciones de borde, coeficientes, régimen, puentes/alcantarillas, llanuras de inundación y calibración."
        )

with tabs[8]:
    st.header("9 · Lámina cartográfica y exportación final")

    st.subheader("Lámina cartográfica preliminar")
    if not has("dem_path"):
        st.warning("Para generar la lámina necesitas al menos DEM. Para mejor salida agrega cuenca, curvas, eje y morfometría.")
    else:
        c1, c2 = st.columns(2)
        with c1:
            map_title = st.text_input("Título de lámina", value="HidroSed · Delimitación de cuenca y curvas de nivel")
        with c2:
            map_contour_interval = st.selectbox("Curvas visibles en lámina [m]", [1, 2, 5, 10, 20, 25, 50, 100, 200], index=3)

        if st.button("Generar lámina cartográfica PNG", type="primary"):
            try:
                png = make_cartographic_sheet(
                    st.session_state["dem_path"],
                    basin_kml_bytes=st.session_state.get("basin_kml"),
                    axis_line=st.session_state.get("axis_line"),
                    control_point=st.session_state.get("control_point"),
                    metrics=st.session_state.get("basin_metrics"),
                    title=map_title,
                    contour_interval=float(map_contour_interval),
                )
                st.session_state["cartographic_png"] = png
                save_bytes("lamina_cartografica.png", png)
                st.success("Lámina cartográfica generada.")
            except Exception as exc:
                st.error(str(exc))

        if has("cartographic_png"):
            st.image(st.session_state["cartographic_png"], caption="Lámina cartográfica preliminar", use_container_width=True)
            st.download_button("Descargar lámina PNG", st.session_state["cartographic_png"], file_name="lamina_cartografica_hidrosed.png", mime="image/png")

    st.divider()
    st.subheader("Exportables técnicos")
    if has("profile_3d_html"):
        st.download_button(
            "Descargar perfil longitudinal 3D HTML",
            st.session_state["profile_3d_html"],
            file_name="perfil_longitudinal_3d_hidrosed.html",
            mime="text/html",
        )


    if has("basin_metrics_df"):
        st.download_button(
            "Descargar morfometría CSV",
            st.session_state["basin_metrics_df"].to_csv(index=False).encode("utf-8"),
            file_name="morfometria_cuenca.csv",
            mime="text/csv",
        )
    if has("basin_kmz"):
        st.download_button("Descargar cuenca delimitada KMZ", st.session_state["basin_kmz"], file_name="cuenca_delimitada.kmz", mime="application/vnd.google-earth.kmz")
    if has("basin_metrics"):
        st.download_button("Descargar morfometría JSON", json.dumps(st.session_state["basin_metrics"], ensure_ascii=False, indent=2).encode("utf-8"), file_name="morfometria_cuenca.json", mime="application/json")
    if has("section_qc_report_df"):
        st.download_button(
            "Descargar QA secciones CSV",
            st.session_state["section_qc_report_df"].to_csv(index=False).encode("utf-8"),
            file_name="qa_secciones.csv",
            mime="text/csv",
        )
    if has("topo_support_report_df"):
        st.download_button(
            "Descargar apoyo topográfico CSV",
            st.session_state["topo_support_report_df"].to_csv(index=False).encode("utf-8"),
            file_name="apoyo_topografico_secciones.csv",
            mime="text/csv",
        )
    if has("sections_df") and has("section_points_df"):
        xlsx = sections_excel_bytes(
            st.session_state["sections_df"],
            st.session_state["section_points_df"],
            st.session_state.get("q_design"),
            st.session_state.get("hydraulic_df"),
            st.session_state.get("sediment_df"),
        )
        st.download_button("Descargar Excel maestro", xlsx, file_name="HidroSed_Resultados_Maestros.xlsx", mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
    if has("contours_kmz"):
        st.download_button("Descargar curvas KMZ", st.session_state["contours_kmz"], file_name="curvas_nivel.kmz")
    if has("axis_kmz_path"):
        p = Path(st.session_state["axis_kmz_path"])
        if p.exists():
            st.download_button("Descargar eje KMZ", p.read_bytes(), file_name="eje_cauce.kmz")
    if has("dem_bytes"):
        st.download_button("Descargar DEM GeoTIFF", st.session_state["dem_bytes"], file_name="dem_hidrosed.tif", mime="image/tiff")

    resumen = {
        "control_point": st.session_state.get("control_point"),
        "basin_metrics": st.session_state.get("basin_metrics"),
        "hydrology_inputs": st.session_state.get("hydrology_inputs"),
        "n_sections": int(len(st.session_state["sections_df"])) if has("sections_df") else 0,
        "n_design_flows": int(len(st.session_state["q_design"])) if has("q_design") else 0,
    }
    st.download_button(
        "Descargar resumen maestro JSON",
        json.dumps(resumen, ensure_ascii=False, indent=2).encode("utf-8"),
        file_name="resumen_maestro_hidrosed.json",
        mime="application/json",
    )

    st.info("Versión integral v2.4: flujo maestro completo configurado para cuencas hasta 10.000 km², curvas mínimo 1 m y perfil hidráulico conectado. Para diseño final se recomienda validar eje, cuenca, secciones y parámetros con antecedentes topográficos/hidrométricos oficiales.")



with tabs[9]:
    st.header("10 · Modo Supremo: rugosidad, granulometría, sección trapezoidal y QA")
    st.markdown(
        """
Este módulo permite avanzar incluso cuando la topografía no entrega secciones suficientes. La app separa claramente resultados **reales/topográficos** de resultados **estimados**.

```text
rugosidad manual / tabla / Cowan / Strickler
↓
sección real o sección trapezoidal estimada
↓
granulometría georreferenciada KMZ
↓
transferencia hidrológica dual
↓
semáforo de confianza
```
"""
    )

    st.subheader("A · Rugosidad avanzada del cauce")
    r1, r2, r3 = st.columns(3)
    with r1:
        rough_mode = st.selectbox("Modo rugosidad", ["manual", "tabla", "cowan", "granulometria/strickler"], index=2)
    with r2:
        cat = st.selectbox("Tipo de cauce", list(ROUGHNESS_TABLE["categoria"]), index=list(ROUGHNESS_TABLE["categoria"]).index("grava_media"))
    with r3:
        has_cal = st.checkbox("Existe calibración nivel/caudal", value=False)

    if rough_mode == "manual":
        a,b,c = st.columns(3)
        with a: n_left = st.number_input("n margen izquierda", min_value=0.010, max_value=0.200, value=0.045, step=0.005, format="%.3f")
        with b: n_ch = st.number_input("n cauce principal", min_value=0.010, max_value=0.200, value=0.038, step=0.005, format="%.3f")
        with c: n_right = st.number_input("n margen derecha", min_value=0.010, max_value=0.200, value=0.045, step=0.005, format="%.3f")
        rough_df = compose_roughness_manual(n_left, n_ch, n_right)
        n_adopt = float(n_ch)
        conf_n = roughness_confidence("manual", has("granulometry_assigned_df"), has_cal, zones=3)
    elif rough_mode == "tabla":
        rough_df = pd.DataFrame([table_n(cat)])
        n_adopt = float(rough_df["n_manning"].iloc[0])
        conf_n = roughness_confidence("tabla", has("granulometry_assigned_df"), has_cal, zones=1)
    elif rough_mode == "cowan":
        c1,c2,c3,c4,c5,c6 = st.columns(6)
        with c1: material = st.selectbox("Material", list(COWAN_FACTORS["n0_material"].keys()), index=3)
        with c2: irr = st.selectbox("Irregularidad", list(COWAN_FACTORS["n1_irregularidad"].keys()), index=2)
        with c3: varsec = st.selectbox("Variación sección", list(COWAN_FACTORS["n2_variacion_seccion"].keys()), index=1)
        with c4: obs = st.selectbox("Obstrucciones", list(COWAN_FACTORS["n3_obstrucciones"].keys()), index=1)
        with c5: veg = st.selectbox("Vegetación", list(COWAN_FACTORS["n4_vegetacion"].keys()), index=1)
        with c6: sinu = st.selectbox("Sinuosidad", list(COWAN_FACTORS["m_sinuosidad"].keys()), index=1)
        rough_df = pd.DataFrame([cowan_n(material, irr, varsec, obs, veg, sinu)])
        n_adopt = float(rough_df["n_manning"].iloc[0])
        conf_n = roughness_confidence("cowan", has("granulometry_assigned_df"), has_cal, zones=3)
    else:
        d50_auto = 0.045
        d84_auto = 0.090
        if has("granulometry_assigned_df") and "D50_m" in st.session_state["granulometry_assigned_df"].columns:
            d50_auto = float(pd.to_numeric(st.session_state["granulometry_assigned_df"]["D50_m"], errors="coerce").median())
        if has("granulometry_assigned_df") and "D84_m" in st.session_state["granulometry_assigned_df"].columns:
            d84_auto = float(pd.to_numeric(st.session_state["granulometry_assigned_df"]["D84_m"], errors="coerce").median())
        rough_df = suggested_roughness(cat, d50_m=d50_auto, d84_m=d84_auto)
        n_adopt = float(rough_df["n_adoptado_recomendado"].dropna().iloc[0])
        conf_n = roughness_confidence("cowan", True, has_cal, zones=3)

    if st.button("Adoptar rugosidad", type="primary"):
        st.session_state["roughness_df"] = rough_df
        st.session_state["n_manning_adoptado"] = n_adopt
        st.session_state["roughness_confidence"] = conf_n
        st.success(f"Rugosidad adoptada n = {n_adopt:.3f} · confianza {conf_n['confianza_rugosidad']}/10")
    st.dataframe(rough_df, use_container_width=True)
    st.json(conf_n)

    st.divider()
    st.subheader("B · Granulometría georreferenciada con KMZ")
    g1, g2 = st.columns(2)
    with g1:
        gran_file = st.file_uploader("Tabla granulométrica CSV/XLSX", type=["csv", "xlsx"], key="gran_table")
    with g2:
        gran_kmz = st.file_uploader("KMZ/KML puntos de muestras", type=["kmz", "kml"], key="gran_kmz")
    if st.button("Leer y validar granulometría"):
        try:
            if gran_file is None:
                raise ValueError("Debes cargar una tabla granulométrica.")
            if gran_file.name.lower().endswith(".csv"):
                gdf = pd.read_csv(gran_file)
            else:
                gdf = pd.read_excel(gran_file)
            gdf = normalize_granulometry_table(gdf)
            if gran_kmz is not None:
                kmltxt = read_kmz_or_kml_to_text(gran_kmz)
                pts = parse_granulometry_points(kmltxt)
                gdf = gdf.merge(pts, on="id_muestra", how="left")
            val = validate_granulometry(gdf)
            st.session_state["granulometry_df"] = gdf
            st.session_state["granulometry_validation_df"] = val
            if has("sections_df"):
                assigned = assign_granulometry_to_sections(st.session_state["sections_df"], gdf)
                st.session_state["granulometry_assigned_df"] = assigned
            st.success("Granulometría leída, validada y asignada por sección si existen secciones.")
        except Exception as exc:
            st.error(str(exc))
    if has("granulometry_df"):
        st.dataframe(st.session_state["granulometry_df"], use_container_width=True)
    if has("granulometry_validation_df"):
        st.dataframe(st.session_state["granulometry_validation_df"], use_container_width=True)
    if has("granulometry_assigned_df"):
        st.subheader("Granulometría asignada por sección")
        st.dataframe(st.session_state["granulometry_assigned_df"], use_container_width=True)

    st.divider()
    st.subheader("C · Sección trapezoidal estimada de respaldo")
    st.caption("Usar cuando no existan suficientes secciones reales. El informe debe marcar estos cálculos como preliminares/estimativos.")
    t1,t2,t3,t4 = st.columns(4)
    with t1:
        btm = st.number_input("Ancho fondo [m]", min_value=0.1, value=6.0, step=0.5)
        reach_len = st.number_input("Longitud tramo [m]", min_value=10.0, value=1000.0, step=100.0)
    with t2:
        dep = st.number_input("Profundidad geométrica [m]", min_value=0.1, value=2.0, step=0.2)
        sep = st.number_input("Separación secciones [m]", min_value=5.0, value=100.0, step=10.0)
    with t3:
        zl = st.number_input("Talud izquierdo H:V", min_value=0.0, value=1.5, step=0.25)
        zr = st.number_input("Talud derecho H:V", min_value=0.0, value=1.5, step=0.25)
    with t4:
        slp = st.number_input("Pendiente longitudinal [m/m]", min_value=0.00001, value=float(st.session_state.get("hydrology_inputs", {}).get("slope", 0.008)), step=0.001, format="%.5f")
        z0 = st.number_input("Cota fondo inicial [m]", value=100.0, step=1.0)
    if st.button("Generar secciones trapezoidales estimadas", type="primary"):
        sec_syn, pts_syn = generate_trapezoid_reach_sections(reach_len, sep, btm, dep, zl, zr, slp, z0_m=z0)
        st.session_state["sections_df"] = sec_syn
        st.session_state["section_points_df"] = pts_syn
        st.session_state["sections_mode"] = "trapezoidal_estimado"
        st.success(f"Secciones trapezoidales generadas: {len(sec_syn)}. El cálculo queda marcado como preliminar estimativo.")
    if has("q_design"):
        qvals = list(pd.to_numeric(st.session_state["q_design"]["Q_m3s"], errors="coerce").dropna())
        if qvals:
            cap = trapezoid_capacity_table(qvals, btm, dep, zl, zr, slp, float(st.session_state.get("n_manning_adoptado", 0.040)))
            st.subheader("Capacidad hidráulica trapezoidal preliminar")
            st.dataframe(cap, use_container_width=True)

    st.divider()
    st.subheader("D · Transferencia hidrológica dual área-altitud-distancia")
    h1,h2,h3,h4 = st.columns(4)
    with h1:
        q_est = st.number_input("Q estación [m³/s]", min_value=0.0, value=10.0, step=1.0)
        a_punto = st.number_input("Área punto [km²]", min_value=0.001, value=float(st.session_state.get("basin_metrics", {}).get("area_km2", 50.0) or 50.0), step=1.0)
    with h2:
        a_est = st.number_input("Área estación [km²]", min_value=0.001, value=60.0, step=1.0, help="Si se calculó desde DEM, ingrese aquí el área obtenida.")
        b_exp = st.number_input("Exponente área b", min_value=0.30, max_value=1.20, value=0.75, step=0.05)
    with h3:
        alt_p = st.number_input("Altitud punto [m]", value=500.0, step=50.0)
        alt_e = st.number_input("Altitud estación [m]", value=450.0, step=50.0)
    with h4:
        dist_km = st.number_input("Distancia estación-punto [km]", min_value=0.0, value=20.0, step=5.0)
    if st.button("Calcular transferencia hidrológica"):
        tr = transfer_flow_area_altitude_distance(q_est, a_punto, a_est, alt_p, alt_e, dist_km, b_exp)
        st.session_state["hydrologic_transfer"] = tr
        st.success(f"Q transferido = {tr.get('Q_transferido_m3s', float('nan')):.2f} m³/s · confianza {tr.get('confianza_transferencia', 0)}/10")
    if has("hydrologic_transfer"):
        st.json(st.session_state["hydrologic_transfer"])

    st.divider()
    st.subheader("E · Semáforo maestro de confianza")
    scores = {
        "DEM / descarga": 8.8 if has("dem_path") else 6.5,
        "Cuenca / morfometría": 8.9 if has("basin_metrics") else 6.0,
        "Curvas / eje": 8.8 if has("contours_kmz") and has("axis_line") else 6.5,
        "Secciones": 8.8 if has("sections_df") and st.session_state.get("sections_mode") != "trapezoidal_estimado" else (7.4 if has("sections_df") else 5.5),
        "Hidrología normativa": 8.9 if has("hydrology_done") else 6.0,
        "Rugosidad": float(st.session_state.get("roughness_confidence", {}).get("confianza_rugosidad", 6.0)),
        "Granulometría": 9.0 if has("granulometry_assigned_df") else 6.5,
        "Hidráulica 1D": 8.8 if has("hydraulic_profile_df") else 6.0,
        "Sedimentos / socavación": 8.8 if has("sediment_df") and has("granulometry_assigned_df") else (7.2 if has("sediment_df") else 5.5),
    }
    conf_df = global_confidence_report(scores)
    st.dataframe(conf_df, use_container_width=True)
    st.session_state["confidence_report_df"] = conf_df
    st.markdown(
        """
<div class='hs-alert'><b>Advertencia técnica:</b> cuando se usen secciones trapezoidales estimadas, los resultados permiten avanzar con prefactibilidad o estimación preliminar, pero no reemplazan levantamiento topográfico ni calibración hidráulica de diseño.</div>
""",
        unsafe_allow_html=True,
    )
