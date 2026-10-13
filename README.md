# Thesis-Companion

Repositório complementar da minha tese. Reúne os **scripts** e os **dados de entrada** e os usados para que os resultados possam ser consultados e reproduzidos.

> Cada pasta tem o seu próprio `README.md`, com instruções detalhadas. Este arquivo é só o **mapa** con informações do que cada pasta contém.

---

## Como navegar

| Pasta | O que contém | Tipo |
|---|---|---|
|[dados/](dados/README.md) | Contém dados de sistemas de distribuição teste que podem ser utilizados. | Dados |
| [scocio data gerador/](scocio%20data%20gerador/README.md) | Gerador de cenários socioeconômicos sintéticos para redes de distribuição (`gerar_cenario.py`). Dada a topologia, a demanda por barra e a classificação das barras, produz renda, moradores por domicílio, número de domicílios e consumo por domicílio. | Função / código |
| [scripts/](scripts/README.md) | Pasta com scripts que foram utilizados ao decorrer do desenvolvimento da tese qeu podem ser úteis para conferência de dados. | - |

---

## Reprodutibilidade

Os dados gerados são **sintéticos** e determinísticos: a mesma semente, as mesmas entradas e as mesmas versões de bibliotecas produzem arquivos idênticos. A semente usada na tese e as versões das bibliotecas estão registradas no README do gerador e no cabeçalho do relatório de geração.

---

## Contato

Norberto Abrante Martinez — norberto.martinez@unesp.br
