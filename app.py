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

from datetime import datetime

import plotly.graph_objects as go
import streamlit as st

# ============================================================================
# 1) DADOS PROTEGIDOS — PREMISSAS CONFIDENCIAIS (não exibir na interface)
#    Fonte: planilha "Rascunho - 04.08" (abas Premissas / Painel Jd Oceânico)
# ============================================================================

# ---- Regras gerais de tributos, faixas de lucro e alçadas de aprovação ----
# (aba "Premissas" — válidas para todos os ativos do Grupo Coruja)
REGRAS_ALCADAS = {
    "tributos_receita": {
        "pis": 0.0165,
        "cofins": 0.0760,
        "iss": 0.0500,
    },
    "tributos_lucro": {
        "irpj": 0.15,
        "adicional_irpj": 0.10,
        "limite_isencao_adicional_mensal": 20000.00,  # consolidado por ativo
        "csll": 0.09,
    },
    "contrato": {
        "prazo_meses": 12,
        "ocupacao_alvo": 0.85,
    },
    # Faixas de Lucro Líquido (% sobre o Valor de Venda) que definem a alçada
    "faixas_lucro_liquido": {
        1: 0.35,  # Faixa 1 — Executivo Comercial autoriza até aqui
        2: 0.25,  # Faixa 2 — Gerente Comercial autoriza até aqui
        3: 0.15,  # Faixa 3 — Diretoria autoriza até aqui
    },
    # Classificação estratégica do ativo/negociação
    "classificacao_estrategica": {
        "grupo_a_lucro_min": 40000.00,
        "grupo_a_ticket_min": 100000.00,
        "grupo_c_margem_max": 0.12,
        "grupo_c_lucro_max": 5000.00,
    },
    # Hierarquia de aprovação — quanto maior o número, maior a alçada.
    # "Diretor Comercial" e "Diretor Financeiro" dividem o mesmo nível
    # máximo (Diretoria), espelhando a aba "Premissas" da planilha, que
    # trata ambos como "Diretoria Financeira / CEO".
    "hierarquia_perfis": {
        "Executivo": 1,
        "Gerente Comercial": 2,
        "Diretor Comercial": 3,
        "Diretor Financeiro": 3,
    },
    # Rótulo da alçada mínima exigida por faixa (1 = mais permissiva)
    "rotulo_alcada": {
        1: "Executivo Comercial",
        2: "Gerente Comercial",
        3: "Diretoria (Comercial ou Financeira)",
        4: "NÃO PERMITIDO — abaixo da Faixa 3 ou do Break-Even",
    },
}

# ---- Estrutura de custos específica do ativo "Painel Jd. Oceânico" ----
# (aba "Painel Jd Oceânico" — custos fixos diretos + taxas comerciais)
PREMISSAS_JD_OCEANICO = {
    "Cota Inteira": {
        "n_unidades_ativo": 8,     # nº de cotas inteiras que compõem o ativo
        "repasse": 2200.00,
        "custos_fixos_diretos": {
            "painel_led": 2062.50,
            "energia": 1125.00,
            "internet_cameras": 150.00,
            "tap": 6250.00,
        },
        "bv": 0.20,            # Bonificação de Veiculação (% sobre Valor de Venda)
        "comissao": 0.05,      # Comissão do executivo (% sobre Valor de Venda)
        "inadimplencia": 0.02, # Provisão de inadimplência (% sobre Valor de Venda)
    },
    "Meia Cota": {
        "n_unidades_ativo": 16,    # nº de meias-cotas que compõem o ativo
        "repasse": 1100.00,
        "custos_fixos_diretos": {
            "painel_led": 1031.25,
            "energia": 562.50,
            "internet_cameras": 75.00,
            "tap": 3125.00,
        },
        "bv": 0.20,
        "comissao": 0.05,
        "inadimplencia": 0.02,
    },
}

# Catálogo de ativos suportados pela calculadora. Para habilitar um novo
# ativo, adicione aqui um dicionário no mesmo formato de PREMISSAS_JD_OCEANICO.
ATIVOS = {
    "Painel Jd. Oceânico": PREMISSAS_JD_OCEANICO,
}

