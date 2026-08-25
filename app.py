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
from datetime import datetime
from pathlib import Path

import streamlit as st

LOGO_PATH = Path(__file__).parent / "assets" / "logo_grupo_coruja.png"
LOGO_DATA_URI = None
if LOGO_PATH.exists():
    LOGO_DATA_URI = "data:image/png;base64," + base64.b64encode(LOGO_PATH.read_bytes()).decode("ascii")

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

# ---- Estrutura de custos por ativo ----
# (uma aba por ativo na planilha "Rascunho" — custos fixos diretos + taxas
# comerciais de cada um, extraídos diretamente das fórmulas de cada aba,
# não dos valores de exemplo digitados nas linhas). Cada ativo é um
# dicionário de "tipo/posição" -> premissas daquele tipo:
#   n_unidades_ativo    nº de unidades que dividem o limite mensal de
#                        isenção do Adicional de IRPJ (lido do divisor
#                        exato da fórmula de Adicional IRPJ de cada aba)
#   custos_fixos_diretos R$ fixos por unidade (repasse, painel, energia,
#                        produção, TAP etc. — um valor negativo representa
#                        uma receita adicional, ex. "Editoração" da Revista
#                        Península, que soma em vez de subtrair)
#   repasse_pct          usado só quando o repasse é um percentual sobre o
#                        Valor de Venda em vez de um valor fixo (ex. MUB
#                        Digital); default 0.0 nos demais ativos
#   bv / comissao / inadimplencia   percentuais sobre o Valor de Venda
def _tipo(n_unidades, custos_fixos, bv, comissao, inadimplencia, repasse_pct=0.0):
    return {
        "n_unidades_ativo": n_unidades,
        "custos_fixos_diretos": custos_fixos,
        "repasse_pct": repasse_pct,
        "bv": bv,
        "comissao": comissao,
        "inadimplencia": inadimplencia,
    }


