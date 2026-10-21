"""Aparência da Intranet Stang: temas claro/escuro, sem modificar regras de negócio.

O tema é uma preferência de exibição por sessão. A escolha é também registrada
como parâmetro não sensível da URL para sobreviver ao recarregamento da página.
Não grava nada no Supabase, nos CSVs ou nas configurações de usuários.
"""
from __future__ import annotations

import base64
from functools import lru_cache
from pathlib import Path

import streamlit as st

TEMAS = ("Escuro", "Claro")

PALETAS = {
    "Escuro": {
        "cor": "#eef5ff", "secundario": "#b9cbe2", "muted": "#8fa9c8",
        "accent": "#2992ec", "hover": "#1975c7", "focus": "rgba(41,146,236,.27)",
        "surface": "rgba(13,30,57,.91)", "surface_soft": "rgba(21,42,74,.89)",
        "side": "#141e31", "input": "#152944", "input_text": "#f3f7ff",
        "border": "rgba(159,191,227,.20)", "border_hover": "rgba(104,178,245,.52)",
        "shadow": "0 12px 38px rgba(0,5,21,.28)",
        "nav_hover": "rgba(44,109,181,.24)", "nav_selected": "rgba(34,123,207,.26)",
        "badge": "rgba(30,146,229,.16)", "metric": "#77d9c0",
        "grid": "rgba(184,207,233,.10)",
        "page_fallback": "#06182e",
        "image_tint": "linear-gradient(120deg,rgba(1,24,58,.91),rgba(2,15,37,.93))",
        "button_text": "#ffffff", "row_hover": "rgba(52,104,160,.22)",
    },
    "Claro": {
        "cor": "#12243d", "secundario": "#475e78", "muted": "#687c96",
        "accent": "#096fd5", "hover": "#095aad", "focus": "rgba(9,111,213,.16)",
        "surface": "rgba(255,255,255,.97)", "surface_soft": "#f4f8fd",
        "side": "#ffffff", "input": "#ffffff", "input_text": "#10243c",
        "border": "rgba(25,64,111,.13)", "border_hover": "rgba(9,111,213,.46)",
        "shadow": "0 10px 28px rgba(25,57,98,.08)",
        "nav_hover": "#eaf3fd", "nav_selected": "#dfeefe",
        "badge": "#e3f0ff", "metric": "#0973b6",
        "grid": "rgba(31,71,118,.12)",
        "page_fallback": "#f3f7fc",
        "image_tint": "linear-gradient(120deg,rgba(247,250,255,.97),rgba(242,247,253,.96))",
        "button_text": "#ffffff", "row_hover": "#eef6ff",
    },
}


@lru_cache(maxsize=2)
def _imagem_base64(caminho: str) -> str:
    arquivo = Path(caminho)
    if not arquivo.is_file():
        return ""
    return base64.b64encode(arquivo.read_bytes()).decode("ascii")


def inicializar_tema() -> None:
    if "tema_stang" not in st.session_state:
        try:
            solicitado = st.query_params.get("tema", "escuro")
        except Exception:
            solicitado = "escuro"
        st.session_state["tema_stang"] = "Claro" if str(solicitado).lower() == "claro" else "Escuro"


def _atualizar_url_tema() -> None:
    # Apenas a preferência visual, nunca credenciais, é colocada na URL.
    st.query_params["tema"] = "claro" if st.session_state["tema_stang"] == "Claro" else "escuro"


def controle_tema() -> None:
    """Exibir no sidebar, depois do login. Não altera o tema global da conta."""
    inicializar_tema()
    st.markdown('<div class="stang-appearance-label">🎨 Aparência</div>', unsafe_allow_html=True)
    st.segmented_control(
        "Tema da Intranet",
        options=TEMAS,
        format_func=lambda valor: "🌙 Escuro" if valor == "Escuro" else "☀️ Claro",
        key="tema_stang",
        on_change=_atualizar_url_tema,
        label_visibility="collapsed",
    )