PERFIS = ["Executivo", "Gerente Comercial", "Diretor Comercial", "Diretor Financeiro"]
TIPOS_COTA = ["Cota Inteira", "Meia Cota"]


# ============================================================================
# 2) MOTOR DE CÁLCULO (DRE, classificação estratégica e alçada)
# ============================================================================

def calcular_dre(valor_venda: float, tipo_cota: str, premissas_ativo: dict, regras: dict) -> dict:
    """Reproduz a apuração de DRE por cota/meia-cota da planilha mestre."""
    p = premissas_ativo[tipo_cota]
    trib_receita = regras["tributos_receita"]
    trib_lucro = regras["tributos_lucro"]

    pis = valor_venda * trib_receita["pis"]
    cofins = valor_venda * trib_receita["cofins"]
    iss = valor_venda * trib_receita["iss"]
    total_tributos_receita = pis + cofins + iss

    repasse = p["repasse"]
    custos_fixos = p["custos_fixos_diretos"]
    total_custos_fixos = sum(custos_fixos.values())

    bv = valor_venda * p["bv"]
    comissao = valor_venda * p["comissao"]
    inadimplencia = valor_venda * p["inadimplencia"]

    margem_bruta = valor_venda - total_tributos_receita - repasse - total_custos_fixos
    resultado_operacional = margem_bruta - bv - comissao - inadimplencia

    receita_liquida_impostos = valor_venda - total_tributos_receita
    margem_contribuicao = valor_venda - total_tributos_receita - repasse - bv - comissao - inadimplencia
    margem_contribuicao_pct = (margem_contribuicao / valor_venda) if valor_venda else 0.0
    margem_ebitda_pct = (resultado_operacional / receita_liquida_impostos) if receita_liquida_impostos else 0.0

    # Adicional de IRPJ: na planilha mestre o limite de isenção mensal
    # (R$ 20.000) é apurado sobre o resultado CONSOLIDADO do ativo (todas
    # as cotas) e depois rateado entre as unidades. Como esta calculadora
    # avalia uma negociação por vez, aplicamos o mesmo limite já rateado
    # proporcionalmente ao número de unidades do ativo (aproximação que
    # assume portfólio simétrico — mesma lógica-base da planilha).
    limite_rateado = trib_lucro["limite_isencao_adicional_mensal"] / p["n_unidades_ativo"]
    irpj = resultado_operacional * trib_lucro["irpj"]
    excedente_adicional = max(0.0, resultado_operacional - limite_rateado)
    adicional_irpj = excedente_adicional * trib_lucro["adicional_irpj"]
    csll = resultado_operacional * trib_lucro["csll"]

    lucro_liquido = resultado_operacional - irpj - adicional_irpj - csll
    margem_liquida_pct = (lucro_liquido / valor_venda) if valor_venda else 0.0

    break_even = None
    if margem_contribuicao_pct > 0:
        break_even = (total_custos_fixos + irpj + adicional_irpj + csll) / margem_contribuicao_pct

    return {
        "valor_venda": valor_venda,
        "pis": pis, "cofins": cofins, "iss": iss,
        "total_tributos_receita": total_tributos_receita,
        "repasse": repasse,
        "custos_fixos_diretos": custos_fixos,
        "total_custos_fixos": total_custos_fixos,
        "bv": bv, "comissao": comissao, "inadimplencia": inadimplencia,
        "margem_bruta": margem_bruta,
        "margem_contribuicao": margem_contribuicao,
        "margem_contribuicao_pct": margem_contribuicao_pct,
        "resultado_operacional": resultado_operacional,
        "margem_ebitda_pct": margem_ebitda_pct,
        "irpj": irpj,
        "adicional_irpj": adicional_irpj,
        "csll": csll,
        "lucro_liquido": lucro_liquido,
        "margem_liquida_pct": margem_liquida_pct,
        "break_even": break_even,
    }


