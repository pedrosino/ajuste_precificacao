"""Streamlit — Ajuste de Precificação de Plano de Benefícios"""

from datetime import datetime
import io
import logging
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
    help="Arquivo com as abas: Titulos, titulos_plano, titulos_carteira, Passivo, contas, dias_uteis",
)

if not uploaded:
    st.info("Carregue o arquivo para começar.")
    st.stop()

# ─────────────────────────────────────────────
# 2. LER ABAS
# ─────────────────────────────────────────────

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

try:
    fluxo, mapa, carteira, passivo, contas, dias_uteis = load_workbook(uploaded.read())
except Exception as e:
    st.error(f"Erro ao ler o arquivo: {e}")
    st.stop()

# ─────────────────────────────────────────────
# 4. PREPARAR DADOS (CACHEADO)
# ─────────────────────────────────────────────

@st.cache_data(show_spinner="Calculando…")
def calcular_precificacao(fluxo, mapa, carteira, passivo, contas, dias_uteis):
    """Realiza todos os cálculos de precificação uma única vez e cacheia o resultado."""

    print(f"Iniciando cálculos {datetime.now()}")

    # Copia dados para evitar alterações nos DataFrames originais
    fluxo = fluxo.copy()
    mapa = mapa.copy()
    carteira = carteira.copy()
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

    print(f"Planos com fluxo atuarial: {len(taxas_plano)}")

    # filtrar fluxo pelo plano/grupo
    df = fluxo.merge(mapa, on="ISIN", how="inner")
    if df.empty:
        return None

    df_carteira = fluxo.merge(carteira, on="ISIN", how="inner")
    if df_carteira.empty:
        return None

    planos_ajuste = df['numero_plano'].unique()
    planos_carteira = df_carteira['numero_plano'].unique()
    print(f"Planos com ajuste: {len(planos_ajuste)}, Planos com Carteira: {len(planos_carteira)}")

    # Comparação das listas
    todos_planos = sorted(set(taxas_plano['numero_plano']) | set(planos_ajuste) | set(planos_carteira))

    # 3. Create a boolean DataFrame checking presence of each item
    df_overlap = pd.DataFrame({
        'Ajuste': [item in planos_ajuste for item in todos_planos],
        'Carteira': [item in planos_carteira for item in todos_planos],
        'Taxas': [item in taxas_plano['numero_plano'].values for item in todos_planos]
    })

    # 4. Group by the combinations, count them, and format 'yes'/'no'
    summary = (
        df_overlap.groupby(['Ajuste', 'Carteira', 'Taxas'], as_index=False)
        .size()
        .rename(columns={'size': 'Count'})
    )

    # 5. Convert True/False to yes/no for the final presentation
    for col in ['Ajuste', 'Carteira', 'Taxas']:
        summary[col] = summary[col].map({True: 'yes', False: 'no'})

    df["fluxo"] = df["valor"] * df["quantidade"]
    df["taxa_diaria"] = (1 + df["taxa"]) ** (1 / 252) - 1

    df_carteira["fluxo"] = df_carteira["valor"] * df_carteira["quantidade"]
    # Arredonda taxa para 5 casas decimais para evitar problemas de merge
    # Na DPAP o máximo que informam é 3 casas percentuais
    df_carteira["taxa"] = df_carteira["taxa"].round(5)
    df_carteira["taxa_diaria"] = (1 + df_carteira["taxa"]) ** (1 / 252) - 1

    # função para pegar último dia útil anterior
    def get_dia_base(data):
        """Busca o dia na lista de dias úteis"""
        if data in mapa_du.index:
            return mapa_du.loc[data]
        anteriores = mapa_du.loc[:data]
        return anteriores.iloc[-1] if len(anteriores) else np.nan

    df["data_base"] = DATA_BASE
    dia_base = get_dia_base(DATA_BASE)

    df_carteira["data_base"] = DATA_BASE
    dia_base_carteira = get_dia_base(DATA_BASE)

    # Prazos em dias úteis
    df["dia_pgto"] = df["data_pgto"].map(mapa_du)
    df = df.dropna(subset=["dia_pgto"])
    df["prazo_du"] = df["dia_pgto"] - dia_base
    df = df[df["prazo_du"] > 0].copy()
    df["ano"] = df["data_pgto"].dt.year

    df_carteira["dia_pgto"] = df_carteira["data_pgto"].map(mapa_du)
    df_carteira = df_carteira.dropna(subset=["dia_pgto"])
    df_carteira["prazo_du"] = df_carteira["dia_pgto"] - dia_base_carteira
    df_carteira = df_carteira[df_carteira["prazo_du"] > 0].copy()
    df_carteira["ano"] = df_carteira["data_pgto"].dt.year

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

    df_carteira = df_carteira.merge(
        taxas_plano[['numero_plano', 'taxa_dia']],
        on='numero_plano',
        how='left',
    )

    # Valor presente - DPAP
    df["vp_curva"]  = df["fluxo"] / (1 + df["taxa_diaria"]) ** df["prazo_du"]
    df["vp_ativo"]  = df["fluxo"] / (1 + df["taxa_dia"]) ** df["prazo_du"]

    # Valor presente - Carteira
    df_carteira["vp_curva"]  = df_carteira["fluxo"] / (1 + df_carteira["taxa_diaria"]) ** df_carteira["prazo_du"]
    df_carteira["vp_ativo"]  = df_carteira["fluxo"] / (1 + df_carteira["taxa_dia"]) ** df_carteira["prazo_du"]

    def _cumsum_ano(frame, keys, value_col, acum_col='acumulado_ativo'):
        """Soma cumulativa por ano, ordenando por ano decrescente"""
        group_keys = [k for k in keys if k != 'ano']
        out = frame.copy()
        out[acum_col] = (
            out
            .sort_values(keys, ascending=[True] * (len(keys) - 1) + [False])
            .groupby(group_keys)[value_col]
            .cumsum()
        )
        return out.sort_values(keys)

    def _vp_ativo_agg(dados, keys_sem_ano, value_col):#_vp_ativo_agg(keys_sem_ano, value_col):
        """Agrega VP Ativo por plano/grupo e ano, somando os valores e calculando o acumulado"""
        keys = keys_sem_ano + ['ano']
        out = (
            dados
            .groupby(keys)[value_col]
            .sum()
            .reset_index()
        )
        return _cumsum_ano(out, keys, value_col)

    def _sum_keys(dados, keys, value_col):#_sum_keys(keys, value_col):
        """Agrega valores por plano/grupo e ano, somando os valores"""
        return (
            dados
            .groupby(keys)[value_col]
            .sum()
            .reset_index()
        )

    # Agregações por plano e por grupo
    vp_curva = _sum_keys(df,['numero_plano', 'data_base'], 'vp_curva')
    vp_curva_carteira = _sum_keys(df_carteira,['numero_plano', 'data_base'], 'vp_curva')
    vp_curva_grupo = _sum_keys(df,['numero_plano', 'grupo', 'data_base'], 'vp_curva')

    vp_ativo = _vp_ativo_agg(df, ['numero_plano'], 'vp_ativo')
    vp_ativo_carteira = _vp_ativo_agg(df_carteira, ['numero_plano'], 'vp_ativo')
    vp_ativo_grupo = _vp_ativo_agg(df, ['numero_plano', 'grupo'], 'vp_ativo')

    # Duração do ativo
    # Em dias úteis
    df["ponderado"] = df["vp_ativo"] * df["prazo_du"]
    # Em anos (prazo no meio do ano)
    df["prazo_anos"] = (df["ano"] - ANO_BASE) - 0.5
    df["ponderado_anos"] = df["vp_ativo"] * df["prazo_anos"]

    df_carteira["ponderado"] = df_carteira["vp_ativo"] * df_carteira["prazo_du"]
    df_carteira["prazo_anos"] = (df_carteira["ano"] - ANO_BASE) - 0.5
    df_carteira["ponderado_anos"] = df_carteira["vp_ativo"] * df_carteira["prazo_anos"]

    def _duracao_ativo(dados, keys, ponderado_col, vp_frame, vp_acum_col='acumulado_ativo'):
        """Calcula a duração do ativo por plano/grupo, usando VP Ativo e ponderado"""
        ponderado = _sum_keys(dados, keys + ['data_base'], ponderado_col)
        base = vp_frame[vp_frame['ano'] == 2026]
        out = ponderado.merge(base, on=keys, how='inner')
        out['duracao'] = out[ponderado_col] / out[vp_acum_col]
        return out

    # Cálculo em dias - mais preciso
    duracao_ativo = _duracao_ativo(
        df, ['numero_plano'], 'ponderado', vp_ativo
    )

    # Cálculo em anos - mais aproximado, mas mais fácil de interpretar
    duracao_ativo_anos = _duracao_ativo(
        df, ['numero_plano'], 'ponderado_anos', vp_ativo
    )

    duracao_ativo_grupo = _duracao_ativo(
        df, ['numero_plano', 'grupo'], 'ponderado', vp_ativo_grupo
    )

    duracao_ativo_anos_grupo = _duracao_ativo(
        df, ['numero_plano', 'grupo'], 'ponderado_anos', vp_ativo_grupo
    )

    # Carteira - em dias
    duracao_ativo_carteira = _duracao_ativo(
        df_carteira, ['numero_plano'], 'ponderado', vp_ativo_carteira
    )
    # Carteira - em anos
    duracao_ativo_carteira_anos = _duracao_ativo(
            df_carteira, ['numero_plano'], 'ponderado_anos', vp_ativo_carteira
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
        """Agrega o passivo por plano/grupo e ano, somando os valores e calculando a duração"""
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
        """Agrega o passivo por plano/grupo e ano, somando os valores"""
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

    resultado_carteira = vp_passivo.merge(
        vp_ativo_carteira,
        on=["numero_plano", "ano"],
        how="left"
    )
    resultado_carteira["excesso_ativo"] = resultado_carteira["acumulado_ativo"] - resultado_carteira["acumulado_passivo"]
    resultado_carteira["flag_excesso"] = resultado_carteira["acumulado_ativo"] > resultado_carteira["acumulado_passivo"]

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

    print(f"Calculado {datetime.now()}")

    return {
        'vp_curva': vp_curva,
        'vp_curva_grupo': vp_curva_grupo,
        'vp_curva_carteira': vp_curva_carteira,
        'vp_ativo': vp_ativo,
        'vp_ativo_grupo': vp_ativo_grupo,
        'vp_ativo_carteira': vp_ativo_carteira,
        'vp_passivo': vp_passivo,
        'vp_passivo_grupo': vp_passivo_grupo,
        'resultado': resultado,
        'resultado_carteira': resultado_carteira,
        'resultado_grupo': resultado_grupo,
        'duracao_ativo': duracao_ativo,
        'duracao_ativo_anos': duracao_ativo_anos,
        'duracao_ativo_grupo': duracao_ativo_grupo,
        'duracao_ativo_anos_grupo': duracao_ativo_anos_grupo,
        'duracao_ativo_carteira': duracao_ativo_carteira,
        'duracao_ativo_carteira_anos': duracao_ativo_carteira_anos,
        'duracao_passivo': duracao_passivo,
        'duracao_passivo_grupo': duracao_passivo_grupo,
        'taxas_plano': taxas_plano,
        'taxas_grupo': taxas_grupo,
        'passivo': passivo,
        'df': df,
        'df_carteira': df_carteira,
        'todos_planos': todos_planos,
    }

# Executar cálculos cacheados
try:
    calc_result = calcular_precificacao(fluxo, mapa, carteira, passivo, contas, dias_uteis)
except Exception as e:
    st.error(f"Erro no cálculo: {e.message if hasattr(e, 'message') else str(e)}")
    logging.exception("Erro no cálculo de precificação")
    raise
    #st.stop()

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
df = calc_result['df']
df_carteira = calc_result['df_carteira']
todos_planos = calc_result['todos_planos']

# ─────────────────────────────────────────────
# 3. RESUMO DE TODOS OS PLANOS
# ─────────────────────────────────────────────

def gerar_resumo_todos_planos(passivo, vp_ativo, vp_ativo_carteira, #vp_ativo_total,
                              vp_curva, vp_curva_carteira,duracao_ativo, duracao_ativo_anos,
                              #duracao_ativo_total, duracao_ativo_anos_total,
                              duracao_ativo_carteira, duracao_ativo_carteira_anos,
                              duracao_passivo, taxas_plano, todos_planos, resultado, resultado_carteira):
    """Gera um resumo consolidado com todos os planos."""
    planos_lista = todos_planos.copy()
    print(f"Planos encontrados: {len(planos_lista)}")
    resumo_data = []

    for num_plano in planos_lista:
        # VP Passivo
        vp_pass = passivo[passivo["numero_plano"] == num_plano]['vp_passivo'].sum()

        # VP Ativo (taxa atuarial)
        vp_ativo_val = vp_ativo[vp_ativo["numero_plano"] == num_plano]["vp_ativo"].sum()
        vp_ativo_carteira_val = vp_ativo_carteira[vp_ativo_carteira["numero_plano"] == num_plano]["vp_ativo"].sum()

        # VP Ativo (taxa curva)
        vp_curva_val = vp_curva[vp_curva["numero_plano"] == num_plano]["vp_curva"].sum()
        vp_curva_carteira_val = vp_curva_carteira[vp_curva_carteira["numero_plano"] == num_plano]["vp_curva"].sum()

        # Ajuste
        ajuste_val = vp_ativo_val - vp_curva_val
        ajuste_perc = (ajuste_val / vp_pass) if vp_pass != 0 else 0

        ajuste_carteira_val = vp_ativo_carteira_val - vp_curva_carteira_val
        ajuste_carteira_perc = (ajuste_carteira_val / vp_pass) if vp_pass != 0 else 0

        # Taxa atuarial
        taxa = taxas_plano[taxas_plano["numero_plano"] == num_plano]["taxa"].iloc[0] if len(taxas_plano[taxas_plano["numero_plano"] == num_plano]) > 0 else np.nan

        # Duração ativo (dias e anos)
        dur_ativo_dias = duracao_ativo[duracao_ativo["numero_plano"] == num_plano]["duracao"].iloc[0] if len(duracao_ativo[duracao_ativo["numero_plano"] == num_plano]) > 0 else np.nan
        dur_ativo_anos = duracao_ativo_anos[duracao_ativo_anos["numero_plano"] == num_plano]["duracao"].iloc[0] if len(duracao_ativo_anos[duracao_ativo_anos["numero_plano"] == num_plano]) > 0 else np.nan
        dur_ativo_carteira_dias = duracao_ativo_carteira[duracao_ativo_carteira["numero_plano"] == num_plano]["duracao"].iloc[0] if len(duracao_ativo_carteira[duracao_ativo_carteira["numero_plano"] == num_plano]) > 0 else np.nan
        dur_ativo_carteira_anos = duracao_ativo_carteira_anos[duracao_ativo_carteira_anos["numero_plano"] == num_plano]["duracao"].iloc[0] if len(duracao_ativo_carteira_anos[duracao_ativo_carteira_anos["numero_plano"] == num_plano]) > 0 else np.nan

        # Duração passivo (anos)
        dur_passivo = duracao_passivo[duracao_passivo["numero_plano"] == num_plano]["duracao"].iloc[0] if len(duracao_passivo[duracao_passivo["numero_plano"] == num_plano]) > 0 else np.nan

        # Verificação do valor presente ano a ano
        vp_compativel = not any(resultado[resultado["numero_plano"] == num_plano]["flag_excesso"])
        vp_compativel_carteira = not any(resultado_carteira[resultado_carteira["numero_plano"] == num_plano]["flag_excesso"])

        resumo_data.append({
            "Plano": num_plano,
            "Data Base": DATA_BASE.date(),
            "VP Passivo": vp_pass,
            "Taxa Atuarial": taxa,
            "Duração Passivo (anos)": dur_passivo,
            "VP Ativo (Taxa Atuarial)": vp_ativo_val,
            "VP Ativo (Taxa Curva)": vp_curva_val,
            "Ajuste de Precificação": ajuste_val,
            "Ajuste (%)": ajuste_perc,
            "Duração Ativo (dias)": dur_ativo_dias,
            "Duração Ativo (anos)": dur_ativo_anos,
            "Diferença Duração (anos)": dur_ativo_anos - dur_passivo if not pd.isna(dur_ativo_anos) and not pd.isna(dur_passivo) else np.nan,
            "Valor presente compatível": vp_compativel,
            "VP Ativo - Carteira": vp_ativo_carteira_val,
            "VP Curva - Carteira": vp_curva_carteira_val,
            "Ajuste de Precificação - Carteira": ajuste_carteira_val,
            "Ajuste (%) - Carteira": ajuste_carteira_perc,
            "Duração Ativo Carteira (dias)": dur_ativo_carteira_dias,
            "Duração Ativo Carteira (anos)": dur_ativo_carteira_anos,
            "Diferença Duração Carteira (anos)": dur_ativo_carteira_anos - dur_passivo if not pd.isna(dur_ativo_carteira_anos) and not pd.isna(dur_passivo) else np.nan,
            "Valor presente compatível Carteira": vp_compativel_carteira
        })

    return pd.DataFrame(resumo_data)

# Gerar resumo apenas uma vez (use session_state para evitar recálculo)
if 'df_resumo_todos' not in st.session_state:
    st.session_state.df_resumo_todos = gerar_resumo_todos_planos(
        passivo, vp_ativo, vp_ativo_carteira, vp_curva, vp_curva_carteira,
        duracao_ativo, duracao_ativo_anos, duracao_ativo_carteira, duracao_ativo_carteira_anos,
        duracao_passivo, taxas_plano, todos_planos, resultado, resultado_carteira
    )

df_resumo_todos = st.session_state.df_resumo_todos

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

# Novo
linha_passivo = resultado_plano["acumulado_passivo"]
linha_ativo = resultado_plano["acumulado_ativo"]
linha_carteira = resultado_plano_carteira["acumulado_ativo"]
anos = resultado_plano["ano"]

sombra_ativo = np.maximum(linha_ativo, linha_passivo)
sombra_carteira = np.maximum(linha_carteira, linha_passivo)

excesso_ativo = np.maximum(0, linha_ativo - linha_passivo)
excesso_carteira = np.maximum(0, linha_carteira - linha_passivo)

# 2. Force the upper boundaries to be NaN wherever the original lines are NaN
# (This ensures the upper boundary doesn't fall back to line_passivo values)
sombra_ativo = np.where(np.isnan(linha_ativo), np.nan, sombra_ativo)
sobra_carteira = np.where(np.isnan(linha_carteira), np.nan, sombra_carteira)

# Truncar as linhas base
base_passivo_ativo = np.where(np.isnan(linha_ativo), np.nan, linha_passivo)
base_passivo_carteira = np.where(np.isnan(linha_carteira), np.nan, linha_passivo)

# 3. Convert arrays to object lists and replace np.nan with None
# Plotly treats 'None' as a strict data gap and stops drawing completely.
sombra_ativo_lista = [None if np.isnan(v) else v for v in sombra_ativo]
sombra_carteira_lista = [None if np.isnan(v) else v for v in sombra_carteira]

base_ativo_lista = [None if np.isnan(v) else v for v in base_passivo_ativo]
base_carteira_lista = [None if np.isnan(v) else v for v in base_passivo_carteira]

x_ativo = np.where(np.isnan(linha_ativo), np.nan, anos)
x_carteira = np.where(np.isnan(linha_carteira), np.nan, anos)
uniao = np.union1d(x_ativo, x_carteira)
eixo_x = uniao[~np.isnan(uniao)].tolist()

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
    y=resultado_plano["acumulado_ativo"],
    name="VP Ativo acumulado",
    mode="lines+markers",
    line=dict(color="#378ADD", width=2),
    marker=dict(size=6),
))

