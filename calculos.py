"""Cálculos do Ajuste de Precificação.

Módulo sem dependência do Streamlit: é usado tanto pelo script
`atualizar_dados.py` (que gera os resultados pré-calculados) quanto pelo app
(que só os utiliza no modo de upload de planilha).
"""

from datetime import datetime

import warnings

import numpy as np
import pandas as pd

# data-base
ANO_BASE = 2025
DATA_BASE = pd.Timestamp(f"{ANO_BASE}-12-31")


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
        how="outer",
    )
    resultado["excesso_ativo"] = resultado["acumulado_ativo"] - resultado["acumulado_passivo"]
    resultado["flag_excesso"]  = resultado["acumulado_ativo"] > resultado["acumulado_passivo"]

    resultado_carteira = vp_passivo.merge(
        vp_ativo_carteira,
        on=["numero_plano", "ano"],
        how="outer"
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


def gerar_resumo(calc):
    """Gera o resumo de todos os planos a partir do dicionário retornado por calcular_precificacao."""
    return gerar_resumo_todos_planos(
        calc['passivo'], calc['vp_ativo'], calc['vp_ativo_carteira'],
        calc['vp_curva'], calc['vp_curva_carteira'],
        calc['duracao_ativo'], calc['duracao_ativo_anos'],
        calc['duracao_ativo_carteira'], calc['duracao_ativo_carteira_anos'],
        calc['duracao_passivo'], calc['taxas_plano'], calc['todos_planos'],
        calc['resultado'], calc['resultado_carteira'],
    )


def calcular_tudo(fluxo, mapa, carteira, passivo, contas, dias_uteis):
    """Executa todos os cálculos e o resumo. Retorna (calc, resumo) ou (None, None) se não houver títulos."""
    calc = calcular_precificacao(fluxo, mapa, carteira, passivo, contas, dias_uteis)
    if calc is None:
        return None, None
    return calc, gerar_resumo(calc)


# ── Datas e ordenação dos títulos ─────────────────────────────────────────────

def para_data(serie):
    """Converte uma coluna de datas em texto para datetime, sem ambiguidade de dia/mês.

    Aceita AAAA-MM-DD e DD/MM/AAAA; se já for datetime, devolve como está.
    O que não puder ser lido vira NaT.
    """
    if pd.api.types.is_datetime64_any_dtype(serie):
        return serie
    txt = serie.astype("string").str.strip()
    datas = pd.to_datetime(txt, format="%Y-%m-%d", errors="coerce")
    datas = datas.fillna(pd.to_datetime(txt, format="%d/%m/%Y", errors="coerce"))
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        return datas.fillna(pd.to_datetime(txt, errors="coerce"))


def ordenar_titulos(frame):
    """Ordena por vencimento (como data), depois taxa e ISIN. Mantém a coluna vencimento como está."""
    out = frame.assign(_venc=para_data(frame["vencimento"]))
    chaves = [c for c in ("_venc", "taxa", "ISIN") if c in out.columns]
    return out.sort_values(chaves, na_position="last").drop(columns="_venc")


def preparar_exportacao_titulos(frame):
    """Para o Excel: vencimento como data de verdade e linhas ordenadas por vencimento, taxa, ISIN e data de pagamento."""
    out = frame.copy()
    out["vencimento"] = para_data(out["vencimento"])
    chaves = [c for c in ("vencimento", "taxa", "ISIN", "data_pgto") if c in out.columns]
    return out.sort_values(chaves, na_position="last").reset_index(drop=True)


def formatar_colunas_data(ws, nomes=("vencimento", "data_pgto"), formato="DD/MM/YYYY"):
    """Aplica o formato de data (dd/mm/aaaa) às colunas de data de uma aba do openpyxl."""
    cabecalho = [c.value for c in ws[1]]
    for nome in nomes:
        if nome in cabecalho:
            col = cabecalho.index(nome) + 1
            for linha in range(2, ws.max_row + 1):
                ws.cell(row=linha, column=col).number_format = formato
