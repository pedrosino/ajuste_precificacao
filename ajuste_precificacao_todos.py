"""Streamlit — Ajuste de Precificação de Plano de Benefícios"""

import io
import json
import logging
from pathlib import Path

import numpy as np
import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from calculos import ANO_BASE, DATA_BASE, calcular_tudo, formatar_colunas_data, ordenar_titulos, preparar_exportacao_titulos

# Função para formatar os números
def moeda_br(x):
    """Substitui ponto por vírgula e vice-versa"""
    return f"{x:,.0f}".replace(",", "X").replace(".", ",").replace("X", ".")

# Formato de percentual
def perc_br(x):
    """Substitui ponto por vírgula"""
    return f"{x:.2f}%".replace(".", ",")

def formatar_numero(x, casas):
    """Formata número com casas decimais e separador de milhar"""
    if pd.isna(x):
        return "—"
    if casas == 0:
        return f"{x:,.0f}".replace(",", "X").replace(".", ",").replace("X", ".")
    else:
        return f"{x:,.{casas}f}".replace(",", "X").replace(".", ",").replace("X", ".")

st.set_page_config(
    page_title="Ajuste de Precificação",
    page_icon="📊",
    layout="wide",
)

st.title("📊 Ajuste de Precificação")
st.caption("Cálculo do valor presente do ativo e passivo do plano de benefícios")

# ─────────────────────────────────────────────
# 0. Barra lateral
# ─────────────────────────────────────────────

st.sidebar.title("Fonte de dados")

st.sidebar.markdown("Os dados serão carregados automaticamente. Caso queira usar dados personalizados, ative a opção de upload abaixo.")

modo = st.sidebar.toggle(
    "Upload de planilha",
    value=False,
    key="upload"
)

# ─────────────────────────────────────────────
# 1. UPLOAD
# ─────────────────────────────────────────────

uploaded = None

if modo:
    uploaded = st.file_uploader(
    "Selecione o arquivo Excel",
    type=["xlsx", "xls"],
    help="Arquivo com as abas: Titulos, titulos_plano, titulos_carteira, Passivo, contas, dias_uteis",
)

if modo and not uploaded:
    st.info("Carregue o arquivo para começar.")
    st.stop()

# ─────────────────────────────────────────────
# 2. CARREGAR RESULTADOS
#    - Padrão: resultados pré-calculados em dados_calculados/
#      (gerados por atualizar_dados.py quando a planilha de origem muda)
#    - Upload: lê a planilha enviada e calcula na hora (cacheado)
# ─────────────────────────────────────────────

PASTA_DADOS = Path(__file__).parent / "dados_calculados"

@st.cache_data(show_spinner="Lendo planilha…")
def load_workbook(file_bytes: bytes):
    """Lê as abas do arquivo Excel e retorna os DataFrames correspondentes."""
    buf = io.BytesIO(file_bytes)
    fluxo      = pd.read_excel(buf, sheet_name="Titulos")
    mapa       = pd.read_excel(buf, sheet_name="titulos_plano")
    carteira   = pd.read_excel(buf, sheet_name="titulos_carteira")
    passivo    = pd.read_excel(buf, sheet_name="Passivo")
    contas     = pd.read_excel(buf, sheet_name="contas")
    dias_uteis = pd.read_excel(buf, sheet_name="dias_uteis", header=None, names=["data"])
    return fluxo, mapa, carteira, passivo, contas, dias_uteis

@st.cache_data(show_spinner="Calculando…")
def calcular_upload(fluxo, mapa, carteira, passivo, contas, dias_uteis):
    """Modo upload: calcula resultados e resumo a partir da planilha enviada."""
    return calcular_tudo(fluxo, mapa, carteira, passivo, contas, dias_uteis)

# cache_resource devolve sempre o mesmo objeto (sem copiar a cada interação) e o
# compartilha entre as sessões. É seguro porque o app não altera esses DataFrames.
@st.cache_resource(show_spinner="Carregando resultados…")
def carregar_pre_calculado():
    """Lê os parquets gerados por atualizar_dados.py."""
    meta = json.loads((PASTA_DADOS / "meta.json").read_text(encoding="utf-8"))
    calc = {nome: pd.read_parquet(PASTA_DADOS / f"{nome}.parquet") for nome in meta["tabelas"]}
    calc["todos_planos"] = pd.read_parquet(PASTA_DADOS / "todos_planos.parquet")["numero_plano"].tolist()
    resumo = pd.read_parquet(PASTA_DADOS / "resumo_todos_planos.parquet")
    entradas = {nome: pd.read_parquet(PASTA_DADOS / f"entrada_{nome}.parquet") for nome in meta["entradas"]}
    return calc, resumo, entradas, meta