def classificar_grupo_estrategico(dre: dict, regras: dict) -> str:
    c = regras["classificacao_estrategica"]
    if dre["lucro_liquido"] >= c["grupo_a_lucro_min"] and dre["valor_venda"] >= c["grupo_a_ticket_min"]:
        return "A — Gerador de Caixa"
    if dre["margem_liquida_pct"] <= c["grupo_c_margem_max"] or dre["lucro_liquido"] <= c["grupo_c_lucro_max"]:
        return "C — Tático"
    return "B — Giro"


def determinar_alcada(dre: dict, regras: dict) -> dict:
    """Retorna a faixa (1 a 4) e o cargo mínimo exigido para aprovar a proposta."""
    faixas = regras["faixas_lucro_liquido"]
    margem = dre["margem_liquida_pct"]
    abaixo_break_even = dre["lucro_liquido"] < 0

    if abaixo_break_even or margem < faixas[3]:
        faixa = 4
    elif margem < faixas[2]:
        faixa = 3
    elif margem < faixas[1]:
        faixa = 2
    else:
        faixa = 1

    return {
        "faixa": faixa,
        "cargo_minimo": regras["rotulo_alcada"][faixa],
        "abaixo_break_even": abaixo_break_even,
    }


def avaliar_autorizacao(perfil_solicitante: str, alcada: dict, regras: dict) -> dict:
    faixa = alcada["faixa"]
    nivel_solicitante = regras["hierarquia_perfis"][perfil_solicitante]
    autorizado = (faixa <= 3) and (nivel_solicitante >= faixa)
    return {"autorizado": autorizado, "nivel_solicitante": nivel_solicitante, "faixa_exigida": faixa}


# ============================================================================
# 3) FUNÇÕES AUXILIARES DE FORMATAÇÃO
# ============================================================================

def fmt_moeda(valor: float) -> str:
    texto = f"R$ {valor:,.2f}"
    return texto.replace(",", "§").replace(".", ",").replace("§", ".")


def fmt_pct(valor: float) -> str:
    return f"{valor * 100:.1f}%"


# ============================================================================
# 4) INTERFACE — STREAMLIT
# ============================================================================

st.set_page_config(
    page_title="Calculadora Comercial | Grupo Coruja",
    page_icon="📊",
    layout="wide",
)

st.markdown(
    """
    <style>
    div[data-testid="stMetric"] {
        background-color: rgba(127, 127, 127, 0.06);
        border-radius: 10px;
        padding: 14px 16px;
    }
    </style>
    """,
    unsafe_allow_html=True,
)

# ---------------------------- Sidebar --------------------------------------
with st.sidebar:
    st.header("Dados da Negociação")
    with st.form("form_negociacao"):
        pi_numero = st.text_input("Nº do PI Negociado", placeholder="Ex.: PI-2026-0842")
        perfil = st.selectbox("Perfil do Solicitante", PERFIS, index=0)
        ativo = st.selectbox("Ativo Negociado", list(ATIVOS.keys()), index=0)
        tipo_cota = st.selectbox("Tipo de Cota", TIPOS_COTA, index=0)
        valor_pi = st.number_input(
            "Valor do PI Proposto (R$)",
            min_value=0.0,
            value=30000.0,
            step=500.0,
            format="%.2f",
        )
        calcular = st.form_submit_button("Calcular Autorização", width="stretch", type="primary")

    st.caption(
        "Os parâmetros de custo, tributos e alçadas usados no cálculo são "
        "confidenciais e não são exibidos nesta interface — apenas o "
        "resultado final da simulação."
    )

# ---------------------------- Corpo principal -------------------------------
st.title("Calculadora Comercial & Liberação de Alçadas")
st.caption("Grupo Coruja — simulação de rentabilidade e verificação automática de alçada de aprovação")

if not calcular and "ultimo_calculo" not in st.session_state:
    st.info("Preencha os dados da negociação na barra lateral e clique em **Calcular Autorização**.")
    st.stop()

