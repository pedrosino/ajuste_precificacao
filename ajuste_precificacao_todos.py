"""Streamlit — Ajuste de Precificação de Plano de Benefícios"""

from datetime import datetime
import io
import numpy as np
import pandas as pd
import plotly.graph_objects as go
import streamlit as st

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
    if casas == 0:
        return f"{x:,.0f}".replace(",", "X").replace(".", ",").replace("X", ".")
    else:
        return f"{x:,.{casas}f}".replace(",", "X").replace(".", ",").replace("X", ".")

# data-base
ANO_BASE = 2025
DATA_BASE = pd.Timestamp(f"{ANO_BASE}-12-31")


st.set_page_config(
    page_title="Ajuste de Precificação",
    page_icon="📊",
    layout="wide",
)

st.title("📊 Ajuste de Precificação")
st.caption("Cálculo do valor presente do ativo e passivo do plano de benefícios")

# ─────────────────────────────────────────────
# 1. UPLOAD
# ─────────────────────────────────────────────

uploaded = st.file_uploader(
    "Selecione o arquivo Excel",
    type=["xlsx", "xls"],
    help="Arquivo com as abas: Titulos, titulos_plano, Passivo, dias_uteis",
)

if not uploaded:
    st.info("Carregue o arquivo para começar.")
    st.stop()

# ─────────────────────────────────────────────
# 2. LER ABAS
# ─────────────────────────────────────────────

@st.cache_data(show_spinner="Lendo planilha…")
def load_workbook(file_bytes: bytes):
    buf = io.BytesIO(file_bytes)
    fluxo      = pd.read_excel(buf, sheet_name="Titulos")
    mapa       = pd.read_excel(buf, sheet_name="titulos_plano")
    passivo    = pd.read_excel(buf, sheet_name="Passivo")
    dias_uteis = pd.read_excel(buf, sheet_name="dias_uteis", header=None, names=["data"])
    return fluxo, mapa, passivo, dias_uteis

try:
    fluxo, mapa, passivo, dias_uteis = load_workbook(uploaded.read())
except Exception as e:
    st.error(f"Erro ao ler o arquivo: {e}")
    st.stop()

# ─────────────────────────────────────────────
# 4. PREPARAR DADOS (CACHEADO)
# ─────────────────────────────────────────────

