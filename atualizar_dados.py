"""Atualiza os resultados pré-calculados usados pelo app.

Rode na sua máquina sempre que a planilha de origem mudar (ou quando mudar
ANO_BASE / a lógica em calculos.py):

    python atualizar_dados.py

Depois faça commit da pasta `dados_calculados/` — o Streamlit Cloud
redeploya sozinho e o app passa a ler os novos resultados.

Dependências: pandas, numpy, pyarrow.
"""

import json
import shutil
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd

from calculos import ANO_BASE, calcular_tudo

# Id da planilha Google
SHEET_ID = "16piO6vTax9hCPA-pRWPpQCK45S1Cpy6-"

PASTA = Path(__file__).parent / "dados_calculados"

# Tabelas grandes que o app só consulta filtradas por plano: são gravadas
# ordenadas por numero_plano e em blocos pequenos (row groups), para o app
# ler do disco apenas as linhas do plano selecionado.
POR_PLANO = ["df", "df_carteira"]


def ler_google_sheet(sheet_id):
    """Lê as abas da planilha Google e retorna os DataFrames."""
    url = f"https://docs.google.com/spreadsheets/d/{sheet_id}/gviz/tq?tqx=out:csv&sheet="
    fluxo      = pd.read_csv(f"{url}Titulos", decimal=",", thousands=".")
    mapa       = pd.read_csv(f"{url}titulos_plano", decimal=",", thousands=".")
    carteira   = pd.read_csv(f"{url}titulos_carteira", decimal=",", thousands=".")
    passivo    = pd.read_csv(f"{url}Passivo", decimal=",", thousands=".")
    contas     = pd.read_csv(f"{url}contas", decimal=",", thousands=".")
    dias_uteis = pd.read_csv(f"{url}dias_uteis", header=None, names=["data"], decimal=",", thousands=".")
    return fluxo, mapa, carteira, passivo, contas, dias_uteis


def salvar(calc, resumo, entradas, pasta=PASTA):
    """Grava os resultados em parquet, arquivo por arquivo, dentro de `pasta`.

    `entradas` são as abas brutas que o app ainda consulta diretamente
    (hoje: mapa e carteira, usadas na tabela "Títulos do Plano").

    Não renomeia nem apaga a pasta (no Windows, o OneDrive/antivírus costuma
    bloquear isso). O meta.json é apagado no início e regravado por último:
    se a execução for interrompida, a pasta fica sem meta.json e o app avisa
    em vez de usar dados pela metade.
    """
    pasta.mkdir(parents=True, exist_ok=True)
    (pasta / "meta.json").unlink(missing_ok=True)

    gravados = set()

    def gravar(frame, nome, **kwargs):
        frame.to_parquet(pasta / f"{nome}.parquet", index=False, compression="zstd", **kwargs)
        gravados.add(f"{nome}.parquet")

    tabelas = [nome for nome, valor in calc.items() if isinstance(valor, pd.DataFrame)]
    for nome in tabelas:
        if nome in POR_PLANO:
            # ordenação estável: dentro de cada plano a ordem das linhas é a original
            gravar(calc[nome].sort_values("numero_plano", kind="stable"), nome, row_group_size=50_000)
        else:
            gravar(calc[nome], nome)

    for nome, frame in entradas.items():
        gravar(frame, f"entrada_{nome}")

    gravar(pd.DataFrame({"numero_plano": calc["todos_planos"]}), "todos_planos")
    gravar(resumo, "resumo_todos_planos")

    # remove parquets de execuções anteriores que não fazem mais parte do conjunto
    for arq in pasta.glob("*.parquet"):
        if arq.name not in gravados:
            arq.unlink()

    meta = {
        "gerado_em": datetime.now(ZoneInfo("America/Sao_Paulo")).strftime("%d/%m/%Y %H:%M"),
        "ano_base": ANO_BASE,
        "tabelas": [n for n in tabelas if n not in POR_PLANO],
        "por_plano": [n for n in tabelas if n in POR_PLANO],
        "entradas": list(entradas),
    }
    (pasta / "meta.json").write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
    return meta


def main():
    # Sobra da versão anterior do script (pode ser apagada à mão se estiver bloqueada)
    shutil.rmtree(PASTA.with_name(PASTA.name + "_tmp"), ignore_errors=True)

    print("Lendo planilha do Google...")
    fluxo, mapa, carteira, passivo, contas, dias_uteis = ler_google_sheet(SHEET_ID)

    print("Calculando...")
    calc, resumo = calcular_tudo(fluxo, mapa, carteira, passivo, contas, dias_uteis)
    if calc is None:
        raise SystemExit("Nenhum título encontrado — nada foi salvo.")

    print("Salvando...")
    meta = salvar(calc, resumo, {"mapa": mapa, "carteira": carteira})

    print(f"\nPronto: {meta['gerado_em']} (ano-base {meta['ano_base']})")
    total = 0
    for arq in sorted(PASTA.iterdir()):
        kb = arq.stat().st_size / 1024
        total += kb
        print(f"  {arq.name:<40} {kb:>10,.0f} KB")
    print(f"  {'TOTAL':<40} {total:>10,.0f} KB")
    print("\nAgora faça o commit da pasta dados_calculados/.")


if __name__ == "__main__":
    main()
