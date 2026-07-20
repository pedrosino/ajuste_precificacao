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
    if pd.isna(x):
        return "—"
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
    help="Arquivo com as abas: Titulos, titulos_plano, Passivo, contas, dias_uteis",
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
    contas     = pd.read_excel(buf, sheet_name="contas")
    dias_uteis = pd.read_excel(buf, sheet_name="dias_uteis", header=None, names=["data"])
    return fluxo, mapa, passivo, contas, dias_uteis

try:
    fluxo, mapa, passivo, contas, dias_uteis = load_workbook(uploaded.read())
except Exception as e:
    st.error(f"Erro ao ler o arquivo: {e}")
    st.stop()

if 'grupo' not in mapa.columns:
    mapa['grupo'] = 'Único'
mapa['grupo'] = mapa['grupo'].fillna('Único').astype(str)

if 'grupo' not in passivo.columns:
    passivo['grupo'] = 'Único'
passivo['grupo'] = passivo['grupo'].fillna('Único').astype(str)

# ─────────────────────────────────────────────
# 4. PREPARAR DADOS (CACHEADO)
# ─────────────────────────────────────────────

@st.cache_data(show_spinner="Calculando…")
def calcular_precificacao(fluxo, mapa, passivo, contas, dias_uteis):
    """Realiza todos os cálculos de precificação uma única vez e cacheia o resultado."""
    
    # datas
    fluxo = fluxo.copy()
    mapa = mapa.copy()
    passivo = passivo.copy()
    contas = contas.copy()
    dias_uteis = dias_uteis.copy()
    
    fluxo["data_pgto"] = pd.to_datetime(fluxo["data_pgto"])
    dias_uteis["data"] = pd.to_datetime(dias_uteis["data"])
    dias_uteis = dias_uteis.sort_values("data").reset_index(drop=True)
    dias_uteis["dia_indice"] = dias_uteis.index
    mapa_du = dias_uteis.set_index("data")["dia_indice"]

    if 'grupo' not in passivo.columns:
        passivo['grupo'] = 'Único'
    passivo['grupo'] = passivo['grupo'].fillna('Único').astype(str)

    if 'grupo' not in mapa.columns:
        mapa['grupo'] = 'Único'
    mapa['grupo'] = mapa['grupo'].fillna('Único').astype(str)

    passivo['taxa_dia'] = (1 + passivo['taxa']) ** (1 / 252) - 1
    taxas_plano = passivo[['numero_plano','taxa','taxa_dia']].drop_duplicates(subset='numero_plano')
    taxas_grupo = passivo[['numero_plano','grupo','taxa','taxa_dia']].drop_duplicates(
        subset=['numero_plano', 'grupo']
    )

    # filtrar fluxo pelo plano/grupo
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

    # Taxa atuarial por plano+grupo (fallback para taxa do plano)
    df = df.merge(
        taxas_grupo[['numero_plano', 'grupo', 'taxa_dia']],
        on=['numero_plano', 'grupo'],
        how='left',
    )
    faltantes_taxa = df['taxa_dia'].isna()
    if faltantes_taxa.any():
        df.loc[faltantes_taxa, 'taxa_dia'] = df.loc[faltantes_taxa, 'numero_plano'].map(
            taxas_plano.set_index('numero_plano')['taxa_dia']
        )

    # VPs
    df["vp_curva"]  = df["fluxo"] / (1 + df["taxa_diaria"]) ** df["prazo_du"]
    df["vp_curva_total"] = df["fluxo_total"] / (1 + df["taxa_diaria"]) ** df["prazo_du"]
    df["vp_ativo"]  = df["fluxo"] / (1 + df["taxa_dia"]) ** df["prazo_du"]
    df["vp_ativo_total"] = df["fluxo_total"] / (1 + df["taxa_dia"]) ** df["prazo_du"]

    def _cumsum_ano(frame, keys, value_col, acum_col='acumulado_ativo'):
        group_keys = [k for k in keys if k != 'ano']
        out = frame.copy()
        out[acum_col] = (
            out
            .sort_values(keys, ascending=[True] * (len(keys) - 1) + [False])
            .groupby(group_keys)[value_col]
            .cumsum()
        )
        return out.sort_values(keys)

    def _vp_ativo_agg(keys_sem_ano, value_col):
        keys = keys_sem_ano + ['ano']
        out = (
            df
            .groupby(keys)[value_col]
            .sum()
            .reset_index()
        )
        return _cumsum_ano(out, keys, value_col)

    def _sum_keys(keys, value_col):
        return (
            df
            .groupby(keys)[value_col]
            .sum()
            .reset_index()
        )

    # Agregações por plano e por grupo
    vp_curva = _sum_keys(['numero_plano', 'data_base'], 'vp_curva')
    vp_curva_total = _sum_keys(['numero_plano', 'data_base'], 'vp_curva_total')
    vp_curva_grupo = _sum_keys(['numero_plano', 'grupo', 'data_base'], 'vp_curva')
    vp_curva_grupo_total = _sum_keys(['numero_plano', 'grupo', 'data_base'], 'vp_curva_total')

    vp_ativo = _vp_ativo_agg(['numero_plano'], 'vp_ativo')
    vp_ativo_total = _vp_ativo_agg(['numero_plano'], 'vp_ativo_total')
    vp_ativo_grupo = _vp_ativo_agg(['numero_plano', 'grupo'], 'vp_ativo')
    vp_ativo_grupo_total = _vp_ativo_agg(['numero_plano', 'grupo'], 'vp_ativo_total')

    # Duração do ativo
    df["ponderado"] = df["vp_ativo"] * df["prazo_du"]
    df["ponderado_total"] = df["vp_ativo_total"] * df["prazo_du"]
    df["prazo_anos"] = (df["ano"] - ANO_BASE) - 0.5
    df["ponderado_anos"] = df["vp_ativo"] * df["prazo_anos"]
    df["ponderado_anos_total"] = df["vp_ativo_total"] * df["prazo_anos"]

    def _duracao_ativo(keys, ponderado_col, vp_frame, vp_acum_col='acumulado_ativo'):
        ponderado = _sum_keys(keys + ['data_base'], ponderado_col)
        base = vp_frame[vp_frame['ano'] == 2026]
        out = ponderado.merge(base, on=keys, how='inner')
        out['duracao'] = out[ponderado_col] / out[vp_acum_col]
        return out

    duracao_ativo = _duracao_ativo(
        ['numero_plano'], 'ponderado', vp_ativo
    )
    duracao_ativo_total = _duracao_ativo(
        ['numero_plano'], 'ponderado_total', vp_ativo_total
    )
    duracao_ativo_anos = _duracao_ativo(
        ['numero_plano'], 'ponderado_anos', vp_ativo
    )
    duracao_ativo_anos_total = _duracao_ativo(
        ['numero_plano'], 'ponderado_anos_total', vp_ativo_total
    )
    duracao_ativo_grupo = _duracao_ativo(
        ['numero_plano', 'grupo'], 'ponderado', vp_ativo_grupo
    )
    duracao_ativo_grupo_total = _duracao_ativo(
        ['numero_plano', 'grupo'], 'ponderado_total', vp_ativo_grupo_total
    )
    duracao_ativo_anos_grupo = _duracao_ativo(
        ['numero_plano', 'grupo'], 'ponderado_anos', vp_ativo_grupo
    )
    duracao_ativo_anos_grupo_total = _duracao_ativo(
        ['numero_plano', 'grupo'], 'ponderado_anos_total', vp_ativo_grupo_total
    )
    # garantir tipos
    passivo['ano'] = passivo['ano'].astype(int)
    passivo['conta_id'] = passivo['conta_id'].astype(int)
    contas['conta_id'] = contas['conta_id'].astype(int)

    # Mapeamento de contas (contar_vp / contar_duracao: 1, -1 ou 0)
    passivo = passivo.merge(
        contas[['conta_id', 'contar_duracao', 'contar_vp']],
        on='conta_id',
        how='left',
    )
    contas_sem_mapa = passivo['contar_vp'].isna() | passivo['contar_duracao'].isna()
    if contas_sem_mapa.any():
        ids_faltantes = sorted(passivo.loc[contas_sem_mapa, 'conta_id'].unique())
        raise ValueError(
            f"conta_id sem mapeamento na aba contas: {ids_faltantes}"
        )

    # Prazo no meio do ano
    passivo['prazo'] = (passivo['ano'] - ANO_BASE) - 0.5

    # Valor presente (apenas contas com contar_vp ≠ 0; sinal conforme mapeamento)
    passivo['vp_passivo'] = (
        (passivo['valor'] * passivo['contar_vp'])
        / (1 + passivo['taxa']) ** passivo['prazo']
    )

    # Duração do passivo (apenas contas com contar_duracao ≠ 0; sinal conforme mapeamento)
    passivo['vp_duracao'] = (
        (passivo['valor'] * passivo['contar_duracao'])
        / (1 + passivo['taxa']) ** passivo['prazo']
    )
    passivo['ponderado_anos_passivo'] = passivo['vp_duracao'] * passivo['prazo']

    def _duracao_passivo_agg(keys):
        out = (
            passivo
            .groupby(keys)
            .agg(
                ponderado_anos_passivo=('ponderado_anos_passivo', 'sum'),
                vp_duracao=('vp_duracao', 'sum'),
            )
            .reset_index()
        )
        out['duracao'] = np.where(
            out['vp_duracao'] != 0,
            out['ponderado_anos_passivo'] / out['vp_duracao'],
            np.nan,
        )
        return out

    duracao_passivo = _duracao_passivo_agg(['numero_plano'])
    duracao_passivo_grupo = _duracao_passivo_agg(['numero_plano', 'grupo'])

    def _vp_passivo_agg(keys):
        out = (
            passivo
            .groupby(keys)['vp_passivo']
            .sum()
            .reset_index()
        )
        group_keys = [k for k in keys if k != 'ano']
        out['acumulado_passivo'] = (
            out
            .sort_values(keys, ascending=[True] * (len(keys) - 1) + [False])
            .groupby(group_keys)['vp_passivo']
            .cumsum()
        )
        return out.sort_values(keys)

    vp_passivo = _vp_passivo_agg(['numero_plano', 'ano'])
    vp_passivo_grupo = _vp_passivo_agg(['numero_plano', 'grupo', 'ano'])

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

    resultado_grupo = vp_passivo_grupo.merge(
        vp_ativo_grupo,
        on=["numero_plano", "grupo", "ano"],
        how="outer",
    )
    resultado_grupo["acumulado_ativo"] = resultado_grupo["acumulado_ativo"].fillna(0)
    resultado_grupo["acumulado_passivo"] = resultado_grupo["acumulado_passivo"].fillna(0)
    resultado_grupo["excesso_ativo"] = (
        resultado_grupo["acumulado_ativo"] - resultado_grupo["acumulado_passivo"]
    )
    resultado_grupo["flag_excesso"] = (
        resultado_grupo["acumulado_ativo"] > resultado_grupo["acumulado_passivo"]
    )

    resultado_grupo_total = vp_passivo_grupo.merge(
        vp_ativo_grupo_total,
        on=["numero_plano", "grupo", "ano"],
        how="outer",
    )
    resultado_grupo_total["acumulado_ativo"] = resultado_grupo_total["acumulado_ativo"].fillna(0)
    resultado_grupo_total["acumulado_passivo"] = resultado_grupo_total["acumulado_passivo"].fillna(0)
    resultado_grupo_total["excesso_ativo"] = (
        resultado_grupo_total["acumulado_ativo"] - resultado_grupo_total["acumulado_passivo"]
    )
    resultado_grupo_total["flag_excesso"] = (
        resultado_grupo_total["acumulado_ativo"] > resultado_grupo_total["acumulado_passivo"]
    )

    print(f"Calculado {datetime.now()}")
    
    return {
        'vp_curva': vp_curva,
        'vp_curva_total': vp_curva_total,
        'vp_curva_grupo': vp_curva_grupo,
        'vp_curva_grupo_total': vp_curva_grupo_total,
        'vp_ativo': vp_ativo,
        'vp_ativo_total': vp_ativo_total,
        'vp_ativo_grupo': vp_ativo_grupo,
        'vp_ativo_grupo_total': vp_ativo_grupo_total,
        'vp_passivo': vp_passivo,
        'vp_passivo_grupo': vp_passivo_grupo,
        'resultado': resultado,
        'resultado_total': resultado_total,
        'resultado_grupo': resultado_grupo,
        'resultado_grupo_total': resultado_grupo_total,
        'duracao_ativo': duracao_ativo,
        'duracao_ativo_total': duracao_ativo_total,
        'duracao_ativo_anos': duracao_ativo_anos,
        'duracao_ativo_anos_total': duracao_ativo_anos_total,
        'duracao_ativo_grupo': duracao_ativo_grupo,
        'duracao_ativo_grupo_total': duracao_ativo_grupo_total,
        'duracao_ativo_anos_grupo': duracao_ativo_anos_grupo,
        'duracao_ativo_anos_grupo_total': duracao_ativo_anos_grupo_total,
        'duracao_passivo': duracao_passivo,
        'duracao_passivo_grupo': duracao_passivo_grupo,
        'taxas_plano': taxas_plano,
        'taxas_grupo': taxas_grupo,
        'passivo': passivo,
        'df': df,
    }