if calcular:
    st.session_state["ultimo_calculo"] = {
        "pi_numero": pi_numero,
        "perfil": perfil,
        "ativo": ativo,
        "tipo_cota": tipo_cota,
        "valor_pi": valor_pi,
        "timestamp": datetime.now(),
    }

dados = st.session_state["ultimo_calculo"]
premissas_ativo = ATIVOS[dados["ativo"]]

dre = calcular_dre(dados["valor_pi"], dados["tipo_cota"], premissas_ativo, REGRAS_ALCADAS)
grupo_estrategico = classificar_grupo_estrategico(dre, REGRAS_ALCADAS)
alcada = determinar_alcada(dre, REGRAS_ALCADAS)
autorizacao = avaliar_autorizacao(dados["perfil"], alcada, REGRAS_ALCADAS)

# ---- Cabeçalho da negociação -----------------------------------------------
col_a, col_b, col_c, col_d = st.columns(4)
col_a.markdown(f"**Nº do PI**  \n{dados['pi_numero'] or '—'}")
col_b.markdown(f"**Ativo**  \n{dados['ativo']} ({dados['tipo_cota']})")
col_c.markdown(f"**Solicitante**  \n{dados['perfil']}")
col_d.markdown(f"**Simulado em**  \n{dados['timestamp'].strftime('%d/%m/%Y %H:%M')}")

st.divider()

# ---- KPIs -------------------------------------------------------------------
k1, k2, k3, k4, k5 = st.columns(5)
k1.metric("Valor do PI Proposto", fmt_moeda(dre["valor_venda"]))
k2.metric("Margem de Contribuição", fmt_pct(dre["margem_contribuicao_pct"]))
k3.metric("Resultado Operacional / EBITDA", fmt_moeda(dre["resultado_operacional"]), fmt_pct(dre["margem_ebitda_pct"]))
k4.metric("Lucro Líquido", fmt_moeda(dre["lucro_liquido"]))
k5.metric("Margem Líquida", fmt_pct(dre["margem_liquida_pct"]))

st.caption(f"Classificação estratégica do negócio: **Grupo {grupo_estrategico}**")

st.markdown("")

# ---- Cartão de status de autorização ---------------------------------------
if autorizacao["autorizado"]:
    bg, fg, status_txt = "#C6EFCE", "#006100", "AUTORIZADO"
else:
    bg, fg, status_txt = "#FFC7CE", "#9C0006", "RECUSADO"

if alcada["faixa"] == 4:
    if alcada["abaixo_break_even"]:
        motivo = "a proposta resulta em prejuízo (abaixo do ponto de equilíbrio) e não pode ser aprovada em nenhuma alçada."
    else:
        motivo = (
            f"a margem líquida de {fmt_pct(dre['margem_liquida_pct'])} está abaixo do piso mínimo de "
            f"{fmt_pct(REGRAS_ALCADAS['faixas_lucro_liquido'][3])} — desconto não permitido em nenhuma alçada."
        )
elif autorizacao["autorizado"]:
    motivo = (
        f"a margem líquida de {fmt_pct(dre['margem_liquida_pct'])} está dentro da alçada de "
        f"**{dados['perfil']}** (nível mínimo exigido: {alcada['cargo_minimo']})."
    )
else:
    motivo = (
        f"a margem líquida de {fmt_pct(dre['margem_liquida_pct'])} exige aprovação de, no mínimo, "
        f"**{alcada['cargo_minimo']}**. O perfil selecionado (**{dados['perfil']}**) não possui alçada suficiente."
    )

st.markdown(
    f"""
    <div style="background-color:{bg}; color:{fg}; padding:20px 24px; border-radius:12px;
                border:1px solid {fg}33; margin-bottom:8px;">
        <div style="font-size:1.4rem; font-weight:700; letter-spacing:0.03em;">{status_txt}</div>
        <div style="font-size:0.98rem; margin-top:6px;">{motivo}</div>
    </div>
    """,
    unsafe_allow_html=True,
)

st.markdown("")

# ============================================================================
# 5) GRÁFICOS INTERATIVOS (Plotly)
# ============================================================================