def aplicar_tema(caminho_capa: str = "capa.png") -> None:
    inicializar_tema()
    cores = PALETAS[st.session_state["tema_stang"]]
    imagem = _imagem_base64(caminho_capa)
    fundo = cores["image_tint"]
    if imagem:
        fundo += f', url("data:image/png;base64,{imagem}")'

    # CSS exclusivamente visual; não interfere nos seletores, formulários ou dados.
    css = r"""
    <style>
    .stApp {
      --stang-text: __cor__;
      --stang-secondary: __secundario__;
      --stang-muted: __muted__;
      --stang-accent: __accent__;
      --stang-hover: __hover__;
      --stang-focus: __focus__;
      --stang-surface: __surface__;
      --stang-soft: __surface_soft__;
      --stang-side: __side__;
      --stang-input: __input__;
      --stang-input-text: __input_text__;
      --stang-border: __border__;
      --stang-border-hover: __border_hover__;
      --stang-shadow: __shadow__;
      --stang-nav-hover: __nav_hover__;
      --stang-nav-selected: __nav_selected__;
      --stang-metric: __metric__;
      --stang-grid: __grid__;
      --stang-row-hover: __row_hover__;
      background: __page_fallback__;
      background-image: __fundo__;
      background-position: center;
      background-size: cover;
      background-attachment: fixed;
      color: var(--stang-text);
      font-family: Inter, ui-sans-serif, system-ui, -apple-system, "Segoe UI", sans-serif;
      font-size: 0.96rem;
    }
    [data-testid="stAppViewContainer"], [data-testid="stHeader"] {
      background: transparent;
    }
    [data-testid="stAppViewBlockContainer"] {
      padding-top: clamp(1.5rem,3vw,2.4rem);
      padding-bottom: 3.2rem;
      max-width: 1700px;
    }
    [data-testid="stSidebar"] {
      background: var(--stang-side);
      border-right: 1px solid var(--stang-border);
    }
    [data-testid="stSidebar"] [data-testid="stVerticalBlock"] { gap: 0.55rem; }
    [data-testid="stSidebar"] .stMarkdown p { color: var(--stang-secondary); }
    [data-testid="stSidebar"] hr { margin: 0.5rem 0; }
    .stang-appearance-label {
      font-size: .78rem;
      letter-spacing: .03em;
      font-weight: 700;
      color: var(--stang-muted);
      margin: .2rem 0;
    }
    [data-testid="stSidebar"] [data-testid="stRadio"] [role="radiogroup"] {
      gap: .35rem;
    }
    [data-testid="stSidebar"] [data-testid="stRadio"] [role="radiogroup"] label {
      background: transparent;
      width: 100%;
      min-height: 2.35rem;
      margin: 0;
      padding: .46rem .6rem;
      border: 1px solid transparent;
      border-radius: 10px;
      display: flex;
      align-items: center;
      transition: background .18s, border-color .18s;
    }
    [data-testid="stSidebar"] [data-testid="stRadio"] [role="radiogroup"] label:hover {
      background: var(--stang-nav-hover);
      border-color: var(--stang-border);
    }
    [data-testid="stSidebar"] [data-testid="stRadio"] [role="radiogroup"] label:has(input:checked) {
      background: var(--stang-nav-selected);
      border-color: var(--stang-border-hover);
    }
    [data-testid="stSidebar"] [data-testid="stRadio"] [role="radiogroup"] label p {
      color: var(--stang-text);
      font-size: .88rem;
    }
    [data-testid="stSidebar"] [data-testid="stSegmentedControl"] button,
    [data-testid="stSidebar"] [data-testid="stSegmentedControl"] label {
      border-radius: 9px;
    }
    .stApp :is(h1,h2,h3,h4,h5,h6) {
      color: var(--stang-text);
      font-weight: 700;
      letter-spacing: -.025em;
    }
    .stApp h1 { font-size: clamp(1.65rem,2.25vw,2.2rem); margin-bottom: .5rem; }
    .stApp h2 { font-size: 1.44rem; }
    .stApp [data-testid="stMarkdownContainer"] p,
    .stApp [data-testid="stCaptionContainer"] { color: var(--stang-secondary); }
    .stApp label, .stApp label p { color: var(--stang-text); }
    .stApp [data-testid="stForm"],
    .stApp [data-testid="stVerticalBlockBorderWrapper"] > div {
      background: var(--stang-surface);
      border: 1px solid var(--stang-border);
      border-radius: 16px;
      box-shadow: var(--stang-shadow);
    }
    .stApp [data-testid="stForm"] { padding: 1.1rem 1.25rem; }
    .stApp :is(.stTextInput,.stNumberInput,.stDateInput,.stTimeInput,.stTextArea) input,
    .stApp :is(.stTextInput,.stNumberInput,.stDateInput,.stTimeInput,.stTextArea) textarea,
    .stApp [data-baseweb="select"] > div,
    .stApp [data-baseweb="input"] > div,
    .stApp [data-baseweb="textarea"] > div {
      background: var(--stang-input) !important;
      color: var(--stang-input-text) !important;
      border: 1px solid var(--stang-border) !important;
      border-radius: 10px !important;
      box-shadow: none;
    }
    .stApp :is(.stTextInput,.stNumberInput,.stDateInput,.stTimeInput) input,
    .stApp [data-baseweb="select"] > div { min-height: 2.55rem; }
    .stApp input::placeholder, .stApp textarea::placeholder { color: var(--stang-muted) !important; opacity: 1; }
    .stApp [data-baseweb="select"] *, .stApp [data-baseweb="input"] input,
    .stApp [data-baseweb="textarea"] textarea {
      color: var(--stang-input-text) !important;
    }
    .stApp :is(input,textarea):focus,
    .stApp [data-baseweb="select"]:focus-within > div {
      border-color: var(--stang-accent) !important;
      box-shadow: 0 0 0 3px var(--stang-focus) !important;
      outline: 0;
    }
    [data-baseweb="popover"], [data-baseweb="menu"], [role="listbox"] {
      background: var(--stang-surface) !important;
      color: var(--stang-text) !important;
      border: 1px solid var(--stang-border) !important;
      border-radius: 10px !important;
    }
    [data-baseweb="popover"] [role="option"], [data-baseweb="menu"] li {
      color: var(--stang-text) !important;
    }
    [data-baseweb="popover"] [role="option"]:hover { background: var(--stang-nav-hover) !important; }
    .stApp .stButton > button, .stApp .stDownloadButton > button,
    .stApp .stFormSubmitButton > button {
      border-radius: 10px;
      border: 1px solid var(--stang-border);
      background: var(--stang-surface);
      color: var(--stang-text);
      min-height: 2.35rem;
      font-weight: 600;
      box-shadow: 0 2px 5px rgba(8,33,65,.07);
      transition: transform .17s, background .17s, border-color .17s;
    }
    .stApp .stButton > button:hover, .stApp .stDownloadButton > button:hover,
    .stApp .stFormSubmitButton > button:hover {
      color: var(--stang-accent);
      border-color: var(--stang-border-hover);
      transform: translateY(-1px);
    }
    .stApp button[kind="primary"], .stApp button[data-testid="stBaseButton-primary"],
    .stApp button[data-testid="stBaseButton-formSubmit"] {
      background: var(--stang-accent) !important;
      color: white !important;
      border-color: var(--stang-accent) !important;
    }
    .stApp button[kind="primary"]:hover, .stApp button[data-testid="stBaseButton-primary"]:hover,
    .stApp button[data-testid="stBaseButton-formSubmit"]:hover {
      background: var(--stang-hover) !important;
      color: white !important;
    }
    .stApp button:disabled {
      opacity: .5;
      transform: none !important;
      cursor: not-allowed;
    }
    .stApp [data-baseweb="tab-list"] {
      display: flex;
      gap: .35rem;
      overflow-x: auto;
      border-bottom: 1px solid var(--stang-border);
      padding-bottom: .35rem;
    }
    .stApp [data-baseweb="tab"] {
      background: transparent;
      border-radius: 10px;
      color: var(--stang-secondary);
      padding: .65rem .9rem;
      white-space: nowrap;
      border: 1px solid transparent;
      font-weight: 600;
    }
    .stApp [data-baseweb="tab"]:hover { background: var(--stang-nav-hover); }
    .stApp [data-baseweb="tab"][aria-selected="true"] {
      background: var(--stang-nav-selected);
      color: var(--stang-text);
      border-color: var(--stang-border);
    }
    .stApp [data-testid="stMetric"] {
      background: var(--stang-surface);
      border-radius: 14px;
      border: 1px solid var(--stang-border);
      padding: .9rem 1.05rem;
      box-shadow: var(--stang-shadow);
    }
    .stApp [data-testid="stMetricValue"] {
      color: var(--stang-metric) !important;
      font-weight: 750;
    }
    .stApp [data-testid="stMetricLabel"] { color: var(--stang-secondary); }
    .stApp [data-testid="stDataFrame"] {
      overflow: hidden;
      border-radius: 13px;
      border: 1px solid var(--stang-border);
      background: var(--stang-surface);
    }
    .stApp [data-testid="stPlotlyChart"] {
      padding: .55rem .7rem;
      background: var(--stang-surface);
      border: 1px solid var(--stang-border);
      border-radius: 14px;
      box-shadow: var(--stang-shadow);
    }
    .stApp [data-testid="stExpander"] {
      border: 1px solid var(--stang-border);
      background: var(--stang-surface);
      border-radius: 12px;
      overflow: hidden;
    }
    .stApp hr { border-color: var(--stang-border); }
    .stApp .status-card-container { display: flex; gap: 12px; margin-bottom: 15px; flex-wrap: wrap; }
    .stApp .status-card {
      flex: 1; min-width: 140px;
      background: var(--stang-surface);
      border: 1px solid var(--stang-border);
      border-radius: 12px;
      padding: .9rem 1rem;
      box-shadow: var(--stang-shadow);
      transition: transform .18s, border-color .18s;
    }
    .stApp .status-card:hover { transform: translateY(-2px); border-color: var(--stang-border-hover); }
    .stApp .status-card-title { font-size: .71rem; text-transform: uppercase; letter-spacing: .06em;
        color: var(--stang-muted); margin-bottom: .25rem; font-weight: 650; }
    .stApp .status-card-value { font-size: 1rem; font-weight: 720; color: var(--stang-text); }
    .stApp .badge-success { color: #079b6b; }
    .stApp .badge-danger { color: #e35a62; }
    .stApp .badge-warning { color: #df8a18; }
    .stApp [data-testid="stAlert"] { border-radius: 12px; }
    @media (max-width: 900px) {
      [data-testid="stAppViewBlockContainer"] { padding-left: .8rem; padding-right: .8rem; }
      .stApp h1 { font-size: 1.6rem; }
      .stApp [data-testid="stForm"] { padding: .85rem; }
      .stApp [data-baseweb="tab"] { padding: .55rem .7rem; }
    }
    @media (prefers-reduced-motion: reduce) {
      .stApp * { transition-duration: .01ms !important; animation-duration: .01ms !important; }
    }
    @media print {
      body, .stApp, [data-testid="stAppViewContainer"] {
        color: black !important; background: #ffffff !important;
      }
      [data-testid="stSidebar"], [data-testid="stHeader"], [data-testid="stToolbar"],
      .stApp button, .no-print { display: none !important; }
      .stApp [data-testid="stForm"], .stApp [data-testid="stMetric"] { box-shadow: none !important; }
    }
    </style>
    """
    for chave, valor in cores.items():
        css = css.replace(f"__{chave}__", valor)
    css = css.replace("__fundo__", fundo)
    # Aspas e braces da string foram fixados no CSS, nenhuma interpolação de usuário.
    st.markdown(css, unsafe_allow_html=True)


def estilizar_grafico(fig):
    """Somente aparência Plotly, sem alterar filtros, dados ou cálculos."""
    inicializar_tema()
    cor = PALETAS[st.session_state["tema_stang"]]
    fig.update_layout(
        paper_bgcolor="rgba(0,0,0,0)",
        plot_bgcolor="rgba(0,0,0,0)",
        font=dict(color=cor["cor"], family="Inter, Segoe UI, Arial, sans-serif", size=12),
        colorway=["#1685d7", "#10a88d", "#f29d38", "#8167cf", "#e46976", "#46a8c7"],
        xaxis=dict(gridcolor=cor["grid"], linecolor=cor["grid"], zerolinecolor=cor["grid"]),
        yaxis=dict(gridcolor=cor["grid"], linecolor=cor["grid"], zerolinecolor=cor["grid"]),
    )
    return fig
