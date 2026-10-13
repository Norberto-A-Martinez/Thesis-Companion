"""
gerar_cenario.py -- cenário socioeconômico sintético para redes de distribuição
================================================================================

Gera, para uma rede radial qualquer, indicadores socioeconômicos sintéticos por
barra (renda, moradores por domicílio, número de domicílios e consumo por
domicílio), com autocorrelação espacial sobre a topologia elétrica e sem
circularidade com a demanda instalada Pd_0.

Entradas (uma pasta, ver README.md):
    ramos.csv       de,para                      -- ramos FECHADOS (config. radial)
    barras.csv      barra,classe,Pd_0[,observacao]
    calibracao.csv  parametro,classe,valor,unidade,fonte,observacao
    config.json     (opcional) semente, comprimento de correlação, etc.

Uso:
    python3 gerar_cenario.py exemplos/ieee33 --saida dados_tese/ieee33
    python3 gerar_cenario.py minha_rede/ --semente 12345

Cadeia causal (resumo):
    renda      <- mediana da classe x exp(sigma x campo espacial)
    moradores  <- média da classe + dispersão x campo espacial
    consumo    <- consumo_ref x (renda/renda_ref)^e_renda
                               x (moradores/mor_ref)^e_mor x exp(sigma_c x campo)
    num_domic  <- energia mensal da barra / consumo por domicílio x fração_residencial

O consumo por domicílio é gerado SEM usar Pd_0. Já o número de domicílios sai da
identidade física energia/consumo e, por isso, depende de Pd_0 de propósito: é
a medida de exposição da barra (quantas famílias um corte afeta).
"""
from __future__ import annotations

import argparse
import json
import math
import os
import platform
import re
import sys

import networkx as nx
import numpy as np
import pandas as pd

# ========================================================== CONSTANTES
ARQ_RAMOS = 'ramos.csv'
ARQ_BARRAS = 'barras.csv'
ARQ_CALIB = 'calibracao.csv'
ARQ_CONFIG = 'config.json'
    
SUBESTACAO = 'subestacao'   # classe reservada: barra de fronteira, sem carga

CONFIG_PADRAO = {
    'titulo': 'REDE SEM TITULO',   # aparece no cabeçalho do relatório
    'semente': None,               # OBRIGATÓRIA (config.json ou --semente)
    # comprimento de correlação do campo espacial, em saltos
    'ell_saltos': 3.0,
    # fração do peso que a própria barra retém na média ponderada espacial
    'peso_proprio_alvo': 0.25,
    # peso do fator comum de vulnerabilidade em renda e moradores (0 a 1)
    'peso_fator_renda': 0.60,
    'peso_fator_moradores': 0.45,
    # dispersão espacial dos moradores/domicílio (em moradores) e piso
    'desvio_moradores': 0.45,
    'moradores_minimo': 1.2,
    # conversão potência -> energia mensal
    'horas_por_mes': 730.0,
}

# Parâmetros de calibração aceitos e como são exigidos.
GRUPOS_POR_CLASSE = ('renda_mediana', 'renda_sigma_log', 'moradores_por_domicilio')
GRUPOS_GLOBAIS = ('consumo_referencia', 'renda_referencia',
                  'elasticidade_renda_consumo', 'moradores_referencia',
                  'elasticidade_moradores_consumo', 'sigma_consumo',
                  'fator_de_carga')
GRUPOS_OPCIONAIS = {'fracao_residencial': 1.0}      # valor assumido se ausente
GRUPOS_CONHECIDOS = set(GRUPOS_POR_CLASSE) | set(GRUPOS_GLOBAIS) | set(GRUPOS_OPCIONAIS)

POSITIVOS = {'renda_mediana', 'moradores_por_domicilio', 'consumo_referencia',
             'renda_referencia', 'moradores_referencia', 'fator_de_carga'}
NAO_NEGATIVOS = {'renda_sigma_log', 'sigma_consumo'}
FRACOES = {'fracao_residencial'}                    # (0, 1]