@st.cache_data(show_spinner="Calculando…")
def calcular_precificacao(fluxo, mapa, passivo, dias_uteis):
    """Realiza todos os cálculos de precificação uma única vez e cacheia o resultado."""
    
    # datas
    fluxo = fluxo.copy()
    passivo = passivo.copy()
    dias_uteis = dias_uteis.copy()
    
    fluxo["data_pgto"] = pd.to_datetime(fluxo["data_pgto"])
    dias_uteis["data"] = pd.to_datetime(dias_uteis["data"])
    dias_uteis = dias_uteis.sort_values("data").reset_index(drop=True)
    dias_uteis["dia_indice"] = dias_uteis.index
    mapa_du = dias_uteis.set_index("data")["dia_indice"]

    passivo['taxa_dia'] = (1 + passivo['taxa']) ** (1 / 252) - 1
    taxas_plano = passivo[['numero_plano','taxa','taxa_dia']].drop_duplicates(subset='numero_plano')

    # filtrar fluxo pelo plano
    df = fluxo.merge(mapa, on="ISIN", how="inner")
    if df.empty:
        return None

    df["fluxo"] = df["valor"] * df["quantidade"]
    df["fluxo_total"] = df["valor"] * df["qtde_total"]
    df["taxa_diaria"] = (1 + df["taxa"]) ** (1 / 252) - 1

    # função para pegar último dia útil anterior
    def get_dia_base(data):
        """Busca o dia na lista de dias úteis"""
        if data in mapa_du.index:
            return mapa_du.loc[data]
        anteriores = mapa_du.loc[:data]
        return anteriores.iloc[-1] if len(anteriores) else np.nan

    df["data_base"] = DATA_BASE
    dia_base = get_dia_base(DATA_BASE)

    df["dia_pgto"] = df["data_pgto"].map(mapa_du)
    df = df.dropna(subset=["dia_pgto"])
    df["prazo_du"] = df["dia_pgto"] - dia_base
    df = df[df["prazo_du"] > 0].copy()
    df["ano"] = df["data_pgto"].dt.year

    # VPs
    df["vp_curva"]  = df["fluxo"] / (1 + df["taxa_diaria"]) ** df["prazo_du"]
    df["vp_curva_total"] = df["fluxo_total"] / (1 + df["taxa_diaria"]) ** df["prazo_du"]
    df['taxa_dia'] = df['numero_plano'].map(taxas_plano.set_index('numero_plano')['taxa_dia'])
    df["vp_ativo"]  = df["fluxo"] / (1 + df["taxa_dia"]) ** df["prazo_du"]
    df["vp_ativo_total"] = df["fluxo_total"] / (1 + df["taxa_dia"]) ** df["prazo_du"]

    vp_curva = (
        df
        .groupby(['numero_plano', 'data_base'])['vp_curva']
        .sum()
        .reset_index()
    )

    vp_curva_total = (
        df
        .groupby(['numero_plano', 'data_base'])['vp_curva_total']
        .sum()
        .reset_index()
    )

    vp_taxa_atuarial = (
        df
        .groupby(['numero_plano', 'data_base'])['vp_ativo']
        .sum()
        .reset_index()
    )

    vp_taxa_atuarial_total = (
        df
        .groupby(['numero_plano', 'data_base'])['vp_ativo_total']
        .sum()
        .reset_index()
    )

    ajuste = vp_curva.merge(
        vp_taxa_atuarial,
        on=['numero_plano', 'data_base'],
        how='inner'
    )

    ajuste_total = vp_curva_total.merge(
        vp_taxa_atuarial_total,
        on=['numero_plano', 'data_base'],
        how='inner'
    )

    ajuste['ajuste'] = ajuste['vp_ativo'] - ajuste['vp_curva']
    ajuste_total['ajuste'] = ajuste_total['vp_ativo_total'] - ajuste_total['vp_curva_total']

    vp_ativo = (
        df
        .groupby(['numero_plano', 'ano'])['vp_ativo']
        .sum()
        .reset_index()
    )

    vp_ativo_total = (
        df
        .groupby(['numero_plano', 'ano'])['vp_ativo_total']
        .sum()
        .reset_index()
    )

    vp_ativo['acumulado_ativo'] = (
        vp_ativo
        .sort_values(['numero_plano', 'ano'], ascending=[True, False])
        .groupby('numero_plano')['vp_ativo']
        .cumsum()
    )

    vp_ativo = vp_ativo.sort_values(['numero_plano', 'ano'])

    vp_ativo_total['acumulado_ativo'] = (
        vp_ativo_total
        .sort_values(['numero_plano', 'ano'], ascending=[True, False])
        .groupby('numero_plano')['vp_ativo_total']
        .cumsum()
    )

    vp_ativo_total = vp_ativo_total.sort_values(['numero_plano', 'ano'])

    # Duração do ativo
    df["ponderado"] = df["vp_ativo"] * df["prazo_du"]
    df["ponderado_total"] = df["vp_ativo_total"] * df["prazo_du"]

    df["prazo_anos"] = (df["ano"] - ANO_BASE) - 0.5

    df["ponderado_anos"] = df["vp_ativo"] * df["prazo_anos"]
    df["ponderado_anos_total"] = df["vp_ativo_total"] * df["prazo_anos"]

    ponderado_ativo = (
        df
        .groupby(['numero_plano', 'data_base'])['ponderado']
        .sum()
        .reset_index()
    )

    ponderado_ativo_total = (
        df
        .groupby(['numero_plano', 'data_base'])['ponderado_total']
        .sum()
        .reset_index()
    )

    ponderado_anos = (
        df
        .groupby(['numero_plano', 'data_base'])['ponderado_anos']
        .sum()
        .reset_index()
    )

    ponderado_anos_total = (
        df
        .groupby(['numero_plano', 'data_base'])['ponderado_anos_total']
        .sum()
        .reset_index()
    )

    duracao_ativo = ponderado_ativo.merge(
        vp_ativo[vp_ativo['ano'] == 2026],
        on=['numero_plano'],
        how='inner'
    )

    duracao_ativo_total = ponderado_ativo_total.merge(
        vp_ativo_total[vp_ativo_total['ano'] == 2026],
        on=['numero_plano'],
        how='inner'
    )

    duracao_ativo_anos = ponderado_anos.merge(
        vp_ativo[vp_ativo['ano'] == 2026],
        on=['numero_plano'],
        how='inner'
    )

    duracao_ativo_anos_total = ponderado_anos_total.merge(
        vp_ativo_total[vp_ativo_total['ano'] == 2026],
        on=['numero_plano'],
        how='inner'
    )

    duracao_ativo['duracao'] = duracao_ativo['ponderado'] / duracao_ativo['acumulado_ativo']
    duracao_ativo_total['duracao'] = duracao_ativo_total['ponderado_total'] / duracao_ativo_total['acumulado_ativo']
    duracao_ativo_anos['duracao'] = duracao_ativo_anos['ponderado_anos'] / duracao_ativo_anos['acumulado_ativo']
    duracao_ativo_anos_total['duracao'] = duracao_ativo_anos_total['ponderado_anos_total'] / duracao_ativo_anos_total['acumulado_ativo']

    # garantir tipos
    passivo['ano'] = passivo['ano'].astype(int)

    # Prazo no meio do ano
    passivo['prazo'] = (passivo['ano'] - ANO_BASE) - 0.5

    # Valor presente
    passivo['vp_passivo'] = passivo['valor'] / (1 + passivo['taxa']) ** passivo['prazo']

    # agregar
    vp_passivo = (
        passivo
        .groupby(['numero_plano', 'ano'])['vp_passivo']
        .sum()
        .reset_index()
    )

    # Acumulado para a frente
    vp_passivo['acumulado_passivo'] = (
        vp_passivo
        .sort_values(['numero_plano', 'ano'], ascending=[True, False])
        .groupby('numero_plano')['vp_passivo']
        .cumsum()
    )

    # voltar ordem crescente
    vp_passivo = vp_passivo.sort_values(['numero_plano', 'ano'])

    # ── Merge resultado ────────────────────────────────────────────
    resultado = vp_passivo.merge(
        vp_ativo,
        on=["numero_plano","ano"],
        how="left",
    )
    resultado["excesso_ativo"] = resultado["acumulado_ativo"] - resultado["acumulado_passivo"]
    resultado["flag_excesso"]  = resultado["acumulado_ativo"] > resultado["acumulado_passivo"]

    resultado_total = vp_passivo.merge(
        vp_ativo_total,
        on=["numero_plano","ano"],
        how="left",
    )
    resultado_total["excesso_ativo"] = resultado_total["acumulado_ativo"] - resultado_total["acumulado_passivo"]
    resultado_total["flag_excesso"]  = resultado_total["acumulado_ativo"] > resultado_total["acumulado_passivo"]

    print(f"Calculado {datetime.now()}")
    
    return {
        'vp_curva': vp_curva,
        'vp_curva_total': vp_curva_total,
        'vp_ativo': vp_ativo,
        'vp_ativo_total': vp_ativo_total,
        'vp_passivo': vp_passivo,
        'resultado': resultado,
        'resultado_total': resultado_total,
        'duracao_ativo': duracao_ativo,
        'duracao_ativo_total': duracao_ativo_total,
        'duracao_ativo_anos': duracao_ativo_anos,
        'duracao_ativo_anos_total': duracao_ativo_anos_total,
        'taxas_plano': taxas_plano,
        'passivo': passivo,
        'df': df,
    }

