# -*- coding: utf-8 -*-
"""
Calculadora Comercial & Dashboard de Liberação de Alçadas — Grupo Coruja
=========================================================================
Ferramenta interna para simular a rentabilidade de uma proposta comercial
(PI) e verificar automaticamente se o perfil do solicitante possui alçada
para autorizar o desconto envolvido.

As premissas de custo, tributos e alçadas ficam embutidas neste arquivo
(dicionários abaixo) e NUNCA são exibidas na interface — o usuário só vê
o resultado final do cálculo (KPIs, status de autorização e gráficos).
"""

import base64
import hashlib
import hmac
from datetime import datetime
from pathlib import Path

import pandas as pd
import streamlit as st

LOGO_PATH = Path(__file__).parent / "assets" / "logo_grupo_coruja.png"
LOGO_DATA_URI = None
if LOGO_PATH.exists():
    LOGO_DATA_URI = "data:image/png;base64," + base64.b64encode(LOGO_PATH.read_bytes()).decode("ascii")

# ============================================================================
# 1) DADOS PROTEGIDOS — PREMISSAS CONFIDENCIAIS (não exibir na interface)
#    Fonte: planilha "Calculado_Comercial.xlsx" (abas "Tabela de Preço" e
#    "Calculadora"), substituindo a base anterior ("Rascunho - 04.08") a
#    pedido do usuário em 2026-09-18. Todos os valores de custo abaixo foram
#    extraídos programaticamente das FÓRMULAS da aba "Calculadora" (nunca
#    dos valores de exemplo), com validação automática linha a linha: o
#    motor de cálculo reproduz o Lucro Líquido das 39 linhas da planilha
#    (18 ativos) com erro zero.
# ============================================================================

# ---- Regras gerais de tributos e alçadas de aprovação ----------------------
# Tributos sobre a receita (PIS+COFINS+ISS) somam sempre 14,25% do Valor de
# Venda, e o IRPJ/CSLL soma sempre 34% do Resultado Operacional — SEM o teto
# mensal de isenção do Adicional de IRPJ que existia na base anterior (a
# nova planilha não usa esse teto em nenhuma das 39 fórmulas de IRPJ/CSLL).
REGRAS_ALCADAS = {
    "tributos_receita": {
        "pis": 0.0165,
        "cofins": 0.0760,
        "iss": 0.0500,
    },
    "irpj_csll_pct": 0.34,  # flat sobre o Resultado Operacional, sem teto de isenção
    "lucro_liquido_alvo_geral": 0.35,     # premissa 1 — meta geral "quando possível"
    "lucro_liquido_alvo_sugerido": 0.40,  # premissa 3 — nova coluna "Preço Sugerido"
    "piso_margem_absoluto": 0.15,         # abaixo disso, NENHUM perfil autoriza
    # Alçada de cada perfil (premissa 2): Executivo e Gerente Comercial são
    # limitados por % de DESCONTO sobre o Preço de Tabela; Diretoria
    # Comercial e Diretoria Financeira/CEO são limitados por % mínima de
    # LUCRO LÍQUIDO sobre o Valor de Venda — duas métricas diferentes por
    # nível, exatamente como a premissa descreve.
    "alcadas": {
        "Executivo":          {"tipo": "desconto_maximo", "valor": 0.20},
        "Gerente Comercial":  {"tipo": "desconto_maximo", "valor": 0.30},
        "Diretor Comercial":  {"tipo": "margem_minima",   "valor": 0.20},
        "Diretor Financeiro": {"tipo": "margem_minima",   "valor": 0.15},
    },
    "hierarquia_perfis": {
        "Executivo": 1,
        "Gerente Comercial": 2,
        "Diretor Comercial": 3,
        "Diretor Financeiro": 4,
    },
}

# ---- Estrutura de custos por ativo -----------------------------------------
# Cada ativo é um dicionário de "tipo/cota/posição" -> premissas daquele
# tipo, extraídas linha a linha da aba "Calculadora":
#   preco_tabela           Valor de Venda de referência da planilha — usado
#                           também como "Preço de Tabela" para calcular o %
#                           de desconto nas alçadas de Executivo/Gerente
#   custos_fixos_diretos    R$ fixos por negociação (repasse_fixo, energia,
#                           internet_cameras, manutencao, producao,
#                           custo_prestadores, tap) — não variam com o
#                           Valor de Venda negociado. São os custos
#                           "negociáveis" da premissa 4 (BV, produção e
#                           repasse do Envelopamento Fretado ficam editáveis
#                           na tela — ver seção de interface).
#   repasse_pct             parte do repasse que é % do Valor de Venda
#                           (Painel Jd Oceânico, MUB Digital, Envelopamento
#                           Fretado, Revista Península) — default 0.0
#   bv / comissao / inadimplencia   % sobre o Valor de Venda
#   recebimento_editoracao  receita adicional fixa (só Revista Península)
def _tipo(preco_tabela, custos_fixos_diretos, bv, comissao, inadimplencia,
          repasse_pct=0.0, recebimento_editoracao=0.0):
    return {
        "preco_tabela": float(preco_tabela),
        "custos_fixos_diretos": dict(custos_fixos_diretos),
        "repasse_pct": repasse_pct,
        "bv": bv,
        "comissao": comissao,
        "inadimplencia": inadimplencia,
        "recebimento_editoracao": recebimento_editoracao,
    }