# socio_type: 1 = valor alto indica MAIOR vulnerabilidade
#             2 = valor alto indica MENOR vulnerabilidade
SOCIO_TYPE = {
    'renda_media_domiciliar': 2,
    'moradores_por_domicilio': 1,
    'num_domicilios': 1,
    'consumo_medio_domicilio': 2,
}
UNIDADES = {
    'renda_media_domiciliar': 'R$/mes',
    'moradores_por_domicilio': 'moradores/domicilio',
    'num_domicilios': 'domicilios',
    'consumo_medio_domicilio': 'kWh/mes/domicilio',
}


class ErroEntrada(ValueError):
    """Problema nos arquivos de entrada (mensagem escrita para o usuário)."""


# ================================================================ LEITURA
def _ler_csv(caminho, obrigatorias):
    if not os.path.isfile(caminho):
        raise ErroEntrada(f'arquivo não encontrado: {caminho}')
    with open(caminho, encoding='utf-8-sig') as f:
        cabecalho = f.readline()
    # aceita CSV exportado pelo Excel em português (separador ';')
    sep = ';' if cabecalho.count(';') > cabecalho.count(',') else ','
    df = pd.read_csv(caminho, sep=sep, dtype=str, encoding='utf-8-sig',
                     keep_default_na=False, skipinitialspace=True)
    df.columns = [c.strip() for c in df.columns]
    faltam = [c for c in obrigatorias if c not in df.columns]
    if faltam:
        raise ErroEntrada(f'{os.path.basename(caminho)}: faltam as colunas {faltam} '
                          f'(encontradas: {list(df.columns)})')
    for c in df.columns:
        df[c] = df[c].str.strip()
    return df.reset_index(drop=True)


def _num(x, contexto):
    try:
        return float(str(x).strip().replace(',', '.'))
    except ValueError:
        raise ErroEntrada(f'{contexto}: valor numérico inválido: {x!r}') from None


def ler_config(caminho):
    cfg = dict(CONFIG_PADRAO)
    if os.path.isfile(caminho):
        try:
            with open(caminho, encoding='utf-8-sig') as f:
                usuario = json.load(f)
        except json.JSONDecodeError as e:
            raise ErroEntrada(f'{ARQ_CONFIG}: JSON inválido ({e})') from None
        usuario = {k: v for k, v in usuario.items() if not k.startswith('_')}
        desconhecidas = sorted(set(usuario) - set(CONFIG_PADRAO))
        if desconhecidas:
            raise ErroEntrada(f'{ARQ_CONFIG}: chaves desconhecidas {desconhecidas}. '
                              f'Válidas: {sorted(CONFIG_PADRAO)}')
        cfg.update(usuario)
    return cfg


def validar_config(cfg):
    if cfg['semente'] is None:
        raise ErroEntrada("defina a semente aleatória em config.json ('semente') "
                          'ou com --semente; ela deve ser declarada na tese')
    if not float(cfg['semente']).is_integer():
        raise ErroEntrada('a semente deve ser um número inteiro')
    cfg['semente'] = int(cfg['semente'])
    if cfg['ell_saltos'] <= 0:
        raise ErroEntrada("'ell_saltos' deve ser > 0")
    if not 0 < cfg['peso_proprio_alvo'] < 1:
        raise ErroEntrada("'peso_proprio_alvo' deve estar entre 0 e 1 (exclusive)")
    for k in ('peso_fator_renda', 'peso_fator_moradores'):
        if not 0 <= cfg[k] <= 1:
            raise ErroEntrada(f"'{k}' deve estar entre 0 e 1")
    if cfg['desvio_moradores'] < 0 or cfg['moradores_minimo'] <= 0 or cfg['horas_por_mes'] <= 0:
        raise ErroEntrada("'desvio_moradores' >= 0, 'moradores_minimo' > 0 e "
                          "'horas_por_mes' > 0")