ATIVOS = {
    # aba "Painel Jd Oceânico"
    "Painel Jd. Oceânico": {
        "Cota Inteira": _tipo(8, {"repasse": 2200.00, "painel_led": 2062.50, "energia": 1125.00,
                                   "internet_cameras": 150.00, "tap": 6250.00}, bv=0.10, comissao=0.05, inadimplencia=0.02),
        "Meia Cota": _tipo(16, {"repasse": 1100.00, "painel_led": 1031.25, "energia": 562.50,
                                 "internet_cameras": 75.00, "tap": 3125.00}, bv=0.20, comissao=0.05, inadimplencia=0.02),
    },
    # aba "Painel Presidente Vargas"
    "Painel Presidente Vargas": {
        "Cota Inteira": _tipo(8, {"repasse": 625.00, "energia": 462.50, "internet_cameras": 150.00,
                                   "manutencao": 93.75}, bv=0.20, comissao=0.05, inadimplencia=0.02),
        "Meia Cota": _tipo(16, {"repasse": 312.50, "energia": 231.25, "internet_cameras": 75.00,
                                 "manutencao": 46.875, "tap": 3125.00}, bv=0.20, comissao=0.05, inadimplencia=0.02),
    },
    # aba "Revista Rio 2" (venda por página)
    "Revista Rio 2": {
        "Padrão": _tipo(1, {"repasse": 0.00, "producao": 382.05, "freelancer": 666.67, "layla": 28.33},
                         bv=0.0, comissao=0.20, inadimplencia=0.02),
    },
    # aba "Revista Península" (venda por página)
    "Revista Península": {
        "Padrão": _tipo(1, {"editoracao_recebimento": -1200.00, "repasse": 520.00, "producao": 0.00,
                             "freelancer": 280.00, "layla": 226.64}, bv=0.0, comissao=0.20, inadimplencia=0.02),
    },
    # aba "Busdoor" — baseline por ponto/inserção com Quantidade = 1
    "Busdoor": {
        "Padrão": _tipo(1, {"repasse_por_onibus": 150.00, "producao": 20.00},
                         bv=0.15, comissao=0.05, inadimplencia=0.02),
    },
    # aba "MUB Estático"
    "MUB Estático": {
        "Face": _tipo(4, {"repasse": 1250.00, "producao": 630.00, "manutencao": 100.00, "tap": 1250.00},
                       bv=0.20, comissao=0.05, inadimplencia=0.02),
    },
    # aba "MUB Digital" — repasse é percentual (10%) sobre o Valor de Venda
    "MUB Digital": {
        "Cota Inteira": _tipo(8, {"tap": 4500.00}, bv=0.20, comissao=0.05, inadimplencia=0.02, repasse_pct=0.10),
        "Meia Cota": _tipo(8, {"tap": 562.50}, bv=0.20, comissao=0.05, inadimplencia=0.05, repasse_pct=0.10),
    },
    # aba "Envelopamento - Rio 2"
    "Envelopamento Rio 2": {
        "Padrão": _tipo(23, {"repasse": 10000.00, "producao": 6000.00}, bv=0.20, comissao=0.05, inadimplencia=0.02),
    },
    # aba "Envelopamento - BRT" — 4 posições heterogêneas, cada uma apurada separadamente
    "Envelopamento BRT": {
        "Estação": _tipo(1, {"repasse": 22000.00, "producao": 102000.00}, bv=0.20, comissao=0.05, inadimplencia=0.02),
        "Articulado": _tipo(1, {"repasse": 20000.00, "producao": 9000.00}, bv=0.20, comissao=0.05, inadimplencia=0.02),
        "Padron": _tipo(1, {"repasse": 8000.00, "producao": 7000.00}, bv=0.20, comissao=0.05, inadimplencia=0.02),
        "Urbano": _tipo(1, {"repasse": 5000.00, "producao": 6000.00}, bv=0.20, comissao=0.05, inadimplencia=0.02),
    },
    # aba "Envelopamento - Fretado"
    "Envelopamento Fretado": {
        "Padrão": _tipo(1, {"repasse": 27500.00, "producao": 6000.00}, bv=0.20, comissao=0.05, inadimplencia=0.02),
    },
    # aba "Envelopamento - Península"
    "Envelopamento Península": {
        "Padrão": _tipo(9, {"repasse": 3250.00, "producao": 6000.00}, bv=0.20, comissao=0.05, inadimplencia=0.02),
    },
    # aba "Envelopamento - Frames"
    "Envelopamento Frames": {
        "Padrão": _tipo(3, {"repasse": 4500.00, "producao": 6000.00}, bv=0.20, comissao=0.05, inadimplencia=0.02),
    },
    # aba "Envelopamento - Barra Bali"
    "Envelopamento Barra Bali": {
        "Padrão": _tipo(13, {"repasse": 6900.00, "producao": 6000.00}, bv=0.20, comissao=0.05, inadimplencia=0.02),
    },
    # aba "Empenas Estáticas" — 12 imóveis heterogêneos, cada um apurado separadamente
    "Empenas Estáticas": {
        "Barra - Jardim Oceânico": _tipo(11, {"repasse_fixo": 8000.00, "repasse_veiculado": 4000.00,
                                               "producao": 8100.00, "tap": 10040.00}, bv=0.20, comissao=0.05, inadimplencia=0.02),
        "Botafogo - Álvaro Rodrigues": _tipo(11, {"repasse_veiculado": 8000.00, "producao": 7900.00,
                                                   "tap": 7800.00}, bv=0.20, comissao=0.05, inadimplencia=0.02),
        "Botafogo - General Polidoro": _tipo(11, {"repasse_veiculado": 12000.00, "energia": 600.00,
                                                    "internet_cameras": 240.00, "producao": 14800.00,
                                                    "tap": 17000.00}, bv=0.20, comissao=0.05, inadimplencia=0.02),
        "Botafogo - Real Grandeza": _tipo(11, {"repasse_fixo": 8000.00, "energia": 800.00,
                                                "internet_cameras": 240.00, "producao": 9000.00,
                                                "tap": 10040.00}, bv=0.20, comissao=0.05, inadimplencia=0.02),
        "Botafogo - Praia de Botafogo": _tipo(11, {"repasse_fixo": 1000.00, "repasse_veiculado": 9000.00,
                                                    "producao": 9000.00, "tap": 133000.00}, bv=0.20, comissao=0.05, inadimplencia=0.02),
        "Centro - Paulo de Frontin": _tipo(11, {"repasse_veiculado": 5000.00, "producao": 5600.00,
                                                 "tap": 5500.00}, bv=0.20, comissao=0.05, inadimplencia=0.02),
        "Copacabana - Santa Clara": _tipo(11, {"repasse_veiculado": 15000.00, "producao": 8900.00,
                                                "tap": 10600.00}, bv=0.20, comissao=0.05, inadimplencia=0.02),
        "Leblon - Ataulfo de Paiva": _tipo(11, {"repasse_veiculado": 5000.00, "producao": 5100.00,
                                                 "tap": 9500.00}, bv=0.20, comissao=0.05, inadimplencia=0.02),
        "Maracanã - Av. Maracanã, 417": _tipo(11, {"repasse_veiculado": 10000.00, "producao": 6400.00,
                                                    "tap": 7800.00}, bv=0.20, comissao=0.05, inadimplencia=0.02),
        "Maracanã - Av. Maracanã, 515": _tipo(11, {"repasse_veiculado": 12000.00, "producao": 10150.00,
                                                    "tap": 15000.00}, bv=0.20, comissao=0.05, inadimplencia=0.02),
        "Maracanã - Av. Maracanã, 526": _tipo(11, {"repasse_veiculado": 5700.00, "producao": 6000.00,
                                                    "tap": 4000.00}, bv=0.20, comissao=0.05, inadimplencia=0.02),
        "Tijuca - Haddock Lobo": _tipo(11, {"repasse_veiculado": 4500.00, "producao": 5490.00,
                                             "tap": 5000.00}, bv=0.20, comissao=0.05, inadimplencia=0.02),
    },
    # aba "Empena Digital Américas"
    "Empena Digital Américas": {
        "Cota Inteira": _tipo(8, {"repasse_fixo": 20000.00, "seguro": 167.50, "energia": 1000.00,
                                   "internet_cameras": 125.00, "manutencao": 200.00, "tap": 12000.00},
                               bv=0.20, comissao=0.05, inadimplencia=0.02),
        "Meia Cota": _tipo(8, {"repasse_fixo": 3750.00, "seguro": 83.75, "energia": 500.00,
                                "internet_cameras": 62.50, "manutencao": 100.00, "tap": 6000.00},
                            bv=0.20, comissao=0.05, inadimplencia=0.02),
    },
    # aba "Empena Digital Tijuca"
    "Empena Digital Tijuca": {
        "Cota Inteira": _tipo(8, {"repasse_fixo": 6687.50, "painel_led": 5270.00, "energia": 1625.00,
                                   "internet_cameras": 87.50, "seguro": 125.00, "tap": 4000.00},
                               bv=0.20, comissao=0.05, inadimplencia=0.02),
        "Meia Cota": _tipo(8, {"repasse_fixo": 3343.75, "painel_led": 2635.00, "energia": 812.50,
                                "internet_cameras": 43.75, "seguro": 62.50, "tap": 2000.00},
                            bv=0.20, comissao=0.05, inadimplencia=0.02),
    },
    # aba "Empena Digital Copacabana"
    "Empena Digital Copacabana": {
        "Cota Inteira": _tipo(8, {"repasse_fixo": 6687.50, "painel_led": 5187.50, "energia": 1000.00,
                                   "internet_cameras": 125.00, "seguro": 125.00, "tap": 6500.00},
                               bv=0.20, comissao=0.05, inadimplencia=0.02),
        "Meia Cota": _tipo(8, {"repasse_fixo": 3343.75, "painel_led": 2593.75, "energia": 500.00,
                                "internet_cameras": 62.50, "seguro": 62.50, "tap": 3250.00},
                            bv=0.20, comissao=0.05, inadimplencia=0.02),
    },
}