ATIVOS = {
    "Painel Presidente Vargas": {
        "Cota Inteira": _tipo(
            preco_tabela=53000,
            custos_fixos_diretos={"repasse_fixo": 625.0, "energia": 462.5, "internet_cameras": 150.0, "manutencao": 93.75, "tap": 6250.0},
            bv=0.2, comissao=0.05, inadimplencia=0.02,
        ),
        "Meia Cota": _tipo(
            preco_tabela=34450,
            custos_fixos_diretos={"repasse_fixo": 312.5, "energia": 231.25, "internet_cameras": 75.0, "manutencao": 46.875, "tap": 3125.0},
            bv=0.2, comissao=0.05, inadimplencia=0.02,
        ),
    },
    "Painel Jardim Oceânico": {
        "Cota Inteira": _tipo(
            preco_tabela=107000,
            custos_fixos_diretos={"repasse_fixo": 2200.0, "energia": 1125.0, "internet_cameras": 150.0, "manutencao": 93.75, "tap": 6250.0},
            bv=0.2, comissao=0.05, inadimplencia=0.02,
            repasse_pct=0.1,
        ),
        "Meia Cota": _tipo(
            preco_tabela=69550,
            custos_fixos_diretos={"repasse_fixo": 1100.0, "energia": 562.5, "internet_cameras": 75.0, "manutencao": 46.875, "tap": 3125.0},
            bv=0.2, comissao=0.05, inadimplencia=0.02,
            repasse_pct=0.1,
        ),
    },
    "Busdoor": {
        "Alpha - 110": _tipo(
            preco_tabela=4500,
            custos_fixos_diretos={"repasse_fixo": 150.0},
            bv=0.0, comissao=0.05, inadimplencia=0.02,
        ),
        "Transurb (Colou Pagou)": _tipo(
            preco_tabela=4500,
            custos_fixos_diretos={"repasse_fixo": 133.0435},
            bv=0.0, comissao=0.05, inadimplencia=0.02,
        ),
        "Graças (Colou Pagou)": _tipo(
            preco_tabela=4500,
            custos_fixos_diretos={"repasse_fixo": 134.8315},
            bv=0.0, comissao=0.05, inadimplencia=0.02,
        ),
        "Tijuquinha (Colou Pagou)": _tipo(
            preco_tabela=4500,
            custos_fixos_diretos={"repasse_fixo": 150.0},
            bv=0.0, comissao=0.05, inadimplencia=0.02,
        ),
        "Trell (Colou Pagou)": _tipo(
            preco_tabela=4500,
            custos_fixos_diretos={"repasse_fixo": 208.6},
            bv=0.0, comissao=0.05, inadimplencia=0.02,
        ),
        "Pendotiba (Colou Pagou)": _tipo(
            preco_tabela=4500,
            custos_fixos_diretos={"repasse_fixo": 208.6},
            bv=0.0, comissao=0.05, inadimplencia=0.02,
        ),
        "Vila Real (Colou Pagou)": _tipo(
            preco_tabela=4500,
            custos_fixos_diretos={"repasse_fixo": 80.0},
            bv=0.0, comissao=0.05, inadimplencia=0.02,
        ),
    },
    "MUB Estático": {
        "Cota Inteira": _tipo(
            preco_tabela=23000,
            custos_fixos_diretos={"repasse_fixo": 1650.0, "manutencao": 200.0, "tap": 2500.0},
            bv=0.2, comissao=0.05, inadimplencia=0.02,
        ),
    },
    "MUB Digital": {
        "Cota Inteira": _tipo(
            preco_tabela=35000,
            custos_fixos_diretos={"manutencao": 200.0, "tap": 1125.0},
            bv=0.2, comissao=0.05, inadimplencia=0.02,
            repasse_pct=0.1,
        ),
        "Meia Cota": _tipo(
            preco_tabela=22750,
            custos_fixos_diretos={"manutencao": 100.0, "tap": 562.5},
            bv=0.2, comissao=0.05, inadimplencia=0.02,
            repasse_pct=0.1,
        ),
    },
    "Envelopamento Rio 2": {
        "Padrão (23 ônibus)": _tipo(
            preco_tabela=32000,
            custos_fixos_diretos={"repasse_fixo": 7035.0},
            bv=0.2, comissao=0.05, inadimplencia=0.02,
        ),
    },
    "Envelopamento Península": {
        "Padrão (9 ônibus)": _tipo(
            preco_tabela=32000,
            custos_fixos_diretos={"repasse_fixo": 3600.0},
            bv=0.2, comissao=0.05, inadimplencia=0.02,
        ),
    },
    "Envelopamento Frames": {
        "Padrão (3 ônibus)": _tipo(
            preco_tabela=32000,
            custos_fixos_diretos={"repasse_fixo": 4500.0},
            bv=0.2, comissao=0.05, inadimplencia=0.02,
        ),
    },
    "Envelopamento Barra Bali": {
        "Padrão (13 ônibus)": _tipo(
            preco_tabela=32000,
            custos_fixos_diretos={"repasse_fixo": 6900.0},
            bv=0.2, comissao=0.05, inadimplencia=0.02,
        ),
    },
    "Envelopamento Estação BRT": {
        "Padrão": _tipo(
            preco_tabela=500000,
            custos_fixos_diretos={"repasse_fixo": 242000.0},
            bv=0.2, comissao=0.05, inadimplencia=0.02,
        ),
    },
    "Envelopamento Articulado": {
        "Padrão": _tipo(
            preco_tabela=387000,
            custos_fixos_diretos={"repasse_fixo": 22000.0},
            bv=0.2, comissao=0.05, inadimplencia=0.02,
        ),
    },
    "Envelopamento Fretado": {
        "Padrão": _tipo(
            preco_tabela=295000,
            custos_fixos_diretos={},
            bv=0.2, comissao=0.05, inadimplencia=0.02,
            repasse_pct=0.1,
        ),
    },
    "Empena Estática": {
        "Barra - Jardim Oceânico": _tipo(
            preco_tabela=190000,
            custos_fixos_diretos={"repasse_fixo": 12000.0, "tap": 10040.0},
            bv=0.2, comissao=0.05, inadimplencia=0.02,
        ),
        "Botafogo - Álvaro Rodrigues": _tipo(
            preco_tabela=160000,
            custos_fixos_diretos={"repasse_fixo": 8000.0, "tap": 7800.0},
            bv=0.2, comissao=0.05, inadimplencia=0.02,
        ),
        "Botafogo - General Polidoro": _tipo(
            preco_tabela=310000,
            custos_fixos_diretos={"repasse_fixo": 8400.0, "energia": 600.0, "internet_cameras": 240.0, "tap": 8500.0},
            bv=0.2, comissao=0.05, inadimplencia=0.02,
        ),
        "Botafogo Real Grandeza": _tipo(
            preco_tabela=280000,
            custos_fixos_diretos={"repasse_fixo": 8000.0, "energia": 800.0, "internet_cameras": 240.0, "tap": 10040.0},
            bv=0.2, comissao=0.05, inadimplencia=0.02,
        ),
        "Copacabana - Santa Clara": _tipo(
            preco_tabela=230000,
            custos_fixos_diretos={"repasse_fixo": 15000.0, "tap": 10600.0},
            bv=0.2, comissao=0.05, inadimplencia=0.02,
        ),
        "Leblon - Ataulfo de Paiva": _tipo(
            preco_tabela=160000,
            custos_fixos_diretos={"repasse_fixo": 5000.0, "tap": 9500.0},
            bv=0.2, comissao=0.05, inadimplencia=0.02,
        ),
        "Maracanã - Av. Maracanã, 417": _tipo(
            preco_tabela=165000,
            custos_fixos_diretos={"repasse_fixo": 10000.0, "tap": 7800.0},
            bv=0.2, comissao=0.05, inadimplencia=0.02,
        ),
        "Maracanã - Av. Maracanã, 515": _tipo(
            preco_tabela=160000,
            custos_fixos_diretos={"repasse_fixo": 12000.0, "tap": 15000.0},
            bv=0.2, comissao=0.05, inadimplencia=0.02,
        ),
        "Maracanã - Av. Maracanã, 526": _tipo(
            preco_tabela=96000,
            custos_fixos_diretos={"repasse_fixo": 5700.0, "tap": 4000.0},
            bv=0.2, comissao=0.05, inadimplencia=0.02,
        ),
        "Tijuca - Haddock Lobo": _tipo(
            preco_tabela=107000,
            custos_fixos_diretos={"repasse_fixo": 4500.0, "tap": 5000.0},
            bv=0.2, comissao=0.05, inadimplencia=0.02,
        ),
    },
    "Mega Empena - Barra Américas": {
        "Cota Inteira": _tipo(
            preco_tabela=430000,
            custos_fixos_diretos={"repasse_fixo": 7500.0, "energia": 1000.0, "internet_cameras": 125.0, "manutencao": 200.0, "tap": 12000.0},
            bv=0.2, comissao=0.05, inadimplencia=0.02,
        ),
        "Meia Cota": _tipo(
            preco_tabela=279500,
            custos_fixos_diretos={"repasse_fixo": 3750.0, "energia": 500.0, "internet_cameras": 62.5, "manutencao": 100.0, "tap": 6000.0},
            bv=0.2, comissao=0.05, inadimplencia=0.02,
        ),
    },
    "Mega Empena - Tijuca": {
        "Cota Inteira": _tipo(
            preco_tabela=462000,
            custos_fixos_diretos={"repasse_fixo": 12000.0, "energia": 1625.0, "internet_cameras": 100.0, "tap": 14000.0},
            bv=0.2, comissao=0.05, inadimplencia=0.02,
        ),
        "Meia Cota": _tipo(
            preco_tabela=300300,
            custos_fixos_diretos={"repasse_fixo": 6000.0, "energia": 812.5, "internet_cameras": 50.0, "tap": 7000.0},
            bv=0.2, comissao=0.05, inadimplencia=0.02,
        ),
    },
    "Mega Empena - Copacabana": {
        "Cota Inteira": _tipo(
            preco_tabela=430000,
            custos_fixos_diretos={"repasse_fixo": 11875.0, "energia": 1000.0, "internet_cameras": 125.0, "tap": 6250.0},
            bv=0.2, comissao=0.05, inadimplencia=0.02,
        ),
        "Meia Cota": _tipo(
            preco_tabela=279500,
            custos_fixos_diretos={"repasse_fixo": 5937.5, "energia": 500.0, "internet_cameras": 62.5, "tap": 3125.0},
            bv=0.2, comissao=0.05, inadimplencia=0.02,
        ),
    },
    "Revista Rio 2": {
        "Página Indeterminada": _tipo(
            preco_tabela=3606,
            custos_fixos_diretos={"producao": 382.05, "custo_prestadores": 695.0},
            bv=0.0, comissao=0.2, inadimplencia=0.02,
        ),
    },
    "Revista Península": {
        "Página Indeterminada": _tipo(
            preco_tabela=5373,
            custos_fixos_diretos={"custo_prestadores": 506.64},
            bv=0.0, comissao=0.2, inadimplencia=0.02,
            repasse_pct=0.65, recebimento_editoracao=6000,
        ),
    },
}