# Executar cálculos cacheados
try:
    calc_result = calcular_precificacao(fluxo, mapa, passivo, contas, dias_uteis)
except Exception as e:
    st.error(f"Erro no cálculo: {e}")
    st.stop()

if calc_result is None:
    st.error("Nenhum título encontrado.")
    st.stop()

# Desempacotar resultados
vp_curva = calc_result['vp_curva']
vp_curva_total = calc_result['vp_curva_total']
vp_curva_grupo = calc_result['vp_curva_grupo']
vp_curva_grupo_total = calc_result['vp_curva_grupo_total']
vp_ativo = calc_result['vp_ativo']
vp_ativo_total = calc_result['vp_ativo_total']
vp_ativo_grupo = calc_result['vp_ativo_grupo']
vp_ativo_grupo_total = calc_result['vp_ativo_grupo_total']
vp_passivo = calc_result['vp_passivo']
vp_passivo_grupo = calc_result['vp_passivo_grupo']
resultado = calc_result['resultado']
resultado_total = calc_result['resultado_total']
resultado_grupo = calc_result['resultado_grupo']
resultado_grupo_total = calc_result['resultado_grupo_total']
duracao_ativo = calc_result['duracao_ativo']
duracao_ativo_total = calc_result['duracao_ativo_total']
duracao_ativo_anos = calc_result['duracao_ativo_anos']
duracao_ativo_anos_total = calc_result['duracao_ativo_anos_total']
duracao_ativo_grupo = calc_result['duracao_ativo_grupo']
duracao_ativo_grupo_total = calc_result['duracao_ativo_grupo_total']
duracao_ativo_anos_grupo = calc_result['duracao_ativo_anos_grupo']
duracao_ativo_anos_grupo_total = calc_result['duracao_ativo_anos_grupo_total']
duracao_passivo = calc_result['duracao_passivo']
duracao_passivo_grupo = calc_result['duracao_passivo_grupo']
taxas_plano = calc_result['taxas_plano']
taxas_grupo = calc_result['taxas_grupo']
passivo = calc_result['passivo']
df = calc_result['df']