# Executar cálculos cacheados
calc_result = calcular_precificacao(fluxo, mapa, passivo, dias_uteis)

if calc_result is None:
    st.error("Nenhum título encontrado.")
    st.stop()

# Desempacotar resultados
vp_curva = calc_result['vp_curva']
vp_curva_total = calc_result['vp_curva_total']
vp_ativo = calc_result['vp_ativo']
vp_ativo_total = calc_result['vp_ativo_total']
vp_passivo = calc_result['vp_passivo']
resultado = calc_result['resultado']
resultado_total = calc_result['resultado_total']
duracao_ativo = calc_result['duracao_ativo']
duracao_ativo_total = calc_result['duracao_ativo_total']
duracao_ativo_anos = calc_result['duracao_ativo_anos']
duracao_ativo_anos_total = calc_result['duracao_ativo_anos_total']
taxas_plano = calc_result['taxas_plano']
passivo = calc_result['passivo']
df = calc_result['df']

# ─────────────────────────────────────────────
# 3. SELEÇÃO DE PLANO
# ─────────────────────────────────────────────

planos = sorted(mapa["numero_plano"].dropna().unique())

col1, col2, col3 = st.columns([2, 1, 1])
with col1:
    plano = st.selectbox("Plano", planos)
with col2:
    ano_base_select = st.number_input("Ano-base", value=2025, min_value=2000, max_value=2100, step=1)