# df e df_carteira (fluxos por título) são as maiores tabelas e o app só as usa
# filtradas pelo plano selecionado: lê-se do parquet apenas as linhas desse plano.
@st.cache_data(show_spinner=False, max_entries=10)
def ler_titulos_do_plano(nome, plano):
    return pd.read_parquet(PASTA_DADOS / f"{nome}.parquet", filters=[("numero_plano", "==", plano)])

try:
    if modo and uploaded:
        fluxo, mapa, carteira, passivo, contas, dias_uteis = load_workbook(uploaded.read())
        calc_result, df_resumo_todos = calcular_upload(fluxo, mapa, carteira, passivo, contas, dias_uteis)
    else:
        calc_result, df_resumo_todos, entradas, meta_dados = carregar_pre_calculado()
        mapa, carteira = entradas["mapa"], entradas["carteira"]  # usadas na tabela "Títulos do Plano"
        st.sidebar.caption(f"Resultados calculados em {meta_dados['gerado_em']}")
        if meta_dados["ano_base"] != ANO_BASE:
            st.sidebar.warning(
                f"Os resultados salvos usam ano-base {meta_dados['ano_base']}, "
                f"mas o código usa {ANO_BASE}. Rode atualizar_dados.py."
            )
except FileNotFoundError:
    st.error("Resultados pré-calculados não encontrados. Rode `python atualizar_dados.py` e faça o commit da pasta `dados_calculados/`.")
    st.stop()
except Exception as e:
    st.error(f"Erro ao carregar os dados: {e.message if hasattr(e, 'message') else str(e)}")
    logging.exception("Erro ao carregar/calcular os dados")
    raise

if calc_result is None:
    st.error("Nenhum título encontrado.")
    st.stop()


# Desempacotar resultados
vp_curva = calc_result['vp_curva']
vp_curva_grupo = calc_result['vp_curva_grupo']
vp_curva_carteira = calc_result['vp_curva_carteira']
vp_ativo = calc_result['vp_ativo']
vp_ativo_grupo = calc_result['vp_ativo_grupo']
vp_ativo_carteira = calc_result['vp_ativo_carteira']
vp_passivo = calc_result['vp_passivo']
vp_passivo_grupo = calc_result['vp_passivo_grupo']
resultado = calc_result['resultado']
resultado_carteira = calc_result['resultado_carteira']
resultado_grupo = calc_result['resultado_grupo']
duracao_ativo = calc_result['duracao_ativo']
duracao_ativo_carteira = calc_result['duracao_ativo_carteira']
duracao_ativo_anos = calc_result['duracao_ativo_anos']
duracao_ativo_carteira_anos = calc_result['duracao_ativo_carteira_anos']
duracao_ativo_grupo = calc_result['duracao_ativo_grupo']
duracao_ativo_anos_grupo = calc_result['duracao_ativo_anos_grupo']
duracao_passivo = calc_result['duracao_passivo']
duracao_passivo_grupo = calc_result['duracao_passivo_grupo']
taxas_plano = calc_result['taxas_plano']
taxas_grupo = calc_result['taxas_grupo']
passivo = calc_result['passivo']
df = calc_result.get('df')                  # None no modo pré-calculado (lido por plano, mais abaixo)
df_carteira = calc_result.get('df_carteira')
todos_planos = calc_result['todos_planos']


# O resumo (df_resumo_todos) já vem pronto da etapa 2.


# Download: Resumo de todos os planos
st.subheader("📊 Resumo - Todos os Planos")
buf_resumo_todos = io.BytesIO()
with pd.ExcelWriter(buf_resumo_todos, engine="openpyxl") as writer:
    df_resumo_todos.to_excel(writer, sheet_name="Resumo", index=False)

col_down1, col_down2 = st.columns([2, 1])
with col_down1:
    st.download_button(
        label="📋  Baixar resumo de todos os planos (.xlsx)",
        data=buf_resumo_todos.getvalue(),
        file_name=f"resumo_todos_planos_{DATA_BASE.date()}.xlsx",
        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )

st.divider()

# ─────────────────────────────────────────────
# 4. SELEÇÃO DE PLANO / GRUPO
# ─────────────────────────────────────────────

planos = todos_planos.copy()

nivel_analise = st.radio(
    "Nível de análise",
    options=["Por plano", "Por grupo"],
    horizontal=True,
    help="Por plano: agrega todos os grupos. Por grupo: analisa um grupo específico do plano.",
)
analise_por_grupo = nivel_analise == "Por grupo"

col1, col2, col3, col4 = st.columns([2, 2, 1, 1])
with col1:
    plano = st.selectbox("Plano", planos)

if df is None:  # modo pré-calculado: carrega só as linhas do plano selecionado
    df = ler_titulos_do_plano("df", plano)
    df_carteira = ler_titulos_do_plano("df_carteira", plano)
