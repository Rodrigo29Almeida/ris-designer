# RIS ε<sub>r</sub> Designer

Aplicação em Python (PySide6 + matplotlib) para o estudo teórico de uma **RIS (Reconfigurable Intelligent Surface)** em que cada célula é um dielétrico de espessura fixa *h* sobre um plano de massa, e **só a permitividade ε<sub>r</sub> varia de célula para célula**. A app calcula a distribuição de fase, converte-a em ε<sub>r</sub> por célula e valida o resultado no **CST Studio Suite**.

É a versão Python da app MATLAB App Designer `Theoretical_RIS_Phase_calculator.mlapp`.

## O que faz

1. **Fase ideal** de cada célula pela lei de Snell generalizada:
   φ(x, y) = −k [x (u<sub>i</sub> + u<sub>r</sub>) + y (v<sub>i</sub> + v<sub>r</sub>)], com u = cos El · sin Az e v = sin El.
2. **Casos** contínuo e quantizados a 1, 2, 3 e 4 bits. Para cada caso escolhe a constante de fase global δ que maximiza a eficiência η = |Σ A·e<sup>j(φ<sub>r</sub>−φ<sub>i</sub>)</sup>|² / (MN)².
3. **Fase → ε<sub>r</sub>** através da curva da célula unitária (simulação Floquet no CST, pasta `Phase/`).
4. **Diagramas teóricos** (fator de rede × cos θ): corte em azimute, mapa 2D Az × El, lóbulo principal, SLL e lóbulo espelho de 1 bit.
5. **Validação no CST** (por COM):
   - **Simular no CST**: cada célula é modelada como uma impedância de superfície (IBC) com o mesmo Γ; usa o I-solver.
   - **Run CST Simulation**: RIS com o dielétrico real (ε<sub>r</sub> por célula); usa o Frequency Domain solver.
   - **Carregar do CST**: reutiliza projetos já simulados, depois de confirmar no histórico que correspondem aos mesmos parâmetros.

   O resultado do CST aparece sobreposto à teoria, normalizado ao contínuo ou em valores originais.

## Requisitos

- Windows
- Python 3.11 (ou mais recente)
- CST Studio Suite (só para a validação; o cálculo teórico funciona sem o CST)

```
py -3.11 -m pip install -r requirements.txt
```

## Como usar

```
py -3.11 eris_app.py
```

Introduz M × N, o tamanho da célula (Lx, Ly), a frequência, a espessura h e os ângulos de incidência e de reflexão, e carrega em **Calculate**.

O cálculo também pode ser usado sem interface:

```python
import ris_core as rc
R = rc.calcular(rc.Params(M=10, N=10, Px=0.025, Py=0.025, Freq=3.9, h_mm=1.524, Az_r=30))
print(R.res[2].er)      # matriz de εr do caso de 2 bits
```

## Estrutura

| Ficheiro / pasta | Conteúdo |
|---|---|
| `eris_app.py` | Interface (tabs de fase, curva, RIS Info, diagrama de radiação, mapa 2D e resumo) |
| `ris_core.py` | Cálculo: fase, δ ótimo, εr, diagramas, lóbulos, impedâncias de superfície, leitura de resultados |
| `ris_cst.py` | Ligação ao CST por COM (pywin32): criação dos modelos, simulação e leitura do campo distante |
| `Phase/` | Curvas fase–εr da célula unitária: `<f>_h<h>[_th<ângulo>].txt`, com colunas εr, fase [°] e \|S11\| |
| `Estudo_Er/Validacao_CST/` | Projetos CST e resultados `.mat`, criados pela app (não estão no repositório) |

A curva é escolhida automaticamente pela frequência, pela espessura e pelo ângulo de incidência. Por exemplo, 3,9 GHz com h = 1,524 mm usa `3_9_h1p524.txt`.

Para usar uma pasta `Estudo_Er` noutro sítio (por exemplo, uma que já tenha simulações), define a variável de ambiente `ERIS_PASTA_ESTUDO_ER` com o caminho dessa pasta.

## Notas

- A polarização é sempre horizontal.
- Os resultados CST (`.mat`) têm o mesmo formato da app MATLAB (struct `reg`), por isso as duas apps partilham as simulações.
- Os nomes dos projetos CST incluem M × N, o tamanho da célula, a frequência, h, a direção de reflexão e a data/hora, por exemplo `Val_2bits_10x10_P25x25mm_3_9GHz_h1p524_Az30_El0_20261123_105932.cst`.
