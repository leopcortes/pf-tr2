# Projeto Final TR2

## Streaming Adaptativo com Failover

Cliente de streaming ABR (Adaptive Bitrate) para a disciplina Teleinformática e Redes 2. Implementa download de segmentos via HTTP, seleção dinâmica de qualidade, gestão de buffer com continuous play, e failover automático entre servidores por prioridade.

Servidor da disciplina: `http://137.131.178.229:8080` (primário, `A`) e `:8081` (secundário, `srv-B`).

## Estado atual

| Entrega | Política | Status |
|---|---|---|
| 1 | P1 - Rate-Based baseline | ✅ |
| 2 | Análise de deficiências + P2 (histerese) + failover A→B | ✅ |
| Final | P3 (EWMA/estatística) + Wireshark + relatório | pendente |

## Estrutura

```
client.py              # cliente: P1 e P2, buffer com pacing real-time, failover
server.py              # mock da infra (A/B) p/ cenarios reprodutiveis + failover killable
experiment.py          # orquestra os 3 cenarios (real, controlado, failover)
graph.py               # graficos individuais e comparativos (com marcador de failover)
results/
  baseline/                  # Entrega 1 (Tarefa 1): P1 no servidor real, rede estavel
  real/                      # P1 vs P2 no servidor real (rede estavel -> equivalentes)
    p1/  p2/  compare/
  controlled/                # P1 vs P2 no mock, banda variavel (a deficiencia aparece)
    p1/  p2/  compare/
  failover/                  # failover A->srv-B (servidor controlavel)
```

Cada pasta `p1/`, `p2/`, `failover/` tem `metrics.csv` + `throughput_quality.png`, `buffer_level.png`, `jitter.png`; `compare/` tem os 3 gráficos sobrepostos. O `experiment.py` cria e popula tudo.

### Dois cenários (real e controlado)

O servidor real da disciplina é fixo (A=2000 / B=1000 kbps, sem variação) e não derrubável. Em rede estável o baseline não tem deficiência para mostrar, e failover é impossível sem matar o A. Então:

- **`real/`** = evidência honesta de que, em rede boa, P1 e P2 são equivalentes (a P2 fica até um pouco atrás pelo slow-start). Mostra que a deficiência é condicional à variação de banda.
- **`controlled/`** = mesmo cliente contra um servidor que nós controlamos (mock), reproduzindo banda variável. É onde a oscilação do baseline aparece e a P2 a resolve. É a comparação que prova o ponto da Tarefa 2 (e o mesmo tipo de cenário que o professor impõe ao vivo).
- **`failover/`** = só é possível num servidor controlável; o real é derrubado ao vivo pelo professor na apresentação.

## Instalação

Python 3.8+. O cliente e o servidor usam só a stdlib; `graph.py` precisa de `matplotlib`.

```bash
cd pf-tr2
python3 -m venv .venv
.venv/bin/pip install matplotlib
```

## Como rodar

### Experimento completo

Roda os três cenários: P1 vs P2 no servidor real, P1 vs P2 no mock com banda variável, e o failover.

```bash
.venv/bin/python experiment.py --mode all --segments 30 --outdir results
```

O playback é em **tempo real** (buffer limitado), então cada run leva ~`segments * 2s`. Use `--segments` menor para iterar mais rápido.

Modos: `--mode real`, `--mode controlled`, `--mode failover`, `--mode all`. Opções: `--real-server URL`, `--max-buffer` (default 20), `--profile`, `--bw-noise`, `--kill-after`.

### Cliente avulso

```bash
# P1 contra o servidor real
.venv/bin/python client.py --policy p1 --server http://137.131.178.229:8080 -n 30 --max-buffer 20 -o results/real/p1/metrics.csv

# politica 2 (histerese) contra o mock local
.venv/bin/python client.py --policy p2 --server http://127.0.0.1:8090 -n 30 --max-buffer 20 -o results/controlled/p2/metrics.csv
```

Flags do cliente:
- `--policy {p1,p2}` - política ABR (default `p1`)
- `--confirm N` - P2: segmentos consecutivos para confirmar mudança de qualidade (default 3)
- `--max-buffer S` - teto do buffer em segundos / playback real-time (default 10; experimento usa 20)
- `--server URL`, `-n/--segments`, `-o/--output`

### Servidor mock (manual)

```bash
.venv/bin/python server.py --id A --port 8090 --port-a 8090 --port-b 8091 --bandwidth 2000
.venv/bin/python server.py --id srv-B --port 8091 --port-a 8090 --port-b 8091 --bandwidth 1000
```

Flags úteis: `--profile "0:2000,8:1100"` (banda em kbps por índice de segmento), `--bw-noise 0.22` (ruído relativo por segmento), `--jitter 2` (ms por chunk), `--seed` (reprodutibilidade). Banda também muda ao vivo via `GET /control?bandwidth_kbps=...&jitter_ms=...&reset=1`.

### Gráficos

```bash
# individual (com linha vertical no failover, se houver)
.venv/bin/python graph.py -i results/failover/metrics.csv -d results/failover

# comparativo P1 vs P2 (sobreposto)
.venv/bin/python graph.py -i results/controlled/p1/metrics.csv --compare results/controlled/p2/metrics.csv -d results/controlled/compare
```