with col2:
    if analise_por_grupo:
        grupos_passivo = set(passivo.loc[passivo["numero_plano"] == plano, "grupo"].dropna())
        grupos_ativo = set(df.loc[df["numero_plano"] == plano, "grupo"].dropna())
        grupos_plano = sorted(grupos_passivo | grupos_ativo)
        if not grupos_plano:
            st.error(f"Nenhum grupo encontrado para o plano {plano}.")
            st.stop()
        grupo = st.selectbox("Grupo", grupos_plano, key="sel_grupo")
    else:
        grupo = None
        st.caption("Agregando todos os grupos")
        st.write("")  # alinha verticalmente com os outros campos
with col3:
    ano_base_select = st.number_input("Ano-base", value=2025, min_value=2000, max_value=2100, step=1)
with col4:
    st.write("")  # alinha verticalmente com os outros campos
    st.write("")
    calcular = st.button("Filtrar", type="primary", width='stretch')

# Guarda o clique em session_state: sem isso, qualquer outro widget (ex.: o
# toggle de destaque) dispara um rerun em que `calcular` volta a ser False,
# e o st.stop() abaixo derrubaria gráficos e tabelas já filtrados.
if calcular:
    st.session_state.filtro_aplicado = True

if not st.session_state.get("filtro_aplicado", False):
    st.stop()

def _primeiro_ou_nan(serie):
    return serie.iloc[0] if len(serie) else np.nan

if analise_por_grupo:
    mask_pg = (passivo["numero_plano"] == plano) & (passivo["grupo"] == grupo)
    passivo_plano = passivo[mask_pg].copy()
    if passivo_plano.empty and df[(df["numero_plano"] == plano) & (df["grupo"] == grupo)].empty:
        st.error(f"Nenhum dado encontrado para o plano {plano}, grupo {grupo}.")
        st.stop()

    resultado_plano = resultado_grupo[
        (resultado_grupo["numero_plano"] == plano) & (resultado_grupo["grupo"] == grupo)
    ]

    resultado_plano_carteira = resultado_carteira[resultado_carteira["numero_plano"] == plano]

    taxa_plano = _primeiro_ou_nan(
        taxas_grupo.loc[
            (taxas_grupo["numero_plano"] == plano) & (taxas_grupo["grupo"] == grupo),
            "taxa",
        ]
    )
    if pd.isna(taxa_plano):
        taxa_plano = taxas_plano.loc[taxas_plano["numero_plano"] == plano, "taxa"].iloc[0]

    duracao_passivo_plano = _primeiro_ou_nan(
        duracao_passivo_grupo.loc[
            (duracao_passivo_grupo["numero_plano"] == plano)
            & (duracao_passivo_grupo["grupo"] == grupo),
            "duracao",
        ]
    )
    vp_ativo_plano = vp_ativo_grupo.loc[
        (vp_ativo_grupo["numero_plano"] == plano) & (vp_ativo_grupo["grupo"] == grupo),
        "vp_ativo",
    ].sum()
    vp_curva_plano = vp_curva_grupo.loc[
        (vp_curva_grupo["numero_plano"] == plano) & (vp_curva_grupo["grupo"] == grupo),
        "vp_curva",
    ].sum()

    vp_ativo_plano_carteira = vp_ativo_carteira[vp_ativo_carteira["numero_plano"] == plano]["vp_ativo"].sum()
    vp_curva_plano_carteira = vp_curva_carteira[vp_curva_carteira["numero_plano"] == plano]["vp_curva"].sum()

    duracao_ativo = _primeiro_ou_nan(
        duracao_ativo_grupo.loc[
            (duracao_ativo_grupo["numero_plano"] == plano)
            & (duracao_ativo_grupo["grupo"] == grupo),
            "duracao",
        ]
    )

    duracao_ativo_anos = _primeiro_ou_nan(
        duracao_ativo_anos_grupo.loc[
            (duracao_ativo_anos_grupo["numero_plano"] == plano)
            & (duracao_ativo_anos_grupo["grupo"] == grupo),
            "duracao",
        ]
    )

    duracao_ativo_carteira = duracao_ativo_carteira.loc[
        duracao_ativo_carteira["numero_plano"] == plano, "duracao"
    ].iloc[0]
    duracao_ativo_carteira_anos = duracao_ativo_carteira_anos.loc[
        duracao_ativo_carteira_anos["numero_plano"] == plano, "duracao"
    ].iloc[0]

    titulos_filtro = (df["numero_plano"] == plano) & (df["grupo"] == grupo)
    titulo_escopo = f"Plano {plano}  ·  Grupo {grupo}  ·  Base 31/12/{ANO_BASE}"
    nome_arquivo = f"resultado_ajuste_plano_{plano}_grupo_{grupo}.xlsx"
