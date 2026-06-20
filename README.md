# Projeto Final TR2

## Streaming Adaptativo com Failover

Cliente de streaming ABR (Adaptive Bitrate) para a disciplina Teleinformática e Redes 2. Implementa download de segmentos via HTTP, seleção dinâmica de qualidade, gestão de buffer com continuous play, e failover automático entre servidores por prioridade.

Servidor da disciplina: `http://137.131.178.229:8080` (primário, `A`) e `:8081` (secundário, `B`).

## Estado atual

| Entrega | Política | Status |
|---|---|---|
| 1 | P1 - Rate-Based baseline | ✅ |
| 2 | Análise de deficiências + P2 (histerese) + failover A→B | ✅ |
| Final | P3 (EWMA + σ + jitter) + painel ao vivo + ensaio do cenário surpresa | ✅ |

## Estrutura

```
client.py              # cliente: P1/P2/P3, buffer com pacing real-time, failover, painel ao vivo
server.py              # mock da infra (A/B): segmentos de 2s, muda banda ao vivo (/control), killable
experiment.py          # gera os artefatos do relatorio (controlado / jitter / failover / live)
graph.py               # graficos (individual e comparativo de N politicas, com marcador de failover)
results/
  baseline/      metrics.csv  throughput_quality.png        # Entrega 1: P1 no real, rede estavel
  controlled/    metrics_p{1,2,3}.csv                        # P1 vs P2 vs P3, banda ruidosa (oscilacao)
                 compare_quality.png  compare_buffer.png    #   -> a deficiencia (oscilacao) do baseline aparece
  jitter/        metrics_p{1,2,3}.csv  compare_*.png         # banda alta -> queda brusca: AQUI a P3 ganha
  failover/      metrics.csv  buffer_level.png               # P3 + A->B (buffer absorve a troca)
  live/          metrics.csv                                 # ensaio do cenario surpresa (P3)
```

### Por que um mock (cenários controlados)

O servidor real da disciplina é fixo (A=2000 / B=1000 kbps, sem variação) e não é derrubável pelos alunos. Em rede estável as políticas são equivalentes e o failover é impossível sem matar o A. Então as comparações que provam as deficiências (oscilação na Tarefa 2, queda brusca/jitter na Tarefa 3) e o failover usam um servidor **mock** - o mesmo tipo de cenário que o professor impõe ao vivo (mudar banda, injetar jitter, derrubar o A). O mock é determinístico por `--seed`, então P1/P2/P3 enfrentam exatamente a mesma sequência de banda e jitter (comparação justa). O `baseline/` é a referência "rede real estável".

## Instalação

Python 3.8+. O cliente e o servidor usam só a stdlib; `graph.py` precisa de `matplotlib`.

```bash
cd pf-tr2
python3 -m venv .venv
.venv/bin/pip install matplotlib
```

## Como rodar

### Experimento completo (recomendado)

Gera os artefatos do relatório: P1 vs P2 vs P3 no controlado e no jitter, mais o failover.

```bash
.venv/bin/python experiment.py --mode all --segments 30 --outdir results
```

O playback é em **tempo real** (buffer limitado), então cada run leva ~`segments * 2s`. Use `--segments` menor para iterar mais rápido.

Modos: `--mode controlled` (oscilação), `--mode jitter` (a P3 ganha), `--mode failover`, `--mode all`, e `--mode live` (ensaio do cenário surpresa, ver abaixo). Opções: `--max-buffer`, `--jitter-profile`, `--jitter-bw-noise`, `--jitter-ms`, `--kill-after`, `--k-sigma`, `--jitter-ref`.

### Simulação da apresentação ao vivo

Roda a P3 com o painel ao vivo enquanto um "professor" simulado, numa timeline, derruba a banda do A, injeta jitter e por fim mata o A (→ failover para B). É o ensaio da demo final.

```bash
.venv/bin/python experiment.py --mode live --segments 30 \
  --bw-at 14 --jit-at 22 --kill-at 32
```

### Apresentação ao vivo com professor

```bash
# P3 contra o servidor real, com o painel ao vivo
.venv/bin/python client.py --policy p3 --server http://137.131.178.229:8080 -n 40 --max-buffer 20 -o sessao.csv

# politicas P1/P2 para comparacao
.venv/bin/python client.py --policy p1 --server http://137.131.178.229:8080 -n 30 -o p1_real.csv
```

Flags do cliente:
- `--policy {p1,p2,p3}` - política ABR (default `p1`)
- `--confirm N` - P2: segmentos para confirmar mudança de qualidade (default 3)
- `--alpha A` `--k-sigma K` `--jitter-ref MS` - parâmetros da P3 (peso EWMA, margem em σ, jitter de referência)
- `--max-buffer S` - teto do buffer em segundos / playback real-time
- `--quiet` - sem painel por segmento (só eventos + resumo); `--no-color` desliga ANSI
- `--server URL`, `-n/--segments`, `-o/--output`

### Servidor mock (manual)

