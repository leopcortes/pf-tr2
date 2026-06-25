# Comandos - Projeto Final TR2

## 0. Setup
```bash
cd pf-tr2
python3 -m venv .venv && .venv/bin/pip install matplotlib
```

---

## 1. Experimentos do relatório (mock, comparação P1 vs P2 vs P3)

Gera CSVs + gráficos comparativos das 3 políticas e imprime a tabela com QoE.
O playback é em tempo real, então cada run leva ~`segments × 2s` por política.

```bash
# tudo de uma vez (controlled + jitter + failover)
.venv/bin/python experiment.py --mode all --segments 30

# ou cenário a cenário:
.venv/bin/python experiment.py --mode controlled --segments 30   # oscilação -> QoE dá P3 > P2 > P1
.venv/bin/python experiment.py --mode jitter     --segments 30   # queda+jitter -> P3 robusta, P2 colapsa
.venv/bin/python experiment.py --mode failover   --segments 20 --kill-after 8   # A->B
```

Saída:
- `results/p2_controlled/` - `metrics_p{1,2,3}.csv` + `compare_quality.png` + `compare_buffer.png`
- `results/p3_jitter/` - `metrics_p{1,2,3}.csv` + `compare_quality.png` + `compare_buffer.png` + `compare_jitter.png`
- `results/p2_failover/` - `metrics.csv` + `throughput_quality.png` + `buffer_level.png`

---

## 2. Demos no servidor real

```bash
# baseline (Entrega 1): baixa 10 segmentos, gera CSV + painel
.venv/bin/python client.py --policy p1 -n 10 -o results/p1_real.csv

# P2 e P3 no real (use n>=20 para a P2 sair do slow-start)
.venv/bin/python client.py --policy p2 -n 20 -o results/p2_real.csv
.venv/bin/python client.py --policy p3 -n 20 -o results/p3_real.csv

# sessão da apresentação final (P3, buffer grande p/ absorver o failover do professor)
.venv/bin/python client.py --policy p3 -n 40 --max-buffer 20 -o results/sessao.csv
```

Gráficos individuais de uma sessão real (ex.: a sessão final):
```bash
.venv/bin/python graph.py -i results/sessao.csv -d results/sessao
```

---

## 3. Ensaio do cenário surpresa

Um "professor" simulado muda a banda, injeta jitter e derruba o A (-> failover p/ B):
```bash
.venv/bin/python experiment.py --mode live --segments 30 --bw-at 14 --jit-at 22 --kill-at 32
```
Saída em `results/live/`.

---

## 4. Regenerar gráficos comparativos das 3 políticas

O `experiment.py` já gera esses gráficos. Para regerar à mão a partir dos CSVs:
```bash
# controlled (oscilação) - sem gráfico de jitter (jitter baixo aqui)
.venv/bin/python graph.py -i results/p2_controlled/metrics_p1.csv \
  --compare results/p2_controlled/metrics_p2.csv \
  --compare results/p2_controlled/metrics_p3.csv \
  --labels P1 P2 P3 --no-jitter -d results/p2_controlled

# jitter (queda) - COM gráfico de jitter EWMA (obrigatório no relatório)
.venv/bin/python graph.py -i results/p3_jitter/metrics_p1.csv \
  --compare results/p3_jitter/metrics_p2.csv \
  --compare results/p3_jitter/metrics_p3.csv \
  --labels P1 P2 P3 -d results/p3_jitter

# failover (individual, com linha vertical no evento)
.venv/bin/python graph.py -i results/p2_failover/metrics.csv -d results/p2_failover --no-jitter
```

---

## 5. Flags do client.py

| Flag | O que faz | Default |
|---|---|---|
| `--policy {p1,p2,p3}` | política ABR | p1 |
| `--server URL` | servidor de manifest | real `…:8080` |
| `-n, --segments N` | nº de segmentos (cada um ~2s real) | 30 |
| `-o, --output ARQ` | CSV de saída | metrics_baseline.csv |
| `--max-buffer S` | teto do buffer (s); maior = mais folga p/ failover | 10 |
| `--confirm N` | (P2) segmentos p/ confirmar troca | 3 |
| `--alpha A` | (P3) peso da EWMA da vazão | 0.4 |
| `--k-sigma K` | (P3) margem conservadora (k·σ) | 1.0 |
| `--jitter-ref MS` | (P3) jitter acima do piso que satura a penalidade | 60 |
| `--jitter-floor MS` | (P3) zona morta: jitter abaixo disso não penaliza | 20 |

## 6. Linha do painel
```
seg 1 │ A │ 480p 600↑│ thr 1257▲ │ buf 3.5✓ │ jit 19 │ ewma1213 -1σ14 x1.00jit = 1199 -> 480p
    │   │    │    │ │      │   │        │ │      │            └ razão da decisão (ewma −kσ ×jit = estimativa)
    │   │    │    │ │      │   │        │ │      └ jitter rede (ms); ⚠ amarelo se ≥40
    │   │    │    │ │      │   │        │ └ can_play: ✓ buf≥2s / ✗ abaixo
    │   │    │    │ │      │   │        └ buffer (s) (verde ok / vermelho)
    │   │    │    │ │      │   └ tendência vazão: ▲sobe ▼cai =estável
    │   │    │    │ │      └ vazão medida (kbps)
    │   │    │    │ └ troca de qualidade: ↑subiu ↓desceu
    │   │    │    └ bitrate nominal (kbps)
    │   │    └ qualidade escolhida      
    │   └ servidor (A verde / B amarelo)
    └ segmento
```
Razão por política:
- P1: `media x0.8 = EST -> q` "estimativa é igual a média 3 últimas vazões, com 20% de folga → cabe 720p."
- P2: `alvo=Q conf n/3 -> q` "a banda pede Q, já confirmei n de 3 vezes, mas até confirmar a 3ª eu seguro em q."
- P3: `ewma… -kσ… x…jit = EST -> q` "vazão recente, menos volatilidade, menos a penalidade de jitter, dá x kbps de estimativa segura → q."