else:
    passivo_plano = passivo[passivo["numero_plano"] == plano].copy()
    if passivo_plano.empty:
        st.error(f"Nenhum dado de passivo encontrado para o plano {plano}.")
        st.stop()
    resultado_plano = resultado[resultado["numero_plano"] == plano]
    resultado_plano_carteira = resultado_carteira[resultado_carteira["numero_plano"] == plano]

    taxa_plano = taxas_plano.loc[taxas_plano["numero_plano"] == plano, "taxa"].iloc[0]
    duracao_passivo_plano = duracao_passivo.loc[
        duracao_passivo["numero_plano"] == plano, "duracao"
    ].iloc[0]
    vp_ativo_plano = vp_ativo[vp_ativo["numero_plano"] == plano]["vp_ativo"].sum()
    vp_curva_plano = vp_curva[vp_curva["numero_plano"] == plano]["vp_curva"].sum()
    vp_ativo_plano_carteira = vp_ativo_carteira[vp_ativo_carteira["numero_plano"] == plano]["vp_ativo"].sum()
    vp_curva_plano_carteira = vp_curva_carteira[vp_curva_carteira["numero_plano"] == plano]["vp_curva"].sum()

    duracao_ativo = duracao_ativo[duracao_ativo["numero_plano"] == plano]["duracao"].iloc[0] if len(duracao_ativo[duracao_ativo["numero_plano"] == plano]) > 0 else np.nan
    duracao_ativo_anos = duracao_ativo_anos[duracao_ativo_anos["numero_plano"] == plano]["duracao"].iloc[0] if len(duracao_ativo_anos[duracao_ativo_anos["numero_plano"] == plano]) > 0 else np.nan
    duracao_ativo_carteira = duracao_ativo_carteira[duracao_ativo_carteira["numero_plano"] == plano]["duracao"].iloc[0] if len(duracao_ativo_carteira[duracao_ativo_carteira["numero_plano"] == plano]) > 0 else np.nan
    duracao_ativo_carteira_anos = duracao_ativo_carteira_anos[duracao_ativo_carteira_anos["numero_plano"] == plano]["duracao"].iloc[0] if len(duracao_ativo_carteira_anos[duracao_ativo_carteira_anos["numero_plano"] == plano]) > 0 else np.nan

    titulo_escopo = f"Plano {plano}  ·  Base 31/12/{ANO_BASE}"
    nome_arquivo = f"resultado_ajuste_plano_{plano}.xlsx"

titulos_filtro = df["numero_plano"] == plano
titulos_carteira_filtro = df_carteira["numero_plano"] == plano
ajuste_plano = vp_ativo_plano - vp_curva_plano
ajuste_plano_carteira = vp_ativo_plano_carteira - vp_curva_plano_carteira

# ─────────────────────────────────────────────
# 5. EXIBIR RESULTADOS
# ─────────────────────────────────────────────

st.divider()
st.subheader(titulo_escopo)
def kpi_card(titulo, valor, delta=None, alerta=False):
    """Cria um card de KPI com título, valor e delta opcional."""
    cor_delta = "green" if delta is not None and delta >= 0 else "red"
    sinal = "+" if delta is not None and delta >= 0 else ""

    delta_html = ""
    if delta is not None:
        delta_html = f'<div style="color:{cor_delta}; font-size:14px;">{sinal}{perc_br(delta)}</div>'

    if alerta:
        cor_titulo = "#7A1F1F"
        cor_valor = "#7A1F1F"
    else:
        cor_titulo = "inherit"
        cor_valor = "inherit"

    return f"""
    <div style="
        padding: 3px 4px;
        border-radius: 10px;
        margin-bottom: 5px;
        {'background-color: #FDECEC;' if alerta else 'background-color: #FFFFFF;'}
    ">
        <div style="font-size:13px; color:{cor_titulo};">{titulo}</div>
        <div style="font-size:18px; font-weight:600; color:{cor_valor};">{valor}</div>
        {delta_html}
    </div>
    """

# Métricas

vp_passivo_total = passivo_plano['vp_passivo'].sum()

# Primeira linha - dados do passivo
c11, c12, c13 = st.columns(3) #, c3 = st.columns(3)
c11.markdown(kpi_card("Valor presente Passivo", moeda_br(vp_passivo_total)), unsafe_allow_html=True)
c12.markdown(kpi_card("Taxa atuarial", perc_br(taxa_plano*100)), unsafe_allow_html=True)
c13.markdown(kpi_card("Duração do passivo (anos)", f"{formatar_numero(duracao_passivo_plano, 4)} anos"), unsafe_allow_html=True)

st.divider()