# ─────────────────────────────────────────────
# 3. SELEÇÃO DE PLANO / GRUPO
# ─────────────────────────────────────────────

planos = sorted(mapa["numero_plano"].dropna().unique())

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
    st.write("")
    st.write("")
    calcular = st.button("Filtrar", type="primary", width='stretch')

if not calcular:
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
    resultado_plano_total = resultado_grupo_total[
        (resultado_grupo_total["numero_plano"] == plano)
        & (resultado_grupo_total["grupo"] == grupo)
    ]
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
    vp_ativo_plano_total = vp_ativo_grupo_total.loc[
        (vp_ativo_grupo_total["numero_plano"] == plano)
        & (vp_ativo_grupo_total["grupo"] == grupo),
        "vp_ativo_total",
    ].sum()
    vp_curva_plano_total = vp_curva_grupo_total.loc[
        (vp_curva_grupo_total["numero_plano"] == plano)
        & (vp_curva_grupo_total["grupo"] == grupo),
        "vp_curva_total",
    ].sum()
    duracao_ativo_plano = _primeiro_ou_nan(
        duracao_ativo_grupo.loc[
            (duracao_ativo_grupo["numero_plano"] == plano)
            & (duracao_ativo_grupo["grupo"] == grupo),
            "duracao",
        ]
    )
    duracao_ativo_plano_total = _primeiro_ou_nan(
        duracao_ativo_grupo_total.loc[
            (duracao_ativo_grupo_total["numero_plano"] == plano)
            & (duracao_ativo_grupo_total["grupo"] == grupo),
            "duracao",
        ]
    )
    duracao_ativo_plano_anos = _primeiro_ou_nan(
        duracao_ativo_anos_grupo.loc[
            (duracao_ativo_anos_grupo["numero_plano"] == plano)
            & (duracao_ativo_anos_grupo["grupo"] == grupo),
            "duracao",
        ]
    )
    duracao_ativo_plano_anos_total = _primeiro_ou_nan(
        duracao_ativo_anos_grupo_total.loc[
            (duracao_ativo_anos_grupo_total["numero_plano"] == plano)
            & (duracao_ativo_anos_grupo_total["grupo"] == grupo),
            "duracao",
        ]
    )
    titulos_filtro = (df["numero_plano"] == plano) & (df["grupo"] == grupo)
    titulo_escopo = f"Plano {plano}  ·  Grupo {grupo}  ·  Base 31/12/{ANO_BASE}"
    nome_arquivo = f"resultado_ajuste_plano_{plano}_grupo_{grupo}.xlsx"
