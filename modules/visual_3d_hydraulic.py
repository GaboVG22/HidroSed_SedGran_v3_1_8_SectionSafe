
from __future__ import annotations

import io
import numpy as np
import pandas as pd


def _merge_profile(points_df: pd.DataFrame, hydraulic_df: pd.DataFrame | None, sediment_df: pd.DataFrame | None):
    pts = points_df.copy()
    if hydraulic_df is not None and not hydraulic_df.empty:
        # Mostrar por defecto el mayor periodo de retorno disponible.
        h = hydraulic_df.copy()
        tmax = h["T_anios"].max()
        h = h[h["T_anios"] == tmax][["section_id", "T_anios", "cota_agua_m", "velocidad_m_s", "Froude"]]
        pts = pts.merge(h, on="section_id", how="left")
    else:
        pts["cota_agua_m"] = np.nan
        pts["velocidad_m_s"] = np.nan
        pts["Froude"] = np.nan
        pts["T_anios"] = np.nan

    if sediment_df is not None and not sediment_df.empty:
        s = sediment_df.copy()
        tmax = s["T_anios"].max()
        keep = [c for c in ["section_id", "T_anios", "socavacion_general_m", "cota_fondo_socavado_m", "Shields", "estado"] if c in s.columns]
        s = s[s["T_anios"] == tmax][keep]
        s = s.drop(columns=["T_anios"], errors="ignore")
        pts = pts.merge(s, on="section_id", how="left")
    else:
        pts["socavacion_general_m"] = np.nan
        pts["cota_fondo_socavado_m"] = np.nan
        pts["Shields"] = np.nan
        pts["estado"] = ""

    return pts


def create_3d_profile_figure(
    sections_df: pd.DataFrame,
    points_df: pd.DataFrame,
    hydraulic_df: pd.DataFrame | None = None,
    sediment_df: pd.DataFrame | None = None,
    vertical_exaggeration: float = 1.0,
    show_water: bool = True,
    show_scour: bool = True,
    show_deposition: bool = True,
):
    import plotly.graph_objects as go

    if sections_df is None or points_df is None or sections_df.empty or points_df.empty:
        raise ValueError("No existen secciones/puntos suficientes para visualización 3D.")

    pts = _merge_profile(points_df, hydraulic_df, sediment_df)
    fig = go.Figure()

    # Terreno y secciones.
    for sid, g in pts.groupby("section_id"):
        g = g.sort_values("offset_m")
        pk = float(g["pk_m"].iloc[0])
        fig.add_trace(go.Scatter3d(
            x=[pk] * len(g),
            y=g["offset_m"],
            z=g["z_m"] * vertical_exaggeration,
            mode="lines",
            line=dict(width=3, color="saddlebrown"),
            name="Terreno/sección",
            showlegend=(sid == pts["section_id"].min()),
            hovertemplate="PK %{x:.1f} m<br>Offset %{y:.1f} m<br>Cota terreno %{z:.2f}<extra></extra>",
        ))

        if show_water and np.isfinite(g["cota_agua_m"]).any():
            wse = float(g["cota_agua_m"].dropna().iloc[0])
            wet = g[g["z_m"] <= wse]
            if len(wet) >= 2:
                fig.add_trace(go.Scatter3d(
                    x=[pk] * len(wet),
                    y=wet["offset_m"],
                    z=[wse * vertical_exaggeration] * len(wet),
                    mode="lines",
                    line=dict(width=5, color="deepskyblue"),
                    name="Lámina de agua",
                    showlegend=(sid == pts["section_id"].min()),
                    hovertemplate="PK %{x:.1f} m<br>Cota agua " + f"{wse:.2f} m" + "<extra></extra>",
                ))

        if show_scour and "cota_fondo_socavado_m" in g and np.isfinite(g["cota_fondo_socavado_m"]).any():
            zsc = float(g["cota_fondo_socavado_m"].dropna().iloc[0])
            # Marca fondo socavado como línea corta al centro de sección.
            center = g.iloc[(g["offset_m"].abs()).argmin()]
            fig.add_trace(go.Scatter3d(
                x=[pk, pk],
                y=[float(center["offset_m"]) - 2, float(center["offset_m"]) + 2],
                z=[zsc * vertical_exaggeration, zsc * vertical_exaggeration],
                mode="lines",
                line=dict(width=7, color="red"),
                name="Fondo socavado",
                showlegend=(sid == pts["section_id"].min()),
                hovertemplate="PK %{x:.1f} m<br>Cota fondo socavado %{z:.2f}<extra></extra>",
            ))

    # Perfil longitudinal de fondo.
    if not sections_df.empty:
        sec = sections_df.sort_values("pk_m")
        zcol = "cota_fondo_m" if "cota_fondo_m" in sec.columns else None
        if zcol:
            fig.add_trace(go.Scatter3d(
                x=sec["pk_m"],
                y=[0] * len(sec),
                z=sec[zcol] * vertical_exaggeration,
                mode="lines+markers",
                line=dict(width=5, color="black"),
                marker=dict(size=3),
                name="Perfil longitudinal fondo",
                hovertemplate="PK %{x:.1f} m<br>Fondo %{z:.2f}<extra></extra>",
            ))

    # Puntos críticos por Froude/Shields.
    crit = pts.copy()
    crit["critico"] = False
    if "Froude" in crit:
        crit["critico"] = crit["critico"] | (crit["Froude"] >= 0.8)
    if "Shields" in crit:
        crit["critico"] = crit["critico"] | (crit["Shields"] >= 0.047)
    crit = crit[crit["critico"]]
    if len(crit):
        fig.add_trace(go.Scatter3d(
            x=crit["pk_m"],
            y=crit["offset_m"],
            z=crit["z_m"] * vertical_exaggeration,
            mode="markers",
            marker=dict(size=4, color="orange", symbol="diamond"),
            name="Zona crítica hidráulica/sedimento",
            hovertemplate="PK %{x:.1f}<br>Offset %{y:.1f}<br>Froude %{customdata[0]:.2f}<br>Shields %{customdata[1]:.3f}<extra></extra>",
            customdata=np.stack([
                crit.get("Froude", pd.Series(np.nan, index=crit.index)).fillna(np.nan),
                crit.get("Shields", pd.Series(np.nan, index=crit.index)).fillna(np.nan),
            ], axis=1)
        ))

    fig.update_layout(
        title="Perfil longitudinal 3D con secciones, lámina de agua y fenómenos hidráulicos",
        scene=dict(
            xaxis_title="PK [m]",
            yaxis_title="Offset transversal [m]",
            zaxis_title=f"Cota x {vertical_exaggeration:g}",
            aspectmode="data",
        ),
        height=750,
        legend=dict(orientation="h"),
        margin=dict(l=0, r=0, t=50, b=0),
    )
    return fig


def figure_to_html_bytes(fig) -> bytes:
    html = fig.to_html(include_plotlyjs="cdn", full_html=True)
    return html.encode("utf-8")