alerta_duracao = (
    pd.notna(duracao_ativo_anos)
    and pd.notna(duracao_passivo_plano)
    and duracao_ativo_anos > duracao_passivo_plano
)
alerta_duracao_carteira = (
    pd.notna(duracao_ativo_carteira_anos)
    and pd.notna(duracao_passivo_plano)
    and duracao_ativo_carteira_anos > duracao_passivo_plano
)

# Segunda linha - Valor presente com taxa atuarial
c21, c22, c23 = st.columns(3)
#c20.markdown("**Item**<hr style='margin: 0px 0px;'>", unsafe_allow_html=True)
#c20.markdown(kpi_card("","Valor presente (taxa atuarial)"), unsafe_allow_html=True)
c21.markdown("**DPAP**<hr style='margin: 0px 0px;'>", unsafe_allow_html=True)
c21.markdown(kpi_card("Valor presente (taxa atuarial)", moeda_br(vp_ativo_plano)), unsafe_allow_html=True)
c22.markdown("**Carteira**<hr style='margin: 0px 0px;'>", unsafe_allow_html=True)
c22.markdown(kpi_card("Valor presente (taxa atuarial)", moeda_br(vp_ativo_plano_carteira)), unsafe_allow_html=True)

# Terceira linha - valor presente com taxa do título
c31, c32, c33 = st.columns(3)
#c30.markdown(kpi_card("","Valor presente (taxa título)"), unsafe_allow_html=True)
c31.markdown(kpi_card("Valor presente (taxa título)", moeda_br(vp_curva_plano)), unsafe_allow_html=True)
c32.markdown(kpi_card("Valor presente (taxa título)", moeda_br(vp_curva_plano_carteira)), unsafe_allow_html=True)

# Quarta linha - Ajuste de precificação
c41, c42, c43 = st.columns(3)
#c40.markdown(kpi_card("","Ajuste de precificação"), unsafe_allow_html=True)
c41.markdown(kpi_card("Ajuste de precificação", moeda_br(ajuste_plano), (ajuste_plano/vp_passivo_total*100) if vp_passivo_total else None), unsafe_allow_html=True)
c42.markdown(kpi_card("Ajuste de precificação", moeda_br(ajuste_plano_carteira), (ajuste_plano_carteira/vp_passivo_total*100) if vp_passivo_total else None), unsafe_allow_html=True)

# Quinta linha - Duração do ativo (anos)
c51, c52, c53 = st.columns(3)
#c50.markdown(kpi_card("","Duração do ativo (anos)"), unsafe_allow_html=True)
c51.markdown(kpi_card("Duração do ativo (anos)", f"{formatar_numero(duracao_ativo_anos, 4)} anos", alerta=alerta_duracao), unsafe_allow_html=True)
c52.markdown(kpi_card("Duração do ativo (anos)", f"{formatar_numero(duracao_ativo_carteira_anos, 4)} anos", alerta=alerta_duracao_carteira), unsafe_allow_html=True)

# Sexta linha - Duração do ativo (dias)
c61, c62, c63 = st.columns(3)
#c60.markdown(kpi_card("","Duração do ativo (dias)"), unsafe_allow_html=True)
c61.markdown(kpi_card("Duração do ativo (dias)", f"{formatar_numero(duracao_ativo, 2)} dias ({formatar_numero(duracao_ativo/252, 4)} anos)", alerta=alerta_duracao), unsafe_allow_html=True)
c62.markdown(kpi_card("Duração do ativo (dias)", f"{formatar_numero(duracao_ativo_carteira, 2)} dias ({formatar_numero(duracao_ativo_carteira/252, 4)} anos)", alerta=alerta_duracao_carteira), unsafe_allow_html=True)

st.divider()

# ── Gráfico ────────────────────────────────────────────────────────────────────
st.subheader("VP Acumulado — Ativo vs. Passivo")

anos = np.union1d(
    resultado_plano["ano"].dropna().to_numpy(),
    resultado_plano_carteira["ano"].dropna().to_numpy(),
)

def serie_por_ano(frame, coluna):
    """Retorna uma série numérica alinhada ao eixo de anos do gráfico."""
    return frame.set_index("ano")[coluna].reindex(anos).to_numpy(dtype=float)

linha_passivo = serie_por_ano(resultado_plano, "acumulado_passivo")
linha_ativo = serie_por_ano(resultado_plano, "acumulado_ativo")
linha_carteira = serie_por_ano(resultado_plano_carteira, "acumulado_ativo")

sombra_ativo = np.maximum(linha_ativo, linha_passivo)
sombra_carteira = np.maximum(linha_carteira, linha_passivo)

excesso_ativo = np.maximum(0, linha_ativo - linha_passivo)
excesso_carteira = np.maximum(0, linha_carteira - linha_passivo)