else:
    passivo_plano = passivo[passivo["numero_plano"] == plano].copy()
    if passivo_plano.empty:
        st.error(f"Nenhum dado de passivo encontrado para o plano {plano}.")
        st.stop()
    resultado_plano = resultado[resultado["numero_plano"] == plano]
    resultado_plano_total = resultado_total[resultado_total["numero_plano"] == plano]
    taxa_plano = taxas_plano.loc[taxas_plano["numero_plano"] == plano, "taxa"].iloc[0]
    duracao_passivo_plano = duracao_passivo.loc[
        duracao_passivo["numero_plano"] == plano, "duracao"
    ].iloc[0]
    vp_ativo_plano = vp_ativo[vp_ativo["numero_plano"] == plano]["vp_ativo"].sum()
    vp_curva_plano = vp_curva[vp_curva["numero_plano"] == plano]["vp_curva"].sum()
    vp_ativo_plano_total = vp_ativo_total[vp_ativo_total["numero_plano"] == plano]["vp_ativo_total"].sum()
    vp_curva_plano_total = vp_curva_total[vp_curva_total["numero_plano"] == plano]["vp_curva_total"].sum()
    duracao_ativo_plano = duracao_ativo.loc[duracao_ativo["numero_plano"] == plano, "duracao"].iloc[0]
    duracao_ativo_plano_total = duracao_ativo_total.loc[
        duracao_ativo_total["numero_plano"] == plano, "duracao"
    ].iloc[0]
    duracao_ativo_plano_anos = duracao_ativo_anos.loc[
        duracao_ativo_anos["numero_plano"] == plano, "duracao"
    ].iloc[0]
    duracao_ativo_plano_anos_total = duracao_ativo_anos_total.loc[
        duracao_ativo_anos_total["numero_plano"] == plano, "duracao"
    ].iloc[0]
    titulos_filtro = df["numero_plano"] == plano
    titulo_escopo = f"Plano {plano}  ·  Base 31/12/{ANO_BASE}"
    nome_arquivo = f"resultado_ajuste_plano_{plano}.xlsx"