PERFIS = ["Executivo", "Gerente Comercial", "Diretor Comercial", "Diretor Financeiro"]


# ============================================================================
# 2) MOTOR DE CÁLCULO (DRE, alçada híbrida e resolução reversa de preço)
# ============================================================================

def calcular_dre(valor_venda: float, tipo_cfg: dict, bv_pct: float = None,
                  producao_r: float = None, repasse_pct: float = None,
                  regras: dict = REGRAS_ALCADAS) -> dict:
    """Reproduz a apuração de DRE de cada ativo/tipo da planilha mestre.

    bv_pct / producao_r / repasse_pct permitem sobrescrever, na tela, os
    valores padrão do catálogo — BV, produção e (só para Envelopamento
    Fretado) repasse são negociáveis (premissa 4). Quando None, usa o
    valor-padrão do ativo/tipo.
    """
    custos_fixos = dict(tipo_cfg["custos_fixos_diretos"])
    if producao_r is not None:
        if producao_r:
            custos_fixos["producao"] = producao_r
        else:
            custos_fixos.pop("producao", None)

    if bv_pct is None:
        bv_pct = tipo_cfg["bv"]
    if repasse_pct is None:
        repasse_pct = tipo_cfg["repasse_pct"]

    trib = regras["tributos_receita"]
    pis = valor_venda * trib["pis"]
    cofins = valor_venda * trib["cofins"]
    iss = valor_venda * trib["iss"]
    impostos_receita = pis + cofins + iss

    repasse_variavel = valor_venda * repasse_pct
    total_custos_fixos = sum(custos_fixos.values())

    bv = valor_venda * bv_pct
    comissao = valor_venda * tipo_cfg["comissao"]
    inadimplencia = valor_venda * tipo_cfg["inadimplencia"]
    recebimento_editoracao = tipo_cfg.get("recebimento_editoracao", 0.0)

    resultado_operacional = (
        valor_venda - impostos_receita - repasse_variavel - total_custos_fixos
        - bv - comissao - inadimplencia + recebimento_editoracao
    )

    irpj_csll = resultado_operacional * regras["irpj_csll_pct"]
    lucro_liquido = resultado_operacional - irpj_csll
    margem_liquida_pct = (lucro_liquido / valor_venda) if valor_venda else 0.0

    return {
        "valor_venda": valor_venda,
        "pis": pis, "cofins": cofins, "iss": iss, "impostos_receita": impostos_receita,
        "repasse_variavel": repasse_variavel, "repasse_pct_usado": repasse_pct,
        "custos_fixos_diretos": custos_fixos, "total_custos_fixos": total_custos_fixos,
        "bv": bv, "bv_pct_usado": bv_pct, "comissao": comissao, "inadimplencia": inadimplencia,
        "recebimento_editoracao": recebimento_editoracao,
        "resultado_operacional": resultado_operacional,
        "irpj_csll": irpj_csll,
        "lucro_liquido": lucro_liquido,
        "margem_liquida_pct": margem_liquida_pct,
        "bonificacao": None,
    }