fig.add_trace(go.Scatter(
    x=eixo_x,#resultado_plano["ano"],
    y=resultado_plano["acumulado_passivo"],
    name="VP Passivo acumulado",
    mode="lines+markers",
    line=dict(color="#D85A30", width=2, dash="dash"),
    marker=dict(size=6),
))

fig.add_trace(go.Scatter(
    x=eixo_x,#resultado_plano_carteira["ano"],
    y=resultado_plano_carteira["acumulado_ativo"],
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
    "VP Passivo acum.",
    "VP Ativo acum.", "Excesso acum.", "Ativo > Passivo",
    "VP Ativo acum. (Carteira)", "Excesso acum. (Carteira)", "Ativo > Passivo (Carteira)",
]

st.dataframe(
    tabela.style.format({
        "VP Ativo (ano)":    lambda x: f"{x:,.2f}".replace(",", "X").replace(".", ",").replace("X", "."),
        "VP Ativo acum.":    lambda x: f"{x:,.0f}".replace(",", "X").replace(".", ",").replace("X", "."),
        "VP Passivo (ano)":  lambda x: f"{x:,.0f}".replace(",", "X").replace(".", ",").replace("X", "."),
        "VP Passivo acum.":  lambda x: f"{x:,.0f}".replace(",", "X").replace(".", ",").replace("X", "."),
        "Excesso acum.":     lambda x: f"{x:,.0f}".replace(",", "X").replace(".", ",").replace("X", "."),
        "VP Ativo acum. (Carteira)":    lambda x: f"{x:,.0f}".replace(",", "X").replace(".", ",").replace("X", "."),
        "Excesso acum. (Carteira)":     lambda x: f"{x:,.0f}".replace(",", "X").replace(".", ",").replace("X", "."),
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

tabela_titulos.sort_values(by=["vencimento","taxa","quantidade"], inplace=True)
tabela_titulos = tabela_conjunta.sort_values(by=["vencimento","taxa","quantidade"],
                                             inplace=False) #inplace=True retorna None e modifica o original

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
    #"ISIN", "Vencimento", "Quantidade usada", "Quantidade Carteira",
    #"Taxa", "VP Curva", "VP Curva (Carteira)",
    #"VP Ativo", "VP Ativo (Carteira)",
    #"Valor Unitário curva", "Valor Unitário","Ajuste", "Ajuste (Carteira)"
    "ISIN", "Vencimento", "Taxa", "Quantidade usada", "VP Curva", "VP Ativo",
    "Quantidade (Carteira)", "VP Curva (Carteira)", "VP Ativo (Carteira)",
    "PU curva", "PU ativo",
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
    "PU ativo": lambda x: f"{x:,.2f}".replace(",", "X").replace(".", ",").replace("X", "."),
    "PU curva": lambda x: f"{x:,.2f}".replace(",", "X").replace(".", ",").replace("X", "."),
    "VP Curva":       lambda x: f"{x:,.2f}".replace(",", "X").replace(".", ",").replace("X", "."),
    "VP Curva (Carteira)": lambda x: f"{x:,.2f}".replace(",", "X").replace(".", ",").replace("X", "."),
    "VP Ativo":       lambda x: f"{x:,.2f}".replace(",", "X").replace(".", ",").replace("X", "."),
    "VP Ativo (Carteira)": lambda x: f"{x:,.2f}".replace(",", "X").replace(".", ",").replace("X", "."),
    "Ajuste":     lambda x: f"{x:,.2f}".replace(",", "X").replace(".", ",").replace("X", "."),
    "Ajuste (Carteira)": lambda x: f"{x:,.2f}".replace(",", "X").replace(".", ",").replace("X", "."),
    "Taxa Curva":     lambda x: f"{x:.4%}".replace(".", ","),
    "Quantidade usada":     lambda x: f"{x:,.2f}".replace(",", "X").replace(".", ",").replace("X", "."),
    "Quantidade (Carteira)": lambda x: f"{x:,.2f}".replace(",", "X").replace(".", ",").replace("X", "."),
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

df_plano = df[titulos_filtro]
df_plano_carteira = df_carteira[titulos_carteira_filtro]

buf = io.BytesIO()
with pd.ExcelWriter(buf, engine="openpyxl") as writer:
    df_plano.to_excel(writer, sheet_name="Titulos",   index=False)
    df_plano_carteira.to_excel(writer, sheet_name="Titulos Carteira", index=False)
    passivo_plano.to_excel(writer, sheet_name="Passivo", index=False)
    resultado_plano.to_excel(writer, sheet_name="Resultado", index=False)
    resultado_plano_carteira.to_excel(writer, sheet_name="Resultado Carteira", index=False)
    df_ajuste.to_excel(writer, sheet_name="Ajuste", index=False)

st.download_button(
    label="⬇️  Baixar resultado completo (.xlsx)",
    data=buf.getvalue(),
    file_name=nome_arquivo,
    mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
)