```bash
.venv/bin/python server.py --id A --port 8090 --port-a 8090 --port-b 8091 --bandwidth 2000
.venv/bin/python server.py --id B --port 8091 --port-a 8090 --port-b 8091 --bandwidth 1000
```

Flags úteis: `--profile "0:2000,8:1100"` (banda em kbps por índice de segmento), `--bw-noise 0.22` (ruído relativo por segmento), `--jitter 2` (ms por chunk), `--seed` (reprodutibilidade). Banda também muda ao vivo via `GET /control?bandwidth_kbps=...&jitter_ms=...&reset=1`.

### Gráficos

```bash
# individual (com linha vertical no failover, se houver)
.venv/bin/python graph.py -i results/failover/metrics.csv -d results/failover --no-jitter

# comparativo P1 vs P2 (sobreposto)
.venv/bin/python graph.py -i results/controlled/metrics_p1.csv --compare results/controlled/metrics_p2.csv -d results/controlled --no-jitter
```

## Política 1 (baseline) - como decide

`RateBasedABR`: mantém as últimas 3 vazões, estima a próxima = média * 0.8, e escolhe o maior bitrate <= estimativa. Sem histórico → menor qualidade.

Deficiência conhecida: sob vazão ruidosa perto da fronteira de uma qualidade, a estimativa cruza a fronteira para os dois lados e o ABR oscila (flapping de qualidade), o que prejudica a experiência sem ganho real de banda.

## Política 2 - Rate-Based com histerese

`RateBasedHysteresisABR`: mesmo estimador do baseline, com duas mudanças:

1. **Slow-start:** começa em 240p e sobe gradualmente (um nível por vez).
2. **Histerese:** só muda de qualidade após `confirm` (default 3) segmentos consecutivos apontando para a mesma direção. Ruído de vazão que faria o baseline oscilar não acumula confirmação, então a qualidade fica estável.

Resolve a deficiência de oscilação do baseline. **Mas introduz uma fraqueza:** numa queda brusca de banda, a histerese (confirm=3) demora a descer e o buffer drena (ver cenário de jitter abaixo) - fraqueza que a P3 corrige.

## Política 3 - EWMA + desvio-padrão + penalidade de jitter

`EwmaStdJitterABR`: estimativa conservadora com componente estatístico e sensibilidade a jitter. Três ideias somadas:

```
thr_ewma = α·thr + (1-α)·thr_ewma        # EWMA: vazão recente pesa mais (reage rápido)
σ        = std(janela de vazões)          # volatilidade da rede
pen_jit  = 1 - min(jitter_ewma/J0, 0.5)   # jitter alto penaliza até 50%
estimativa = (thr_ewma − k·σ) · pen_jit   # escolhe maior bitrate ≤ estimativa
```

1. **EWMA da vazão** (α=0.4): reage mais rápido a mudanças de banda que a média simples do baseline.
2. **Margem por desvio-padrão** (k=1): subtrai `k·σ`. Quanto mais volátil a vazão, mais conservadora a escolha - não escolhe uma qualidade que só a média sustenta.
3. **Penalidade de jitter** (J0=45 ms): quando o jitter EWMA sobe, a entrega fica irregular e a vazão média engana; a estimativa cai proporcionalmente, protegendo o buffer. **É o tratamento explícito de jitter** que a Tarefa 3 exige.

Mantém **histerese assimétrica**: sobe devagar (confirm_up=2, evita oscilação como a P2) mas desce rápido (confirm_down=1, protege o buffer na queda). É o que falta na P2.

## Análise de deficiências e comparação das 3 políticas

**Cenário 1 - oscilação (`results/controlled/`):** banda ruidosa (`bw-noise=0.22`) perto da fronteira de 480p. Aqui aparece a deficiência do baseline.

| Métrica | P1 | P2 | P3 |
|---|---|---|---|
| trocas de qualidade | 7 | 2 | 4 |
| rebuffers | 0 | 0 | 0 |
| bitrate médio (kbps) | 653 | 620 | 627 |

O baseline (P1) oscila (7 trocas, flapa 480p↔360p quando o ruído cruza a fronteira). P2 e P3 estabilizam (2 e 4 trocas); a P3 fica entre as duas porque o downshift rápido (confirm_down=1) reage a alguns vales - preço que ela paga para vencer no cenário seguinte.

**Cenário 2 - jitter / queda brusca (`results/jitter/`):** banda alta e estável (3000 kbps) que despenca para 430 kbps no segmento 10, com jitter alto. Aqui a P3 ganha.

| Métrica | P1 | P2 | P3 |
|---|---|---|---|
| rebuffers | 2 | 7 | 1 |
| stall total (s) | 0.47 | 8.79 | 0.06 |
| bitrate médio (kbps) | 380 | 390 | 337 |

