"""
Testa se uma rede é radial (topologia em árvore, sem malhas) a partir
de um dataframe com as colunas:
    m -> nó de origem (barra "de")
    n -> nó de destino (barra "para")
    r -> atributo da aresta (ex.: resistência do trecho/ramo)

Uma rede é radial quando, em cada subsistema conectado, não existe
nenhum caminho fechado (ciclo) entre os nós -- ou seja, o grafo é
uma árvore (ou uma floresta, se houver mais de um subsistema isolado).
"""
#%%
import pandas as pd
import networkx as nx
import matplotlib.pyplot as plt

# Insira aqui o caminho para o arquivo csv contendo a topologia da rede elétrica
REDE_TESTE = '.csv'

df = pd.read_csv(REDE_TESTE,index_col=0)
G = nx.from_pandas_edgelist(df, source="m", target="n")

def rede_e_radial(G: nx.Graph) -> bool:
    """
    True se o grafo é uma floresta (nenhum ciclo em nenhum componente).
    Cobre tanto o caso de um único alimentador (árvore) quanto o caso
    de vários subsistemas desconectados entre si.
    """
    return nx.is_forest(G)


is_radial = rede_e_radial(G)
ciclos = nx.cycle_basis(G)  # lista dos ciclos encontrados (vazia se radial)

print(f"Número de nós (barras): {G.number_of_nodes()}")
print(f"Número de arestas (ramos): {G.number_of_edges()}")
print(f"Número de componentes conectados: {nx.number_connected_components(G)}")
print(f"A rede é radial? {'Sim' if is_radial else 'Não'}")
if not is_radial:
    print(f"Foram encontrados {len(ciclos)} ciclo(s), por exemplo: {ciclos[0]}")

# plot da rede para visualização

plt.figure(figsize=(10, 8))
pos = nx.kamada_kawai_layout(G)
nx.draw(
    G,
    pos,
    with_labels=True,
    node_color="lightblue",
    node_size=150,
    font_size=8,
    edge_color="gray",
)

# mostra o valor de r ao lado de cada aresta (descomente se quiser exibir)
# edge_labels = nx.get_edge_attributes(G, "r")
# nx.draw_networkx_edge_labels(G, pos)

titulo = "Topologia da rede - " + ("Radial" if is_radial else "Malhada (com ciclo)")
plt.title(titulo)
plt.axis("off")
plt.tight_layout()
plt.savefig("topologia_rede.svg", dpi=150)
plt.show()