# Gerador de cenários socioeconômicos sintéticos para redes de distribuição

Script em Python ([gerar_cenario.py](gerar_cenario.py)) que, dada a topologia de uma rede de distribuição radial, a demanda instalada por barra e a classe socioeconômica de cada barra, gera **indicadores socioeconômicos sintéticos por barra**: renda, moradores por domicílio, número de domicílios e consumo por domicílio.

- **Autocorrelação espacial** sobre a topologia elétrica: barras próximas se parecem.
- **Sem circularidade com a demanda:** renda, moradores e consumo por domicílio são gerados sem usar `Pd_0`.
- **Calibração rastreável:** todo parâmetro fica em uma tabela com coluna de fonte.

> Os dados são sintéticos: servem para testar métodos em cenários controlados, não descrevem uma população real.

## O que há nesta pasta

```
gerar_cenario.py   # o gerador (arquivo único)
README.md
```

As entradas (`ramos.csv`, `barras.csv`, `calibracao.csv`, `config.json`) não estão aqui: você as fornece em uma pasta própria. Os dados de rede estão em [dados/](../dados/) (`sys33.dat`, `sys134.dat`).

## Uso

Requisitos: Python 3.9+, `numpy`, `pandas`, `networkx`.

```bash
python gerar_cenario.py minha_rede/ --saida saida/minha_rede --semente 12345
```

| Opção | Efeito |
|---|---|
| `entrada` | Pasta com `ramos.csv`, `barras.csv`, `calibracao.csv` e, opcionalmente, `config.json` |
| `--saida DIR` | Pasta de saída. Padrão: `saida/<nome da pasta de entrada>` |
| `--semente N` | Sobrepõe a semente do `config.json` |
| `--silencioso` | Não imprime o relatório no terminal (ele é sempre gravado) |

Como biblioteca:

```python
from gerar_cenario import gerar
res = gerar("minha_rede", "saida/minha_rede", semente=12345, verboso=False)
res["nos"], res["indicadores"], res["bandwidth"], res["pesos"], res["avisos"]
```

Erros de entrada interrompem o programa com mensagem em português (`ErroEntrada`; código de saída 2 na linha de comando).

## Entradas

Os CSVs aceitam separador `,` ou `;` (com vírgula decimal).

**`ramos.csv`** — `de,para`. Apenas os ramos **fechados** da configuração radial (chaves normalmente abertas ficam de fora). Rótulos podem ser inteiros ou texto.

**`barras.csv`** — `barra,classe,Pd_0[,observacao]`. Uma linha por barra; a ordem das linhas define a ordem da saída e do fluxo aleatório. `Pd_0` em kW. A classe `subestacao` é reservada (barra sem carga, recebe 0 nos indicadores). Classifique por **contiguidade elétrica e perfil da região**, não pelo nível de demanda, para não reintroduzir circularidade.

**`calibracao.csv`** — `parametro,classe,valor,unidade,fonte,observacao`. Use `todas` em `classe` para valores globais; valor específico de classe tem prioridade.

| Parâmetro | Escopo | Significado |
|---|---|---|
| `renda_mediana` | por classe | Mediana da renda domiciliar (R$/mês) |
| `renda_sigma_log` | por classe | Desvio-padrão do log da renda na classe |
| `moradores_por_domicilio` | por classe | Média de moradores por domicílio |
| `consumo_referencia` | global | Consumo médio (kWh/mês/domicílio) na renda e tamanho de referência |
| `renda_referencia` | global | Renda em que vale `consumo_referencia` |
| `moradores_referencia` | global | Moradores em que vale `consumo_referencia` |
| `elasticidade_renda_consumo` | global | Elasticidade-renda do consumo |
| `elasticidade_moradores_consumo` | global | Elasticidade do consumo ao tamanho domiciliar |
| `sigma_consumo` | global | Dispersão idiossincrática do consumo (escala log) |
| `fator_de_carga` | global | Converte demanda instalada em energia mensal |
| `fracao_residencial` | opcional, por classe | Fração da energia atribuída a domicílios (padrão 1,0) |