## Política 1 (baseline) - como decide

`RateBasedABR`: mantém as últimas 3 vazões, estima a próxima = média * 0.8, e escolhe o maior bitrate <= estimativa. Sem histórico → menor qualidade.

Deficiência conhecida: sob vazão ruidosa perto da fronteira de uma qualidade, a estimativa cruza a fronteira para os dois lados e o ABR oscila (flapping de qualidade), o que prejudica a experiência sem ganho real de banda.

## Política 2 - Rate-Based com histerese

`RateBasedHysteresisABR`: mesmo estimador do baseline, com duas mudanças:

1. **Slow-start:** começa em 240p e sobe gradualmente (um nível por vez).
2. **Histerese:** só muda de qualidade após `confirm` (default 3) segmentos consecutivos apontando para a mesma direção. Ruído de vazão que faria o baseline oscilar **não acumula confirmação**, então a qualidade fica estável.

Resolve a deficiência de **oscilação** do baseline.

## Análise de deficiências (P1 vs P2)

### Cenário real (rede estável) - `results/real/`

P1 e P2 contra o servidor real, em sequência (vazão ~1250 kbps, CV ~5%):

| Métrica | P1 real | P2 real |
|---|---|---|
| trocas de qualidade | 1 | 2 |
| rebuffers | 0 | 0 |
| bitrate médio (kbps) | 683 | 620 |

Em rede estável **não há oscilação** - as duas convergem para 480p. A P2 fica até um pouco atrás (1 troca a mais e bitrate menor) por causa do slow-start. **Conclusão honesta: a deficiência não existe quando a banda não varia.** Por isso a comparação que prova o ponto é a controlada.

### Cenário controlado (banda variável) - `results/controlled/`

Mesmo cliente contra o mock: ramp a 1600 kbps (seg 0–7) e platô ruidoso a 1100 kbps (seg 8+, `bw-noise=0.22`), onde a vazão medida (~950 kbps) faz a estimativa cruzar a fronteira de 700 kbps (480p).

| Métrica | P1 | P2 |
|---|---|---|
| trocas de qualidade | **5** | **2** |
| rebuffers | 0 | 0 |
| bitrate médio (kbps) | 663 | 620 |

Aqui a deficiência aparece: o baseline **oscila** (flipa 480p↔360p toda vez que o ruído cruza a fronteira), enquanto a P2 segura 480p. P2 reduz as trocas em ~60% mantendo praticamente a mesma qualidade média (a diferença é só o slow-start).

## Failover

- Lê a lista `servers` do manifest e ordena por `priority`.
- Em falha do servidor ativo (timeout / conexão recusada / status ≠ 200): faz `GET /health` no próximo da fila, migra para o primeiro saudável, **re-baixa o segmento** no novo servidor, incrementa `failover_total` e troca `server_id`.
- O `buffer_can_play` na linha do evento indica se o buffer absorveu a troca sem rebuffer.

Failover **só é testável num servidor que controlamos** (o real não é derrubável; na apresentação quem mata o A é o professor, ao vivo). No cenário do mock (A=1500, B=1000 kbps), derrubando A no segmento 12:

- **Tempo:** ~1 ms para o health-check achar o B saudável; o segmento é re-baixado no B na mesma iteração.
- **Buffer suficiente?** Sim - buffer ~20 s no momento da queda → `can_play=1`, **zero rebuffer**.
- **Qualidade após a troca e por quê:** segue **480p** imediatamente (segs 12–16), porque o buffer cheio dá folga para continuar na qualidade alta enquanto a P2 reavalia. Em seguida (seg 17) a P2 **desce para 360p**, pois aprende que o B é mais lento (vazão ~870 vs ~1200 kbps no A) e a histerese confirma a queda — adaptação correta à capacidade real do novo servidor.

Ver `results/failover/buffer_level.png` (linha vertical no segmento do evento).

## Buffer e pacing

`BufferManager`: nível em segundos. `add_segment()` soma `segment_duration_s`; `consume()` subtrai o tempo real decorrido (registra `rebuffer_event` + `stall_duration_s` se o buffer zerar antes). O cliente faz **pacing**: quando o buffer passa de `max_buffer`, espera o playback drenar antes de buscar o próximo segmento (modela playback em tempo real). Sem isso o cliente baixaria tudo de uma vez e o buffer cresceria sem limite, escondendo rebuffer/oscilação.

## CSV gerado

| Coluna | Descrição |
|---|---|
| `segment` | índice (0..N-1) |
| `timestamp` | ISO 8601 do fim do download |
| `server_id` | ID do servidor ativo (`A`, `srv-B`) |
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
    {"id": "srv-B", "url": "http://...:8081", "priority": 2}
  ],
  "representations": [
    {"quality": "240p",  "bitrate_kbps": 200,  "url_path": "/segment/240p"},
    {"quality": "1080p", "bitrate_kbps": 3000, "url_path": "/segment/1080p"}
  ]
}
```

O cliente também aceita o nome antigo `qualities` com campo `name`, por compatibilidade.