def ler_entradas(pasta):
    """Lê e valida os quatro arquivos da pasta. Devolve um dicionário."""
    if not os.path.isdir(pasta):
        raise ErroEntrada(f'pasta de entrada não encontrada: {pasta}')
    ramos_df = _ler_csv(os.path.join(pasta, ARQ_RAMOS), ['de', 'para'])
    barras_df = _ler_csv(os.path.join(pasta, ARQ_BARRAS), ['barra', 'classe', 'Pd_0'])
    calib_df = _ler_csv(os.path.join(pasta, ARQ_CALIB), ['parametro', 'classe', 'valor'])
    cfg = ler_config(os.path.join(pasta, ARQ_CONFIG))

    # rótulos de barra: inteiros se TODOS forem inteiros, senão texto
    rotulos = list(ramos_df['de']) + list(ramos_df['para']) + list(barras_df['barra'])
    if any(r == '' for r in rotulos):
        raise ErroEntrada('há rótulo de barra vazio em ramos.csv ou barras.csv')
    conv = int if all(re.fullmatch(r'-?\d+', r) for r in rotulos) else str

    ramos = [(conv(a), conv(b)) for a, b in zip(ramos_df['de'], ramos_df['para'])]
    if not ramos:
        raise ErroEntrada('ramos.csv está vazio')

    nos, classe, pd0, obs = [], {}, {}, {}
    tem_obs = 'observacao' in barras_df.columns
    for _, lin in barras_df.iterrows():
        n = conv(lin['barra'])
        if n in classe:
            raise ErroEntrada(f'barras.csv: barra {n!r} aparece mais de uma vez')
        c = lin['classe']
        if c == '':
            raise ErroEntrada(f'barras.csv: barra {n!r} sem classe')
        if c.lower() == SUBESTACAO:
            c, p = SUBESTACAO, 0.0
        else:
            p = _num(lin['Pd_0'], f'barras.csv, barra {n!r}, Pd_0')
            if p <= 0:
                raise ErroEntrada(f'barras.csv: Pd_0 da barra {n!r} deve ser > 0 '
                                  f'(use classe "{SUBESTACAO}" para barras sem carga)')
        nos.append(n)
        classe[n], pd0[n] = c, p
        obs[n] = lin['observacao'] if tem_obs else ''

    # calibração
    calib = {}
    for _, lin in calib_df.iterrows():
        g = lin['parametro']
        c = lin['classe'] or 'todas'
        if g not in GRUPOS_CONHECIDOS:
            raise ErroEntrada(f"calibracao.csv: parâmetro desconhecido '{g}'. "
                              f'Válidos: {sorted(GRUPOS_CONHECIDOS)}')
        v = _num(lin['valor'], f"calibracao.csv, {g}/{c}")
        if c in calib.setdefault(g, {}):
            raise ErroEntrada(f"calibracao.csv: '{g}' repetido para a classe '{c}'")
        calib[g][c] = {'valor': v, 'fonte': lin.get('fonte', ''),
                       'unid': lin.get('unidade', ''), 'nota': lin.get('observacao', '')}

    return {'ramos': ramos, 'nos': nos, 'classe': classe, 'pd0': pd0, 'obs': obs,
            'calib': calib, 'config': cfg}