ajuste_plano = vp_ativo_plano - vp_curva_plano
ajuste_plano_total = vp_ativo_plano_total - vp_curva_plano_total

# ─────────────────────────────────────────────
# 5. EXIBIR RESULTADOS
# ─────────────────────────────────────────────

st.divider()
st.subheader(titulo_escopo)
def kpi_card(titulo, valor, delta=None, alerta=False):
    """Cria um card de KPI com título, valor e delta opcional."""
    cor_delta = "green" if delta and delta >= 0 else "red"
    sinal = "+" if delta and delta >= 0 else ""

    delta_html = ""
    if delta is not None:
        delta_html = f'<div style="color:{cor_delta}; font-size:14px;">{sinal}{perc_br(delta)}</div>'

    if alerta:
        cor_titulo = "#7A1F1F"
        cor_valor = "#7A1F1F"
    else:
        cor_titulo = "dark-gray"
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
c6.markdown(kpi_card("Ajuste de precificação", moeda_br(ajuste_plano), (ajuste_plano/vp_passivo_total*100) if vp_passivo_total else None), unsafe_allow_html=True)

c7, c8, c9 = st.columns(3)
c7.markdown(kpi_card("VP Ativo (taxa atuarial) - Total", moeda_br(vp_ativo_plano_total)), unsafe_allow_html=True)
c8.markdown(kpi_card("VP Ativo (taxa curva) - Total", moeda_br(vp_curva_plano_total)), unsafe_allow_html=True)
c9.markdown(kpi_card("Ajuste de precificação - Total", moeda_br(ajuste_plano_total), (ajuste_plano_total/vp_passivo_total*100) if vp_passivo_total else None), unsafe_allow_html=True)

alerta_duracao = (
    pd.notna(duracao_ativo_plano_anos)
    and pd.notna(duracao_passivo_plano)
    and duracao_ativo_plano_anos > duracao_passivo_plano
)
alerta_duracao_total = (
    pd.notna(duracao_ativo_plano_anos_total)
    and pd.notna(duracao_passivo_plano)
    and duracao_ativo_plano_anos_total > duracao_passivo_plano
)

