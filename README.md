# Projeto Final TR2

## Streaming Adaptativo com Failover

Cliente de streaming ABR (Adaptive Bitrate) para a disciplina Teleinformática e Redes 2. Implementa download de segmentos via HTTP, seleção dinâmica de qualidade, gestão de buffer com continuous play, e failover automático entre servidores por prioridade.

Servidor da disciplina: `http://137.131.178.229:8080` (primário, `A`) e `:8081` (secundário, `B`).

## Estado atual

| Entrega | Política | Status |
|---|---|---|
| 1 | P1 - Rate-Based baseline | ✅ |
| 2 | Análise de deficiências + P2 (histerese) + failover A→B | ✅ |
| Final | P3 (EWMA/estatística) + Wireshark + relatório | pendente |

## Estrutura

```
client.py              # cliente: P1 e P2, buffer com pacing real-time, failover
server.py              # mock da infra (A/B): muda banda ao vivo (/control) e e killable
experiment.py          # gera os artefatos do relatorio (controlado + failover)
graph.py               # graficos (individual e comparativo, com marcador de failover)
results/
  baseline/      metrics.csv  throughput_quality.png        # Entrega 1: P1 no real, rede estavel
  controlled/    metrics_p1.csv  metrics_p2.csv             # P1 vs P2, banda variavel (mock)
                 compare_quality.png  compare_buffer.png    #   -> a deficiencia (oscilacao) aparece
  failover/      metrics.csv  buffer_level.png  throughput_quality.png  # A->B
```

### Cenário controlado (mock)

O servidor real da disciplina é fixo (A=2000 / B=1000 kbps, sem variação) e não é derrubável pelos alunos. Em rede estável o baseline não tem deficiência para mostrar (P1≈P2) e failover é impossível sem matar o A. Então a comparação que prova a Tarefa 2 e o failover usam um servidor que mock, o mesmo tipo de cenário que o professor impõe ao vivo (mudar banda, derrubar o A). O `baseline/` é a referência "rede real estável".

## Instalação

Python 3.8+. O cliente e o servidor usam só a stdlib; `graph.py` precisa de `matplotlib`.

```bash
cd pf-tr2
python3 -m venv .venv
.venv/bin/pip install matplotlib
```

## Como rodar

### Experimento completo (recomendado)

Gera os artefatos do relatório: P1 vs P2 no mock com banda variável e o failover.

```bash
.venv/bin/python experiment.py --mode all --segments 30 --outdir results
```

O playback é em **tempo real** (buffer limitado), então cada run leva ~`segments * 2s`. Use `--segments` menor para iterar mais rápido.

Modos: `--mode controlled`, `--mode failover`, `--mode all`. Opções: `--max-buffer` (default 20), `--profile`, `--bw-noise`, `--kill-after`.

### Cliente avulso

```bash
# P1 contra o servidor real (rede estavel)
.venv/bin/python client.py --policy p1 --server http://137.131.178.229:8080 -n 30 --max-buffer 20 -o p1_real.csv

# politica 2 (histerese) contra o mock local
.venv/bin/python client.py --policy p2 --server http://127.0.0.1:8090 -n 30 --max-buffer 20 -o p2_mock.csv
```

Flags do cliente:
- `--policy {p1,p2}` - política ABR (default `p1`)
- `--confirm N` - P2: segmentos consecutivos para confirmar mudança de qualidade (default 3)
- `--max-buffer S` - teto do buffer em segundos / playback real-time (default 10; experimento usa 20)
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

Resolve a deficiência de oscilação do baseline.

## Análise de deficiências (P1 vs P2)

No servidor real (rede estável), P1 e P2 são equivalentes: ambas convergem para 480p, sem oscilação (a P2 fica só 1 troca atrás pelo slow-start). Ou seja, a deficiência não existe quando a banda não varia - por isso a comparação que prova o ponto é a controlada, com banda variável.

Cenário controlado (`results/controlled/`): mesmo cliente contra o mock - ramp a 1600 kbps (seg 0-7) e platô ruidoso a 1100 kbps (seg 8+, `bw-noise=0.22`), onde a vazão medida (~950 kbps) faz a estimativa cruzar a fronteira de 700 kbps (480p).

| Métrica | P1 | P2 |
|---|---|---|
| trocas de qualidade | 5 | 2 |
| rebuffers | 0 | 0 |
| bitrate médio (kbps) | 663 | 620 |

Aqui a deficiência aparece: o baseline oscila (flapa 480p↔360p toda vez que o ruído cruza a fronteira), enquanto a P2 segura 480p. P2 reduz as trocas em ~60% mantendo praticamente a mesma qualidade média (a diferença é só o slow-start). Ver `results/controlled/compare_quality.png`.

## Failover

- Lê a lista `servers` do manifest e ordena por `priority`.
- Em falha do servidor ativo (timeout / conexão recusada / status ≠ 200): faz `GET /health` no próximo da fila, migra para o primeiro saudável, re-baixa o segmento no novo servidor, incrementa `failover_total` e troca `server_id`.
- O `buffer_can_play` na linha do evento indica se o buffer absorveu a troca sem rebuffer.

Failover só é testável num servidor que controlamos (o real não é derrubável; na apresentação quem mata o A é o professor, ao vivo). No cenário do mock (A=1500, B=1000 kbps), derrubando A no segmento 12:

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