PERFIS = ["Executivo", "Gerente Comercial", "Diretor Comercial", "Diretor Financeiro"]


# ============================================================================
# 2) MOTOR DE CÁLCULO (DRE, classificação estratégica e alçada)
# ============================================================================

def calcular_dre(valor_venda: float, tipo: str, premissas_ativo: dict, regras: dict) -> dict:
    """Reproduz a apuração de DRE por cota/posição de cada ativo da planilha mestre."""
    p = premissas_ativo[tipo]
    trib_receita = regras["tributos_receita"]
    trib_lucro = regras["tributos_lucro"]

    pis = valor_venda * trib_receita["pis"]
    cofins = valor_venda * trib_receita["cofins"]
    iss = valor_venda * trib_receita["iss"]
    total_tributos_receita = pis + cofins + iss

    # Repasse: a maioria dos ativos usa um valor fixo em R$ (já incluído em
    # custos_fixos_diretos); alguns (ex. MUB Digital) usam um percentual do
    # Valor de Venda, indicado em repasse_pct.
    repasse_variavel = valor_venda * p.get("repasse_pct", 0.0)
    custos_fixos = p["custos_fixos_diretos"]
    total_custos_fixos = sum(custos_fixos.values())

    bv = valor_venda * p["bv"]
    comissao = valor_venda * p["comissao"]
    inadimplencia = valor_venda * p["inadimplencia"]

    margem_bruta = valor_venda - total_tributos_receita - repasse_variavel - total_custos_fixos
    resultado_operacional = margem_bruta - bv - comissao - inadimplencia

    # Adicional de IRPJ: na planilha mestre o limite de isenção mensal
    # (R$ 20.000) é apurado sobre o resultado CONSOLIDADO do ativo (todas
    # as cotas/posições daquele tipo) e depois rateado entre as unidades.
    # Como esta calculadora avalia uma negociação por vez, aplicamos o
    # mesmo limite já rateado proporcionalmente ao número de unidades do
    # ativo — lido diretamente do divisor da fórmula de cada aba
    # (aproximação que assume portfólio simétrico, mesma lógica-base da
    # planilha).
    limite_rateado = trib_lucro["limite_isencao_adicional_mensal"] / p["n_unidades_ativo"]
    irpj = resultado_operacional * trib_lucro["irpj"]
    excedente_adicional = max(0.0, resultado_operacional - limite_rateado)
    adicional_irpj = excedente_adicional * trib_lucro["adicional_irpj"]
    csll = resultado_operacional * trib_lucro["csll"]

    lucro_liquido = resultado_operacional - irpj - adicional_irpj - csll
    margem_liquida_pct = (lucro_liquido / valor_venda) if valor_venda else 0.0

    return {
        "valor_venda": valor_venda,
        "pis": pis, "cofins": cofins, "iss": iss,
        "total_tributos_receita": total_tributos_receita,
        "custos_fixos_diretos": custos_fixos,
        "total_custos_fixos": total_custos_fixos,
        "bv": bv, "comissao": comissao, "inadimplencia": inadimplencia,
        "margem_bruta": margem_bruta,
        "resultado_operacional": resultado_operacional,
        "irpj": irpj,
        "adicional_irpj": adicional_irpj,
        "csll": csll,
        "lucro_liquido": lucro_liquido,
        "margem_liquida_pct": margem_liquida_pct,
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
#     O cargo informado aqui TRAVA o perfil usado na avaliação de alçada —
#     o solicitante não escolhe livremente o próprio nível de aprovação.
# ============================================================================
if "identificacao" not in st.session_state:
    st.write("")
    col_esq, col_meio, col_dir = st.columns([1, 1.3, 1])
    with col_meio:
        with st.container(border=True):
            st.subheader("Identifique-se para continuar")
            st.caption("Seu cargo define automaticamente a alçada de aprovação disponível nesta simulação.")
            with st.form("form_identificacao"):
                nome_input = st.text_input("Nome completo")
                cargo_input = st.selectbox("Cargo", PERFIS, index=None, placeholder="Selecione seu cargo")
                entrar = st.form_submit_button("Continuar", width="stretch", type="primary")

            if entrar:
                if not nome_input.strip():
                    st.error("Informe seu nome para continuar.")
                elif not cargo_input:
                    st.error("Selecione seu cargo para continuar.")
                else:
                    st.session_state["identificacao"] = {"nome": nome_input.strip(), "cargo": cargo_input}
                    st.rerun()
    st.stop()

identificacao = st.session_state["identificacao"]
perfil = identificacao["cargo"]  # travado pela identificação — não é mais escolhido na calculadora

# ---------------------------- Sidebar --------------------------------------
with st.sidebar:
    st.header("Solicitante")
    st.info(f"**{identificacao['nome']}**  \nCargo: {perfil}")
    if st.button("Trocar usuário", width="stretch"):
        st.session_state.pop("identificacao", None)
        st.session_state.pop("ultimo_calculo", None)
        st.rerun()

    st.divider()
    st.header("Dados da Negociação")
    # O seletor de "Ativo" fica fora de um st.form (que só atualiza os
    # demais campos ao ser enviado) porque as opções de "Tipo / Posição"
    # dependem do ativo escolhido — cada ativo tem seu próprio conjunto de
    # tipos (ex.: "Cota Inteira"/"Meia Cota" nos painéis, mas nomes de
    # imóvel nas Empenas Estáticas, ou "Face" no MUB Estático).
    pi_numero = st.text_input("Nº do PI Negociado", placeholder="Ex.: PI-2026-0842")
    ativo = st.selectbox("Ativo Negociado", list(ATIVOS.keys()), index=0, key="ativo_select")
    tipos_disponiveis = list(ATIVOS[ativo].keys())
    rotulo_tipo = "Tipo de Cota" if tipos_disponiveis in (["Cota Inteira", "Meia Cota"],) else "Tipo / Posição"
    tipo_cota = st.selectbox(rotulo_tipo, tipos_disponiveis, index=0, key=f"tipo_select_{ativo}")
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

if not calcular and "ultimo_calculo" not in st.session_state:
    st.info("Preencha os dados da negociação na barra lateral e clique em **Calcular Autorização**.")
    st.stop()

if calcular:
    st.session_state["ultimo_calculo"] = {
        "pi_numero": pi_numero,
        "nome": identificacao["nome"],
        "perfil": perfil,
        "ativo": ativo,
        "tipo_cota": tipo_cota,
        "valor_pi": valor_pi,
        "timestamp": datetime.now(),
    }

dados = st.session_state["ultimo_calculo"]
premissas_ativo = ATIVOS[dados["ativo"]]

dre = calcular_dre(dados["valor_pi"], dados["tipo_cota"], premissas_ativo, REGRAS_ALCADAS)
alcada = determinar_alcada(dre, REGRAS_ALCADAS)
autorizacao = avaliar_autorizacao(dados["perfil"], alcada, REGRAS_ALCADAS)

# ---- Cabeçalho da negociação -----------------------------------------------
col_a, col_b, col_c, col_d = st.columns(4)
col_a.markdown(f"**Nº do PI**  \n{dados['pi_numero'] or '—'}")
col_b.markdown(f"**Ativo**  \n{dados['ativo']} ({dados['tipo_cota']})")
col_c.markdown(f"**Solicitante**  \n{dados['nome']} ({dados['perfil']})")
col_d.markdown(f"**Simulado em**  \n{dados['timestamp'].strftime('%d/%m/%Y %H:%M')}")

st.divider()

# ---- Cartão de status de autorização ---------------------------------------
# Somente o resultado (autorizado / recusado) é exibido — nenhum valor, margem
# ou percentual calculado aparece na tela, apenas a decisão final.
if autorizacao["autorizado"]:
    bg, fg, status_txt = "#C6EFCE", "#006100", "AUTORIZADO"
else:
    bg, fg, status_txt = "#FFC7CE", "#9C0006", "RECUSADO"

if alcada["faixa"] == 4:
    if alcada["abaixo_break_even"]:
        motivo = "Esta negociação resulta em prejuízo para a empresa e não pode ser aprovada em nenhuma alçada."
    else:
        motivo = "Esta negociação está abaixo do piso mínimo de rentabilidade aceitável e não pode ser aprovada em nenhuma alçada."
elif autorizacao["autorizado"]:
    motivo = f"Esta negociação está dentro da alçada de aprovação do perfil <strong>{dados['perfil']}</strong>."
else:
    motivo = (
        f"Esta negociação exige aprovação de um perfil com alçada superior — no mínimo "
        f"<strong>{alcada['cargo_minimo']}</strong>. O perfil selecionado (<strong>{dados['perfil']}</strong>) "
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
    "Dashboard de uso interno — Grupo Coruja. As premissas de custo, os valores calculados e as "
    "regras de alçada não são exibidos nesta tela — apenas a decisão final de autorização."
)
