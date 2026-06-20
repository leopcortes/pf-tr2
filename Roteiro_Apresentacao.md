# Apresentação Final TR2 (ABR + Failover)

## 0. Setup (antes de entrar)
```bash
cd pf-tr2
python3 -m venv .venv && .venv/bin/pip install matplotlib
```

---

## 1. Comandos da demo

### (a) Baseline no servidor REAL, painel ao vivo - Entrega 1
```bash
.venv/bin/python client.py --policy p1 --server http://137.131.178.229:8080 -n 10 -o results/p1_real.csv
```

### (b) P3 no servidor REAL durante o cenário surpresa (banda/jitter/failover do professor)
```bash
.venv/bin/python client.py --policy p3 --server http://137.131.178.229:8080 -n 40 --max-buffer 20 -o results/sessao.csv
```

### (c) Ensaio offline do cenário surpresa (pra treinar sem o professor)
```bash
.venv/bin/python experiment.py --mode live --segments 30 --bw-at 14 --jit-at 22 --kill-at 32
```

### (d) Regerar gráficos comparativos (se precisar)
```bash
.venv/bin/python graph.py -i results/p3_jitter/metrics_p1.csv --compare results/p3_jitter/metrics_p2.csv -d results/p3_jitter --no-jitter
```

---

## 2. Flags do CLIENTE (client.py)

| Flag | O que faz | Valor / default |
|---|---|---|
| `--policy {p1,p2,p3}` | escolhe a política ABR | p1 = baseline, p2 = histerese, p3 = EWMA+σ+jitter |
| `--server URL` | servidor de manifest (de onde sai a lista A/B) | default real `…:8080` |
| `-n, --segments N` | quantos segmentos baixar | cada um leva ~2s real (pacing) |
| `-o, --output ARQ` | CSV de saída | uma linha por segmento |
| `--max-buffer S` | teto do buffer em segundos | 10. Maior = mais folga p/ absorver failover |
| `--confirm N` | (só P2) segmentos p/ confirmar troca | 3. Maior = mais estável, reage mais devagar |
| `--alpha A` | (só P3) peso da EWMA da vazão | 0.4. Maior = reage mais rápido, menos suave |
| `--k-sigma K` | (só P3) margem conservadora (k desvios-padrão) | 1.0. Maior = mais conservador em rede instável |
| `--jitter-ref MS` | (só P3) jitter que satura a penalidade | 45ms. Menor = pune jitter mais cedo |
| `--quiet` | só eventos + resumo (sem linha por segmento) | usado nos lotes |
| `--no-color` | desliga cores ANSI | - |

## 3. Flags do SERVIDOR mock (server.py) - caso precise mostrar localmente
| Flag | O que faz |
|---|---|
| `--id A/B` `--port` | identidade e porta da instância |
| `--bandwidth KBPS` | banda inicial |
| `--profile "0:2000,8:400"` | banda por índice de segmento (cria a queda brusca) |
| `--bw-noise 0.22` | ruído relativo por segmento (cria a oscilação) |
| `--jitter MS` | jitter por chunk |
| `--seed N` | reprodutibilidade (P1/P2/P3 enfrentam o mesmo cenário) |
| controle ao vivo | `GET /control?bandwidth_kbps=...&jitter_ms=...&reset=1` |

## 4. Flags do experiment.py (modo live)
`--bw-at SEG` muda a banda nesse segmento · `--jit-at SEG` injeta jitter · `--kill-at SEG` mata o A (→ failover).

---

## 5. Linha do painel - leitura rápida
```
seg 1 │ A │ 480p 600↑│ thr 1257▲ │ buf 3.5✓ │ jit 19 │ media x0.8 = 894 -> 480p
 │       │    │    │ │       │        │   │       │            └ razão da decisão (o "porquê")
 │       │    │    │ │       │        │   │       └ jitter rede (ms); ⚠ amarelo se ≥40
 │       │    │    │ │       │        │   └ can_play: ✓ buf≥2s / ✗ abaixo
 │       │    │    │ │       │        └ buffer em segundos (verde ok / vermelho)
 │       │    │    │ │       └ tendência vazão: ▲sobe ▼cai =estável(±5%)
 │       │    │    │ └ vazão medida neste segmento (kbps)
 │       │    │    └ troca de qualidade: ↑subiu ↓desceu
 │       │    └ bitrate nominal (kbps)
 │       └ qualidade escolhida
 └ servidor ativo (A verde / B amarelo)
```
Razão por política: P1 `media x0.8 = EST -> q` · P2 `alvo=Q conf n/3 -> q` · P3 `ewma… -kσ… x…jit = EST -> q`

---

## 6. Respostas para possíveis perguntas

"Onde no código a decisão ABR é tomada?"
→ método `select()` da política. P3 em client.py:189; a fórmula `est=(ewma−k·σ)·pen` em client.py:179.

"Quanto durou o failover?"
→ o banner mostra `(X ms p/ achar servidor saudável)`. ~1–2ms; o segmento é re-baixado no B na MESMA iteração (loop em client.py:457). Failover em client.py:282.

"O buffer foi suficiente?"
→ olhar `buf …✓` da linha do failover. Cheio → can_play=1 → zero rebuffer.

"Qualidade após a troca e por quê?"
→ segue alta logo após (buffer cheio dá folga); depois a P3 desce 1 nível porque aprende que o B é mais lento (EWMA + σ confirmam a vazão menor).

"E se o buffer estivesse abaixo de 4s na queda do A?"
→ can_play viraria 0; consume() (client.py:28) registraria stall durante o re-download no B; banner mostraria "BUFFER INSUFICIENTE - rebuffer". É o que o cenário de jitter demonstra.

"Qual política reagiu melhor?" (responder com NÚMERO, não opinião)
→ Cenário jitter: rebuffers P1=2, P2=7, P3=1; stall P1=0,47s, P2=8,79s, P3=0,06s, bitrate comparável.
→ Cenário oscilação: trocas P1=7, P2=2, P3=4.

"Por que os comparativos são do mock e não do real?"
→ servidor real é fixo (A=2000/B=1000, sem variação) e não-derrubável; mock replica a mesma interface e as mesmas perturbações que o professor aplica ao vivo; `--seed` garante comparação justa.