# ============================================================== VALIDAÇÃO
def validar_rede(G, nos, ramos, avisos):
    ramos_nos = {x for r in ramos for x in r}
    so_ramos = sorted(ramos_nos - set(nos))
    so_barras = sorted(set(nos) - ramos_nos)
    if so_ramos:
        raise ErroEntrada(f'barras em ramos.csv ausentes de barras.csv: {so_ramos}')
    if so_barras:
        raise ErroEntrada(f'barras em barras.csv que não aparecem em ramos.csv: {so_barras}')
    laco = [r for r in ramos if r[0] == r[1]]
    if laco:
        raise ErroEntrada(f'ramos que ligam a barra a ela mesma: {laco}')
    if not nx.is_connected(G):
        comps = [sorted(c) for c in nx.connected_components(G)]
        raise ErroEntrada(f'a rede não é conexa; componentes: {comps}')
    if G.number_of_edges() < len(ramos):
        avisos.append('ramos.csv tem ramos repetidos (ignorados).')
    if G.number_of_edges() != G.number_of_nodes() - 1:
        avisos.append(
            f'a rede NÃO é radial ({G.number_of_edges()} ramos para '
            f'{G.number_of_nodes()} barras). As distâncias são calculadas sobre os '
            'ramos listados; mantenha em ramos.csv só os ramos FECHADOS da '
            'configuração simulada (chaves de interconexão abertas ficam de fora).')


def validar_calibracao(calib, classes_carga):
    faltam = [g for g in GRUPOS_GLOBAIS if g not in calib]
    if faltam:
        raise ErroEntrada(f'calibracao.csv: faltam os parâmetros {faltam}')
    for g in GRUPOS_POR_CLASSE:
        if g not in calib:
            raise ErroEntrada(f"calibracao.csv: falta o parâmetro '{g}'")
        for c in classes_carga:
            if c not in calib[g] and 'todas' not in calib[g]:
                raise ErroEntrada(f"calibracao.csv: '{g}' sem valor para a classe '{c}' "
                                  f"(informe a classe ou use 'todas')")
    for g, d in calib.items():
        for c, meta in d.items():
            v = meta['valor']
            if g in POSITIVOS and v <= 0:
                raise ErroEntrada(f'calibracao.csv: {g}/{c} deve ser > 0 (veio {v})')
            if g in NAO_NEGATIVOS and v < 0:
                raise ErroEntrada(f'calibracao.csv: {g}/{c} deve ser >= 0 (veio {v})')
            if g in FRACOES and not 0 < v <= 1:
                raise ErroEntrada(f'calibracao.csv: {g}/{c} deve estar em (0, 1] (veio {v})')


def cal(calib, grupo, classe='todas', padrao=None):
    d = calib.get(grupo)
    if d is not None:
        if classe in d:
            return d[classe]['valor']
        if 'todas' in d:
            return d['todas']['valor']
    if padrao is not None:
        return padrao
    raise ErroEntrada(f"parâmetro '{grupo}' sem valor para a classe '{classe}'")


# ================================================================== NÚCLEO
def _z(v):
    return (v - v.mean()) / v.std()