def calcular_dre_com_bonificacao(valor_venda: float, tipo_cfg: dict, bonus_tipo_cfg: dict = None,
                                  **kwargs) -> dict:
    """Premissa 5 — bonificação: quando um segundo ativo é dado como bônus
    (faturamento R$ 0) junto com o ativo vendido, os custos FIXOS desse
    ativo-bônus entram diminuindo o Resultado Operacional do ativo vendido.
    Se o resultado combinado ficar negativo, NÃO há crédito de impostos —
    o IRPJ/CSLL fica travado em zero (nunca negativo) e o prejuízo passa
    direto para o Lucro Líquido, sem compensação tributária.
    """
    dre = calcular_dre(valor_venda, tipo_cfg, regras=REGRAS_ALCADAS, **kwargs)
    if bonus_tipo_cfg is None:
        return dre

    custos_bonus = dict(bonus_tipo_cfg["custos_fixos_diretos"])
    total_custos_bonus = sum(custos_bonus.values())

    resultado_combinado = dre["resultado_operacional"] - total_custos_bonus
    irpj_csll = max(0.0, resultado_combinado) * REGRAS_ALCADAS["irpj_csll_pct"]
    lucro_liquido = resultado_combinado - irpj_csll
    margem_liquida_pct = (lucro_liquido / valor_venda) if valor_venda else 0.0

    dre = dict(dre)
    dre["bonificacao"] = {"custos_fixos_ativo_bonus": custos_bonus, "total": total_custos_bonus}
    dre["resultado_operacional"] = resultado_combinado
    dre["irpj_csll"] = irpj_csll
    dre["lucro_liquido"] = lucro_liquido
    dre["margem_liquida_pct"] = margem_liquida_pct
    return dre


def resolver_preco_para_margem(tipo_cfg: dict, margem_alvo: float, bv_pct: float = None,
                                producao_r: float = None, repasse_pct: float = None) -> float | None:
    """Resolve algebricamente o Valor de Venda (E) que entrega a margem
    líquida alvo para este ativo/tipo.

    Como o Resultado Operacional é linear em E (Resultado = k*E + F_fixo,
    onde k é a soma dos custos percentuais e F_fixo é o líquido dos custos
    fixos menos receitas fixas) e o IRPJ/CSLL é um percentual flat sobre o
    Resultado (sem teto de isenção), a margem líquida também é uma função
    simples de E:

        margem(E) = (1 - irpj_csll_pct) * k  +  (1 - irpj_csll_pct) * F_fixo / E

    Isolando E:  E = (1 - irpj_csll_pct) * F_fixo / (margem_alvo - (1 - irpj_csll_pct) * k)

    Retorna None quando a meta é matematicamente inatingível para este
    ativo em QUALQUER preço positivo — o que acontece sempre que o "teto
    assintótico" de margem, (1 - irpj_csll_pct) * k, já é menor ou igual à
    meta (ex.: com BV padrão de 20%, o teto de margem da maioria dos ativos
    é ~38,8% — abaixo dos 40% da premissa 3). É exatamente o cenário que a
    premissa 1 antecipa com "quando possível".
    """
    custos_fixos = dict(tipo_cfg["custos_fixos_diretos"])
    if producao_r is not None:
        if producao_r:
            custos_fixos["producao"] = producao_r
        else:
            custos_fixos.pop("producao", None)
    if bv_pct is None:
        bv_pct = tipo_cfg["bv"]
    if repasse_pct is None:
        repasse_pct = tipo_cfg["repasse_pct"]

    trib = REGRAS_ALCADAS["tributos_receita"]
    pct_receita = trib["pis"] + trib["cofins"] + trib["iss"]
    k = 1.0 - pct_receita - repasse_pct - bv_pct - tipo_cfg["comissao"] - tipo_cfg["inadimplencia"]
    f_fixo = tipo_cfg.get("recebimento_editoracao", 0.0) - sum(custos_fixos.values())

    fator_liquido = 1.0 - REGRAS_ALCADAS["irpj_csll_pct"]  # 0.66
    denom = margem_alvo - fator_liquido * k
    if abs(denom) < 1e-9:
        return None
    preco = (fator_liquido * f_fixo) / denom
    if preco is None or preco <= 0 or preco != preco:  # preco!=preco descarta NaN
        return None
    return preco


