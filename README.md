# Projeto Final TR2

## Streaming Adaptativo com Failover

Cliente de streaming ABR (Adaptive Bitrate) para a disciplina Teleinformática e Redes 2. Implementa download de segmentos a partir de um servidor HTTP, seleção dinâmica de qualidade baseada em vazão medida, gerenciamento de buffer e (nas próximas entregas) failover automático entre servidores.

Servidor da disciplina: `http://137.131.178.229:8080` (primário) e `:8081` (secundário).

## Estado atual

| Entrega | Política |
|---|---|
| 1 | P1 - Rate-Based baseline |
| 2  | P2 - Rate-Based com histerese + failover A→B |
| Final | P3 - EWMA + penalidade de jitter, Wireshark, relatório |

## Estrutura

```
client.py                    # cliente baseline (P1) + runner
graph.py                     # gera 3 gráficos a partir do CSV
metrics_baseline.csv         # saída da última execução
throughput_quality.png       # vazão medida vs bitrate selecionado
buffer_level.png             # nível do buffer + eventos de rebuffer
jitter.png                   # jitter por segmento e EWMA
```

## Instalação

Requer Python 3.8+. O cliente usa apenas a stdlib; `graph.py` precisa de `matplotlib`.

```bash
cd pf-tr2
python3 -m venv .venv
.venv/bin/pip install matplotlib
```

## Como rodar

### Cliente baseline

```bash
python3 client.py                           # 30 segmentos, servidor padrão
python3 client.py -n 10                     # demo da Entrega 1 (10 segmentos)
python3 client.py --server http://outra/   -o p1_estavel.csv
python3 client.py -n 30 -o metrics_baseline.csv
```

Flags:
- `--server URL` - base do servidor (default `http://137.131.178.229:8080`)
- `-n, --segments N` - número de segmentos (default 30)
- `-o, --output FILE` - caminho do CSV de saída (default `metrics_baseline.csv`)

### Gráficos

```bash
.venv/bin/python graph.py -i metrics_baseline.csv -d .
```

Gera `throughput_quality.png`, `buffer_level.png` e `jitter.png` no diretório indicado.

## Política 1 (baseline) - como decide

`RateBasedABR`:

1. Mantém histórico das **últimas 3 vazões** medidas.
2. Estima a próxima vazão = média do histórico × **0.8** (safety factor).
3. Seleciona o **maior bitrate** das `representations` que seja ≤ estimativa.
4. Sem histórico (primeiro segmento) → menor qualidade disponível.

`BufferManager`:

- Nível em segundos. `add_segment()` soma `segment_duration_s`.
- `consume()` subtrai o tempo real decorrido desde a chamada anterior.
- Se decorrido > nível → registra `rebuffer_event` e `stall_duration_s`.
- `can_play(threshold=2.0)` indica se o buffer está acima do mínimo de 2s.

## CSV gerado

| Coluna | Descrição |
|---|---|
| `segment` | índice (0..N-1) |
| `timestamp` | ISO 8601 do fim do download |
| `server_id` | ID do servidor ativo (`A`, `srv-B`, ...) |
| `quality` | nome da qualidade (`240p`, `360p`, ...) |
| `bitrate_kbps` | bitrate nominal escolhido |
| `throughput_kbps` | vazão medida no download |
| `download_time_s` | duração do download |
| `jitter_network_ms` | jitter médio entre chunks de 4KB |
| `jitter_ewma_ms` | jitter suavizado (α=0.2) entre segmentos |
| `buffer_level_s` | nível do buffer após receber o segmento |
| `buffer_can_play` | 1 se buffer ≥ 2s, senão 0 |
| `rebuffer_event` | 1 se houve stall antes deste segmento |
| `stall_duration_s` | duração do stall em segundos |
| `failover_total` | contagem acumulada de trocas de servidor |

## Gráficos

- **throughput_quality.png** - se a curva vermelha (bitrate) acompanha a azul (vazão) com folga, o ABR está calibrado. Se a vermelha "salta" muito = oscilação.
- **buffer_level.png** - linha verde subindo continuamente é saúde. Cruzamentos com a linha pontilhada (2s) e marcadores X vermelhos indicam rebuffer.
- **jitter.png** - picos na cinza são esperados. A roxa (EWMA) deve subir só quando a rede degrada de verdade.

## Manifest esperado

```json
{
  "version": "2.0",
  "segment_duration_s": 2,
  "servers": [
    {"id": "A", "url": "http://...:8080", "priority": 1},
    {"id": "srv-B", "url": "http://...:8081", "priority": 2}
  ],
  "representations": [
    {"quality": "240p",  "bitrate_kbps": 200,  "url_path": "/segment/240p"},
    {"quality": "1080p", "bitrate_kbps": 3000, "url_path": "/segment/1080p"}
  ]
}
```

O cliente também aceita o nome antigo `qualities` com campo `name`, por compatibilidade.

Resultado esperado em rede estável: zero rebuffers, buffer crescendo monotonicamente até ~15s, ABR estabilizando em uma qualidade compatível com a banda disponível (~480p para 1.2 Mbps medidos).