**`config.json`** (opcional; `semente` é obrigatória aqui ou em `--semente`):

| Chave | Padrão | Significado |
|---|---|---|
| `titulo` | `REDE SEM TITULO` | Título do relatório |
| `semente` | — | Semente do gerador aleatório |
| `ell_saltos` | 3.0 | Comprimento de correlação espacial (em saltos) |
| `peso_proprio_alvo` | 0.25 | Peso que a própria barra retém na média espacial (define a *bandwidth*) |
| `peso_fator_renda` | 0.60 | Peso do fator comum de vulnerabilidade na renda |
| `peso_fator_moradores` | 0.45 | Idem, nos moradores |
| `desvio_moradores` | 0.45 | Dispersão espacial dos moradores por domicílio |
| `moradores_minimo` | 1.2 | Piso de moradores por domicílio |
| `horas_por_mes` | 730.0 | Horas por mês na conversão potência → energia |

## Saídas

Gravadas em `--saida`:

| Arquivo | Conteúdo |
|---|---|
| `nodes_with_social_factors.csv` | `N,H,f_socio`: valor de cada indicador em cada barra (subestação = 0, ignorar) |
| `socio_indicators.csv` | `H,socio_type`: 1 = valor alto é **mais** vulnerável; 2 = valor alto é **menos** |
| `ponderation_matrix_long.csv` | `i,j,distance`: distância em saltos entre todos os pares de barras |
| `classificacao_barras.csv` | `N,classe,Pd_0` |
| `exposicao_barras.csv` | `N,classe,num_domicilios,habitantes` |
| `tabela_calibracao.csv` | Parâmetros efetivamente usados, com unidade e fonte |
| `pesos_indicadores.csv` | Pesos do índice composto: iguais, entropia e Pearson |
| `relatorio_geracao.txt` | Validação: semente, versões, correlações, Moran, pesos |

Indicadores: `renda_media_domiciliar` (tipo 2), `moradores_por_domicilio` (1), `num_domicilios` (1), `consumo_medio_domicilio` (2).

## Modelo

1. **Campo espacial latente:** campo gaussiano com covariância `exp(-d/ℓ)` sobre a distância em saltos. Gera um fator comum de vulnerabilidade, um campo por indicador e um para o ruído do consumo; cada indicador latente é padronizado dentro da classe.
2. **Renda e moradores:**
   `renda = renda_mediana[c] · exp(renda_sigma_log[c] · z)` e `moradores = max(mínimo, média[c] + desvio · z)`.
3. **Consumo por domicílio** (sem `Pd_0`):
   `consumo = consumo_ref · (renda/renda_ref)^e_renda · (moradores/mor_ref)^e_mor · exp(sigma_consumo · z)`.
4. **Número de domicílios** pela identidade física:
   `energia = Pd_0 · fator_de_carga · horas_por_mes`; `domicílios = round(energia / consumo · fracao_residencial)`; `habitantes = round(domicílios · moradores)`.

O consumo por domicílio é independente de `Pd_0`; o número de domicílios depende dela de propósito, pois mede a exposição da barra.

## Reprodutibilidade

Mesma semente, mesmas entradas (inclusive a ordem de `barras.csv`) e mesmas versões de bibliotecas produzem arquivos idênticos. As versões ficam registradas no cabeçalho de `relatorio_geracao.txt`. A ordem dos sorteios (fator comum, renda, moradores, consumo) não deve ser alterada.

## Limitações

- Dados sintéticos; reproduzem estrutura, não observações.
- Distância topológica em saltos, sem comprimento de linha nem geografia.
- Quatro indicadores fixos; adicionar outro exige editar `gerar_cenario.py`.
- Todas as barras de uma classe compartilham os mesmos parâmetros.
- São necessárias ao menos 3 barras de carga; classes com menos de 3 barras geram aviso.