with col3:
    st.write("")
    st.write("")
    calcular = st.button("Filtrar", type="primary", width='stretch')

if not calcular:
    st.stop()

passivo_plano = passivo[passivo["numero_plano"] == plano].copy()
if passivo_plano.empty:
    st.error(f"Nenhum dado de passivo encontrado para o plano {plano}.")
    st.stop()

#vp_curva_total = df["vp_curva"].sum()
#vp_ativo_total = df["vp_ativo"].sum()
#ajuste_total   = vp_ativo_total - vp_curva_total

vp_ativo_plano = vp_ativo[vp_ativo["numero_plano"] == plano]["vp_ativo"].sum()
#vp_ativo plano = vp_ativo.loc[vp_ativo["numero_plano"] == plano, "acumulado_ativo"].iloc[0]
vp_curva_plano = vp_curva[vp_curva["numero_plano"] == plano]["vp_curva"].sum()
ajuste_plano = vp_ativo_plano - vp_curva_plano
taxa_plano = taxas_plano.loc[taxas_plano["numero_plano"] == plano, "taxa"].iloc[0]
resultado_plano = resultado[resultado["numero_plano"] == plano]

vp_ativo_plano_total = vp_ativo_total[vp_ativo_total["numero_plano"] == plano]["vp_ativo_total"].sum()
vp_curva_plano_total = vp_curva_total[vp_curva_total["numero_plano"] == plano]["vp_curva_total"].sum()
ajuste_plano_total = vp_ativo_plano_total - vp_curva_plano_total
resultado_plano_total = resultado_total[resultado_total["numero_plano"] == plano]
duracao_ativo_plano = duracao_ativo.loc[duracao_ativo["numero_plano"] == plano, "duracao"].iloc[0]
duracao_ativo_plano_total = duracao_ativo_total.loc[duracao_ativo_total["numero_plano"] == plano, "duracao"].iloc[0]
duracao_ativo_plano_anos = duracao_ativo_anos.loc[duracao_ativo_anos["numero_plano"] == plano, "duracao"].iloc[0]
duracao_ativo_plano_anos_total = duracao_ativo_anos_total.loc[duracao_ativo_anos_total["numero_plano"] == plano, "duracao"].iloc[0]
#duracao_curva_plano = duracao_curva[duracao_curva["numero_plano"] == plano]["duracao"].iloc[0]

# ─────────────────────────────────────────────
# 5. EXIBIR RESULTADOS
# ─────────────────────────────────────────────

st.divider()
st.subheader(f"Plano {plano}  ·  Base 31/12/{ANO_BASE}")

def kpi_card(titulo, valor, delta=None):
    """Cria um card de KPI com título, valor e delta opcional."""
    cor_delta = "green" if delta and delta >= 0 else "red"
    sinal = "+" if delta and delta >= 0 else ""

    delta_html = ""
    if delta is not None:
        delta_html = f'<div style="color:{cor_delta}; font-size:14px;">{sinal}{perc_br(delta)}</div>'

    return f"""
    <div style="
        padding: 3px 4px;
        border-radius: 10px;
        margin-bottom: 5px;
    ">
        <div style="font-size:13px; color:dark-gray;">{titulo}</div>
        <div style="font-size:18px; font-weight:600;">{valor}</div>
        {delta_html}
    </div>
    """

# métricas

#m1, m2, m0 = st.columns(3)
vp_passivo_total = passivo_plano['vp_passivo'].sum()
#m1.metric("VP Ativo (tx. atuarial)", moeda_br(vp_ativo_total))
#m1.metric("VP Passivo",              moeda_br(vp_passivo_total))
#excesso = vp_ativo_total - vp_passivo_total  # usando vp_curva como proxy do passivo total
#m3.metric("Excesso ativo − passivo", moeda_br(resultado['excesso_ativo'].iloc[0]))
#m3.metric("Ajuste de precificação",  moeda_br(ajuste_total), delta=perc_br(ajuste_total/vp_passivo_total*100))
#m2.metric("Taxa atuarial",           perc_br(taxa_plano*100))