def calcular_desconto_pct(valor_venda: float, preco_tabela: float) -> float:
    """% de desconto do Valor de Venda negociado sobre o Preço de Tabela
    (usado nas alçadas de Executivo e Gerente Comercial — premissa 2)."""
    if not preco_tabela:
        return 0.0
    return max(0.0, 1.0 - (valor_venda / preco_tabela))


def determinar_alcada(dre: dict, preco_tabela: float, valor_venda: float,
                       regras: dict = REGRAS_ALCADAS) -> dict:
    """Alçada híbrida (premissa 2): Executivo e Gerente Comercial são
    limitados por % de DESCONTO sobre o Preço de Tabela; Diretoria
    Comercial e Diretoria Financeira/CEO são limitados por % mínima de
    LUCRO LÍQUIDO sobre o Valor de Venda.

    Abaixo do piso absoluto de margem (15% — o mínimo da Diretoria
    Financeira/CEO, a maior alçada que existe) NINGUÉM autoriza: um
    desconto pequeno não "salva" uma negociação que fica abaixo do piso
    mínimo de rentabilidade da empresa.
    """
    desconto = calcular_desconto_pct(valor_venda, preco_tabela)
    margem = dre["margem_liquida_pct"]
    alcadas = regras["alcadas"]
    piso = regras["piso_margem_absoluto"]

    abaixo_piso = margem < piso
    if abaixo_piso:
        cargo_exigido = None  # nenhum perfil autoriza
    elif desconto <= alcadas["Executivo"]["valor"]:
        cargo_exigido = "Executivo"
    elif desconto <= alcadas["Gerente Comercial"]["valor"]:
        cargo_exigido = "Gerente Comercial"
    elif margem >= alcadas["Diretor Comercial"]["valor"]:
        cargo_exigido = "Diretor Comercial"
    else:
        cargo_exigido = "Diretor Financeiro"

    return {
        "desconto_pct": desconto,
        "margem_liquida_pct": margem,
        "abaixo_piso_absoluto": abaixo_piso,
        "cargo_exigido": cargo_exigido,
    }


def avaliar_autorizacao(perfil_solicitante: str, alcada: dict, regras: dict = REGRAS_ALCADAS) -> dict:
    if alcada["cargo_exigido"] is None:
        return {"autorizado": False, "cargo_exigido": None}
    hierarquia = regras["hierarquia_perfis"]
    autorizado = hierarquia[perfil_solicitante] >= hierarquia[alcada["cargo_exigido"]]
    return {"autorizado": autorizado, "cargo_exigido": alcada["cargo_exigido"]}


# ============================================================================
# 3) FUNÇÕES AUXILIARES DE FORMATAÇÃO
# ============================================================================

def fmt_moeda(valor: float) -> str:
    texto = f"R$ {valor:,.2f}"
    return texto.replace(",", "§").replace(".", ",").replace("§", ".")


def fmt_pct(valor: float) -> str:
    return f"{valor * 100:.1f}%"


def gerar_tabela_referencia() -> pd.DataFrame:
    """Monta a versão preenchida da aba "Tabela de Preço" da planilha
    original: para cada ativo/tipo, o Preço de Tabela, o Preço Sugerido
    para 40% de lucro líquido (premissa 3) e os 4 limites de alçada
    (premissa 2).

    Nota sobre o Preço Sugerido (40%): com BV padrão (20% na maioria dos
    ativos), o teto assintótico de margem líquida é ~38,8% — abaixo dos
    40% pedidos, então a meta é matematicamente inatingível em QUALQUER
    preço com BV ligado. Por isso esta coluna é calculada assumindo BV=0%
    (a mesma alavanca que a premissa 4 já libera para negociação manual),
    o que a torna atingível para praticamente todos os ativos. Já o Limite
    Executivo/Gerente (desconto) e o Preço mínimo de Diretoria (margem)
    usam o BV padrão de cada ativo, por serem limites da alçada normal, não
    uma meta comercial "de esforço".
    """
    linhas = []
    alc = REGRAS_ALCADAS["alcadas"]
    for ativo, tipos in ATIVOS.items():
        for tipo_label, cfg in tipos.items():
            preco_tabela = cfg["preco_tabela"]
            preco_sugerido_40 = resolver_preco_para_margem(
                cfg, REGRAS_ALCADAS["lucro_liquido_alvo_sugerido"], bv_pct=0.0
            )
            limite_executivo = preco_tabela * (1 - alc["Executivo"]["valor"])
            limite_gerente = preco_tabela * (1 - alc["Gerente Comercial"]["valor"])
            preco_min_dir_comercial = resolver_preco_para_margem(cfg, alc["Diretor Comercial"]["valor"])
            preco_min_dir_financeiro = resolver_preco_para_margem(cfg, alc["Diretor Financeiro"]["valor"])
            linhas.append({
                "Ativo": ativo,
                "Tipo / Posição": tipo_label,
                "Preço de Tabela (R$)": preco_tabela,
                "Preço Sugerido 40% líquido, sem BV (R$)": preco_sugerido_40,
                "Limite Executivo — até 20% desconto (R$)": limite_executivo,
                "Limite Gerente Comercial — até 30% desconto (R$)": limite_gerente,
                "Preço mín. Diretoria Comercial — 20% margem (R$)": preco_min_dir_comercial,
                "Preço mín. Diretoria Financeira/CEO — 15% margem (R$)": preco_min_dir_financeiro,
            })
    return pd.DataFrame(linhas)