# 2. Force the upper boundaries to be NaN wherever the original lines are NaN
# (This ensures the upper boundary doesn't fall back to line_passivo values)
sombra_ativo = np.where(np.isnan(linha_ativo), np.nan, sombra_ativo)
sombra_carteira = np.where(np.isnan(linha_carteira), np.nan, sombra_carteira)

# Truncar as linhas base
base_passivo_ativo = np.where(np.isnan(linha_ativo), np.nan, linha_passivo)
base_passivo_carteira = np.where(np.isnan(linha_carteira), np.nan, linha_passivo)

# 3. Convert arrays to object lists and replace np.nan with None
# Plotly treats 'None' as a strict data gap and stops drawing completely.
sombra_ativo_lista = [None if np.isnan(v) else v for v in sombra_ativo]
sombra_carteira_lista = [None if np.isnan(v) else v for v in sombra_carteira]

base_ativo_lista = [None if np.isnan(v) else v for v in base_passivo_ativo]
base_carteira_lista = [None if np.isnan(v) else v for v in base_passivo_carteira]

eixo_x = anos

fig = go.Figure()

# Linha base
fig.add_trace(go.Scatter(
    x=eixo_x,#resultado_plano["ano"],
    y=base_ativo_lista,
    mode="lines",
    line=dict(color="rgba(0,0,0,0)"), showlegend=False, hoverinfo="skip"
))

# Primeira sombra
fig.add_trace(go.Scatter(
    x=eixo_x,#resultado_plano["ano"],
    y=sombra_ativo_lista,
    customdata=excesso_ativo,
    hovertemplate="%{customdata:,.0f}",
    mode='lines',
    fill="tonexty",
    fillcolor="rgba(27,119,90,0.12)",
    line=dict(color="rgba(0,0,0,0)"),
    name="Excesso ativo"
))

# Repete linha base
fig.add_trace(go.Scatter(
    x=eixo_x,#resultado_plano["ano"],
    y=base_carteira_lista,
    mode="lines",
    line=dict(color="rgba(0,0,0,0)"), showlegend=False, hoverinfo="skip"
))

# Segunda sombra
fig.add_trace(go.Scatter(
    x=eixo_x,#resultado_plano["ano"],
    y=sombra_carteira_lista,
    customdata=excesso_carteira,
    hovertemplate="%{customdata:,.0f}",
    mode="lines",
    fill="tonexty",
    fillcolor="rgba(29,158,117,0.2)",
    line=dict(color="rgba(0,0,0,0)"),
    name="Excesso carteira"
))

# Agora as linhas de verdade
fig.add_trace(go.Scatter(
    x=eixo_x,#resultado_plano["ano"],
    y=linha_ativo,
    name="VP Ativo acumulado",
    mode="lines+markers",
    line=dict(color="#378ADD", width=2),
    marker=dict(size=6),
))

fig.add_trace(go.Scatter(
    x=eixo_x,#resultado_plano["ano"],
    y=linha_passivo,
    name="VP Passivo acumulado",
    mode="lines+markers",
    line=dict(color="#D85A30", width=2, dash="dash"),
    marker=dict(size=6),
))

fig.add_trace(go.Scatter(
    x=eixo_x,
    y=linha_carteira,
    name="VP Ativo acumulado (Carteira)",
    mode="lines+markers",
    line=dict(color="#1B775A", width=2, dash="dot"),
    marker=dict(size=6),
))

fig.update_layout(
    xaxis_title="Ano",
    yaxis_title="Valor Presente",
    legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1),
    margin=dict(l=0, r=0, t=30, b=0),
    height=380,
    hovermode="x unified",
    yaxis=dict(tickformat=",.0f"),
)

st.plotly_chart(fig, width='stretch')

# ── Tabela detalhada ───────────────────────────────────────────────────────────
st.subheader("Detalhe por Ano")

tabela = resultado_plano.merge(
    resultado_plano_carteira[["ano", "acumulado_ativo", "excesso_ativo", "flag_excesso"]],
    on="ano",
    how="left",
    suffixes=("", " (Carteira)"),
)[["ano", "acumulado_passivo",
   "acumulado_ativo", "excesso_ativo", "flag_excesso",
   "acumulado_ativo (Carteira)", "excesso_ativo (Carteira)", "flag_excesso (Carteira)"]]

tabela.columns = [
    "Ano", 
    "VP Passivo acumulado",
    "VP Ativo acumulado", "Excesso acumulado", "Ativo > Passivo",
    "VP Ativo acumulado (Carteira)", "Excesso acumulado (Carteira)", "Ativo > Passivo (Carteira)",
]