Na fase estável as três cavalgam 480p. Na queda (seg 10), P1 (média) demora 1-2 segmentos a descer e P2 (histerese) trava em 480p por ~5 segmentos - 480p a 430 kbps leva ~3,3 s/segmento, o buffer drena e estola em cadeia (7 rebuffers, 8,8 s de stall). A P3 reage no mesmo segmento (EWMA + salto do σ) e desce para 360p→240p, sobrevivendo com 1 rebuffer (0,06 s), ao custo de ~10% menos bitrate que o baseline. Em `results/jitter/compare_buffer.png` a P2 fica grudada no threshold de 2 s com a fileira de rebuffers.

> Conclusão: a P3 reduziu o rebuffering de 2→1 vs baseline e de 7→1 vs P2, e o stall de 0,47→0,06 s, mantendo bitrate comparável. É robusta nos dois cenários, enquanto P1 falha na queda e P2 na queda também.

## Failover

- Lê a lista `servers` do manifest e ordena por `priority`.
- Em falha do servidor ativo (timeout / conexão recusada / status ≠ 200): faz `GET /health` no próximo da fila, migra para o primeiro saudável, re-baixa o segmento no novo servidor, incrementa `failover_total` e troca `server_id`.
- O `buffer_can_play` na linha do evento indica se o buffer absorveu a troca sem rebuffer.

Failover só é testável num servidor que controlamos (o real não é derrubável; na apresentação quem mata o A é o professor, ao vivo). No cenário do mock (A=1500, B=1000 kbps), com a P3 e derrubando o A no meio do streaming:

- **Tempo:** ~1-2 ms para o health-check achar o B saudável; o segmento é re-baixado no B na mesma iteração.
- **Buffer suficiente?** Sim - buffer cheio no momento da queda → `can_play=1`, **zero rebuffer** (a linha do evento no CSV mostra `buffer_can_play=1`).
- **Qualidade após a troca e por quê:** segue na qualidade alta imediatamente após a troca (o buffer cheio dá folga); em seguida a P3 **desce um nível**, pois aprende que o B é mais lento (vazão menor) e o desvio-padrão/EWMA confirmam a queda - adaptação correta à capacidade real do novo servidor.

Ver `results/failover/buffer_level.png` (linha vertical no segmento do evento).

## Painel ao vivo

O cliente imprime um painel no terminal (stdlib, sem dependências; cores ANSI que desligam sozinhas fora de um terminal). Uma linha por segmento - mantém o histórico visível, então responde "em qual segmento o cliente detectou a mudança" - com:

- servidor ativo (verde A / amarelo B), qualidade, vazão com tendência ▲▼, buffer com `can_play` ✓/✗, jitter com alerta ⚠;
- a razão da decisão ABR ao lado (`ewma… −σ… ×jit = estimativa → qualidade`) - é o "porquê" que o professor pergunta, e mostra onde a decisão é tomada (`select()` da política em `client.py`);
- failover e rebuffer em destaque (banner vermelho com tempo da troca e veredito do buffer);
- quadro-resumo ao final (trocas, rebuffers, stall, bitrate médio, failovers).

`--quiet` reduz para só os eventos + resumo (usado pelo `experiment.py` nas runs em lote).

## Buffer e pacing

`BufferManager`: nível em segundos. `add_segment()` soma `segment_duration_s`; `consume()` subtrai o tempo real decorrido (registra `rebuffer_event` + `stall_duration_s` se o buffer zerar antes). O cliente faz **pacing**: quando o buffer passa de `max_buffer`, espera o playback drenar antes de buscar o próximo segmento (modela playback em tempo real). Sem isso o cliente baixaria tudo de uma vez e o buffer cresceria sem limite, escondendo rebuffer/oscilação.

## CSV gerado

| Coluna | Descrição |
|---|---|
| `segment` | índice (0..N-1) |
| `timestamp` | ISO 8601 do fim do download |
| `server_id` | ID do servidor ativo (`A`, `B`) |
| `quality` / `bitrate_kbps` | qualidade e bitrate nominal escolhidos |
| `throughput_kbps` | vazão medida no download |
| `download_time_s` | duração do download |
| `jitter_network_ms` | jitter médio entre chunks de 4KB |
| `jitter_ewma_ms` | jitter suavizado (α=0.2) entre segmentos |
| `buffer_level_s` | nível do buffer após receber o segmento |
| `buffer_can_play` | 1 se buffer ≥ 2s, senão 0 |
| `rebuffer_event` / `stall_duration_s` | stall antes deste segmento e sua duração |
| `failover_total` | contagem acumulada de trocas de servidor |

## Manifest esperado

```json
{
  "version": "2.0",
  "segment_duration_s": 2,
  "servers": [
    {"id": "A",     "url": "http://...:8080", "priority": 1},
    {"id": "B", "url": "http://...:8081", "priority": 2}
  ],
  "representations": [
    {"quality": "240p",  "bitrate_kbps": 200,  "url_path": "/segment/240p"},
    {"quality": "1080p", "bitrate_kbps": 3000, "url_path": "/segment/1080p"}
  ]
}
```

O cliente também aceita o nome antigo `qualities` com campo `name`, por compatibilidade.