# ============================================================================
# 4) INTERFACE — STREAMLIT
# ============================================================================

st.set_page_config(
    page_title="Calculadora Comercial | Grupo Coruja",
    page_icon=str(LOGO_PATH) if LOGO_PATH.exists() else "📊",
    layout="wide",
)

# ---- Identidade visual Grupo Coruja (crimson + teal, extraídas do logo) ----
# NOTA: o HTML/CSS abaixo é escrito SEM indentação (textwrap.dedent) de propósito —
# um bloco indentado com 4+ espaços é interpretado pelo parser Markdown como um
# bloco de código e aparece como texto cru na tela, em vez de ser renderizado.
_CSS = """
<style>
html, body, [class*="css"]  {
    font-family: system-ui, -apple-system, "Segoe UI", sans-serif;
}
.coruja-header{
    display:flex; align-items:center; gap:18px;
    padding:20px 26px; margin-bottom:22px; border-radius:16px;
    background: linear-gradient(135deg, rgba(220,34,74,0.08), rgba(7,127,129,0.08));
    border:1px solid rgba(11,11,11,0.08);
    border-top: 4px solid transparent;
    border-image: linear-gradient(90deg, #dc224a, #077f81) 1;
}
.coruja-header img{
    height:52px; width:auto; flex:none;
    background:#ffffff; padding:8px 12px; border-radius:10px;
    box-shadow: 0 1px 3px rgba(11,11,11,0.12);
}
.coruja-header h1{
    font-size:1.55rem; font-weight:800; letter-spacing:-0.01em;
    margin:0; color:#0b0b0b;
}
.coruja-header p{
    margin:4px 0 0; font-size:0.98rem; font-weight:600; color:#077f81;
}
</style>
"""
st.markdown(_CSS, unsafe_allow_html=True)

if LOGO_PATH.exists():
    st.logo(str(LOGO_PATH), icon_image=str(LOGO_PATH))

_logo_img_tag = f'<img src="{LOGO_DATA_URI}" alt="Grupo Coruja" />' if LOGO_DATA_URI else ""
_header_html = f"""<div class="coruja-header">{_logo_img_tag}<div>
<h1>Calculadora Comercial &amp; Liberação de Alçadas</h1>
<p>Grupo Coruja — verificação automática de alçada de aprovação</p>
</div></div>"""
st.markdown(_header_html, unsafe_allow_html=True)

# ============================================================================
# 4a) IDENTIFICAÇÃO DO SOLICITANTE (obrigatória antes de qualquer cálculo)
#     O cargo NÃO é mais autodeclarado. Cada pessoa tem usuário e senha
#     próprios, cadastrados em Secrets (nunca no código-fonte nem no
#     GitHub) — o cargo vem desse cadastro, travado ao login, e não é
#     digitado nem escolhido livremente na tela. As senhas são guardadas
#     com hash (PBKDF2-HMAC-SHA256 com salt por usuário), nunca em texto
#     puro — mesmo quem tem acesso aos Secrets não vê a senha real.
# ============================================================================
def _usuarios_configurados() -> bool:
    """True se existe pelo menos um usuário cadastrado em Secrets."""
    try:
        return "usuarios" in st.secrets and len(st.secrets["usuarios"]) > 0
    except Exception:
        return False


def _verificar_senha(senha: str, senha_hash: str) -> bool:
    """Confere a senha digitada contra o hash "salt_hex:hash_hex" salvo em
    Secrets, usando comparação em tempo constante (evita timing attacks)."""
    try:
        salt_hex, hash_hex = str(senha_hash).split(":", 1)
        salt = bytes.fromhex(salt_hex)
        esperado = bytes.fromhex(hash_hex)
    except (ValueError, AttributeError):
        return False
    calculado = hashlib.pbkdf2_hmac("sha256", senha.encode("utf-8"), salt, 200_000)
    return hmac.compare_digest(calculado, esperado)


def autenticar(email: str, senha: str, usuarios) -> dict | None:
    """Confere e-mail + senha contra o cadastro em Secrets e retorna nome e
    cargo do solicitante — ou None se a credencial for inválida, o usuário
    não existir, ou o cargo cadastrado não for um dos perfis válidos
    (protege contra erro de digitação em Secrets liberar acesso indevido).
    Recebe `usuarios` como objeto dict-like para poder ser testado com um
    dict comum, sem depender de st.secrets diretamente.
    """
    email_norm = str(email).strip().lower()
    if not email_norm or not senha:
        return None
    usuarios_norm = {str(k).strip().lower(): v for k, v in dict(usuarios).items()}
    registro = usuarios_norm.get(email_norm)
    if not registro:
        return None
    cargo = registro.get("cargo")
    senha_hash = registro.get("senha_hash")
    if not cargo or cargo not in PERFIS or not senha_hash:
        return None
    if not _verificar_senha(senha, senha_hash):
        return None
    nome = registro.get("nome") or email_norm
    return {"nome": nome, "cargo": cargo, "email": email_norm}