st.dataframe(
    tabela.style.format({
        "VP Ativo (ano)":    lambda x: f"{x:,.2f}".replace(",", "X").replace(".", ",").replace("X", "."),
        "VP Ativo acumulado":    lambda x: f"{x:,.0f}".replace(",", "X").replace(".", ",").replace("X", "."),
        "VP Passivo (ano)":  lambda x: f"{x:,.0f}".replace(",", "X").replace(".", ",").replace("X", "."),
        "VP Passivo acumulado":  lambda x: f"{x:,.0f}".replace(",", "X").replace(".", ",").replace("X", "."),
        "Excesso acumulado":     lambda x: f"{x:,.0f}".replace(",", "X").replace(".", ",").replace("X", "."),
        "VP Ativo acumulado (Carteira)":    lambda x: f"{x:,.0f}".replace(",", "X").replace(".", ",").replace("X", "."),
        "Excesso acumulado (Carteira)":     lambda x: f"{x:,.0f}".replace(",", "X").replace(".", ",").replace("X", "."),
        ##"{:,.2f}", decimal=",", thousands="."
    }    ).map(
        lambda v: "color: #1D9E75; font-weight:600" if v is True
        else ("color: #D85A30; font-weight:600" if v is False else ""),
        subset=["Ativo > Passivo"],
    ),
    width='stretch',
    hide_index=True,
    height=555,
)

# ── Títulos ───────────────────────────────────────────────────────────────────
st.divider()

st.subheader("Títulos do Plano")

titulos_plano = df[titulos_filtro].copy()
titulos_carteira = df_carteira[titulos_carteira_filtro].copy()

mapa_plano = mapa[mapa["numero_plano"] == plano].copy()
carteira_plano = carteira[carteira["numero_plano"] == plano].copy()
carteira_plano["taxa"] = carteira_plano["taxa"].round(5)

qtd_agg = (
    mapa_plano
    .groupby(["ISIN", "vencimento", "taxa"])["quantidade"]
    .sum()
    .reset_index()
)

vp_agg = (
    titulos_plano
    .groupby(["ISIN", "vencimento", #"quantidade",
               "taxa"])
               [["vp_curva", #"vp_curva_total",
                "vp_ativo"]]#, "vp_ativo_total"]]
    .sum()
    .reset_index()
)

tabela_titulos = vp_agg.merge(qtd_agg, on=["ISIN", "vencimento", "taxa"])

qtd_carteira_agg = (
    carteira_plano
    .groupby(["ISIN", "vencimento", "taxa"])["quantidade"]
    .sum()
    .reset_index()
)

vp_carteira_agg = (
    titulos_carteira
    .groupby(["ISIN", "vencimento", #"quantidade",
               "taxa"])
               [["vp_curva", "vp_ativo"]]
    .sum()
    .reset_index()
)

tabela_carteira = vp_carteira_agg.merge(
    qtd_carteira_agg, on=["ISIN", "vencimento", "taxa"])

tabela_conjunta = tabela_titulos.merge(
    tabela_carteira,
    on=["ISIN", "vencimento", "taxa"],
    how="outer",
    suffixes=("", " (Carteira)"),
)

colunas_preencher = ["quantidade", "vp_curva", "vp_ativo",
                     "quantidade (Carteira)", "vp_curva (Carteira)", "vp_ativo (Carteira)"]
tabela_conjunta[colunas_preencher] = tabela_conjunta[colunas_preencher].fillna(0)

#tabela_titulos.sort_values(by=["vencimento","taxa","quantidade"], inplace=True)
#tabela_titulos = tabela_conjunta.sort_values(
#    by=["vencimento","taxa","quantidade"],
#    inplace=False) #inplace=True retorna None e modifica o original

# Ordena por vencimento (convertido para data), depois taxa e ISIN
tabela_titulos = ordenar_titulos(tabela_conjunta)

tabela_titulos["PU curva"] = tabela_titulos["vp_curva"] / tabela_titulos["quantidade"]
tabela_titulos["PU ativo"] = tabela_titulos["vp_ativo"] / tabela_titulos["quantidade"]
tabela_titulos["Ajuste"] = tabela_titulos["vp_ativo"] - tabela_titulos["vp_curva"]
tabela_titulos["Ajuste (Carteira)"] = tabela_titulos["vp_ativo (Carteira)"] - tabela_titulos["vp_curva (Carteira)"]

tabela_titulos = tabela_titulos[[
    "ISIN", "vencimento", "taxa",
    "quantidade", "vp_curva", "vp_ativo",
    "quantidade (Carteira)", "vp_curva (Carteira)", "vp_ativo (Carteira)",
    "PU curva", "PU ativo",
    "Ajuste", "Ajuste (Carteira)"]]

tabela_titulos.columns = [
    "ISIN", "Vencimento", "Taxa", "Qtde. usada", "VP Curva", "VP Taxa Atuarial",
    "Qtde. (Carteira)", "VP Curva (Carteira)", "VP Taxa Atuarial (Carteira)",
    "PU curva", "PU Taxa Atuarial",
    "Ajuste", "Ajuste (Carteira)"
]