def gerar(entrada, saida, semente=None, verboso=True):
    """Gera o cenário. Escreve os arquivos em `saida` e devolve um resumo."""
    ent = ler_entradas(entrada)
    cfg = ent['config']
    if semente is not None:
        cfg['semente'] = semente
    validar_config(cfg)

    NOS, ramos = ent['nos'], ent['ramos']
    CLASSE, PD_0, calib = ent['classe'], ent['pd0'], ent['calib']
    avisos = []

    G = nx.Graph()
    G.add_nodes_from(NOS)
    G.add_edges_from(ramos)
    validar_rede(G, NOS, ramos, avisos)

    n_nos = len(NOS)
    classes_arr = np.array([CLASSE[n] for n in NOS])
    eh_sub = classes_arr == SUBESTACAO
    msk = ~eh_sub                                  # barras de carga
    if msk.sum() < 3:
        raise ErroEntrada('são necessárias ao menos 3 barras de carga')
    classes_carga = sorted(set(classes_arr[msk]))
    validar_calibracao(calib, classes_carga)

    # ordem das classes no relatório: a de renda_mediana em calibracao.csv
    ordem = [c for c in calib['renda_mediana'] if c in classes_carga] + \
            [c for c in classes_carga if c not in calib['renda_mediana']]
    for c in ordem:
        k = int((classes_arr == c).sum())
        if k < 3:
            avisos.append(f"a classe '{c}' tem só {k} barra(s): a variação espacial "
                          'intraclasse fica pobre (campo padronizado por classe).')

    rng = np.random.default_rng(cfg['semente'])

    # distância topológica (saltos) na rede
    dist = dict(nx.all_pairs_shortest_path_length(G))
    D = np.array([[dist[a][b] for b in NOS] for a in NOS], dtype=float)

    # ----------------------------------------- campo espacial latente
    ELL = float(cfg['ell_saltos'])
    _chol = {}

    def campo_gaussiano(ell):
        """Campo aleatório gaussiano com covariância exponencial sobre a rede."""
        if ell not in _chol:
            for jitter in (1e-8, 1e-6, 1e-4):
                try:
                    _chol[ell] = np.linalg.cholesky(np.exp(-D / ell) + jitter * np.eye(n_nos))
                    break
                except np.linalg.LinAlgError:
                    continue
            else:
                raise ErroEntrada('a covariância espacial não é definida positiva; '
                                  "tente outro 'ell_saltos'")
        return _chol[ell] @ rng.standard_normal(n_nos)

    # fator latente de vulnerabilidade: alto = mais vulnerável
    z_vuln = _z(campo_gaussiano(ELL))

    def centrar_por_classe(v):
        """Centra e padroniza o campo DENTRO de cada classe, para que as medianas
        calibradas sejam respeitadas e só a variação intraclasse venha do campo."""
        out = np.zeros_like(v)
        for c in set(classes_arr[msk]):
            m = msk & (classes_arr == c)
            out[m] = (v[m] - v[m].mean()) / (v[m].std() + 1e-12)
        return out

    def indicador_latente(peso, ell=ELL):
        """Componente comum + ruído próprio, ambos espacializados."""
        r = _z(campo_gaussiano(ell))
        return centrar_por_classe(peso * z_vuln + math.sqrt(max(0.0, 1 - peso ** 2)) * r)

    # ATENÇÃO: a ordem destas quatro chamadas define o fluxo de números
    # aleatórios; alterá-la muda os dados gerados para a mesma semente.
    z_renda = -indicador_latente(cfg['peso_fator_renda'])      # renda alta -> vuln. baixa
    z_mora = indicador_latente(cfg['peso_fator_moradores'])
    z_cons = _z(campo_gaussiano(ELL))                           # idiossincrático espacializado

    # ------------------------------------------ geração dos indicadores
    renda = np.zeros(n_nos)
    moradores = np.zeros(n_nos)
    consumo = np.zeros(n_nos)
    num_domic = np.zeros(n_nos)
    habitantes = np.zeros(n_nos)

    for i, n in enumerate(NOS):
        if eh_sub[i]:
            continue
        c = CLASSE[n]

        # (1) renda: log-normal centrada na mediana da classe
        renda[i] = cal(calib, 'renda_mediana', c) * math.exp(
            cal(calib, 'renda_sigma_log', c) * z_renda[i])

        # (2) moradores por domicílio: média da classe + dispersão espacial
        moradores[i] = max(cfg['moradores_minimo'],
                           cal(calib, 'moradores_por_domicilio', c)
                           + cfg['desvio_moradores'] * z_mora[i])

        # (3) consumo por domicílio: renda, tamanho domiciliar e termo
        #     idiossincrático. NÃO depende de Pd_0.
        consumo[i] = (cal(calib, 'consumo_referencia')
                      * (renda[i] / cal(calib, 'renda_referencia'))
                      ** cal(calib, 'elasticidade_renda_consumo')
                      * (moradores[i] / cal(calib, 'moradores_referencia'))
                      ** cal(calib, 'elasticidade_moradores_consumo')
                      * math.exp(cal(calib, 'sigma_consumo') * z_cons[i]))

        # (4) número de domicílios: energia mensal da barra / consumo por
        #     domicílio, vezes a fração residencial da classe (padrão 1.0).
        #     Em barras industriais/comerciais a fração < 1 evita atribuir toda
        #     a energia a "domicílios" (o que geraria outliers de consumo).
        energia_mes = PD_0[n] * cal(calib, 'fator_de_carga') * cfg['horas_por_mes']  # kWh/mes
        frac = cal(calib, 'fracao_residencial', c, padrao=GRUPOS_OPCIONAIS['fracao_residencial'])
        num_domic[i] = max(1, int(round(energia_mes / consumo[i] * frac)))
        habitantes[i] = max(1, int(round(num_domic[i] * moradores[i])))

    INDICADORES = {
        'renda_media_domiciliar': renda,
        'moradores_por_domicilio': moradores,
        'num_domicilios': num_domic,
        'consumo_medio_domicilio': consumo,
    }
    nomes = list(INDICADORES)

    # ---------------------------------------------- kernel espacial
    def peso_proprio(bw):
        W = np.exp(-((D / bw) ** 2))
        return float(np.mean(np.diag(W) / W.sum(axis=1)))

    lo, hi = 0.05, max(50.0, float(D.max()))
    for _ in range(80):
        mid = 0.5 * (lo + hi)
        if peso_proprio(mid) > cfg['peso_proprio_alvo']:
            lo = mid
        else:
            hi = mid
    BANDWIDTH = 0.5 * (lo + hi)
    if abs(peso_proprio(BANDWIDTH) - cfg['peso_proprio_alvo']) > 1e-3:
        avisos.append("'peso_proprio_alvo' não é atingível nesta rede; "
                      f'obtido {peso_proprio(BANDWIDTH):.3f}.')

    def moran_I(v, bw):
        W = np.exp(-((D / bw) ** 2))
        np.fill_diagonal(W, 0.0)
        vv = v[msk] - v[msk].mean()
        Wm = W[np.ix_(msk, msk)]
        return (msk.sum() / Wm.sum()) * ((Wm * np.outer(vv, vv)).sum() / (vv ** 2).sum())

    # ------------------------------------- pesos do índice composto
    pd0 = np.array([PD_0[n] for n in NOS])
    M = np.column_stack([INDICADORES[h][msk] for h in nomes])

    def corr(a, b):
        if a.std() == 0 or b.std() == 0:
            return 0.0
        return float(np.corrcoef(a, b)[0, 1])

    w_igual = np.ones(len(nomes)) / len(nomes)                       # (a) iguais

    Mn = (M - M.min(axis=0)) / (M.max(axis=0) - M.min(axis=0) + 1e-12)   # (b) entropia
    P = (Mn + 1e-12) / (Mn + 1e-12).sum(axis=0)
    k = 1.0 / math.log(len(M))
    ent_ = -k * (P * np.log(P)).sum(axis=0)
    w_entropia = (1 - ent_) / (1 - ent_).sum()

    r_pd = np.array([abs(corr(pd0[msk], M[:, j])) for j in range(len(nomes))])  # (c) Pearson
    w_pearson = r_pd / r_pd.sum() if r_pd.sum() > 0 else w_igual

    # ------------------------------------------------- relatório
    sem_fonte = sum(1 for d in calib.values() for m in d.values()
                    if m['fonte'] == '' or 'PREENCHER' in m['fonte'].upper())
    L = []
    L.append(f"CENARIO SOCIOECONOMICO SINTETICO -- {cfg['titulo']}")
    L.append(f"Semente aleatoria: {cfg['semente']}")
    L.append(f'Comprimento de correlacao do campo espacial: {ELL:.1f} saltos')
    L.append(f'Software: python {platform.python_version()}, numpy {np.__version__}, '
             f'pandas {pd.__version__}, networkx {nx.__version__}')
    L.append(f'Rede: {n_nos} barras ({int(msk.sum())} de carga), {G.number_of_edges()} ramos')
    if sem_fonte:
        L.append('')
        L.append(f'AVISO: {sem_fonte} parametro(s) de calibracao sem fonte (vazia ou')
        L.append('[PREENCHER]). Os valores correspondentes sao ficticios ate que')
        L.append('sejam substituidos por estatisticas publicadas. Ver tabela_calibracao.csv.')
    for a in avisos:
        L.append('')
        L.append(f'AVISO: {a}')
    L.append('')
    L.append('CLASSIFICACAO (por contiguidade eletrica)')
    for c in ordem:
        ns = [n for n in NOS if CLASSE[n] == c]
        blocos = [sorted(b) for b in nx.connected_components(G.subgraph(ns))]
        L.append(f'  {c:11s}: {len(ns):2d} barras em {len(blocos)} bloco(s) contiguo(s)')
        L.append(f'               {blocos}')
    L.append('')
    L.append('ESTATISTICAS DOS INDICADORES GERADOS')
    L.append(f"  {'indicador':26s} {'tipo':>4} {'min':>10} {'mediana':>10} {'max':>10}")
    for h, v in INDICADORES.items():
        vv = v[msk]
        L.append(f'  {h:26s} {SOCIO_TYPE[h]:>4} {vv.min():10.1f} {np.median(vv):10.1f} '
                 f'{vv.max():10.1f}   [{UNIDADES[h]}]')
    L.append('')
    L.append('POR CLASSE (mediana)')
    L.append(f"  {'classe':11s} " + ' '.join(f'{h[:14]:>15s}' for h in nomes))
    for c in ordem:
        sel = msk & (classes_arr == c)
        L.append(f'  {c:11s} ' + ' '.join(f'{np.median(INDICADORES[h][sel]):15.1f}'
                                          for h in nomes))
    L.append('')
    L.append('VALIDACAO 1 -- CORRELACAO COM A DEMANDA INSTALADA Pd_0')
    for j, h in enumerate(nomes):
        L.append(f'  {h:26s} r = {corr(pd0[msk], M[:, j]):+.3f}')
    L.append('  Renda, moradores e consumo sao gerados sem usar Pd_0: |r| alto com Pd_0')
    L.append('  nesses tres indicadores apontaria circularidade. num_domicilios depende de')
    L.append('  Pd_0 pela identidade energia/consumo (e a medida de exposicao); sua')
    L.append('  correlacao depende de fracao_residencial e da composicao das classes.')
    L.append('')
    L.append('VALIDACAO 2 -- CORRELACAO ENTRE INDICADORES')
    for i_ in range(len(nomes)):
        for j_ in range(i_ + 1, len(nomes)):
            L.append(f'  {nomes[i_][:24]:24s} x {nomes[j_][:24]:24s} '
                     f'r = {corr(M[:, i_], M[:, j_]):+.3f}')
    L.append('')
    L.append('VALIDACAO 3 -- AUTOCORRELACAO ESPACIAL (indice de Moran)')
    L.append(f'  kernel: W_ij = exp(-(d_ij / bw)^2), d em saltos')
    L.append(f'  bandwidth calibrada: {BANDWIDTH:.3f} saltos')
    L.append(f'  peso do proprio no na media ponderada: {peso_proprio(BANDWIDTH) * 100:.1f}%')
    for h, v in INDICADORES.items():
        L.append(f'  {h:26s} I = {moran_I(v, BANDWIDTH):+.3f}')
    L.append('')
    L.append('VALIDACAO 4 -- PESOS DO INDICE COMPOSTO SOB TRES ESQUEMAS')
    L.append(f"  {'indicador':26s} {'iguais':>9} {'entropia':>9} {'Pearson':>9}")
    for j, h in enumerate(nomes):
        L.append(f'  {h:26s} {w_igual[j]:9.3f} {w_entropia[j]:9.3f} {w_pearson[j]:9.3f}')
    L.append('')
    L.append('  O esquema Pearson pondera cada indicador pela correlacao com a DEMANDA.')
    L.append('  Como os indicadores sao gerados independentemente de Pd_0, essas')
    L.append('  correlacoes tendem a ser pequenas e os pesos refletem em boa parte ruido')
    L.append('  amostral. Recomenda-se pesos iguais ou por entropia, e o esquema Pearson')
    L.append('  apenas como analise de sensibilidade.')
    relatorio = '\n'.join(L)

    # ------------------------------------------------------ escrita
    os.makedirs(saida, exist_ok=True)

    linhas = [(n, h, round(float(INDICADORES[h][i]), 4))
              for h in nomes for i, n in enumerate(NOS)]
    pd.DataFrame(linhas, columns=['N', 'H', 'f_socio']).sort_values(['N', 'H']).to_csv(
        f'{saida}/nodes_with_social_factors.csv', index=False)

    pd.DataFrame({'H': nomes, 'socio_type': [SOCIO_TYPE[h] for h in nomes]}).to_csv(
        f'{saida}/socio_indicators.csv', index=False)

    rows = [(a, b, int(D[i, j])) for i, a in enumerate(NOS) for j, b in enumerate(NOS)]
    pd.DataFrame(rows, columns=['i', 'j', 'distance']).to_csv(
        f'{saida}/ponderation_matrix_long.csv', index=False)

    pd.DataFrame({'N': NOS, 'classe': [CLASSE[n] for n in NOS],
                  'Pd_0': [PD_0[n] for n in NOS]}).to_csv(
        f'{saida}/classificacao_barras.csv', index=False)

    pd.DataFrame({'N': NOS, 'classe': [CLASSE[n] for n in NOS],
                  'num_domicilios': num_domic.astype(int),
                  'habitantes': habitantes.astype(int)}).to_csv(
        f'{saida}/exposicao_barras.csv', index=False)

    tab = []
    for grupo, d in calib.items():
        for classe, meta in d.items():
            tab.append({'parametro': grupo, 'classe': classe, 'valor': meta['valor'],
                        'unidade': meta['unid'], 'fonte': meta['fonte'],
                        'observacao': meta['nota']})
    pd.DataFrame(tab).to_csv(f'{saida}/tabela_calibracao.csv', index=False)

    pd.DataFrame({'indicador': nomes, 'peso_iguais': w_igual.round(4),
                  'peso_entropia': w_entropia.round(4),
                  'peso_pearson': w_pearson.round(4)}).to_csv(
        f'{saida}/pesos_indicadores.csv', index=False)

    with open(f'{saida}/relatorio_geracao.txt', 'w', encoding='utf-8') as f:
        f.write(relatorio + '\n')

    if verboso:
        print(relatorio)
        print(f'\nArquivos escritos em {saida}/')
        print(f'  bandwidth para o suavizador espacial: {BANDWIDTH:.4f}')

    return {'indicadores': INDICADORES, 'nos': NOS, 'bandwidth': BANDWIDTH,
            'pesos': {'iguais': w_igual, 'entropia': w_entropia, 'pearson': w_pearson},
            'avisos': avisos, 'saida': saida}


# ==================================================================== CLI
def main(argv=None):
    ap = argparse.ArgumentParser(
        description='Gera um cenário socioeconômico sintético para uma rede de distribuição.')
    ap.add_argument('entrada', help='pasta com ramos.csv, barras.csv, calibracao.csv '
                                    '[e config.json]')
    ap.add_argument('--saida', help='pasta de saída (padrão: saida/<nome da pasta de entrada>)')
    ap.add_argument('--semente', type=int, help='sobrepõe a semente do config.json')
    ap.add_argument('--silencioso', action='store_true', help='não imprime o relatório')
    a = ap.parse_args(argv)
    saida = a.saida or os.path.join('saida', os.path.basename(os.path.normpath(a.entrada)))
    try:
        gerar(a.entrada, saida, semente=a.semente, verboso=not a.silencioso)
    except ErroEntrada as e:
        print(f'ERRO: {e}', file=sys.stderr)
        return 2
    return 0


if __name__ == '__main__':
    sys.exit(main())