# ⚠️ TEMPORÁRIO (desativado a pedido do usuário em 2026-09-18, enquanto o
# Secrets do Streamlit Cloud não está 100% configurado): com EXIGIR_LOGIN =
# False, a calculadora libera acesso sem pedir e-mail/senha — o cargo volta a
# ser escolhido na tela (autodeclarado), exatamente como antes do controle de
# acesso. Nada da lógica de login foi removida — é só religar, trocando para
# True, assim que o cadastro em Secrets estiver funcionando de verdade.
EXIGIR_LOGIN = False

if EXIGIR_LOGIN:
    if not _usuarios_configurados():
        st.error(
            "Nenhum usuário cadastrado ainda nesta implantação. Configure a seção "
            "**[usuarios]** em Settings → Secrets do Streamlit Community Cloud "
            "antes de usar a calculadora."
        )
        st.stop()

    if "identificacao" not in st.session_state:
        st.write("")
        col_esq, col_meio, col_dir = st.columns([1, 1.3, 1])
        with col_meio:
            with st.container(border=True):
                st.subheader("Entrar para continuar")
                st.caption("Seu cargo é definido pelo cadastro interno — não é mais escolhido na tela.")
                with st.form("form_login"):
                    email_input = st.text_input("E-mail corporativo")
                    senha_input = st.text_input("Senha", type="password")
                    entrar = st.form_submit_button("Entrar", width="stretch", type="primary")

                if entrar:
                    identidade = autenticar(email_input, senha_input, st.secrets.get("usuarios", {}))
                    if identidade is None:
                        st.error("E-mail ou senha inválidos.")
                    else:
                        st.session_state["identificacao"] = identidade
                        st.rerun()
        st.stop()

    identificacao = st.session_state["identificacao"]
    perfil = identificacao["cargo"]  # determinado pelo cadastro — não é mais escolhido na calculadora

# ---------------------------- Sidebar --------------------------------------
with st.sidebar:
    st.header("Solicitante")
    if EXIGIR_LOGIN:
        st.info(f"**{identificacao['nome']}**  \nCargo: {perfil}")
        if st.button("Sair", width="stretch"):
            st.session_state.pop("identificacao", None)
            st.session_state.pop("ultimo_calculo", None)
            st.rerun()
    else:
        st.caption("⚠️ Login temporariamente desativado — informe seu nome e cargo abaixo.")
        nome_input = st.text_input("Seu nome")
        cargo_input = st.selectbox("Seu cargo", PERFIS, index=0)
        identificacao = {"nome": nome_input.strip() or "Não informado", "cargo": cargo_input, "email": ""}
        perfil = identificacao["cargo"]

    st.divider()
    st.header("Dados da Negociação")
    # O seletor de "Ativo" fica fora de um st.form (que só atualiza os
    # demais campos ao ser enviado) porque as opções de "Tipo / Posição"
    # dependem do ativo escolhido — cada ativo tem seu próprio conjunto de
    # tipos (ex.: "Cota Inteira"/"Meia Cota" nos painéis, mas nomes de
    # imóvel/posição nas Empenas Estáticas e no Busdoor).
    pi_numero = st.text_input("Nº do PI Negociado", placeholder="Ex.: PI-2026-0842")
    ativo = st.selectbox("Ativo Negociado", list(ATIVOS.keys()), index=0, key="ativo_select")
    tipos_disponiveis = list(ATIVOS[ativo].keys())
    rotulo_tipo = "Tipo de Cota" if tipos_disponiveis in (["Cota Inteira", "Meia Cota"],) else "Tipo / Posição"
    tipo_cota = st.selectbox(rotulo_tipo, tipos_disponiveis, index=0, key=f"tipo_select_{ativo}")
    tipo_cfg = ATIVOS[ativo][tipo_cota]

    st.caption(
        "BV, produção e (só no Envelopamento Fretado) repasse são negociáveis — os "
        "campos abaixo já vêm preenchidos com o padrão da planilha, mas podem ser "
        "ajustados para esta negociação específica."
    )
    bv_pct = st.number_input(
        "BV — Bonificação de Veiculação (%)",
        min_value=0.0, max_value=100.0, value=round(tipo_cfg["bv"] * 100, 2), step=1.0,
        key=f"bv_{ativo}_{tipo_cota}",
    ) / 100.0
    producao_default = float(tipo_cfg["custos_fixos_diretos"].get("producao", 0.0))
    producao_r = st.number_input(
        "Produção (R$)", min_value=0.0, value=producao_default, step=50.0,
        key=f"prod_{ativo}_{tipo_cota}",
    )
    if ativo == "Envelopamento Fretado":
        repasse_pct = st.number_input(
            "Repasse (%) — Envelopamento Fretado",
            min_value=0.0, max_value=100.0, value=round(tipo_cfg["repasse_pct"] * 100, 2), step=1.0,
            key=f"repasse_fretado_{tipo_cota}",
        ) / 100.0
    else:
        repasse_pct = None  # usa o padrão do catálogo para os demais ativos

    st.divider()
    tem_bonificacao = st.checkbox(
        "Negociação com bonificação (um ativo extra dado como bônus, faturamento R$ 0)?"
    )
    ativo_bonus = tipo_bonus = None
    if tem_bonificacao:
        st.caption(
            "O ativo bonificado não gera receita — seus custos fixos entram diminuindo o "
            "resultado do ativo vendido, e um resultado combinado negativo não gera crédito "
            "de impostos."
        )
        ativo_bonus = st.selectbox("Ativo dado como bônus", list(ATIVOS.keys()), key="ativo_bonus_select")
        tipos_bonus_disponiveis = list(ATIVOS[ativo_bonus].keys())
        tipo_bonus = st.selectbox(
            "Tipo / Posição do bônus", tipos_bonus_disponiveis, key=f"tipo_bonus_select_{ativo_bonus}"
        )

    st.divider()
    valor_pi = st.number_input(
        "Valor do PI Proposto (R$)",
        min_value=0.0,
        value=30000.0,
        step=500.0,
        format="%.2f",
    )
    calcular = st.button("Calcular Autorização", width="stretch", type="primary")

    st.caption(
        "Os parâmetros de custo, tributos e alçadas usados no cálculo são "
        "confidenciais e não são exibidos nesta interface — apenas o "
        "resultado final da simulação."
    )