c10, c11, c12 = st.columns(3)
c10.markdown(kpi_card("Duração do ativo (dias)", f"{formatar_numero(duracao_ativo_plano, 2)} dias ({formatar_numero(duracao_ativo_plano/252, 4)} anos)", alerta=alerta_duracao), unsafe_allow_html=True)
c11.markdown(kpi_card("Duração do ativo (dias) - Total", f"{formatar_numero(duracao_ativo_plano_total, 2)} dias ({formatar_numero(duracao_ativo_plano_total/252, 4)} anos)", alerta=alerta_duracao_total), unsafe_allow_html=True)
#c12.markdown(kpi_card("Duração do ativo (taxa curva)", f"{formatar_numero(duracao_curva_plano, 2)} dias"), unsafe_allow_html=True)

c13, c14, c15 = st.columns(3)
c13.markdown(kpi_card("Duração do ativo (anos)", f"{formatar_numero(duracao_ativo_plano_anos, 4)} anos", alerta=alerta_duracao), unsafe_allow_html=True)
c14.markdown(kpi_card("Duração do ativo (anos) - Total", f"{formatar_numero(duracao_ativo_plano_anos_total, 4)} anos", alerta=alerta_duracao_total), unsafe_allow_html=True)
c15.markdown(kpi_card("Duração do passivo (anos)", f"{formatar_numero(duracao_passivo_plano, 4)} anos"), unsafe_allow_html=True)

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

# ── Títulos ───────────────────────────────────────────────────────────────────
st.divider()

st.subheader("Títulos do Plano")

titulos_plano = df[titulos_filtro].copy()

tabela_titulos = (
    titulos_plano
    .groupby(["ISIN", "quantidade", "qtde_total", "taxa"])[["vp_curva", "vp_curva_total", 
                                              "vp_ativo", "vp_ativo_total"]]
    .sum()
    .reset_index()
)

tabela_titulos["Valor unitário curva"] = tabela_titulos["vp_curva"] / tabela_titulos["quantidade"]
tabela_titulos["Valor unitário"] = tabela_titulos["vp_ativo"] / tabela_titulos["quantidade"]

tabela_titulos.columns = [
    "ISIN", "Quantidade usada", "Quantidade total", "Taxa", "VP Curva", "VP Curva (Total)", "VP Ativo", "VP Ativo (Total)", "Valor Unitário curva", "Valor Unitário"
]

st.dataframe(
    tabela_titulos.style.format({
        "Valor Unitário": lambda x: f"{x:,.2f}".replace(",", "X").replace(".", ",").replace("X", "."),
        "Valor Unitário curva": lambda x: f"{x:,.2f}".replace(",", "X").replace(".", ",").replace("X", "."),
        "VP Curva":       lambda x: f"{x:,.2f}".replace(",", "X").replace(".", ",").replace("X", "."),
        "VP Curva (Total)": lambda x: f"{x:,.2f}".replace(",", "X").replace(".", ",").replace("X", "."),
        "VP Ativo":       lambda x: f"{x:,.2f}".replace(",", "X").replace(".", ",").replace("X", "."),
        "VP Ativo (Total)": lambda x: f"{x:,.2f}".replace(",", "X").replace(".", ",").replace("X", "."),
        "Taxa Curva":     lambda x: f"{x:.4%}".replace(".", ","),
        "Quantidade usada":     lambda x: f"{x:,.2f}".replace(",", "X").replace(".", ",").replace("X", "."),
        "Quantidade total": lambda x: f"{x:,.2f}".replace(",", "X").replace(".", ",").replace("X", "."),
        "Taxa": lambda x: f"{x:.3%}".replace(".", ","),
    }),
    width='stretch',
    hide_index=True,
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
}])

df_plano = df[titulos_filtro]

buf = io.BytesIO()
with pd.ExcelWriter(buf, engine="openpyxl") as writer:
    df_plano.to_excel(writer,        sheet_name="Titulos",   index=False)
    passivo_plano.to_excel(writer, sheet_name="Passivo",   index=False)
    resultado_plano.to_excel(writer, sheet_name="Resultado", index=False)
    df_ajuste.to_excel(writer, sheet_name="Ajuste",    index=False)

st.download_button(
    label="⬇️  Baixar resultado (.xlsx)",
    data=buf.getvalue(),
    file_name=nome_arquivo,
    mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
)