def vermelho_se_negativo(val):
    """Retorna estilo vermelho se o valor for negativo."""
    try:
        return "color: #900; background-color: #ffe6e6; font-weight: bold;" if val < 0 else ""
    except (ValueError, TypeError):
        return ""

destacar_negativos = st.toggle(
    "Destacar ajustes negativos em vermelho",
    value=True,
    key="destacar_negativos_titulos",
)

def destacar_linha_ajuste_negativo(row):
    """Aplica cor de fundo/texto na linha inteira se 'Ajuste' for negativo."""
    if row["Ajuste"] < 0 or row["Ajuste (Carteira)"] < 0:
        estilo = "color: #b34700; background-color: #ffe6b3; font-weight: bold;"
    else:
        estilo = ""
    return [estilo] * len(row)

titulos_styler = tabela_titulos.style.format({
    "PU Taxa Atuarial": lambda x: f"{x:,.2f}".replace(",", "X").replace(".", ",").replace("X", "."),
    "PU curva": lambda x: f"{x:,.2f}".replace(",", "X").replace(".", ",").replace("X", "."),
    "VP Curva":       lambda x: f"{x:,.2f}".replace(",", "X").replace(".", ",").replace("X", "."),
    "VP Curva (Carteira)": lambda x: f"{x:,.2f}".replace(",", "X").replace(".", ",").replace("X", "."),
    "VP Taxa Atuarial":       lambda x: f"{x:,.2f}".replace(",", "X").replace(".", ",").replace("X", "."),
    "VP Taxa Atuarial (Carteira)": lambda x: f"{x:,.2f}".replace(",", "X").replace(".", ",").replace("X", "."),
    "Ajuste":     lambda x: f"{x:,.2f}".replace(",", "X").replace(".", ",").replace("X", "."),
    "Ajuste (Carteira)": lambda x: f"{x:,.2f}".replace(",", "X").replace(".", ",").replace("X", "."),
    "Taxa Curva":     lambda x: f"{x:.4%}".replace(".", ","),
    "Qtde. usada":     lambda x: f"{x:,.2f}".replace(",", "X").replace(".", ",").replace("X", "."),
    "Qtde. (Carteira)": lambda x: f"{x:,.2f}".replace(",", "X").replace(".", ",").replace("X", "."),
    "Taxa": lambda x: f"{x:.3%}".replace(".", ","),
    "Vencimento": lambda x: pd.to_datetime(x).strftime("%d/%m/%Y") if pd.notna(x) else "",
})

if destacar_negativos:
    #titulos_styler = titulos_styler.map(vermelho_se_negativo, subset=["Ajuste", "Ajuste (Total)"])
    titulos_styler = titulos_styler.apply(destacar_linha_ajuste_negativo, axis=1)

st.dataframe(
    titulos_styler,
    width='stretch',
    hide_index=True,
    height=555,
)

# ── Download ───────────────────────────────────────────────────────────────────
st.divider()

df_ajuste = pd.DataFrame([{
    "numero_plano":        plano,
    "grupo":               grupo if analise_por_grupo else "(todos)",
    "data_base":           DATA_BASE.date(),
    "vp_curva":            vp_curva_plano,
    "vp_ativo":            vp_ativo_plano,
    "ajuste":              ajuste_plano,
    "vp_curva_carteira":   vp_curva_plano_carteira,
    "vp_ativo_carteira":   vp_ativo_plano_carteira
}])

# vencimento como data (não texto) e títulos ordenados por vencimento e taxa
df_plano = preparar_exportacao_titulos(df[titulos_filtro])
df_plano_carteira = preparar_exportacao_titulos(df_carteira[titulos_carteira_filtro])

buf = io.BytesIO()
with pd.ExcelWriter(buf, engine="openpyxl") as writer:
    df_plano.to_excel(writer, sheet_name="Titulos",   index=False)
    df_plano_carteira.to_excel(writer, sheet_name="Titulos Carteira", index=False)
    passivo_plano.to_excel(writer, sheet_name="Passivo", index=False)
    resultado_plano.to_excel(writer, sheet_name="Resultado", index=False)
    resultado_plano_carteira.to_excel(writer, sheet_name="Resultado Carteira", index=False)
    df_ajuste.to_excel(writer, sheet_name="Ajuste", index=False)
    formatar_colunas_data(writer.sheets["Titulos"])
    formatar_colunas_data(writer.sheets["Titulos Carteira"])

st.download_button(
    label="⬇️  Baixar resultado completo (.xlsx)",
    data=buf.getvalue(),
    file_name=nome_arquivo,
    mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
)