tab_calc, tab_tabela = st.tabs(["🔐 Calculadora de Autorização", "📋 Tabela de Preços de Referência"])

with tab_calc:
    if calcular:
        st.session_state["ultimo_calculo"] = {
            "pi_numero": pi_numero,
            "nome": identificacao["nome"],
            "perfil": perfil,
            "ativo": ativo,
            "tipo_cota": tipo_cota,
            "bv_pct": bv_pct,
            "producao_r": producao_r,
            "repasse_pct": repasse_pct,
            "ativo_bonus": ativo_bonus,
            "tipo_bonus": tipo_bonus,
            "valor_pi": valor_pi,
            "timestamp": datetime.now(),
        }

    if "ultimo_calculo" not in st.session_state:
        st.info("Preencha os dados da negociação na barra lateral e clique em **Calcular Autorização**.")
    else:
        dados = st.session_state["ultimo_calculo"]
        tipo_cfg_calc = ATIVOS[dados["ativo"]][dados["tipo_cota"]]
        bonus_tipo_cfg = (
            ATIVOS[dados["ativo_bonus"]][dados["tipo_bonus"]] if dados.get("ativo_bonus") else None
        )

        dre = calcular_dre_com_bonificacao(
            dados["valor_pi"], tipo_cfg_calc, bonus_tipo_cfg=bonus_tipo_cfg,
            bv_pct=dados["bv_pct"], producao_r=dados["producao_r"], repasse_pct=dados["repasse_pct"],
        )
        alcada = determinar_alcada(dre, tipo_cfg_calc["preco_tabela"], dados["valor_pi"])
        autorizacao = avaliar_autorizacao(dados["perfil"], alcada)

        # ---- Cabeçalho da negociação ---------------------------------------
        col_a, col_b, col_c, col_d, col_e, col_f = st.columns(6)
        col_a.markdown(f"**Nº do PI**  \n{dados['pi_numero'] or '—'}")
        col_b.markdown(f"**Ativo**  \n{dados['ativo']} ({dados['tipo_cota']})")
        col_c.markdown(f"**BV**  \n{fmt_pct(dados['bv_pct'])}")
        col_d.markdown(
            f"**Bonificação**  \n{'Sim — ' + dados['ativo_bonus'] if dados.get('ativo_bonus') else 'Não'}"
        )
        col_e.markdown(f"**Solicitante**  \n{dados['nome']} ({dados['perfil']})")
        col_f.markdown(f"**Simulado em**  \n{dados['timestamp'].strftime('%d/%m/%Y %H:%M')}")

        st.divider()

        # ---- Cartão de status de autorização -------------------------------
        # Somente o resultado (autorizado / recusado) é exibido — nenhum valor,
        # margem ou percentual calculado aparece nesta aba, apenas a decisão final.
        if autorizacao["autorizado"]:
            bg, fg, status_txt = "#C6EFCE", "#006100", "AUTORIZADO"
        else:
            bg, fg, status_txt = "#FFC7CE", "#9C0006", "RECUSADO"

        if alcada["abaixo_piso_absoluto"]:
            motivo = (
                "Esta negociação está abaixo do piso mínimo de rentabilidade aceitável pela "
                "empresa e não pode ser aprovada em nenhuma alçada."
            )
        elif autorizacao["autorizado"]:
            motivo = f"Esta negociação está dentro da alçada de aprovação do perfil <strong>{dados['perfil']}</strong>."
        else:
            motivo = (
                f"Esta negociação exige aprovação de um perfil com alçada superior — no mínimo "
                f"<strong>{alcada['cargo_exigido']}</strong>. O perfil selecionado (<strong>{dados['perfil']}</strong>) "
                f"não possui alçada suficiente."
            )

        _status_html = (
            f'<div style="background-color:{bg}; color:{fg}; padding:24px 28px; border-radius:12px; '
            f'border:1px solid {fg}33; margin-bottom:8px;">'
            f'<div style="font-size:1.6rem; font-weight:700; letter-spacing:0.03em;">{status_txt}</div>'
            f'<div style="font-size:1rem; margin-top:8px;">{motivo}</div>'
            f"</div>"
        )
        st.markdown(_status_html, unsafe_allow_html=True)

        st.markdown("")
        st.caption(
            "Dashboard de uso interno — Grupo Coruja. As premissas de custo, os valores calculados "
            "e as regras de alçada não são exibidos nesta aba — apenas a decisão final de autorização."
        )

with tab_tabela:
    st.caption(
        "Referência de preços por ativo/tipo, calculada a partir das premissas atuais "
        "(tributos, alçadas e metas de margem líquida) — aqui os valores APARECEM, "
        "diferente da aba de autorização. '—' indica que a meta de margem não é "
        "atingível para aquele ativo em nenhum preço positivo (a premissa geral já "
        "prevê isso com o termo 'quando possível')."
    )
    df_ref = gerar_tabela_referencia()
    df_fmt = df_ref.copy()
    for col in df_fmt.columns[2:]:
        df_fmt[col] = df_fmt[col].apply(lambda v: fmt_moeda(v) if v is not None else "—")
    st.dataframe(df_fmt, width="stretch", hide_index=True, height=560)