col_wf, col_gauge = st.columns([1.4, 1])

with col_wf:
    st.subheader("Composição da DRE")
    fig_wf = go.Figure(
        go.Waterfall(
            orientation="v",
            measure=[
                "absolute", "relative", "relative",
                "total", "relative", "relative", "relative",
                "total", "relative", "relative", "relative",
                "total",
            ],
            x=[
                "Valor de Venda", "Tributos s/ Receita", "Repasse + Custos Fixos",
                "Margem Bruta", "BV", "Comissão", "Inadimplência",
                "Resultado Operacional", "IRPJ", "Adicional IRPJ", "CSLL",
                "Lucro Líquido",
            ],
            y=[
                dre["valor_venda"], -dre["total_tributos_receita"], -(dre["repasse"] + dre["total_custos_fixos"]),
                0, -dre["bv"], -dre["comissao"], -dre["inadimplencia"],
                0, -dre["irpj"], -dre["adicional_irpj"], -dre["csll"],
                0,
            ],
            connector={"line": {"color": "rgba(120,120,120,0.4)"}},
            decreasing={"marker": {"color": "#E07A5F"}},
            increasing={"marker": {"color": "#3D9970"}},
            totals={"marker": {"color": "#264653"}},
        )
    )
    fig_wf.update_layout(
        height=420,
        margin=dict(l=10, r=10, t=10, b=10),
        showlegend=False,
        yaxis_title="R$",
    )
    st.plotly_chart(fig_wf, width="stretch")

with col_gauge:
    st.subheader("Margem Líquida vs. Alçadas")
    faixas = REGRAS_ALCADAS["faixas_lucro_liquido"]
    margem_pct = dre["margem_liquida_pct"] * 100
    fig_gauge = go.Figure(
        go.Indicator(
            mode="gauge+number",
            value=margem_pct,
            number={"suffix": "%"},
            gauge={
                "axis": {"range": [min(0, margem_pct - 5), 45]},
                "bar": {"color": fg},
                "steps": [
                    {"range": [min(0, margem_pct - 5), faixas[3] * 100], "color": "#FFC7CE"},
                    {"range": [faixas[3] * 100, faixas[2] * 100], "color": "#FFEB9C"},
                    {"range": [faixas[2] * 100, faixas[1] * 100], "color": "#FFFFCC"},
                    {"range": [faixas[1] * 100, 45], "color": "#C6EFCE"},
                ],
                "threshold": {
                    "line": {"color": "black", "width": 3},
                    "thickness": 0.8,
                    "value": margem_pct,
                },
            },
        )
    )
    fig_gauge.update_layout(height=420, margin=dict(l=10, r=10, t=30, b=10))
    st.plotly_chart(fig_gauge, width="stretch")

st.subheader("Estrutura de Custos e Resultado (% do Valor de Venda)")
labels = [
    "Tributos s/ Receita", "Repasse + Custos Fixos", "BV",
    "Comissão", "Inadimplência", "IRPJ + Adicional + CSLL", "Lucro Líquido",
]
valores = [
    dre["total_tributos_receita"],
    dre["repasse"] + dre["total_custos_fixos"],
    dre["bv"],
    dre["comissao"],
    dre["inadimplencia"],
    dre["irpj"] + dre["adicional_irpj"] + dre["csll"],
    max(dre["lucro_liquido"], 0),
]
fig_pie = go.Figure(
    go.Pie(
        labels=labels,
        values=valores,
        hole=0.45,
        textinfo="label+percent",
        marker={"colors": ["#E07A5F", "#F2CC8F", "#81B29A", "#3D5A80", "#B08968", "#6D6875", "#264653"]},
    )
)
fig_pie.update_layout(height=420, margin=dict(l=10, r=10, t=10, b=10))
st.plotly_chart(fig_pie, width="stretch")

st.caption(
    "Dashboard de uso interno — Grupo Coruja. As premissas de custo e as regras de alçada "
    "utilizadas nesta simulação são confidenciais e não são exibidas nesta tela."
)