c1, c2, c3 = st.columns(3)
c1.markdown(kpi_card("VP Passivo", moeda_br(vp_passivo_total)), unsafe_allow_html=True)
c2.markdown(kpi_card("Taxa atuarial", perc_br(taxa_plano*100)), unsafe_allow_html=True)

c4, c5, c6 = st.columns(3)
c4.markdown(kpi_card("VP Ativo (taxa atuarial)", moeda_br(vp_ativo_plano)), unsafe_allow_html=True)
c5.markdown(kpi_card("VP Ativo (taxa curva)", moeda_br(vp_curva_plano)), unsafe_allow_html=True)
c6.markdown(kpi_card("Ajuste de precificação", moeda_br(ajuste_plano), (ajuste_plano/vp_passivo_total*100)), unsafe_allow_html=True)

c7, c8, c9 = st.columns(3)
c7.markdown(kpi_card("VP Ativo (taxa atuarial) - Total", moeda_br(vp_ativo_plano_total)), unsafe_allow_html=True)
c8.markdown(kpi_card("VP Ativo (taxa curva) - Total", moeda_br(vp_curva_plano_total)), unsafe_allow_html=True)
c9.markdown(kpi_card("Ajuste de precificação - Total", moeda_br(ajuste_plano_total), (ajuste_plano_total/vp_passivo_total*100)), unsafe_allow_html=True)

c10, c11, c12 = st.columns(3)
c10.markdown(kpi_card("Duração do ativo (dias)", f"{formatar_numero(duracao_ativo_plano, 2)} dias ({formatar_numero(duracao_ativo_plano/365.25, 4)} anos)"), unsafe_allow_html=True)
c11.markdown(kpi_card("Duração do ativo (dias) - Total", f"{formatar_numero(duracao_ativo_plano_total, 2)} dias ({formatar_numero(duracao_ativo_plano_total/365.25, 4)} anos)"), unsafe_allow_html=True)
#c12.markdown(kpi_card("Duração do ativo (taxa curva)", f"{formatar_numero(duracao_curva_plano, 2)} dias"), unsafe_allow_html=True)

c13, c14, c15 = st.columns(3)
c13.markdown(kpi_card("Duração do ativo (anos)", f"{formatar_numero(duracao_ativo_plano_anos, 4)} anos"), unsafe_allow_html=True)
c14.markdown(kpi_card("Duração do ativo (anos) - Total", f"{formatar_numero(duracao_ativo_plano_anos_total, 4)} anos"), unsafe_allow_html=True)

#m3, m4, m5 = st.columns(3)
#m3.metric("VP Ativo (tx. atuarial)", moeda_br(vp_ativo_total))
#m4.metric("VP Ativo (tx. curva)", moeda_br(vp_curva_total))
#m5.metric("Ajuste de precificação",  moeda_br(ajuste_total), delta=f"{perc_br(ajuste_total/vp_passivo_total*100)} (do passivo)")


st.divider()

# ── Gráfico ────────────────────────────────────────────────────────────────────
st.subheader("VP Acumulado — Ativo vs. Passivo")

fig = go.Figure()

fig.add_trace(go.Scatter(
    x=resultado_plano["ano"],
    y=resultado_plano["acumulado_ativo"],
    name="VP Ativo acumulado",
    mode="lines+markers",
    line=dict(color="#378ADD", width=2),
    marker=dict(size=6),
))

fig.add_trace(go.Scatter(
    x=resultado_plano["ano"],
    y=resultado_plano["acumulado_passivo"],
    name="VP Passivo acumulado",
    mode="lines+markers",
    line=dict(color="#D85A30", width=2, dash="dash"),
    marker=dict(size=6),
))

fig.add_trace(go.Scatter(
    x=resultado_plano_total["ano"],
    y=resultado_plano_total["acumulado_ativo"],
    name="VP Ativo acumulado (Total)",
    mode="lines+markers",
    line=dict(color="#1B775A", width=2),
    marker=dict(size=6),
))

# área de excesso (quando ativo > passivo)
excesso_pos = resultado_plano[resultado_plano["flag_excesso"]]
if not excesso_pos.empty:
    fig.add_trace(go.Scatter(
        x=pd.concat([excesso_pos["ano"], excesso_pos["ano"].iloc[::-1]]),
        y=pd.concat([excesso_pos["acumulado_ativo"], excesso_pos["acumulado_passivo"].iloc[::-1]]),
        fill="toself",
        fillcolor="rgba(29,158,117,0.12)",
        line=dict(color="rgba(0,0,0,0)"),
        name="Excesso de ativo",
        showlegend=True,
    ))

excesso_pos_total = resultado_plano_total[resultado_plano_total["flag_excesso"]]
if not excesso_pos_total.empty:
    fig.add_trace(go.Scatter(
        x=pd.concat([excesso_pos_total["ano"], excesso_pos_total["ano"].iloc[::-1]]),
        y=pd.concat([excesso_pos_total["acumulado_ativo"], excesso_pos_total["acumulado_passivo"].iloc[::-1]]),
        fill="toself",
        fillcolor="rgba(27,119,90,0.12)",
        line=dict(color="rgba(0,0,0,0)"),
        name="Excesso de ativo (Total)",
        showlegend=True,
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
    resultado_plano_total[["ano", "acumulado_ativo", "excesso_ativo", "flag_excesso"]],
    on="ano",
    how="left",
    suffixes=("", " (Total)"),
)[["ano", "acumulado_passivo",
   "acumulado_ativo", "excesso_ativo", "flag_excesso",
   "acumulado_ativo (Total)", "excesso_ativo (Total)", "flag_excesso (Total)"]]

#tabela = resultado_plano[[
#    "ano",
#    "acumulado_passivo", 
#    "acumulado_ativo",
#    "excesso_ativo", "flag_excesso",
#]].copy()
tabela.columns = [
    "Ano", 
    "VP Passivo acum.",
    "VP Ativo acum.", "Excesso acum.", "Ativo > Passivo",
    "VP Ativo acum. (Total)", "Excesso acum. (Total)", "Ativo > Passivo (Total)",
]

st.dataframe(
    tabela.style.format({
        "VP Ativo (ano)":    lambda x: f"{x:,.2f}".replace(",", "X").replace(".", ",").replace("X", "."),
        "VP Ativo acum.":    lambda x: f"{x:,.0f}".replace(",", "X").replace(".", ",").replace("X", "."),
        "VP Passivo (ano)":  lambda x: f"{x:,.0f}".replace(",", "X").replace(".", ",").replace("X", "."),
        "VP Passivo acum.":  lambda x: f"{x:,.0f}".replace(",", "X").replace(".", ",").replace("X", "."),
        "Excesso acum.":     lambda x: f"{x:,.0f}".replace(",", "X").replace(".", ",").replace("X", "."),
        "VP Ativo acum. (Total)":    lambda x: f"{x:,.0f}".replace(",", "X").replace(".", ",").replace("X", "."),
        "Excesso acum. (Total)":     lambda x: f"{x:,.0f}".replace(",", "X").replace(".", ",").replace("X", "."),
        ##"{:,.2f}", decimal=",", thousands="."
    }    ).map(
        lambda v: "color: #1D9E75; font-weight:600" if v is True
        else ("color: #D85A30; font-weight:600" if v is False else ""),
        subset=["Ativo > Passivo"],
    ),
    width='stretch',
    hide_index=True,
)

# ── Download ───────────────────────────────────────────────────────────────────
st.divider()

df_ajuste = pd.DataFrame([{
    "numero_plano":        plano,
    "data_base":           DATA_BASE.date(),
    "vp_curva":            vp_curva_plano,
    "vp_ativo":            vp_ativo_plano,
    "ajuste":              ajuste_plano,
}])

df_plano = df[df["numero_plano"] == plano]

buf = io.BytesIO()
with pd.ExcelWriter(buf, engine="openpyxl") as writer:
    df_plano.to_excel(writer,        sheet_name="Titulos",   index=False)
    passivo_plano.to_excel(writer, sheet_name="Passivo",   index=False)
    resultado_plano.to_excel(writer, sheet_name="Resultado", index=False)
    df_ajuste.to_excel(writer, sheet_name="Ajuste",    index=False)

st.download_button(
    label="⬇️  Baixar resultado (.xlsx)",
    data=buf.getvalue(),
    file_name=f"resultado_ajuste_plano_{plano}.xlsx",
    mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
)
